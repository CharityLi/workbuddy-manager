"""页面监控：配置盯哪些页面、看抓取结果与「哪次变了」的事件。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .. import security
from ..services import pagewatch

router = APIRouter(prefix='/api', tags=['watch'])


@router.get('/watch/pages')
async def list_pages(user: dict = Depends(security.require_admin)) -> dict:
    return {'items': pagewatch.list_pages()}


@router.post('/watch/pages')
async def add_page(payload: dict = Body(...),
                   user: dict = Depends(security.require_admin)) -> dict:
    try:
        return pagewatch.add_page(payload.get('url') or '',
                                  payload.get('name') or '',
                                  int(payload.get('interval_min') or 30))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch('/watch/pages/{page_id}')
async def toggle_page(page_id: int, payload: dict = Body(...),
                      user: dict = Depends(security.require_admin)) -> dict:
    try:
        return pagewatch.set_enabled(page_id, bool(payload.get('enabled', True)))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete('/watch/pages/{page_id}')
async def delete_page(page_id: int,
                      user: dict = Depends(security.require_admin)) -> dict:
    pagewatch.delete_page(page_id)
    return {'ok': True}


@router.post('/watch/pages/{page_id}/check')
async def check_now(page_id: int,
                    user: dict = Depends(security.require_admin)) -> dict:
    try:
        return await pagewatch.check_now(page_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get('/watch/events')
async def list_events(page_id: int | None = Query(None),
                      limit: int = Query(50, ge=1, le=200),
                      user: dict = Depends(security.require_admin)) -> dict:
    return {'items': pagewatch.events(page_id, limit)}
