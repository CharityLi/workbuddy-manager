#!/usr/bin/env bash
# 安全重启：让「重启不打断会话」这件事变成一条命令。
#
# 背景（2026-10-26 实测）：这台机器上 DSH 的模型请求走
#   DSH → 7864（管理端）→ 7863（wb2api 默认分组）
# 所以随便 kill 一下，正在跑的对话就会拿到 502。而重启本身是必需的（改代码、
# 改配置都要）。下面这套流程把「空窗」压到 1.5–2 秒，并且**保证一定被拉起**：
# 三个服务都是 launchd 托管的，`kill -TERM` 之后 launchd 的 KeepAlive 会自己
# 重新拉起——测过：管理端 1.5–2.0s、网关 1–3s 就回来了。
#
# 用法：
#   scripts/restart-safe.sh manager     # 7864 管理端后端
#   scripts/restart-safe.sh web         # 3000 Next 前端
#   scripts/restart-safe.sh gateway     # 7863 wb2api 默认分组
#   scripts/restart-safe.sh all         # 三个都重启（顺序：gateway → web → manager）
#   scripts/restart-safe.sh check       # 只看状态，不动任何东西
#
# 铁律（都是踩过的坑，别改）：
#   1. 取 PID 一律 `lsof -nP -iTCP:<port> -sTCP:LISTEN -t`。
#      少了 `-sTCP:LISTEN` 会把**客户端连接**也算进来（管理端连着上游 7863，
#      于是你会 kill 到管理端而不是网关）。
#   2. 不要用 `launchctl kickstart -k`：实测连按两次 PID 不变，等于没重启。
#   3. 不要 `launchctl bootout`：它在这台机器上既不报错也**不杀进程**，
#      反而会让 launchd 的服务清单与实际进程脱节（曾导致一个孤儿进程占着
#      7863，launchd 反复 spawn 又 bind 失败）。
#   4. 停进程必须**等它真的消失**再动文件/再启动。wb2api 优雅退出时会最后
#      持久化一次 state.json，会把你刚改的内容覆盖回去（兰亭序就是这么"复活"的）。
#   5. 7865（流水账号独立实例）**不由 launchd 托管**，本脚本不碰它；
#      它靠 instance-liushui/start-liushui.sh 手动起。
#   6. 重启前要清掉**重复进程**（2026-10-26 实测的「持续中断」元凶）：
#      曾出现一个孤儿 wb2api 占着 7863 而 launchd 每 10 秒 spawn 一个新实例、
#      bind 失败、退出、再 spawn —— 日志里攒了 1.6 万行 `bind: address already
#      in use`。这期间服务时好时坏、会话反复断，而 `check` 只看端口，一切"正常"。
#      所以现在重启前会把同指纹的多余进程一起 TERM 掉，重启后还会回看日志，
#      一旦又冒出 bind 失败就明确报错。
set -uo pipefail

# 仓库位置可用环境变量覆盖（本机默认值写在这里，换机器/换目录不用改脚本）：
#   WB2API_MANAGER_DIR  管理端仓库根（默认=脚本所在目录的上一级，clone 到哪都行）
#   WB2API_PANEL_DIR    上游 wb2api 面板目录（只用于读 launchd label 和网关日志，
#                       指错只影响 bind 失败自检，不影响起停本身）
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANAGER_DIR="${WB2API_MANAGER_DIR:-$(cd "$_here/.." && pwd)}"
PANEL_DIR="${WB2API_PANEL_DIR:-$HOME/WorkBuddy/2026-10-13-13-17-27/ref/workbuddy2api-panel}"
USER_DOMAIN="gui/$(id -u)"

say() { printf '%s\n' "$*"; }
err() { printf '[err] %s\n' "$*" >&2; }

