"""托盘当前用户开机启动项测试。"""

from __future__ import annotations

from pathlib import Path
import subprocess


class MemoryStartupRegistry:
    """在测试中模拟 HKCU 启动项注册表。"""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_value(self, key_path: str, value_name: str) -> str:
        try:
            return self.values[(key_path, value_name)]
        except KeyError as exc:
            raise FileNotFoundError(value_name) from exc

    def set_value(self, key_path: str, value_name: str, value: str) -> None:
        self.values[(key_path, value_name)] = value

    def delete_value(self, key_path: str, value_name: str) -> None:
        try:
            del self.values[(key_path, value_name)]
        except KeyError as exc:
            raise FileNotFoundError(value_name) from exc


class FailingStartupRegistry(MemoryStartupRegistry):
    """在测试中模拟注册表访问失败。"""

    def get_value(self, key_path: str, value_name: str) -> str:
        raise OSError("注册表不可用")

    def set_value(self, key_path: str, value_name: str, value: str) -> None:
        raise OSError("注册表不可用")


def test_startup_manager_reports_missing_value_as_disabled():
    from app.tray.startup import STARTUP_KEY_PATH, STARTUP_VALUE_NAME, StartupManager

    registry = MemoryStartupRegistry()
    manager = StartupManager(
        registry=registry,
        executable=Path(r"C:\Python312\python.exe"),
        script_path=Path(r"D:\archive-scan-service\run.py"),
    )

    assert manager.is_enabled() is False
    assert (STARTUP_KEY_PATH, STARTUP_VALUE_NAME) not in registry.values


def test_startup_manager_enables_source_entry_with_windows_command_line():
    from app.tray.startup import STARTUP_KEY_PATH, STARTUP_VALUE_NAME, StartupManager

    executable = Path(r"C:\Python312\python.exe")
    script_path = Path(r"D:\archive-scan-service\run.py")
    registry = MemoryStartupRegistry()
    manager = StartupManager(
        registry=registry,
        executable=executable,
        script_path=script_path,
        frozen=False,
    )

    assert manager.set_enabled(True) is True
    assert manager.is_enabled() is True
    assert registry.values[(STARTUP_KEY_PATH, STARTUP_VALUE_NAME)] == (
        subprocess.list2cmdline([str(executable), str(script_path)])
    )


def test_startup_manager_disables_entry_by_deleting_value():
    from app.tray.startup import STARTUP_KEY_PATH, STARTUP_VALUE_NAME, StartupManager

    executable = Path(r"C:\Program Files\Archive Scan\ArchiveScanService.exe")
    registry = MemoryStartupRegistry()
    manager = StartupManager(
        registry=registry,
        executable=executable,
        frozen=True,
    )
    assert manager.set_enabled(True) is True
    assert registry.values[(STARTUP_KEY_PATH, STARTUP_VALUE_NAME)] == (
        subprocess.list2cmdline([str(executable)])
    )

    assert manager.set_enabled(False) is False
    assert manager.is_enabled() is False
    assert (STARTUP_KEY_PATH, STARTUP_VALUE_NAME) not in registry.values


def test_startup_manager_registry_error_does_not_raise():
    from app.tray.startup import StartupManager

    manager = StartupManager(registry=FailingStartupRegistry(), frozen=True)

    assert manager.is_enabled() is False
    assert manager.set_enabled(True) is False
