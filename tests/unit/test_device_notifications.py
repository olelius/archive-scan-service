"""Windows 图像设备变化通知测试。"""

from __future__ import annotations

from threading import Event
import os

import pytest


def test_relevant_device_change_codes_are_limited_to_refresh_events():
    from app.tray.device_notifications import (
        DBT_DEVICEARRIVAL,
        DBT_DEVICEREMOVECOMPLETE,
        DBT_DEVNODES_CHANGED,
        DeviceChangeMonitor,
    )

    assert DeviceChangeMonitor.is_relevant_change(DBT_DEVICEARRIVAL)
    assert DeviceChangeMonitor.is_relevant_change(DBT_DEVICEREMOVECOMPLETE)
    assert DeviceChangeMonitor.is_relevant_change(DBT_DEVNODES_CHANGED)
    assert not DeviceChangeMonitor.is_relevant_change(0)
    assert not DeviceChangeMonitor.is_relevant_change(0xFFFF)


@pytest.mark.integration
@pytest.mark.skipif(os.name != "nt", reason="Windows 隐藏窗口只在 Windows 上验证")
def test_device_change_monitor_registers_and_receives_device_change():
    from app.tray.device_notifications import (
        DBT_DEVNODES_CHANGED,
        DeviceChangeMonitor,
        WM_DEVICECHANGE,
    )

    changed = Event()
    monitor = DeviceChangeMonitor(changed.set, startup_timeout=3.0)
    monitor.start()
    try:
        with monitor._condition:
            user32 = monitor._user32
            hwnd = monitor._hwnd
        assert user32 is not None
        assert hwnd is not None
        assert user32.PostMessageW(hwnd, WM_DEVICECHANGE, DBT_DEVNODES_CHANGED, 0)
        assert changed.wait(timeout=2.0)
    finally:
        monitor.stop()

    assert monitor._thread is None