# 端口 → (launchd label, 健康检查 URL, 允许的空窗秒数)
resolve() {
  case "$1" in
    manager) LABEL=com.wb2api.manager; PORT=7864; HEALTH="http://127.0.0.1:7864/healthz"; BUDGET=40 ;;
    web)     LABEL=com.wb2api.web;     PORT=3000; HEALTH="http://127.0.0.1:3000/";        BUDGET=90 ;;
    gateway) LABEL=com.wb2api.gateway; PORT=7863; HEALTH="http://127.0.0.1:7863/healthz"; BUDGET=40 ;;
    *) err "不认识的目标：$1（可用：manager / web / gateway / all / check）"; return 1 ;;
  esac
}

listener_pid() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1; }

job_loaded() { launchctl print "$USER_DOMAIN/$1" >/dev/null 2>&1; }

status_line() {
  local p="${1:-}" code
  if [ -n "$p" ]; then
    code="$(curl -s -o /dev/null -m 3 -w '%{http_code}' "$2" 2>/dev/null)"
    say "  [ok] $3 pid=$p http=$code"
  else
    say "  [down] $3 没有监听进程"
    return 1
  fi
}

check_all() {
  local rc=0
  for pair in "3000:web:com.wb2api.web" "7863:gateway:com.wb2api.gateway" \
              "7864:manager:com.wb2api.manager" "7865:liushui(手动):-"; do
    port="${pair%%:*}"; rest="${pair#*:}"; name="${rest%%:*}"
    p="$(listener_pid "$port")"
    if [ -n "$p" ]; then say "  [UP]   $port  $name  pid=$p"; else say "  [DOWN] $port  $name"; rc=1; fi
  done
  # 重复进程 / bind 失败循环：端口照常 UP，但服务随时会在下一次重启时翻车。
  for name in gateway manager; do
    dup="$(dup_pids "$name")"
    set -- $dup
    if [ "$#" -gt 1 ]; then
      say "  [warn] $name 有 $# 个进程：$dup —— launchd 托管的那一个之外都是孤儿"
      rc=1
    fi
  done
  be="$(recent_bind_errors 300)"
  if [ "${be:-0}" -gt 0 ]; then
    say "  [warn] 网关最近 5 分钟有 $be 次 bind 失败（有进程占着 7863，launchd 在空转重启）"
    rc=1
  fi
  return $rc
}

# 与服务对应的**命令行指纹**。只匹配本服务的默认实例：
#   网关用默认分组的 config.json 路径，天然排除 7865 的 instance-liushui/config.json；
#   管理端用 uvicorn 的 app 路径；前端用 next-server。
proc_pattern() {
  case "$1" in
    gateway) printf '%s' "$PANEL_DIR/config.json" ;;
    manager) printf '%s' 'server.main:app' ;;
    web)     printf '%s' 'next-server' ;;
    *)       printf '%s' '' ;;
  esac
}

# 同一指纹下的所有 pid（空格分隔），用于发现「多起了一份」。
# **显式排除任何 `instance-*` 的进程**：那是 7865 流水实例（铁律 5，绝不能碰）。
# 指纹本身已经不含它的路径，这里是第二道闸——kill 错进程的代价太大，不嫌多。
dup_pids() {
  local pat pid out=''
  pat="$(proc_pattern "$1")"
  [ -n "$pat" ] || return 0
  for pid in $(pgrep -f -- "$pat" 2>/dev/null); do
    case "$(ps -o command= -p "$pid" 2>/dev/null)" in
      *instance-*) continue ;;
    esac
    out="$out$pid "
  done
  printf '%s' "$out"
}

# 网关日志里最近 N 秒的 bind 失败次数。>0 说明有进程占着端口、
# launchd 正在做「spawn→失败→再 spawn」的无谓循环。
recent_bind_errors() {
  local secs="${1:-120}" cutoff log="$PANEL_DIR/data/gateway.err.log"
  [ -f "$log" ] || { printf '0'; return 0; }
  cutoff="$(date -v-"${secs}"S "+%Y/%m/%d %H:%M:%S" 2>/dev/null || true)"
  [ -n "$cutoff" ] || { printf '0'; return 0; }
  awk -v c="$cutoff" '/bind: address already in use/ { if (substr($0,1,19) >= c) n++ } END { print n+0 }' "$log"
}

