#!/usr/bin/env bash
# 补齐 launchd 作业：只注册「该在跑但没被加载」的服务，已加载的一律不动。
#
# 背景（2026-10-26 事故）：DSH 会话把 com.wb2api.* 三个 label 全部 bootout 之后，
# manager 因为 server/main.py 的 IndentationError 起不来，launchd 的 KeepAlive
# 变成「spawn→崩溃→再 spawn」的空转，最终该 label 彻底不在 gui 域里，
# 面板与 DSH 的模型请求全断（链路：DSH → 7864 → 7863）。
#
# 与 scripts/restart-safe.sh 的分工：
#   restart-safe.sh  负责「已经在托管中」的服务原地重启，且**明确禁止 bootout**；
#   本脚本        负责「托管关系已经丢了」的补注册，绝不 kill 任何进程，
#                 也不 bootout，因此不会把还在跑的服务弄断。
#
# 用法（**必须在自己的 Terminal 里跑**：本机实测 IDE/受管进程树
# 向 gui 域 bootstrap 会稳定返回 EIO，连一个 /bin/sleep 的最小作业也一样）：
#   scripts/ensure-agents.sh          # 补齐 + 健康检查
#   scripts/ensure-agents.sh --check  # 只看状态，不做任何变更
set -uo pipefail

USER_DOMAIN="gui/$(id -u)"
AGENTS_DIR="$HOME/Library/LaunchAgents"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

# label:端口:健康检查 URL，顺序按依赖方向（网关在前，管理端最后）
TARGETS=(
  "com.wb2api.gateway:7863:http://127.0.0.1:7863/healthz"   # wb2api 网关，默认分组
  "com.wb2api.web:3000:http://127.0.0.1:3000/"              # Next 前端面板
  "com.wb2api.manager:7864:http://127.0.0.1:7864/healthz"   # Python 管理端后端
)

rc=0
for t in "${TARGETS[@]}"; do
  label="${t%%:*}"; rest="${t#*:}"; port="${rest%%:*}"; url="${rest#*:}"
  plist="$AGENTS_DIR/$label.plist"

  if launchctl print "$USER_DOMAIN/$label" >/dev/null 2>&1; then
    printf '[skip] %-22s 已被 launchd 托管\n' "$label"
  elif [ "$CHECK_ONLY" = "1" ]; then
    printf '[miss] %-22s 未被托管（plist 存在？%s）\n' "$label" \
      "$([ -f "$plist" ] && echo yes || echo NO)"
    rc=1
    continue
  elif [ ! -f "$plist" ]; then
    printf '[err ] %-22s plist 不存在：%s\n' "$label" "$plist"; rc=1; continue
  else
    if launchctl bootstrap "$USER_DOMAIN" "$plist" 2>&1; then
      printf '[boot] %-22s 已注册\n' "$label"
    else
      printf '[err ] %-22s bootstrap 失败（EIO 时请确认是在自己的 Terminal 里执行）\n' "$label"
      rc=1; continue
    fi
  fi

  # 等端口真的开始监听，别拿 bootstrap 的 0 当作「服务好了」
  waited=0
  while [ -z "$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)" ]; do
    sleep 0.2; waited=$((waited + 1))
    [ "$waited" -ge 100 ] && break
  done
  pid="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)"
  if [ -z "$pid" ]; then
    printf '[down] %-22s 20s 内没有监听 %s（看 /tmp/wbm-server.err.log）\n' "$label" "$port"
    rc=1; continue
  fi
  code="$(curl -s --noproxy '*' -o /dev/null -m 4 -w '%{http_code}' "$url" 2>/dev/null)"
  printf '[ ok ] %-22s pid=%s port=%s http=%s\n' "$label" "$pid" "$port" "$code"
done

exit $rc
