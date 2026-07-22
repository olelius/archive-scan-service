"""Windows PnP 扫描仪在线状态测试。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FakePnpDevice:
    class_name: str
    description: str
    manufacturer: str
    present: bool
    problem_code: int = 0
    config_return_code: int = 0


def test_offline_pnp_device_overrides_twain_default_online_value():
    from app.scanner.pnp_status import WindowsPnpStatusResolver

    resolver = WindowsPnpStatusResolver(
        enumerator=lambda: [
            FakePnpDevice(
                class_name="Image",
                description="KODAK i2800 Scanner",
                manufacturer="Kodak",
                present=False,
                config_return_code=13,
            )
        ]
    )

    devices = resolver.enrich_devices(
        [
            {
                "deviceId": "twain-device-1",
                "manufacturer": "Eastman Kodak",
                "productName": "KODAK Scanner: i2000",
                "online": True,
            }
        ]
    )

    assert devices[0]["online"] is False


def test_present_pnp_device_reports_online():
    from app.scanner.pnp_status import WindowsPnpStatusResolver

    resolver = WindowsPnpStatusResolver(
        enumerator=lambda: [
            FakePnpDevice(
                class_name="Image",
                description="KODAK i2800 Scanner",
                manufacturer="Kodak",
                present=True,
            )
        ]
    )

    devices = resolver.enrich_devices(
        [
            {
                "deviceId": "twain-device-1",
                "manufacturer": "Eastman Kodak",
                "productName": "KODAK Scanner: i2000",
            }
        ]
    )

    assert devices[0]["online"] is True


def test_unmatched_pnp_device_keeps_unknown_status_as_online_fallback():
    from app.scanner.pnp_status import WindowsPnpStatusResolver

    resolver = WindowsPnpStatusResolver(
        enumerator=lambda: [
            FakePnpDevice(
                class_name="Image",
                description="Other Scanner",
                manufacturer="Other",
                present=False,
                config_return_code=13,
            )
        ]
    )

    devices = resolver.enrich_devices(
        [
            {
                "deviceId": "twain-device-1",
                "manufacturer": "Eastman Kodak",
                "productName": "KODAK Scanner: i2000",
            }
        ]
    )

    assert devices[0]["online"] is True


def test_pnp_query_failure_does_not_break_device_enumeration():
    from app.scanner.pnp_status import WindowsPnpStatusResolver

    def failed_enumerator() -> list[Any]:
        raise OSError("PnP 查询失败")

    resolver = WindowsPnpStatusResolver(enumerator=failed_enumerator)

    devices = resolver.enrich_devices(
        [{"deviceId": "twain-device-1", "productName": "KODAK Scanner: i2000"}]
    )

    assert devices == [
        {
            "deviceId": "twain-device-1",
            "productName": "KODAK Scanner: i2000",
            "online": True,
        }
    ]
