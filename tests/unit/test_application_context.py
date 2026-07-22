"""ApplicationContext 与设备状态查询的接入测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class FakeWorker:
    def start(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def enumerate_devices(self) -> list[dict[str, Any]]:
        return [
            {
                "deviceId": "twain-device-1",
                "manufacturer": "Eastman Kodak",
                "productName": "KODAK Scanner: i2000",
            }
        ]


class OfflineStatusResolver:
    def enrich_devices(self, devices):
        return [dict(device, online=False) for device in devices]


def test_application_context_uses_pnp_status_for_list_devices(tmp_path: Path):
    from app.api.dependencies import ApplicationContext
    from app.config import Settings

    context = ApplicationContext(
        settings=Settings(data_root=tmp_path),
        worker=FakeWorker(),
        device_status_resolver=OfflineStatusResolver(),
    )
    try:
        devices = context.list_devices()
    finally:
        context.close()

    assert devices[0]["online"] is False
