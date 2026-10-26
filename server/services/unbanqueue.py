"""封控账号的解禁队列。

背景：账号被上游判 `12153 session dead` 后，网关会把它写进 state.json 的
`disabled=true` 并**只在启动时读一次**——所以在线改文件没用，必须"停网关 →
改 state → 启网关"。手工做这件事要记时间、记得停和启，容易漏。

这里把它做成队列：到点自动执行一次 `force_enable_account`，失败留痕而不是
静默重试到天荒地老（反复重启网关比账号晚解禁几分钟更糟）。

一个回合只处理**一个**账号：每次解禁都要重启网关（几秒空窗），
同时处理多个会让空窗叠加。排队多个时按 unlock_at 顺序一个一个来。
"""
from __future__ import annotations

import asyncio
import time

from .. import db
from . import wb2api

TICK_SECONDS = 60


def add(uid: str, file: str = '', nickname: str = '', reason: str = '',
        unlock_at: int | None = None) -> dict:
    """把账号加入解禁队列；同一账号已有 pending 项时改为更新它的时间。"""
    uid = str(uid or '').strip()
    if not uid:
        raise ValueError('uid 为空')
    now = int(time.time())
    at = int(unlock_at) if unlock_at else now
    rows = db.query(
        "SELECT id FROM unban_queue WHERE uid = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
        (uid,))
    if rows:
        qid = rows[0]['id']
        db.execute(
            'UPDATE unban_queue SET unlock_at = ?, reason = ?, file = ?, nickname = ?, updated_at = ? WHERE id = ?',
            (at, str(reason or ''), str(file or ''), str(nickname or ''), now, qid))
        return {'id': qid, 'updated': True, 'unlock_at': at}
    qid = db.execute(
        'INSERT INTO unban_queue (uid, file, nickname, reason, unlock_at, status, note, created_at, updated_at)'
        " VALUES (?, ?, ?, ?, ?, 'pending', '', ?, ?)",
        (uid, str(file or ''), str(nickname or ''), str(reason or ''), at, now, now))
    return {'id': qid, 'updated': False, 'unlock_at': at}


def items(status: str | None = None) -> list[dict]:
    if status:
        return db.query('SELECT * FROM unban_queue WHERE status = ? ORDER BY unlock_at ASC, id ASC', (status,))
    return db.query('SELECT * FROM unban_queue ORDER BY id DESC LIMIT 200')


def remove(qid: int) -> bool:
    """只删还没执行的项；已在执行/已完成的保留下来做审计。"""
    rows = db.query("SELECT id FROM unban_queue WHERE id = ? AND status = 'pending'", (int(qid),))
    if not rows:
        return False
    db.execute('DELETE FROM unban_queue WHERE id = ?', (int(qid),))
    return True


def due(now: int | None = None) -> list[dict]:
    return db.query(
        "SELECT * FROM unban_queue WHERE status = 'pending' AND unlock_at <= ? ORDER BY unlock_at ASC, id ASC",
        (int(now if now is not None else time.time()),))


async def run_due(now: int | None = None) -> dict:
    """处理到期的第一条。返回 {ran, ok, message, item}。"""
    rows = due(now)
    if not rows:
        return {'ran': False}
    item = rows[0]
    qid = int(item['id'])
    db.execute("UPDATE unban_queue SET status = 'running', updated_at = ? WHERE id = ?",
               (int(time.time()), qid))
    ok, message, _changed = await wb2api.force_enable_account(item['uid'])
    db.execute(
        "UPDATE unban_queue SET status = ?, note = ?, updated_at = ? WHERE id = ?",
        ('done' if ok else 'failed', str(message)[:500], int(time.time()), qid))
    return {'ran': True, 'ok': ok, 'message': message, 'item': item}


_task: asyncio.Task | None = None


async def _loop() -> None:
    # 启动后稍等一下再跑第一轮，避免和上游/前端的启动窗口挤在一起
    await asyncio.sleep(20)
    while True:
        try:
            await run_due()
        except Exception:  # noqa: BLE001
            # 队列本身出错不能把整个后端带崩：下一轮再试
            pass
        await asyncio.sleep(TICK_SECONDS)


def start_scheduler() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


def stop_scheduler() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
