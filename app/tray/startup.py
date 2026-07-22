"""Windows 当前用户开机启动项管理。"""

from __future__ import annotations

from typing import Any, Protocol
import logging
import os
from pathlib import Path
import subprocess
import sys


LOGGER = logging.getLogger("archive_scan_service.tray")
STARTUP_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE_NAME = "ArchiveScanService"


class RegistryBackend(Protocol):
    """开机启动注册表访问所需的最小接口。"""

    def get_value(self, key_path: str, value_name: str) -> Any:
        """读取指定注册表值。"""

    def set_value(self, key_path: str, value_name: str, value: str) -> None:
        """写入指定注册表值。"""

    def delete_value(self, key_path: str, value_name: str) -> None:
        """删除指定注册表值。"""


class WindowsRegistryBackend:
    """通过 winreg 访问当前用户注册表。"""

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("开机启动项只支持 Windows")
        import winreg

        self._winreg = winreg

    def get_value(self, key_path: str, value_name: str) -> Any:
        with self._winreg.OpenKey(
            self._winreg.HKEY_CURRENT_USER,
            key_path,
            0,
            self._winreg.KEY_QUERY_VALUE,
        ) as key:
            value, _value_type = self._winreg.QueryValueEx(key, value_name)
            return value

    def set_value(self, key_path: str, value_name: str, value: str) -> None:
        with self._winreg.CreateKeyEx(
            self._winreg.HKEY_CURRENT_USER,
            key_path,
            0,
            self._winreg.KEY_SET_VALUE,
        ) as key:
            self._winreg.SetValueEx(key, value_name, 0, self._winreg.REG_SZ, value)

    def delete_value(self, key_path: str, value_name: str) -> None:
        with self._winreg.OpenKey(
            self._winreg.HKEY_CURRENT_USER,
            key_path,
            0,
            self._winreg.KEY_SET_VALUE,
        ) as key:
            self._winreg.DeleteValue(key, value_name)


class UnavailableRegistryBackend:
    """非 Windows 环境中的不可用后端，保持托盘菜单可构造。"""

    @staticmethod
    def _raise() -> None:
        raise OSError("当前环境不支持 Windows 注册表")

    def get_value(self, _key_path: str, _value_name: str) -> Any:
        self._raise()

    def set_value(self, _key_path: str, _value_name: str, _value: str) -> None:
        self._raise()

    def delete_value(self, _key_path: str, _value_name: str) -> None:
        self._raise()


class StartupManager:
    """读写当前用户的档案扫描服务开机启动项。"""

    def __init__(
        self,
        *,
        registry: RegistryBackend | None = None,
        executable: Path | None = None,
        script_path: Path | None = None,
        frozen: bool | None = None,
    ) -> None:
        if registry is None:
            try:
                registry = WindowsRegistryBackend()
            except (ImportError, RuntimeError):
                registry = UnavailableRegistryBackend()
        self._registry = registry
        self._startup_command = self._build_startup_command(
            executable=executable or Path(sys.executable),
            script_path=script_path,
            frozen=bool(getattr(sys, "frozen", False)) if frozen is None else frozen,
        )

    @property
    def startup_command(self) -> str:
        """返回将要写入注册表的启动命令。"""

        return self._startup_command

    def is_enabled(self) -> bool:
        """返回启动项是否存在。"""

        try:
            self._registry.get_value(STARTUP_KEY_PATH, STARTUP_VALUE_NAME)
        except FileNotFoundError:
            return False
        except OSError:
            LOGGER.warning("读取当前用户开机启动项失败", exc_info=True)
            return False
        return True

    def set_enabled(self, enabled: bool) -> bool:
        """设置启动项并返回本次操作后的期望状态。"""

        try:
            if enabled:
                self._registry.set_value(
                    STARTUP_KEY_PATH,
                    STARTUP_VALUE_NAME,
                    self._startup_command,
                )
            else:
                try:
                    self._registry.delete_value(
                        STARTUP_KEY_PATH,
                        STARTUP_VALUE_NAME,
                    )
                except FileNotFoundError:
                    pass
        except OSError:
            LOGGER.warning("更新当前用户开机启动项失败", exc_info=True)
            return self.is_enabled()
        return bool(enabled)

    @staticmethod
    def _build_startup_command(
        *,
        executable: Path,
        script_path: Path | None,
        frozen: bool,
    ) -> str:
        command = [str(executable)]
        if not frozen:
            command.append(
                str(script_path or (Path(__file__).resolve().parents[2] / "run.py"))
            )
        return subprocess.list2cmdline(command)


def create_startup_manager() -> StartupManager:
    """创建当前平台可用的开机启动项管理器。"""

    return StartupManager()


__all__ = [
    "STARTUP_KEY_PATH",
    "STARTUP_VALUE_NAME",
    "RegistryBackend",
    "StartupManager",
    "WindowsRegistryBackend",
    "create_startup_manager",
]
