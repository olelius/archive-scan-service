"""Windows 图像设备即插即用变化通知。"""

from __future__ import annotations

from collections.abc import Callable
import ctypes
from ctypes import wintypes
import logging
import os
import threading
from typing import Any
from uuid import uuid4


LOGGER = logging.getLogger("archive_scan_service.tray.device_notifications")

WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_DEVICECHANGE = 0x0219
DBT_DEVNODES_CHANGED = 0x0007
DBT_DEVICEARRIVAL = 0x8000
DBT_DEVICEREMOVECOMPLETE = 0x8004
DBT_DEVTYP_DEVICEINTERFACE = 0x00000005
DEVICE_NOTIFY_WINDOW_HANDLE = 0x00000000


class _GUID(ctypes.Structure):
    _fields_ = [
        ("data1", wintypes.DWORD),
        ("data2", wintypes.WORD),
        ("data3", wintypes.WORD),
        ("data4", wintypes.BYTE * 8),
    ]


GUID_DEVINTERFACE_IMAGE = _GUID(
    0x6BDD1FC6,
    0x810F,
    0x11D0,
    (wintypes.BYTE * 8)(0xBE, 0xC7, 0x08, 0x00, 0x2B, 0xE2, 0x09, 0x2F),
)

_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_HCURSOR = getattr(wintypes, "HCURSOR", wintypes.HANDLE)
_LRESULT = getattr(wintypes, "LRESULT", ctypes.c_ssize_t)
_WNDPROC = _WINFUNCTYPE(
    _LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", _HCURSOR),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class _DEV_BROADCAST_DEVICEINTERFACE_W(ctypes.Structure):
    _fields_ = [
        ("dbcc_size", wintypes.DWORD),
        ("dbcc_devicetype", wintypes.DWORD),
        ("dbcc_reserved", wintypes.DWORD),
        ("dbcc_classguid", _GUID),
        ("dbcc_name", wintypes.WCHAR * 1),
    ]


class DeviceChangeMonitor:
    """在独立隐藏窗口上监听 Windows 图像设备变化。"""

    def __init__(
        self,
        callback: Callable[[], None],
        *,
        startup_timeout: float = 5.0,
    ) -> None:
        if not callable(callback):
            raise TypeError("callback 必须可调用")
        if startup_timeout <= 0:
            raise ValueError("startup_timeout 必须大于 0")
        self._callback = callback
        self._startup_timeout = startup_timeout
        self._class_name = f"ArchiveScanDeviceMonitor-{uuid4().hex}"
        self._condition = threading.RLock()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None
        self._user32: Any | None = None
        self._kernel32: Any | None = None
        self._hinstance: Any | None = None
        self._atom: Any | None = None
        self._hwnd: Any | None = None
        self._device_notification: Any | None = None
        self._device_filter: Any | None = None
        self._window_proc = _WNDPROC(self._window_proc_callback)

    @staticmethod
    def is_relevant_change(event_code: int) -> bool:
        """判断设备变化消息是否需要重新枚举图像设备。"""

        return event_code in {
            DBT_DEVNODES_CHANGED,
            DBT_DEVICEARRIVAL,
            DBT_DEVICEREMOVECOMPLETE,
        }

    def start(self) -> None:
        """启动隐藏窗口和设备通知注册。"""

        if os.name != "nt":
            raise RuntimeError("Windows 设备通知只支持 Windows")
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._stop.clear()
            self._error = None
            thread = threading.Thread(
                target=self._run,
                name="ArchiveScanDeviceMonitor",
                daemon=True,
            )
            self._thread = thread
        thread.start()

        if not self._ready.wait(timeout=self._startup_timeout):
            self.stop()
            raise TimeoutError("Windows 设备通知窗口启动超时")
        with self._condition:
            error = self._error
        if error is not None:
            self.stop()
            raise RuntimeError("Windows 设备通知注册失败") from error

    def stop(self) -> None:
        """停止消息窗口并注销设备通知。"""

        with self._condition:
            thread = self._thread
            self._stop.set()
            hwnd = self._hwnd
            user32 = self._user32
        if hwnd is not None and user32 is not None:
            try:
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            except Exception:
                LOGGER.exception("发送 Windows 设备通知窗口关闭消息失败")
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._startup_timeout)
        with self._condition:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def _run(self) -> None:
        try:
            self._run_message_loop()
        except BaseException as exc:
            with self._condition:
                self._error = exc
            LOGGER.exception("Windows 设备通知消息循环异常退出")
        finally:
            self._cleanup()
            self._ready.set()

    def _run_message_loop(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_apis(user32, kernel32)
        with self._condition:
            self._user32 = user32
            self._kernel32 = kernel32

        hinstance = kernel32.GetModuleHandleW(None)
        if not hinstance:
            raise ctypes.WinError(ctypes.get_last_error())
        with self._condition:
            self._hinstance = hinstance

        window_class = _WNDCLASSW(
            style=0,
            lpfnWndProc=self._window_proc,
            cbClsExtra=0,
            cbWndExtra=0,
            hInstance=hinstance,
            hIcon=None,
            hCursor=None,
            hbrBackground=None,
            lpszMenuName=None,
            lpszClassName=self._class_name,
        )
        atom = user32.RegisterClassW(ctypes.byref(window_class))
        if not atom:
            raise ctypes.WinError(ctypes.get_last_error())
        with self._condition:
            self._atom = atom

        hwnd = user32.CreateWindowExW(
            0,
            self._class_name,
            self._class_name,
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            hinstance,
            None,
        )
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        with self._condition:
            self._hwnd = hwnd

        device_filter = _DEV_BROADCAST_DEVICEINTERFACE_W(
            dbcc_size=ctypes.sizeof(_DEV_BROADCAST_DEVICEINTERFACE_W),
            dbcc_devicetype=DBT_DEVTYP_DEVICEINTERFACE,
            dbcc_reserved=0,
            dbcc_classguid=GUID_DEVINTERFACE_IMAGE,
            dbcc_name="",
        )
        with self._condition:
            self._device_filter = device_filter
        device_notification = user32.RegisterDeviceNotificationW(
            hwnd,
            ctypes.byref(device_filter),
            DEVICE_NOTIFY_WINDOW_HANDLE,
        )
        if not device_notification:
            raise ctypes.WinError(ctypes.get_last_error())
        with self._condition:
            self._device_notification = device_notification
        LOGGER.info("Windows 图像设备变化通知已注册")
        self._ready.set()

        if self._stop.is_set():
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

        message = wintypes.MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result == -1:
                raise ctypes.WinError(ctypes.get_last_error())
            if result == 0:
                return
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def _window_proc_callback(
        self,
        hwnd: Any,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        if message == WM_DEVICECHANGE and self.is_relevant_change(int(wparam)):
            try:
                self._callback()
            except Exception:
                LOGGER.exception("处理 Windows 图像设备变化通知失败")
            return 1
        user32 = self._user32
        if message == WM_CLOSE and user32 is not None:
            user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY and user32 is not None:
            user32.PostQuitMessage(0)
            return 0
        if user32 is not None:
            return int(user32.DefWindowProcW(hwnd, message, wparam, lparam))
        return 0

    @staticmethod
    def _configure_apis(user32: Any, kernel32: Any) -> None:
        user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.RegisterDeviceNotificationW.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        user32.RegisterDeviceNotificationW.restype = wintypes.HANDLE
        user32.UnregisterDeviceNotification.argtypes = [wintypes.HANDLE]
        user32.UnregisterDeviceNotification.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = _LRESULT
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = _LRESULT
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.PostQuitMessage.restype = None
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.UnregisterClassW.restype = wintypes.BOOL
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    def _cleanup(self) -> None:
        with self._condition:
            user32 = self._user32
            device_notification = self._device_notification
            hwnd = self._hwnd
            atom = self._atom
            hinstance = self._hinstance
            self._device_notification = None
            self._device_filter = None
            self._hwnd = None
            self._atom = None
        if user32 is None:
            return
        if device_notification is not None:
            try:
                user32.UnregisterDeviceNotification(device_notification)
            except Exception:
                LOGGER.exception("注销 Windows 设备变化通知失败")
        if hwnd is not None:
            try:
                user32.DestroyWindow(hwnd)
            except Exception:
                LOGGER.exception("销毁 Windows 设备通知隐藏窗口失败")
        if atom is not None and hinstance is not None:
            try:
                user32.UnregisterClassW(self._class_name, hinstance)
            except Exception:
                LOGGER.exception("注销 Windows 设备通知窗口类失败")


__all__ = [
    "DBT_DEVNODES_CHANGED",
    "DBT_DEVICEARRIVAL",
    "DBT_DEVICEREMOVECOMPLETE",
    "DeviceChangeMonitor",
    "GUID_DEVINTERFACE_IMAGE",
    "WM_DEVICECHANGE",
]
