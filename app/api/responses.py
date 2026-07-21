"""统一 JSON 响应和领域记录序列化。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from fastapi.responses import JSONResponse

from app.models.records import ScanPageRecord, ScanTaskRecord


def success(data: Any, message: str = "操作成功") -> JSONResponse:
    """返回统一成功响应。"""

    return JSONResponse(
        status_code=200,
        content={"code": 200, "message": message, "data": data},
    )


def _snapshot(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def task_payload(task: ScanTaskRecord, *, page_count: int = 0) -> dict[str, Any]:
    """把 SQLite 任务记录转换为不含本机绝对路径的 JSON。"""

    return {
        "taskId": task.task_id,
        "deviceId": task.device_id,
        "status": task.status.value,
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
        "lastPageSequence": task.last_page_sequence,
        "pageCount": page_count,
        "errorCode": task.error_code,
        "errorMessage": task.error_message,
        "deviceSnapshot": _snapshot(task.device_snapshot_json),
        "capabilitySnapshot": _snapshot(task.capability_snapshot_json),
        "scanParamsSnapshot": _snapshot(task.scan_params_snapshot_json),
    }


def page_payload(
    page: ScanPageRecord,
    *,
    original_url: str,
    thumbnail_url: str,
) -> dict[str, Any]:
    """把 SQLite 页面记录转换为页面元数据和接口 URL。"""

    return {
        "pageId": page.page_id,
        "taskId": page.task_id,
        "sequence": page.sequence,
        "originalPath": page.original_path,
        "thumbnailPath": page.thumbnail_path,
        "originalUrl": original_url,
        "thumbnailUrl": thumbnail_url,
        "sha256": page.sha256,
        "fileSize": page.file_size,
        "createdAt": page.created_at,
        "width": page.width,
        "height": page.height,
    }


def device_payload(device: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(device)
    payload.setdefault("online", True)
    return payload


def capabilities_payload(
    device_id: str,
    capabilities: list[Mapping[str, Any]],
) -> dict[str, Any]:
    values = [dict(item) for item in capabilities]
    return {
        "deviceId": device_id,
        "capabilities": values,
        "items": values,
        "count": len(values),
    }


def extract_settings(body: Mapping[str, Any] | None) -> dict[str, Any]:
    """兼容 `{settings: {...}}` 和直接传固定配置对象。"""

    if body is None:
        return {}
    if not isinstance(body, Mapping):
        raise ValueError("请求体必须是 JSON 对象")
    nested = body.get("settings")
    if nested is not None:
        if not isinstance(nested, Mapping):
            raise ValueError("settings 必须是 JSON 对象")
        return dict(nested)
    return dict(body)


def require_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value


def require_identifier(value: Any, field_name: str) -> str:
    result = require_string(value, field_name)
    if result in {".", ".."} or "/" in result or "\\" in result:
        raise ValueError(f"{field_name} 必须是单段标识")
    return result


__all__ = [
    "capabilities_payload",
    "device_payload",
    "extract_settings",
    "page_payload",
    "require_identifier",
    "require_string",
    "success",
    "task_payload",
]
