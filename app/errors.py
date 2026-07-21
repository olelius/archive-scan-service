"""FastAPI 对外统一错误边界。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


LOGGER = logging.getLogger("archive_scan_service")


_ERROR_STATUS: dict[str, int] = {
    "TASK_NOT_FOUND": 404,
    "PAGE_NOT_FOUND": 404,
    "FILE_NOT_FOUND": 404,
    "TWAIN_SOURCE_NOT_FOUND": 404,
    "SCANNER_BUSY": 409,
    "TASK_STATE_INVALID": 409,
    "TASK_ALREADY_EXISTS": 409,
    "TWAIN_SOURCE_ALREADY_OPEN": 409,
    "TWAIN_UI_FORBIDDEN": 400,
    "INVALID_REQUEST": 400,
    "PAGE_PATH_INVALID": 400,
    "FILE_DELETE_FAILED": 500,
    "WORKER_UNAVAILABLE": 503,
}

_ERROR_CODE: dict[str, int] = {
    "INVALID_REQUEST": 4001,
    "TASK_NOT_FOUND": 4041,
    "PAGE_NOT_FOUND": 4042,
    "FILE_NOT_FOUND": 4043,
    "SCANNER_BUSY": 4091,
    "TASK_STATE_INVALID": 4092,
    "TASK_ALREADY_EXISTS": 4093,
    "TWAIN_SOURCE_NOT_FOUND": 4044,
    "FILE_DELETE_FAILED": 5001,
    "WORKER_UNAVAILABLE": 5031,
    "INTERNAL_ERROR": 5000,
}

_DEFAULT_MESSAGES: dict[str, str] = {
    "INVALID_REQUEST": "请求参数无效",
    "TASK_NOT_FOUND": "扫描任务不存在",
    "PAGE_NOT_FOUND": "扫描页面不存在",
    "FILE_NOT_FOUND": "页面文件不存在",
    "SCANNER_BUSY": "扫描仪正在被其他任务占用",
    "TASK_STATE_INVALID": "当前任务状态不允许执行该操作",
    "TASK_ALREADY_EXISTS": "扫描任务已存在",
    "TWAIN_SOURCE_NOT_FOUND": "TWAIN 设备不存在或当前不可用",
    "TWAIN_DSM_NOT_FOUND": "未找到或无法加载 64 位 TWAINDSM.DLL",
    "TWAIN_SOURCE_ENUMERATION_FAILED": "TWAIN 设备枚举失败",
    "TWAIN_SOURCE_OPEN_FAILED": "TWAIN 设备打开失败",
    "TWAIN_SOURCE_ALREADY_OPEN": "已有其他 TWAIN 设备处于打开状态",
    "TWAIN_SOURCE_NOT_OPEN": "TWAIN 设备尚未打开",
    "TWAIN_UI_FORBIDDEN": "禁止打开扫描仪厂商界面",
    "TWAIN_CAPABILITY_QUERY_FAILED": "TWAIN Capability 查询失败",
    "TWAIN_CAPABILITY_SET_FAILED": "TWAIN Capability 设置失败",
    "TWAIN_FILE_TRANSFER_UNSUPPORTED": "扫描设备不支持文件传输",
    "TWAIN_JPEG_UNSUPPORTED": "扫描设备不支持 JPEG 输出",
    "SCANNER_OFFLINE": "扫描仪当前离线",
    "PAPER_JAM": "扫描仪卡纸",
    "DISK_SPACE_LOW": "磁盘空间不足",
    "SCAN_FAILED": "扫描失败",
    "WORKER_UNAVAILABLE": "扫描工作进程不可用",
    "PAGE_PATH_INVALID": "页面文件路径无效",
    "PAGE_FILE_MISSING": "页面原图文件不存在",
    "PAGE_FILE_INVALID": "页面原图不是有效 JPEG 文件",
    "FILE_DELETE_FAILED": "页面文件删除失败",
    "INTERNAL_ERROR": "服务内部错误",
}


class ApiError(RuntimeError):
    """可以安全返回给调用方的稳定 API 错误。"""

    def __init__(
        self,
        error_code: str,
        message: str | None = None,
        *,
        status_code: int | None = None,
        api_code: int | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        self.error_code = error_code
        self.status_code = status_code or _ERROR_STATUS.get(error_code, 500)
        self.api_code = api_code or _ERROR_CODE.get(error_code, 5000)
        self.message = message or _DEFAULT_MESSAGES.get(error_code, "服务内部错误")
        self.data = {"errorCode": error_code}
        if data:
            self.data.update(dict(data))
        super().__init__(self.message)


def api_error_from_domain(exc: BaseException) -> ApiError:
    """把现有领域异常转换为 API 错误，不暴露异常堆栈或绝对路径。"""

    if isinstance(exc, ValueError):
        return ApiError("INVALID_REQUEST")
    error_code = getattr(exc, "error_code", None)
    if not isinstance(error_code, str) or not error_code:
        return ApiError("INTERNAL_ERROR")
    message = _DEFAULT_MESSAGES.get(error_code)
    return ApiError(error_code, message)


def _payload(error: ApiError) -> dict[str, Any]:
    return {
        "code": error.api_code,
        "message": error.message,
        "data": error.data,
    }


async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=_payload(exc))


async def handle_validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=_payload(ApiError("INVALID_REQUEST")),
    )


async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == 404:
        error = ApiError("FILE_NOT_FOUND", "请求的接口或文件不存在")
    else:
        error = ApiError("INVALID_REQUEST", "HTTP 请求无效", status_code=exc.status_code)
    return JSONResponse(status_code=exc.status_code, content=_payload(error))


async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    LOGGER.exception("HTTP 请求处理失败", exc_info=exc)
    error_code = getattr(exc, "error_code", None)
    if isinstance(error_code, str) and error_code:
        error = ApiError(error_code)
    elif isinstance(exc, ValueError):
        error = ApiError("INVALID_REQUEST")
    else:
        error = ApiError("INTERNAL_ERROR")
    return JSONResponse(status_code=error.status_code, content=_payload(error))


async def handle_stable_exception(request: Request, exc: Exception) -> JSONResponse:
    """处理领域异常和 ValueError，避免由 Starlette 再抛回测试/调用方。"""

    return await handle_api_error(request, api_error_from_domain(exc))


def register_exception_handlers(app: Any) -> None:
    """向 FastAPI 应用注册统一异常处理器。"""

    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(ValueError, handle_stable_exception)
    app.add_exception_handler(Exception, handle_unexpected_error)


__all__ = [
    "ApiError",
    "api_error_from_domain",
    "handle_stable_exception",
    "register_exception_handlers",
]
