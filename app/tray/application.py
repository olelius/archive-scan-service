"""登录用户级 Windows 托盘程序和单实例运行入口。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Protocol

from app.config import Settings
from app.tray.device_notifications import DeviceChangeMonitor
from app.tray.startup import create_startup_manager


LOGGER = logging.getLogger("archive_scan_service.tray")
DEFAULT_MUTEX_NAME = r"Local\ArchiveScanService"
ERROR_ALREADY_EXISTS = 183


class MutexBackend(Protocol):
    """命名互斥体所需的最小底层操作。"""

    def create(self, name: str) -> tuple[Any, bool]:
        """创建或打开互斥体，返回句柄和是否已存在。"""

    def release(self, handle: Any) -> None:
        """释放当前进程持有的互斥体。"""

    def close(self, handle: Any) -> None:
        """关闭互斥体句柄。"""


class WindowsMutexBackend:
    """通过 Kernel32 API 操作 Windows 命名互斥体。"""

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("托盘程序只支持 Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self._kernel32.CreateMutexW.restype = wintypes.HANDLE
        self._kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self._kernel32.ReleaseMutex.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def create(self, name: str) -> tuple[Any, bool]:
        ctypes.set_last_error(0)
        handle = self._kernel32.CreateMutexW(None, True, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle, ctypes.get_last_error() == ERROR_ALREADY_EXISTS

    def release(self, handle: Any) -> None:
        if not self._kernel32.ReleaseMutex(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self, handle: Any) -> None:
        if not self._kernel32.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


class SingleInstanceGuard:
    """持有当前用户命名互斥体，阻止服务重复启动。"""

    def __init__(
        self,
        *,
        name: str = DEFAULT_MUTEX_NAME,
        backend: MutexBackend | None = None,
    ) -> None:
        if not name:
            raise ValueError("互斥体名称不能为空")
        self.name = name
        self._backend = backend
        self._handle: Any | None = None
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        """尝试获取互斥体；已有实例时返回 False。"""

        with self._lock:
            if self._handle is not None:
                return True
            backend = self._backend or WindowsMutexBackend()
            handle, already_exists = backend.create(self.name)
            if already_exists:
                backend.close(handle)
                return False
            self._backend = backend
            self._handle = handle
            return True

    def release(self) -> None:
        """释放互斥体和底层句柄；重复调用无副作用。"""

        with self._lock:
            handle = self._handle
            self._handle = None
            backend = self._backend
        if handle is None or backend is None:
            return
        try:
            backend.release(handle)
        finally:
            backend.close(handle)

    def __enter__(self) -> "SingleInstanceGuard":
        if not self.acquire():
            raise RuntimeError("档案本机扫描服务已经运行")
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


ServerFactory = Callable[[Any], Any]
IconFactory = Callable[..., Any]
OpenDirectory = Callable[[Path], None]
DeviceMonitorFactory = Callable[[Callable[[], None]], Any]
ApplicationFactory = Callable[[], Any]


class StartupManagerProtocol(Protocol):
    """托盘开机启动控制所需的最小接口。"""

    def is_enabled(self) -> bool:
        """读取当前用户启动项状态。"""

    def set_enabled(self, enabled: bool) -> bool:
        """更新当前用户启动项状态。"""


class TrayApplication:
    """在托盘主线程运行图标，并在后台线程承载 Uvicorn。"""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        application: Any | None = None,
        instance_guard: SingleInstanceGuard | None = None,
        server_factory: ServerFactory | None = None,
        icon_factory: IconFactory | None = None,
        open_directory: OpenDirectory | None = None,
        device_monitor_factory: DeviceMonitorFactory | None = None,
        application_factory: ApplicationFactory | None = None,
        startup_manager: StartupManagerProtocol | None = None,
        shutdown_timeout: float = 10.0,
    ) -> None:
        if shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout 必须大于 0")
        self.settings = settings or Settings()
        self._initial_application = application
        self._application = application
        self._application_factory = application_factory or self._create_application
        self._use_loaded_application = application is None and application_factory is None
        self._instance_guard = instance_guard or SingleInstanceGuard()
        self._server_factory = server_factory or self._create_uvicorn_server
        self._icon_factory = icon_factory or self._create_pystray_icon
        self._open_directory = open_directory or self._open_directory_with_shell
        self._device_monitor_factory = device_monitor_factory or DeviceChangeMonitor
        self._shutdown_timeout = shutdown_timeout
        self._condition = threading.RLock()
        self._service_operation = threading.Lock()
        self._server: Any | None = None
        self._server_thread: threading.Thread | None = None
        self._icon: Any | None = None
        self._server_error: BaseException | None = None
        self._exit_requested = False
        self._shutdown_started = False
        self._guard_acquired = False
        self._status_snapshot: dict[str, Any] | None = None
        self._device_monitor: Any | None = None
        self._service_stopping = False
        self._service_was_stopped = False
        self._startup_manager = startup_manager or create_startup_manager()

    @property
    def server(self) -> Any | None:
        """返回当前 Uvicorn Server，供托盘状态和测试读取。"""

        with self._condition:
            return self._server

    def run(self) -> int:
        """启动单实例托盘应用，返回进程退出码。"""

        if not self._instance_guard.acquire():
            LOGGER.warning("档案本机扫描服务已有实例运行")
            return 1
        with self._condition:
            self._guard_acquired = True
        try:
            if not self.start_service(refresh_menu=False):
                return 1
            self._icon = self._icon_factory(
                "ArchiveScanService",
                create_tray_image(),
                "档案本机扫描服务",
                self.build_menu(),
            )
            if self._server_error is not None:
                self._icon.stop()
            else:
                self._start_device_monitor()
                self._icon.run()
            return 1 if self._server_error is not None else 0
        except KeyboardInterrupt:
            LOGGER.info("托盘程序收到中断请求")
            return 0
        except Exception:
            LOGGER.exception("托盘程序启动或运行失败")
            return 1
        finally:
            self.shutdown()

    def request_exit(self, _icon: Any | None = None, _item: Any | None = None) -> None:
        """请求 Server 停止接收新请求，并退出托盘图标。"""

        with self._condition:
            if self._exit_requested:
                return
            self._exit_requested = True
            server = self._server
            icon = self._icon or _icon
        if server is not None and not getattr(server, "should_exit", False):
            server.should_exit = True
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                LOGGER.exception("关闭托盘图标失败")

    def shutdown(self) -> None:
        """等待 Uvicorn 关闭后清理应用资源并释放单实例句柄。"""

        with self._condition:
            if self._shutdown_started:
                return
            self._shutdown_started = True
            was_exit_requested = self._exit_requested
            self._exit_requested = True
            icon = self._icon

        self._stop_device_monitor()
        if icon is not None and not was_exit_requested:
            try:
                icon.stop()
            except Exception:
                LOGGER.exception("关闭托盘图标失败")

        if not self._stop_service(refresh_menu=False):
            return

        with self._condition:
            guard_acquired = self._guard_acquired
            self._guard_acquired = False
        if guard_acquired:
            try:
                self._instance_guard.release()
            except Exception:
                LOGGER.exception("释放本机服务单实例句柄失败")

    def start_service(self, *, refresh_menu: bool = True) -> bool:
        """启动 HTTP 服务和 Worker，托盘图标保持不变。"""

        with self._service_operation:
            with self._condition:
                if self._shutdown_started or self._exit_requested:
                    return False
                if self._service_running_locked():
                    return True

                application = self._next_application()
                self._application = application
                self._server_error = None
                self._service_stopping = False
                self._service_was_stopped = False

            try:
                server = self._server_factory(self._build_server_config())
            except Exception:
                self._close_application_context()
                with self._condition:
                    self._application = None
                raise

            server_thread = threading.Thread(
                target=self._run_server,
                name="ArchiveScanUvicorn",
                daemon=True,
            )
            with self._condition:
                self._server = server
                self._server_thread = server_thread
            server_thread.start()
            self._wait_for_server_start(server, server_thread)

        if refresh_menu:
            self.refresh_menu()
        return True

    def _wait_for_server_start(
        self,
        server: Any,
        server_thread: threading.Thread,
    ) -> None:
        """等待 FastAPI lifespan 完成，确保菜单读取到 Worker 最新状态。"""

        deadline = time.monotonic() + self._shutdown_timeout
        while True:
            started = getattr(server, "started", None)
            if started is None or self._is_started_value(started):
                return
            if not server_thread.is_alive():
                return
            if time.monotonic() >= deadline:
                LOGGER.warning("等待本机服务启动完成超时，继续保留当前服务线程")
                return
            time.sleep(0.01)

    @staticmethod
    def _is_started_value(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        is_set = getattr(value, "is_set", None)
        return bool(is_set()) if callable(is_set) else bool(value)

    def stop_service(self, *_args: Any, refresh_menu: bool = True) -> bool:
        """停止 HTTP 服务和 Worker，但保留托盘图标及单实例守卫。"""

        try:
            return self._stop_service(refresh_menu=refresh_menu)
        except Exception:
            LOGGER.exception("关闭本机服务失败")
            return False

    def toggle_service(
        self,
        _icon: Any | None = None,
        _item: Any | None = None,
    ) -> None:
        """在“开启服务”和“关闭服务”之间切换。"""

        try:
            if self.service_running:
                self.stop_service()
            else:
                self.start_service()
        except Exception:
            LOGGER.exception("切换本机服务状态失败")
            self.refresh_menu()

    @property
    def service_running(self) -> bool:
        """返回当前 HTTP 服务线程是否仍在运行。"""

        with self._condition:
            return self._service_running_locked()

    def _service_running_locked(self) -> bool:
        server = self._server
        if server is None or self._server_error is not None:
            return False
        if getattr(server, "should_exit", False):
            return False
        server_thread = self._server_thread
        return server_thread is None or server_thread.is_alive()

    def _stop_service(self, *, refresh_menu: bool) -> bool:
        with self._service_operation:
            with self._condition:
                server = self._server
                server_thread = self._server_thread
                if server is None and self._application is None:
                    stopped = True
                else:
                    stopped = False
                    self._service_stopping = True

            if not stopped:
                if server is not None and not getattr(server, "should_exit", False):
                    server.should_exit = True

                if server_thread is not None:
                    if server_thread is threading.current_thread():
                        LOGGER.error("不能从 Uvicorn 服务线程自身执行有序退出")
                        return False
                    server_thread.join(timeout=self._shutdown_timeout)
                    if server_thread.is_alive() and server is not None:
                        server.force_exit = True
                        server_thread.join(timeout=self._shutdown_timeout)
                    if server_thread.is_alive():
                        LOGGER.error(
                            "Uvicorn 服务线程在强制退出后仍未结束，保留单实例句柄"
                        )
                        return False

                self._close_application_context()
                with self._condition:
                    self._application = None
                    self._server = None
                    self._server_thread = None
                    self._server_error = None
                    self._status_snapshot = None
                    self._service_stopping = False
                    self._service_was_stopped = True

            if refresh_menu:
                self.refresh_menu()
            return True

    def refresh_menu(self) -> None:
        """要求托盘后端重新生成原生菜单，读取最新状态文本。"""

        icon = self._icon
        update_menu = getattr(icon, "update_menu", None) if icon is not None else None
        if not callable(update_menu):
            return
        try:
            update_menu()
        except Exception:
            LOGGER.exception("刷新托盘菜单失败")

    def _start_device_monitor(self) -> None:
        icon = self._icon
        if icon is None or not callable(getattr(icon, "update_menu", None)):
            return
        with self._condition:
            if self._device_monitor is not None:
                return
        monitor: Any | None = None
        try:
            monitor = self._device_monitor_factory(self.refresh_menu)
            with self._condition:
                self._device_monitor = monitor
            monitor.start()
        except Exception:
            with self._condition:
                if self._device_monitor is monitor:
                    self._device_monitor = None
            if monitor is not None:
                try:
                    monitor.stop()
                except Exception:
                    LOGGER.exception("清理失败的 Windows 设备变化通知监视器失败")
            LOGGER.warning("注册 Windows 设备变化通知失败，继续运行托盘服务", exc_info=True)

    def _stop_device_monitor(self) -> None:
        with self._condition:
            monitor = self._device_monitor
            self._device_monitor = None
        if monitor is None:
            return
        try:
            monitor.stop()
        except Exception:
            LOGGER.exception("停止 Windows 设备变化通知监视器失败")

    def status_text(self, _item: Any | None = None) -> str:
        """生成托盘“服务状态”菜单文本并刷新状态快照。"""

        with self._condition:
            if self._service_was_stopped:
                return "服务状态：未启动"
        snapshot = self._refresh_status_snapshot()
        if snapshot["status_error"]:
            return "服务状态：异常"
        status = snapshot["status"]
        if not isinstance(status, dict) or not status.get("ready"):
            return "服务状态：工作进程未就绪"
        return "服务状态：正常"

    def scanner_status_text(self, _item: Any | None = None) -> str:
        """生成托盘“扫描仪状态”菜单文本。"""

        snapshot = self._get_status_snapshot()
        status = snapshot["status"]
        if snapshot["status_error"] or not isinstance(status, Mapping) or not status.get("ready"):
            return "扫描仪状态：未就绪"
        devices = snapshot["devices"]
        if any(device.get("online") is True for device in devices):
            return "扫描仪状态：在线"
        return "扫描仪状态：离线"

    def scanner_name_text(self, _item: Any | None = None) -> str:
        """生成托盘扫描仪名称菜单文本。"""

        device = self._primary_device(self._get_status_snapshot())
        product_name = device.get("productName") if device is not None else None
        return f"扫描仪：{product_name or '未识别'}"

    def manufacturer_text(self, _item: Any | None = None) -> str:
        """生成托盘扫描仪厂商菜单文本。"""

        device = self._primary_device(self._get_status_snapshot())
        manufacturer = device.get("manufacturer") if device is not None else None
        return f"厂商：{manufacturer or '未识别'}"

    def worker_text(self, _item: Any | None = None) -> str:
        """生成托盘 Worker 进程菜单文本。"""

        status = self._get_status_snapshot()["status"]
        pid = status.get("pid") if isinstance(status, Mapping) else None
        if isinstance(pid, int) and not isinstance(pid, bool):
            return f"Worker：{pid}"
        return "Worker：未启动"

    def service_control_text(self, _item: Any | None = None) -> str:
        """生成动态服务控制菜单文本。"""

        return "关闭服务" if self.service_running else "开启服务"

    @property
    def startup_enabled(self) -> bool:
        """返回当前用户开机启动项是否已启用。"""

        try:
            return bool(self._startup_manager.is_enabled())
        except Exception:
            LOGGER.warning("读取当前用户开机启动状态失败", exc_info=True)
            return False

    def startup_checked(self, _item: Any | None = None) -> bool:
        """为 pystray 返回开机启动复选状态。"""

        return self.startup_enabled

    def toggle_startup(
        self,
        _icon: Any | None = None,
        _item: Any | None = None,
    ) -> None:
        """切换当前用户开机启动项。"""

        try:
            self._startup_manager.set_enabled(not self.startup_enabled)
        except Exception:
            LOGGER.warning("切换当前用户开机启动状态失败", exc_info=True)
        self.refresh_menu()

    def _get_status_snapshot(self) -> dict[str, Any]:
        with self._condition:
            snapshot = self._status_snapshot
        if snapshot is None:
            return self._refresh_status_snapshot()
        return snapshot

    def _refresh_status_snapshot(self) -> dict[str, Any]:
        context = self._application_context()
        previous_devices: list[dict[str, Any]] = []
        with self._condition:
            previous = self._status_snapshot
        if isinstance(previous, dict) and isinstance(previous.get("devices"), list):
            previous_devices = list(previous["devices"])

        if context is None:
            snapshot = {
                "status": {},
                "devices": [],
                "status_error": False,
            }
        else:
            status_error = False
            try:
                status = context.status()
            except Exception:
                status = {}
                status_error = True

            devices = previous_devices
            if isinstance(status, Mapping) and status.get("ready"):
                list_devices = getattr(context, "list_devices", None)
                if callable(list_devices):
                    try:
                        listed = list_devices()
                    except Exception:
                        listed = None
                    if isinstance(listed, list):
                        devices = [
                            dict(device)
                            for device in listed
                            if isinstance(device, Mapping)
                        ]
            snapshot = {
                "status": status,
                "devices": devices,
                "status_error": status_error,
            }

        with self._condition:
            self._status_snapshot = snapshot
        return snapshot

    @staticmethod
    def _primary_device(snapshot: Mapping[str, Any]) -> Mapping[str, Any] | None:
        status = snapshot.get("status")
        if not isinstance(status, Mapping) or not status.get("ready"):
            return None
        devices = snapshot.get("devices")
        if not isinstance(devices, list):
            return None
        for device in devices:
            if isinstance(device, Mapping) and device.get("online") is True:
                return device
        for device in devices:
            if isinstance(device, Mapping):
                return device
        return None

    def build_menu(self) -> Any:
        """构造托盘状态和控制菜单项。"""

        import pystray

        return pystray.Menu(
            pystray.MenuItem(self.status_text, None, enabled=False),
            pystray.MenuItem(self.scanner_status_text, None, enabled=False),
            pystray.MenuItem(self.scanner_name_text, None, enabled=False),
            pystray.MenuItem(self.manufacturer_text, None, enabled=False),
            pystray.MenuItem(self.worker_text, None, enabled=False),
            pystray.MenuItem(self.service_control_text, self.toggle_service),
            pystray.MenuItem(
                "开机启动",
                self.toggle_startup,
                checked=self.startup_checked,
            ),
            pystray.MenuItem("打开数据目录", self.open_data_directory),
            pystray.MenuItem("退出", self.request_exit),
        )

    def open_data_directory(
        self,
        _icon: Any | None = None,
        _item: Any | None = None,
    ) -> None:
        """打开当前配置的数据根目录。"""

        self._open_directory(Path(self.settings.data_root).resolve())

    def _build_server_config(self) -> Any:
        import uvicorn

        return uvicorn.Config(
            app=self._application,
            host=self.settings.host,
            port=self.settings.port,
            log_level=self.settings.log_level.lower(),
            lifespan="on",
        )

    @staticmethod
    def _create_uvicorn_server(config: Any) -> Any:
        import uvicorn

        return uvicorn.Server(config)

    @staticmethod
    def _create_pystray_icon(*args: Any) -> Any:
        import pystray

        return pystray.Icon(*args)

    @staticmethod
    def _open_directory_with_shell(path: Path) -> None:
        if os.name != "nt":
            raise RuntimeError("打开数据目录只支持 Windows")
        os.startfile(str(path))  # type: ignore[attr-defined]

    @staticmethod
    def _load_application() -> Any:
        from app.main import app

        return app

    @staticmethod
    def _create_application() -> Any:
        from app.main import create_app

        return create_app()

    def _next_application(self) -> Any:
        if self._initial_application is not None:
            application = self._initial_application
            self._initial_application = None
            return application
        if self._use_loaded_application:
            self._use_loaded_application = False
            return self._load_application()
        return self._application_factory()

    def _close_application_context(self) -> None:
        try:
            context = self._application_context()
            close = getattr(context, "close", None)
            if callable(close):
                close()
        except Exception:
            LOGGER.exception("关闭本机扫描服务资源失败")

    def _application_context(self) -> Any | None:
        application = self._application
        state = getattr(application, "state", None)
        return getattr(state, "context", None)

    def _run_server(self) -> None:
        server = self.server
        if server is None:
            return
        try:
            server.run()
        except BaseException as exc:
            with self._condition:
                self._server_error = exc
            LOGGER.exception("Uvicorn 服务线程异常退出")
        finally:
            with self._condition:
                icon = self._icon
                exit_requested = self._exit_requested
                service_stopping = self._service_stopping
            if icon is not None and not exit_requested and not service_stopping:
                try:
                    icon.stop()
                except Exception:
                    LOGGER.exception("服务异常退出时关闭托盘图标失败")


def create_tray_image() -> Any:
    """创建无需外部文件的基础托盘图标。"""

    from PIL import Image

    return Image.new("RGBA", (64, 64), (46, 160, 67, 255))


def main() -> int:
    """托盘程序进程入口。"""

    return TrayApplication().run()


__all__ = [
    "DEFAULT_MUTEX_NAME",
    "SingleInstanceGuard",
    "TrayApplication",
    "WindowsMutexBackend",
    "create_tray_image",
    "main",
]
