"""真实 Windows TWAIN Data Source 探测测试。"""

from __future__ import annotations

import os

import pytest


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_TWAIN_DEVICE_MANUAL") != "1",
    reason="设置 RUN_TWAIN_DEVICE_MANUAL=1 后才连接真实 TWAIN 设备探测",
)
def test_real_twain_device_enumeration():
    from app.scanner.twain_backend import TwainBackend

    with TwainBackend() as backend:
        devices = backend.enumerate_devices()

    assert devices
    assert all(device.architecture == "x64" for device in devices)
    assert any("KODAK" in device.product_name.upper() for device in devices)

    expected_product = os.environ.get("EXPECTED_TWAIN_PRODUCT")
    if expected_product:
        assert any(
            device.product_name == expected_product for device in devices
        )
