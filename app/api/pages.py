"""扫描页面查询、JPEG 文件和删除接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import FileResponse

from app.api.dependencies import ApplicationContext, get_context
from app.api.responses import page_payload, require_identifier, success
from app.models.records import ScanPageRecord
from app.services.task_service import TaskNotFoundError


router = APIRouter(prefix="/tasks/{task_id}/pages", tags=["页面"])


def _page_urls(request: Request, task_id: str, page_id: str) -> tuple[str, str]:
    original = str(
        request.url_for(
            "get_original",
            task_id=task_id,
            page_id=page_id,
        )
    )
    thumbnail = str(
        request.url_for(
            "get_thumbnail",
            task_id=task_id,
            page_id=page_id,
        )
    )
    return original, thumbnail


def _record_payload(request: Request, page: ScanPageRecord) -> dict[str, Any]:
    original, thumbnail = _page_urls(request, page.task_id, page.page_id)
    return page_payload(
        page,
        original_url=original,
        thumbnail_url=thumbnail,
    )


def _require_task(context: ApplicationContext, task_id: str):
    task_id = require_identifier(task_id, "taskId")
    task = context.task_service.get(task_id)
    if task is None:
        raise TaskNotFoundError(task_id)
    return task_id


@router.get("", name="list_pages")
def list_pages(
    request: Request,
    task_id: str = Path(...),
    after_sequence: int | None = Query(default=None, alias="afterSequence"),
    context: ApplicationContext = Depends(get_context),
):
    task_id = _require_task(context, task_id)
    if after_sequence is not None and after_sequence < 0:
        raise ValueError("afterSequence 必须是非负整数")
    pages = context.page_repository.list_by_task(task_id, after_sequence=after_sequence)
    values = [_record_payload(request, page) for page in pages]
    return success(
        {
            "items": values,
            "pages": values,
            "total": len(values),
            "afterSequence": after_sequence,
        }
    )


@router.get("/{page_id}/thumbnail", name="get_thumbnail")
def get_thumbnail(
    task_id: str = Path(...),
    page_id: str = Path(...),
    context: ApplicationContext = Depends(get_context),
):
    page = _get_page(context, task_id, page_id)
    path = context.page_service.resolve_page_file(page, kind="thumbnail")
    return FileResponse(path, media_type="image/jpeg", filename=f"{page.page_id}.jpg")


@router.get("/{page_id}/original", name="get_original")
def get_original(
    task_id: str = Path(...),
    page_id: str = Path(...),
    context: ApplicationContext = Depends(get_context),
):
    page = _get_page(context, task_id, page_id)
    path = context.page_service.resolve_page_file(page, kind="original")
    return FileResponse(path, media_type="image/jpeg", filename=f"{page.page_id}.jpg")


@router.get("/{page_id}", name="get_page")
def get_page(
    request: Request,
    task_id: str = Path(...),
    page_id: str = Path(...),
    context: ApplicationContext = Depends(get_context),
):
    page = _get_page(context, task_id, page_id)
    return success(_record_payload(request, page))


@router.delete("/{page_id}", name="delete_page")
def delete_page(
    task_id: str = Path(...),
    page_id: str = Path(...),
    context: ApplicationContext = Depends(get_context),
):
    task_id = require_identifier(task_id, "taskId")
    page_id = require_identifier(page_id, "pageId")
    context.page_service.delete_page(task_id, page_id)
    context.event_hub.publish(
        {
            "event": "page_deleted",
            "taskId": task_id,
            "data": {"pageId": page_id},
        }
    )
    return success({"taskId": task_id, "pageId": page_id})


def _get_page(context: ApplicationContext, task_id: str, page_id: str) -> ScanPageRecord:
    task_id = require_identifier(task_id, "taskId")
    page_id = require_identifier(page_id, "pageId")
    if context.task_service.get(task_id) is None:
        raise TaskNotFoundError(task_id)
    page = context.page_repository.get(task_id, page_id)
    if page is None:
        from app.errors import ApiError

        raise ApiError("PAGE_NOT_FOUND")
    return page


__all__ = ["router"]
