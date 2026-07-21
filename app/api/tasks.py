"""扫描任务和扫描动作接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Path

from app.api.dependencies import ApplicationContext, get_context
from app.api.responses import require_identifier, success, task_payload


router = APIRouter(prefix="/tasks", tags=["任务"])


def _task_data(context: ApplicationContext, task: Any) -> dict[str, Any]:
    page_count = len(context.page_repository.list_by_task(task.task_id))
    return task_payload(task, page_count=page_count)


@router.post("", name="create_task")
def create_task(
    body: dict[str, Any] | None = Body(default=None),
    context: ApplicationContext = Depends(get_context),
):
    if body is None:
        body = {}
    task = context.create_task(body)
    return success(_task_data(context, task))


@router.get("", name="list_tasks")
def list_tasks(context: ApplicationContext = Depends(get_context)):
    tasks = context.task_service.list_tasks()
    values = [_task_data(context, task) for task in tasks]
    return success({"items": values, "tasks": values, "total": len(values)})


@router.get("/{task_id}", name="get_task")
def get_task(
    task_id: str = Path(...),
    context: ApplicationContext = Depends(get_context),
):
    task = context.task_service.get(require_identifier(task_id, "taskId"))
    if task is None:
        from app.services.task_service import TaskNotFoundError

        raise TaskNotFoundError(task_id)
    return success(_task_data(context, task))


@router.post("/{task_id}/scan/start", name="start_scan")
def start_scan(
    task_id: str = Path(...),
    body: dict[str, Any] | None = Body(default=None),
    context: ApplicationContext = Depends(get_context),
):
    task = context.start_scan(require_identifier(task_id, "taskId"), body)
    return success(_task_data(context, task))


@router.post("/{task_id}/scan/stop", name="stop_scan")
def stop_scan(
    task_id: str = Path(...),
    context: ApplicationContext = Depends(get_context),
):
    task = context.stop_scan(require_identifier(task_id, "taskId"))
    return success(_task_data(context, task))


@router.post("/{task_id}/scan/complete", name="complete_scan")
def complete_scan(
    task_id: str = Path(...),
    context: ApplicationContext = Depends(get_context),
):
    task = context.complete_scan(require_identifier(task_id, "taskId"))
    return success(_task_data(context, task))


@router.delete("/{task_id}", name="delete_task")
def delete_task(
    task_id: str = Path(...),
    context: ApplicationContext = Depends(get_context),
):
    task_id = require_identifier(task_id, "taskId")
    context.delete_task(task_id)
    return success({"taskId": task_id})


__all__ = ["router"]
