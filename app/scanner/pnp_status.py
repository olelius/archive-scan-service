"""Windows PnP 图像设备状态查询。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import logging
import os
import re
from typing import Any, Protocol


LOGGER = logging.getLogger("archive_scan_service.scanner.pnp_status")

ERROR_NO_MORE_ITEMS = 259
CR_SUCCESS = 0
DIGCF_ALLCLASSES = 0x00000004

SPDRP_DEVICEDESC = 0x00000000
SPDRP_HARDWAREID = 0x00000001
SPDRP_MFG = 0x0000000B
SPDRP_CLASS = 0x00000007
SPDRP_FRIENDLYNAME = 0x0000000C

_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_GENERIC_DEVICE_TOKENS = {
    "device",
    "document",
    "image",
    "imaging",
    "scanner",
    "series",
}


class _GUID(ctypes.Structure):
    _fields_ = [
        ("data1", wintypes.DWORD),
        ("data2", wintypes.WORD),
        ("data3", wintypes.WORD),
        ("data4", wintypes.BYTE * 8),
    ]


class _SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cb_size", wintypes.DWORD),
        ("class_guid", _GUID),
        ("dev_inst", wintypes.DWORD),
        ("reserved", ctypes.c_void_p),
    ]


@dataclass(frozen=True, slots=True)
class PnpDeviceSnapshot:
    """一个 Windows PnP 设备节点的状态快照。"""

    class_name: str
    description: str
    manufacturer: str
    instance_id: str
    present: bool
    problem_code: int
    config_return_code: int

    @property
    def online(self) -> bool:
        """把 PnP 节点状态转换为应用层在线状态。"""

        return (
            self.present
            and self.problem_code == 0
            and self.config_return_code == CR_SUCCESS
        )


class DeviceStatusResolver(Protocol):
    """把 Windows 设备状态合并到 TWAIN 枚举结果的边界。"""

    def enrich_devices(
        self,
        devices: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """返回带有在线状态的设备快照。"""


PnpEnumerator = Callable[[], Sequence[PnpDeviceSnapshot]]


class WindowsPnpStatusResolver:
    """使用 Windows SetupAPI/CfgMgr32 查询扫描仪当前 PnP 状态。"""

    def __init__(self, *, enumerator: PnpEnumerator | None = None) -> None:
        self._enumerator = enumerator or enumerate_windows_image_devices

    def enrich_devices(
        self,
        devices: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        values = [dict(device) for device in devices]
        for value in values:
            value.setdefault("online", True)

        if os.name != "nt":
            return values

        try:
            pnp_devices = list(self._enumerator())
        except Exception:
            LOGGER.warning("查询 Windows PnP 扫描仪状态失败，保留 TWAIN 枚举结果", exc_info=True)
            return values

        for value in values:
            online = self._resolve_online(value, pnp_devices)
            if online is not None:
                value["online"] = online
        return values

    @classmethod
    def _resolve_online(
        cls,
        device: Mapping[str, Any],
        pnp_devices: Sequence[PnpDeviceSnapshot],
    ) -> bool | None:
        matched = [
            item
            for item in pnp_devices
            if cls._matches(device, item)
        ]
        if not matched:
            return None
        return any(cls._snapshot_online(item) for item in matched)

    @staticmethod
    def _snapshot_online(snapshot: PnpDeviceSnapshot) -> bool:
        value = getattr(snapshot, "online", None)
        if isinstance(value, bool):
            return value
        return bool(
            getattr(snapshot, "present", False)
            and getattr(snapshot, "problem_code", 0) == 0
            and getattr(snapshot, "config_return_code", 1) == CR_SUCCESS
        )

    @staticmethod
    def _matches(
        device: Mapping[str, Any],
        snapshot: PnpDeviceSnapshot,
    ) -> bool:
        if str(getattr(snapshot, "class_name", "")).casefold() != "image":
            return False

        twain_manufacturer = _tokens(device.get("manufacturer"))
        pnp_manufacturer = _tokens(getattr(snapshot, "manufacturer", ""))
        if twain_manufacturer and pnp_manufacturer:
            if twain_manufacturer & pnp_manufacturer:
                return True

        twain_name = _tokens(device.get("productName"))
        pnp_name = _tokens(
            " ".join(
                (
                    str(getattr(snapshot, "description", "")),
                    str(getattr(snapshot, "manufacturer", "")),
                )
            )
        )
        meaningful_twain_name = twain_name - _GENERIC_DEVICE_TOKENS
        meaningful_pnp_name = pnp_name - _GENERIC_DEVICE_TOKENS
        return bool(meaningful_twain_name & meaningful_pnp_name)


def _tokens(value: Any) -> set[str]:
    return set(_TOKEN_PATTERN.findall(str(value or "").casefold()))


def enumerate_windows_image_devices() -> list[PnpDeviceSnapshot]:
    """枚举包含当前和 phantom 节点在内的 Windows Image 类设备。"""

    if os.name != "nt":
        return []

    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    cfgmgr32 = ctypes.WinDLL("cfgmgr32", use_last_error=True)
    _configure_apis(setupapi, cfgmgr32)

    device_info_set = setupapi.SetupDiGetClassDevsW(
        None,
        None,
        None,
        DIGCF_ALLCLASSES,
    )
    if not device_info_set or device_info_set == _INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        devices: list[PnpDeviceSnapshot] = []
        index = 0
        while True:
            data = _SP_DEVINFO_DATA(cb_size=ctypes.sizeof(_SP_DEVINFO_DATA))
            if not setupapi.SetupDiEnumDeviceInfo(
                device_info_set,
                index,
                ctypes.byref(data),
            ):
                error_code = ctypes.get_last_error()
                if error_code == ERROR_NO_MORE_ITEMS:
                    break
                raise ctypes.WinError(error_code)
            index += 1

            class_name = _read_property(
                setupapi,
                device_info_set,
                data,
                SPDRP_CLASS,
            )
            if class_name.casefold() != "image":
                continue

            description = _read_property(
                setupapi,
                device_info_set,
                data,
                SPDRP_DEVICEDESC,
            )
            friendly_name = _read_property(
                setupapi,
                device_info_set,
                data,
                SPDRP_FRIENDLYNAME,
            )
            manufacturer = _read_property(
                setupapi,
                device_info_set,
                data,
                SPDRP_MFG,
            )
            instance_id = _read_instance_id(setupapi, device_info_set, data)
            status = wintypes.ULONG()
            problem = wintypes.ULONG()
            config_return_code = int(
                cfgmgr32.CM_Get_DevNode_Status(
                    ctypes.byref(status),
                    ctypes.byref(problem),
                    data.dev_inst,
                    0,
                )
            )
            devices.append(
                PnpDeviceSnapshot(
                    class_name=class_name,
                    description=friendly_name or description,
                    manufacturer=manufacturer,
                    instance_id=instance_id,
                    present=(
                        config_return_code == CR_SUCCESS
                        and problem.value == 0
                    ),
                    problem_code=int(problem.value),
                    config_return_code=config_return_code,
                )
            )
        return devices
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(device_info_set)


def _read_property(
    setupapi: Any,
    device_info_set: Any,
    data: _SP_DEVINFO_DATA,
    property_id: int,
) -> str:
    buffer = ctypes.create_unicode_buffer(4096)
    data_type = wintypes.DWORD()
    required_size = wintypes.DWORD()
    if not setupapi.SetupDiGetDeviceRegistryPropertyW(
        device_info_set,
        ctypes.byref(data),
        property_id,
        ctypes.byref(data_type),
        ctypes.cast(buffer, ctypes.c_void_p),
        ctypes.sizeof(buffer),
        ctypes.byref(required_size),
    ):
        return ""
    return buffer.value.strip()


def _read_instance_id(
    setupapi: Any,
    device_info_set: Any,
    data: _SP_DEVINFO_DATA,
) -> str:
    buffer = ctypes.create_unicode_buffer(4096)
    required_size = wintypes.DWORD()
    if not setupapi.SetupDiGetDeviceInstanceIdW(
        device_info_set,
        ctypes.byref(data),
        buffer,
        len(buffer),
        ctypes.byref(required_size),
    ):
        return ""
    return buffer.value.strip()


def _configure_apis(setupapi: Any, cfgmgr32: Any) -> None:
    setupapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(_GUID),
        wintypes.LPCWSTR,
        wintypes.HWND,
        wintypes.DWORD,
    ]
    setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    setupapi.SetupDiEnumDeviceInfo.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(_SP_DEVINFO_DATA),
    ]
    setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_SP_DEVINFO_DATA),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    setupapi.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_SP_DEVINFO_DATA),
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    setupapi.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
    cfgmgr32.CM_Get_DevNode_Status.argtypes = [
        ctypes.POINTER(wintypes.ULONG),
        ctypes.POINTER(wintypes.ULONG),
        wintypes.DWORD,
        wintypes.ULONG,
    ]
    cfgmgr32.CM_Get_DevNode_Status.restype = wintypes.ULONG


__all__ = [
    "CR_SUCCESS",
    "DeviceStatusResolver",
    "PnpDeviceSnapshot",
    "WindowsPnpStatusResolver",
    "enumerate_windows_image_devices",
]
