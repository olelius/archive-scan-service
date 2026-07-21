"""服务健康检查和运行信息接口。"""

from __future__ import annotations

import struct

from fastapi import APIRouter, Depends

from app.api.dependencies import ApplicationContext, get_context
from app.api.responses import success


router = APIRouter(tags=["服务"])


@router.get("/health", name="health")
def health(context: ApplicationContext = Depends(get_context)):
    status = context.status()
    return success(
        {
            "status": "ok",
            "workerReady": status["ready"],
            "workerPid": status["pid"],
            "workerGeneration": status["generation"],
        }
    )


@router.get("/info", name="info")
def info(context: ApplicationContext = Depends(get_context)):
    return success(
        {
            "serviceName": "archive-scan-service",
            "version": "0.1.0",
            "apiVersion": "v1",
            "host": context.settings.host,
            "port": context.settings.port,
            "architecture": f"x{struct.calcsize('P') * 8}",
            "protocol": "TWAIN",
        }
    )


__all__ = ["router"]
