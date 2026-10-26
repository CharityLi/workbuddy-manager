"""账号显示名：**备注优先、昵称兜底**。

背景：用户给账号起备注（如「主号」「测试号」），就是为了在列表里一眼认出来；
所以凡是「展示账号」的地方，默认都该显示备注，没备注才退回上游昵称。

两个例外，都别绕过本模块自己拼：

- **账号页的主名称**相反：主行显示上游昵称（那是账号本来的名字，便于和上游
  后台/其他设备对照），备注放第二行。那里读 `nickname_raw`（本模块保留的原始
  昵称）而不是 `nickname`（已被覆盖成备注）。
- **上游日志里只有 uid 前 8 位**的那些行（签到记录、任务记录）：昵称本身要靠
  `uid` 前缀解析出来（见 `routers/accounts._account_resolver`），所以调用方要
  **先解析昵称、再调 apply()** —— 顺序反了会把前缀当成昵称。

为什么在服务端出口统一覆盖，而不是前端逐处 `a.note || a.nickname`：前端有十几处
直接读 `nickname`（账号页两套视图、仪表盘、请求日志、签到记录、代理绑定、各种
弹窗），逐处改必然漏一处，而且漏了不报错——只是那一处还显示旧名字。
"""

from __future__ import annotations

from typing import Callable

from .. import db


def notes() -> dict[str, str]:
    """全部备注 `{uid: note}`。一次取回，避免按行逐条查库。"""
    return db.account_notes()


def display_name(uid: str, raw_name: str, notes_map: dict[str, str] | None = None) -> str:
    """单个账号的显示名：备注 → 原始昵称 → uid 前 8 位。"""
    note = (notes_map or notes()).get(str(uid or ''), '')
    return note or (raw_name or '').strip() or str(uid or '')[:8]


def apply(
    rows: list[dict],
    *,
    notes_map: dict[str, str] | None = None,
    name_key: str = 'nickname',
    uid_of: Callable[[str], str] | None = None,
    fallback_uid: bool = False,
) -> None:
    """就地给一批行注入 `note` / `nickname_raw`，并把 `name_key` 覆盖成显示名。

    `rows` 里每行至少要能从 `uid` 取到账号标识；`name_key` 指向该行现有的昵称
    字段（可能是空串——上游只打 uid 前 8 位的历史记录就是这种）。

    `uid_of`：把行里的 uid 归一成**完整 uid** 的回调。签到记录 / 任务记录里的 uid
    来自上游日志，只有前 8 位，而备注按完整 uuid 存 —— 不做这一步，那两类记录
    永远查不到备注（用户反馈「任务记录里还是显示老昵称」就是这个）。

    `fallback_uid`：备注和昵称都没有时要不要用 uid 前 8 位顶上。账号列表要（一行
    里总得有东西可看），日志类记录**不要** —— 那里宁可留空，也不要把一个认不出
    是谁的 uid 片段当名字显示（既有测试按这个语义钉着：
    `test_tasklog_collect.test_unknown_uid_returns_empty`）。
    """
    if not rows:
        return
    mapping = notes_map if notes_map is not None else notes()
    for row in rows:
        uid = str(row.get('uid') or '')
        if uid_of is not None:
            uid = str(uid_of(uid) or uid)
        raw = str(row.get(name_key) or row.get('display_name') or '').strip()
        note = mapping.get(uid, '')
        row['note'] = note
        row['nickname_raw'] = raw
        row.setdefault('display_name_raw', raw)
        row[name_key] = note or raw or (uid[:8] if fallback_uid else '')
