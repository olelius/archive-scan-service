"""登录用户级 Windows 托盘程序和单实例运行入口。"""

from __future__ import annotations

from collections.abc import Callable
import ctypes
import logging
import os
from pathlib import Path
import threading
from typing import Any, Protocol

from app.config import Settings


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
            ctypes.wintypes.LPVOID,
            ctypes.wintypes.BOOL,
            ctypes.wintypes.LPCWSTR,
        ]
        self._kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
        self._kernel32.ReleaseMutex.argtypes = [ctypes.wintypes.HANDLE]
        self._kernel32.ReleaseMutex.restype = ctypes.wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = ctypes.wintypes.BOOL

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
        shutdown_timeout: float = 10.0,
    ) -> None:
        if shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout 必须大于 0")
        self.settings = settings or Settings()
        self._application = application
        self._instance_guard = instance_guard or SingleInstanceGuard()
        self._server_factory = server_factory or self._create_uvicorn_server
        self._icon_factory = icon_factory or self._create_pystray_icon
        self._open_directory = open_directory or self._open_directory_with_shell
        self._shutdown_timeout = shutdown_timeout
        self._condition = threading.RLock()
        self._server: Any | None = None
        self._server_thread: threading.Thread | None = None
        self._icon: Any | None = None
        self._server_error: BaseException | None = None
        self._exit_requested = False
        self._shutdown_started = False
        self._guard_acquired = False

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
            self._application = self._application or self._load_application()
            self._server = self._server_factory(self._build_server_config())
            self._icon = self._icon_factory(
                "ArchiveScanService",
                create_tray_image(),
                "档案本机扫描服务",
                self.build_menu(),
            )
            self._server_thread = threading.Thread(
                target=self._run_server,
                name="ArchiveScanUvicorn",
                daemon=True,
            )
            self._server_thread.start()
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
            server = self._server
            server_thread = self._server_thread
            icon = self._icon

        if server is not None and not getattr(server, "should_exit", False):
            server.should_exit = True
        if icon is not None and not was_exit_requested:
            try:
                icon.stop()
            except Exception:
                LOGGER.exception("关闭托盘图标失败")

        if (
            server_thread is not None
            and server_thread is not threading.current_thread()
        ):
            server_thread.join(timeout=self._shutdown_timeout)
            if server_thread.is_alive() and server is not None:
                server.force_exit = True
                server_thread.join(timeout=self._shutdown_timeout)

        try:
            context = self._application_context()
            close = getattr(context, "close", None)
            if callable(close):
                close()
        except Exception:
            LOGGER.exception("关闭本机扫描服务资源失败")
        finally:
            with self._condition:
                guard_acquired = self._guard_acquired
                self._guard_acquired = False
            if guard_acquired:
                try:
                    self._instance_guard.release()
                except Exception:
                    LOGGER.exception("释放本机服务单实例句柄失败")

    def status_text(self, _item: Any | None = None) -> str:
        """生成托盘“服务状态”菜单文本。"""

        context = self._application_context()
        if context is None:
            return "服务状态：未启动"
        try:
            status = context.status()
        except Exception:
            return "服务状态：异常"
        if not isinstance(status, dict) or not status.get("ready"):
            return "服务状态：工作进程未就绪"
        pid = status.get("pid")
        if isinstance(pid, int) and not isinstance(pid, bool):
            return f"服务状态：正常（Worker {pid}）"
        return "服务状态：正常"

    def build_menu(self) -> Any:
        """构造 Task 12 规定的三个托盘菜单项。"""

        import pystray

        return pystray.Menu(
            pystray.MenuItem(self.status_text, None, enabled=False),
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
        except Exception as exc:
            with self._condition:
                self._server_error = exc
            LOGGER.exception("Uvicorn 服务线程异常退出")
        finally:
            with self._condition:
                icon = self._icon
                exit_requested = self._exit_requested
            if icon is not None and not exit_requested:
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
