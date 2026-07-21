"""TWAIN 设备和 Capability 接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Path

from app.api.dependencies import ApplicationContext, get_context
from app.api.responses import (
    capabilities_payload,
    extract_settings,
    require_identifier,
    success,
)


router = APIRouter(prefix="/devices", tags=["设备"])


@router.get("", name="list_devices")
def list_devices(context: ApplicationContext = Depends(get_context)):
    devices = context.list_devices()
    return success({"devices": devices, "items": devices, "total": len(devices)})


@router.get("/{device_id}/capabilities", name="get_capabilities")
def get_capabilities(
    device_id: str = Path(...),
    context: ApplicationContext = Depends(get_context),
):
    device_id = require_identifier(device_id, "deviceId")
    capabilities = context.get_capabilities(device_id)
    return success(capabilities_payload(device_id, capabilities))


@router.post("/{device_id}/capabilities/resolve", name="resolve_capabilities")
def resolve_capabilities(
    device_id: str = Path(...),
    body: dict[str, Any] | None = Body(default=None),
    context: ApplicationContext = Depends(get_context),
):
    device_id = require_identifier(device_id, "deviceId")
    settings = extract_settings(body)
    capabilities = context.resolve_capabilities(device_id, settings)
    return success(capabilities_payload(device_id, capabilities))


__all__ = ["router"]