# 管理端在跑批量任务时重启会打断它：先问一下 /api/task-run。
manager_busy() {
  local tok
  tok="$(cd "$MANAGER_DIR" && ./server/.venv/bin/python - <<'PY' 2>/dev/null
import sys; sys.path.insert(0, '.')
from server import security
print(security.issue_token('admin', 'admin'))
PY
)"
  [ -n "${tok:-}" ] || return 1
  out="$(curl -s -m 3 "http://127.0.0.1:7864/api/task-run" -H "Cookie: wb_session=$tok" 2>/dev/null)"
  case "$out" in
    *'"running":true'*|*'"running": true'*) return 0 ;;
  esac
  return 1
}

restart_one() {
  resolve "$1" || return 1
  local old new gap=0
  old="$(listener_pid "$PORT")"

  if ! job_loaded "$LABEL"; then
    err "$LABEL 没有被 launchd 托管 —— 现在 kill 掉就没人拉起了，拒绝执行。"
    err "先跑：launchctl bootstrap $USER_DOMAIN ~/Library/LaunchAgents/$LABEL.plist"
    return 1
  fi

  if [ "$1" = "manager" ] && manager_busy; then
    err "管理端正在跑批量任务（/api/task-run running=true）。"
    err "等任务跑完，或确认无所谓后手动：kill -TERM $old"
    return 1
  fi

  say "[1/3] $1 当前 pid=${old:-无}"

  # 重复进程先清掉（见文件头第 6 条）：只留正在监听的那个，其余 TERM。
  # 不留着它们，否则 launchd 拉起的新实例会 bind 失败，重启变成空转。
  dup="$(dup_pids "$1")"
  for dp in $dup; do
    [ "$dp" = "${old:-}" ] && continue
    say "      清掉重复进程 pid=$dp"
    kill -TERM "$dp" 2>/dev/null || true
  done

  if [ -n "$old" ]; then
    kill -TERM "$old" || { err "kill 失败"; return 1; }
    # 必须等进程真的消失：wb2api 退出时会再持久化一次 state.json
    while kill -0 "$old" 2>/dev/null; do
      sleep 0.2; gap=$((gap + 1))
      [ "$gap" -gt 100 ] && { err "进程 20s 内没退出"; return 1; }
    done
  fi
  say "[2/3] 已停止（launchd 会 KeepAlive 拉起），等待监听恢复…"

  local waited=0
  while [ "$waited" -lt $((BUDGET * 10)) ]; do
    sleep 0.1; waited=$((waited + 1))
    new="$(listener_pid "$PORT")"
    [ -n "$new" ] && [ "$new" != "$old" ] && break
    new=""
  done

  if [ -z "${new:-}" ]; then
    err "launchd 没在 ${BUDGET}s 内拉起（检查：launchctl print $USER_DOMAIN/${LABEL}）"
    return 1
  fi
  local ms=$((waited * 100))
  say "[3/3] 新 pid=${new}，空窗约 $((ms / 1000)).$((ms % 1000 / 100))s"
  status_line "$new" "$HEALTH" "$1" || return 1

  # 收尾回看：刚起的实例如果又 bind 失败过，说明还有别的进程占着端口，
  # 这次「重启成功」只是假象（旧的还在答话）。
  if [ "$1" = "gateway" ]; then
    local be; be="$(recent_bind_errors 20)"
    if [ "${be:-0}" -gt 0 ]; then
      err "重启后 20 秒内网关日志出现 $be 次 bind 失败 —— 端口仍被别的进程占着："
      lsof -nP -iTCP:7863 -sTCP:LISTEN 2>/dev/null | tail -n +1 >&2
      return 1
    fi
  fi
}

case "${1:-check}" in
  check) check_all ;;
  manager|web|gateway) restart_one "$1" ;;
  all)
    rc=0
    for t in gateway web manager; do restart_one "$t" || rc=1; done
    say "--- 全量状态 ---"; check_all || rc=1
    exit $rc ;;
  *) err "用法：$0 [check|manager|web|gateway|all]"; exit 2 ;;
esac
