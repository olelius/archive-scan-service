"""WebSocket 事件接口。"""

from __future__ import annotations

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.api.dependencies import ApplicationContext
from app.api.responses import require_identifier
from app.services.event_hub import EventHubClosedError


router = APIRouter(tags=["事件"])


@router.websocket("/events", name="events")
async def events(
    websocket: WebSocket,
    task_id: str | None = Query(default=None, alias="taskId"),
):
    if task_id is not None:
        try:
            task_id = require_identifier(task_id, "taskId")
        except ValueError:
            await websocket.close(code=1008, reason="taskId 无效")
            return
    await websocket.accept()
    context: ApplicationContext = websocket.app.state.context
    try:
        async with context.event_hub.subscribe(task_id=task_id) as subscription:
            while True:
                await websocket.send_json(await subscription.receive())
    except (WebSocketDisconnect, EventHubClosedError):
        return


__all__ = ["router"]
