"""TWAIN DSM 加载、Data Source 身份读取和设备枚举。"""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
from collections.abc import Callable, Generator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
import struct
import sys
import time
from typing import Any, Protocol
from uuid import uuid4

from app.scanner.capability_codec import (
    CAP_AUTOFEED,
    CAP_AUTOSCAN,
    CAP_DUPLEXENABLED,
    CAP_FEEDERENABLED,
    CAP_FEEDERLOADED,
    CAP_INDICATORS,
    CAP_XFERCOUNT,
    CapabilityMessage,
    ICAP_IMAGEFILEFORMAT,
    ICAP_BITDEPTH,
    ICAP_COMPRESSION,
    ICAP_FRAMES,
    ICAP_JPEGQUALITY,
    ICAP_ORIENTATION,
    ICAP_PIXELTYPE,
    ICAP_SUPPORTEDSIZES,
    ICAP_XFERMECH,
    ICAP_XRESOLUTION,
    ICAP_YRESOLUTION,
    RawCapability,
    TW_ARRAY,
    TW_ENUMERATION,
    TW_ONEVALUE,
    TW_RANGE,
    TWFF_JFIF,
    TWSX_FILE,
)
from app.scanner.capability_service import CapabilityService
from app.scanner.diagnostics import describe_failure, failure_message, safe_diagnostics
from app.scanner.file_transfer import (
    FileTransfer,
    FileTransferError,
    FileTransferResult,
    TransferStatus,
)


LOGGER = logging.getLogger("archive_scan_service.worker")


class TwainBackendError(RuntimeError):
    """TWAIN 后端对上层暴露的稳定错误。"""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.diagnostics = safe_diagnostics(diagnostics)


class TwainScanStopped(TwainBackendError):
    """扫描在安全传输边界收到停止请求。"""

    def __init__(self) -> None:
        super().__init__("SCAN_STOPPED", "扫描已停止")


class TwainCapabilityFormatError(ValueError):
    """Capability 容器或 Item 类型无法解码，但保留原始头信息。"""

    def __init__(
        self,
        message: str,
        *,
        container_type: str,
        item_type: int,
    ) -> None:
        super().__init__(message)
        self.container_type = container_type
        self.item_type = item_type


@dataclass(frozen=True, slots=True)
class TwainSourceIdentity:
    """TWAIN `TW_IDENTITY` 中与设备枚举有关的字段。"""

    source_id: int
    manufacturer: str
    product_family: str
    product_name: str
    protocol_major: int
    protocol_minor: int


@dataclass(frozen=True, slots=True)
class TwainDevice:
    """可传给主进程和后续 API 层的设备快照。"""

    device_id: str
    manufacturer: str
    product_family: str
    product_name: str
    protocol_major: int
    protocol_minor: int
    architecture: str

    def to_payload(self) -> dict[str, str | int]:
        """转换为 IPC/API 可传输的 JSON 对象。"""

        return {
            "deviceId": self.device_id,
            "manufacturer": self.manufacturer,
            "productFamily": self.product_family,
            "productName": self.product_name,
            "protocolMajor": self.protocol_major,
            "protocolMinor": self.protocol_minor,
            "architecture": self.architecture,
        }


@dataclass(slots=True)
class TwainSourceHandle:
    """工作进程内已打开的 Data Source 和可追溯身份信息。"""

    identity: TwainSourceIdentity
    capability_source: Any = field(repr=False)
    transfer_source: Any = field(default=None, repr=False)
    dsm_path: Path | None = None
    source_identity: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self, *, show_ui: bool) -> dict[str, Any]:
        """转换为只包含 JSON 值的打开结果。"""

        return {
            "manufacturer": self.identity.manufacturer,
            "productFamily": self.identity.product_family,
            "productName": self.identity.product_name,
            "protocolMajor": self.identity.protocol_major,
            "protocolMinor": self.identity.protocol_minor,
            "architecture": f"x{struct.calcsize('P') * 8}",
            "dsmPath": str(self.dsm_path) if self.dsm_path is not None else None,
            "dsmArchitecture": f"x{struct.calcsize('P') * 8}",
            "sourceIdentity": dict(self.source_identity),
            "showUi": show_ui,
        }

    def close(self) -> None:
        """关闭底层 Data Source。"""

        close = getattr(self.capability_source, "close", None)
        if close is not None:
            close()


class DsmAdapter(Protocol):
    """TWAIN DSM 的最小可替换边界。"""

    def enumerate_sources(self) -> Sequence[TwainSourceIdentity]:
        """返回 DSM 当前注册的全部 Data Source 身份。"""

    def open_source(
        self,
        product_name: str,
        *,
        show_ui: bool = False,
    ) -> TwainSourceHandle:
        """在工作进程内无界面打开指定 Data Source。"""

    def close_source(self) -> None:
        """关闭当前打开的 Data Source。"""

    def close(self) -> None:
        """关闭 Data Source Manager。"""


DsmFactory = Callable[[], DsmAdapter]


def _candidate_dsm_paths() -> list[Path]:
    """返回只包含 64 位系统目录的 TWAINDSM.DLL 候选路径。"""

    candidates: list[Path] = []
    system_root = os.environ.get("SystemRoot")
    if system_root:
        candidates.append(Path(system_root) / "System32" / "TWAINDSM.DLL")

    candidates.append(Path(sys.executable).resolve().parent / "TWAINDSM.DLL")
    candidates.append(Path.cwd() / "TWAINDSM.DLL")
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry:
            candidates.append(Path(entry) / "TWAINDSM.DLL")

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate).lower()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(candidate)
    return unique


def _resolve_dsm_path(dsm_path: str | Path | None) -> Path | None:
    """解析实际使用的 DSM 路径，并拒绝 32 位 SysWOW64 路径。"""

    if dsm_path is not None:
        resolved = Path(dsm_path).expanduser().resolve()
        if "\\syswow64\\" in str(resolved).lower():
            raise TwainBackendError(
                "TWAIN_DSM_ARCHITECTURE_MISMATCH",
                "不能加载 SysWOW64 下的 32 位 TWAINDSM.DLL",
            )
        return resolved

    for candidate in _candidate_dsm_paths():
        if candidate.is_file() and "\\syswow64\\" not in str(candidate).lower():
            return candidate.resolve()
    return None


class PytwainCapabilitySource:
    """保留 pytwain 原始容器和 Item 类型的 Capability 适配器。"""

    def __init__(self, source: Any, twain_module: Any) -> None:
        self._source = source
        self._twain = twain_module
        identity = source.identity
        self.source_manufacturer = str(identity.get("Manufacturer", ""))
        self.source_product_name = str(identity.get("ProductName", source.name))

    def query_support(self, capability_id: int) -> int:
        """执行 `MSG_QUERYSUPPORT` 并读取 TWAIN 操作位。"""

        constants = self._twain.constants
        capability = self._twain.structs.TW_CAPABILITY(
            capability_id,
            constants.TWON_DONTCARE16,
            0,
        )
        self._source._call(
            constants.DG_CONTROL,
            constants.DAT_CAPABILITY,
            constants.MSG_QUERYSUPPORT,
            ctypes.byref(capability),
        )
        # TWAIN 将 QuerySupport 的 TWQC_* 位写入 ConType；少数 DSM
        # 实现会把它放入 hContainer，因此保留兼容回退。
        mask = int(capability.ConType)
        if mask == constants.TWON_DONTCARE16 and capability.hContainer:
            mask = int(
                ctypes.cast(
                    capability.hContainer,
                    ctypes.POINTER(ctypes.c_uint32),
                )[0]
            )
        return mask

    def set_capability(self, capability_id: int, item_type: str, value: Any) -> int:
        """用 pytwain 的 `TW_ONEVALUE/MSG_SET` 设置一个 Capability。"""

        type_id = getattr(self._twain.constants, item_type, None)
        if not isinstance(type_id, int):
            raise ValueError(f"未知 TWAIN Item 类型 {item_type}")
        try:
            result = self._source.set_capability(capability_id, type_id, value)
        except self._twain.exceptions.CheckStatus:
            return int(self._twain.constants.TWRC_CHECKSTATUS)
        if result is None:
            return int(self._twain.constants.TWRC_SUCCESS)
        return int(result)

    def get_capability_current(self, capability_id: int) -> RawCapability:
        """兼容扫描适配器读取当前值的便捷入口。"""

        return self.get_capability(capability_id, CapabilityMessage.GET_CURRENT)

    def get_status(self) -> dict[str, int]:
        """读取 `DAT_STATUS/MSG_GET`，保留 TWAIN 状态码。"""

        status = self._twain.structs.TW_STATUS()
        constants = self._twain.constants
        self._source._call(
            constants.DG_CONTROL,
            constants.DAT_STATUS,
            constants.MSG_GET,
            ctypes.byref(status),
        )
        return {
            "conditionCode": int(status.ConditionCode),
            "data": int(status.Data),
        }

    def get_capability(
        self,
        capability_id: int,
        message: CapabilityMessage,
    ) -> RawCapability:
        """读取一个 Capability，并保留容器和原始 Item 类型。"""

        constants = self._twain.constants
        messages = {
            CapabilityMessage.GET: constants.MSG_GET,
            CapabilityMessage.GET_CURRENT: constants.MSG_GETCURRENT,
            CapabilityMessage.GET_DEFAULT: constants.MSG_GETDEFAULT,
        }
        try:
            twain_message = messages[message]
        except KeyError as exc:
            raise ValueError(f"不支持的 Capability 消息 {message}") from exc

        capability = self._twain.structs.TW_CAPABILITY(
            capability_id,
            constants.TWON_DONTCARE16,
            0,
        )
        self._source._call(
            constants.DG_CONTROL,
            constants.DAT_CAPABILITY,
            twain_message,
            ctypes.byref(capability),
        )
        if not capability.hContainer:
            raise ValueError("TWAIN 返回空 Capability 容器")

        pointer = self._source._lock(capability.hContainer)
        try:
            container_type = int(capability.ConType)
            structs = self._twain.structs
            if container_type == constants.TWON_ONEVALUE:
                item_type = int(
                    ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint16))[0]
                )
                ctype = self._item_ctype(item_type, container_type=TW_ONEVALUE)
                value = ctypes.cast(pointer + 2, ctypes.POINTER(ctype))[0]
                return RawCapability(
                    container_type=TW_ONEVALUE,
                    item_type=item_type,
                    value=self._item_value(item_type, value),
                )

            if container_type == constants.TWON_RANGE:
                range_value = ctypes.cast(
                    pointer,
                    ctypes.POINTER(structs.TW_RANGE),
                ).contents
                item_type = int(range_value.ItemType)
                return RawCapability(
                    container_type=TW_RANGE,
                    item_type=item_type,
                    minimum=self._range_value(item_type, range_value.MinValue),
                    maximum=self._range_value(item_type, range_value.MaxValue),
                    step=self._range_value(item_type, range_value.StepSize),
                    default=self._range_value(item_type, range_value.DefaultValue),
                    current=self._range_value(item_type, range_value.CurrentValue),
                )

            if container_type == constants.TWON_ENUMERATION:
                enumeration = ctypes.cast(
                    pointer,
                    ctypes.POINTER(structs.TW_ENUMERATION),
                ).contents
                item_type = int(enumeration.ItemType)
                ctype = self._item_ctype(item_type, container_type=TW_ENUMERATION)
                item_pointer = ctypes.cast(
                    pointer + ctypes.sizeof(structs.TW_ENUMERATION),
                    ctypes.POINTER(ctype),
                )
                values = tuple(
                    self._item_value(item_type, item_pointer[index])
                    for index in range(int(enumeration.NumItems))
                )
                current_index = int(enumeration.CurrentIndex)
                default_index = int(enumeration.DefaultIndex)
                if current_index >= len(values) or default_index >= len(values):
                    raise ValueError("TWAIN 枚举 Capability 索引越界")
                return RawCapability(
                    container_type=TW_ENUMERATION,
                    item_type=item_type,
                    values=values,
                    current_index=current_index,
                    default_index=default_index,
                    current=values[current_index],
                    default=values[default_index],
                )

            if container_type == constants.TWON_ARRAY:
                array = ctypes.cast(
                    pointer,
                    ctypes.POINTER(structs.TW_ARRAY),
                ).contents
                item_type = int(array.ItemType)
                ctype = self._item_ctype(item_type, container_type=TW_ARRAY)
                item_pointer = ctypes.cast(
                    pointer + ctypes.sizeof(structs.TW_ARRAY),
                    ctypes.POINTER(ctype),
                )
                values = tuple(
                    self._item_value(item_type, item_pointer[index])
                    for index in range(int(array.NumItems))
                )
                return RawCapability(
                    container_type=TW_ARRAY,
                    item_type=item_type,
                    values=values,
                )

            raise ValueError(f"未知 TWAIN Capability 容器 {container_type}")
        finally:
            self._source._unlock(capability.hContainer)
            self._source._free(capability.hContainer)

    def close(self) -> None:
        """关闭底层 pytwain Data Source。"""

        self._source.close()
        if getattr(self._source, "_state", "closed") != "closed":
            raise TwainBackendError("SCAN_FAILED", "TWAIN 数据源未能正常关闭")

    def _item_ctype(self, item_type: int, *, container_type: str) -> Any:
        ctype = getattr(self._twain, "_mapping", {}).get(item_type)
        if ctype is None:
            raise TwainCapabilityFormatError(
                f"pytwain 不支持的 TWAIN Item 类型 {item_type}",
                container_type=container_type,
                item_type=item_type,
            )
        return ctype

    def _item_value(self, item_type: int, value: Any) -> Any:
        constants = self._twain.constants
        if item_type == constants.TWTY_BOOL:
            return bool(value.value if hasattr(value, "value") else value)
        if item_type == constants.TWTY_FIX32:
            return self._twain.structs.fix2float(value)
        if item_type == constants.TWTY_FRAME:
            return self._twain.structs.frame2tuple(value)
        if item_type in {
            constants.TWTY_STR32,
            constants.TWTY_STR64,
            constants.TWTY_STR128,
            constants.TWTY_STR255,
        }:
            raw = value if isinstance(value, bytes) else bytes(value)
            return self._source._decode(raw.split(b"\0", 1)[0]).strip()
        if hasattr(value, "value"):
            return int(value.value)
        return value

    def _range_value(self, item_type: int, value: int) -> Any:
        if item_type == self._twain.constants.TWTY_FIX32:
            raw = ctypes.c_uint32(int(value))
            fix = ctypes.cast(
                ctypes.pointer(raw),
                ctypes.POINTER(self._twain.structs.TW_FIX32),
            ).contents
            return self._twain.structs.fix2float(fix)
        ctype = self._item_ctype(item_type, container_type=TW_RANGE)
        converted = ctype(int(value))
        return self._item_value(item_type, converted)


class _TW_INFO(ctypes.Structure):
    """TWAIN `TW_INFO` 的最小布局。"""

    _pack_ = 2
    _fields_ = [
        ("InfoID", ctypes.c_uint16),
        ("ItemType", ctypes.c_uint16),
        ("NumItems", ctypes.c_uint16),
        ("ReturnCode", ctypes.c_uint16),
        ("Item", ctypes.c_uint32),
    ]


class _TW_EXTIMAGEINFO(ctypes.Structure):
    """TWAIN `TW_EXTIMAGEINFO` 的最小布局。"""

    _pack_ = 2
    _fields_ = [
        ("NumInfos", ctypes.c_uint32),
        ("Info", _TW_INFO * 1),
    ]


class PytwainFileTransferSource:
    """把 pytwain Source 暴露为一次无界面文件传输所需的接口。"""

    END_TRANSFER_TIMEOUT = 8.0

    def __init__(self, source: Any, manager: Any, twain_module: Any) -> None:
        self._source = source
        self._manager = manager
        self._twain = twain_module
        self._stop_requested: Callable[[], bool] | None = None

    @property
    def transfer_ready_message(self) -> int:
        return int(self._twain.constants.MSG_XFERREADY)

    @property
    def close_request_message(self) -> int:
        return int(self._twain.constants.MSG_CLOSEDSREQ)

    def prepare_file_transfer(self) -> None:
        """文件传输不再覆盖本轮已设置的采集页数。"""

        return None

    def start_acquisition(self) -> None:
        """以隐藏、非模态厂商界面启动采集。"""

        self._source.request_acquire(show_ui=False, modal_ui=False)

    def wait_for_event(self, *, should_stop: Callable[[], bool] | None = None) -> int:
        """运行 pytwain 消息循环并返回最后一个 TWAIN 事件。"""

        if should_stop is not None:
            self._stop_requested = should_stop
            return self._wait_interruptibly(should_stop)
        events: list[int] = []

        def callback(event: int) -> None:
            events.append(int(event))

        self._manager.set_callback(callback)
        try:
            self._source.modal_loop()
        finally:
            self._manager.set_callback(None)
        if not events:
            raise TwainBackendError(
                "TWAIN_SCAN_EVENT_MISSING",
                "TWAIN消息循环未返回扫描事件",
            )
        return events[-1]

    def _wait_interruptibly(self, should_stop: Callable[[], bool]) -> int:
        """在 TWAIN 所属线程泵送消息，同时轮询停止命令。"""

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        message = self._twain.structs.MSG()
        peek = user32.PeekMessageW
        peek.argtypes = [
            ctypes.POINTER(type(message)),
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        peek.restype = ctypes.c_int
        wait = user32.MsgWaitForMultipleObjectsEx
        wait.argtypes = [
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        wait.restype = ctypes.c_uint
        translate = user32.TranslateMessage
        translate.argtypes = [ctypes.POINTER(type(message))]
        dispatch = user32.DispatchMessageW
        dispatch.argtypes = [ctypes.POINTER(type(message))]
        dispatch.restype = ctypes.c_ssize_t
        while True:
            if should_stop():
                raise TwainScanStopped()
            if not peek(ctypes.byref(message), None, 0, 0, 1):
                if wait(0, None, 50, 0x04FF, 0x0004) == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                continue
            if message.message == 0x0012:
                raise TwainBackendError("SCANNER_OFFLINE", "TWAIN 消息窗口已退出")
            return_code, event = self._source._process_event(message)
            if event in {self.transfer_ready_message, self.close_request_message}:
                return int(event)
            if return_code == self._twain.constants.TWRC_NOTDSEVENT:
                translate(ctypes.byref(message))
                dispatch(ctypes.byref(message))

    def read_image_info(self) -> Any:
        """读取当前页面的标准图像信息。"""

        return self._source.image_info

    def read_extended_image_info(self) -> None:
        """读取 Data Source 支持的扩展图像信息入口。"""

        constants = self._twain.constants
        ext_info = _TW_EXTIMAGEINFO()
        self._source._call(
            constants.DG_IMAGE,
            constants.DAT_EXTIMAGEINFO,
            0x8005,
            ctypes.byref(ext_info),
        )

    def abort_transfer(self) -> None:
        """异常路径通过 `DAT_PENDINGXFERS/MSG_RESET` 清理传输。"""

        self._source._end_all_xfers()

    def transfer_file(self, path: Path, *, file_format: int) -> TransferStatus:
        """执行 `DAT_SETUPFILEXFER`、`DAT_IMAGEFILEXFER` 和 `MSG_ENDXFER`。"""

        stage = "setup_file_transfer"
        try:
            LOGGER.info("文件传输阶段：%s", stage)
            self._source.file_xfer_params = (str(path), int(file_format))
            stage = "image_file_transfer"
            LOGGER.info("文件传输阶段：%s", stage)
            return_code = int(self._source._get_file_image())
            LOGGER.info("图像传输返回：TWRC=%s", return_code)
            stage = "end_transfer"
            LOGGER.info("文件传输阶段：%s", stage)
            pending_count = self._end_file_transfer()
        except Exception as exc:
            detail = describe_failure(exc, stage)
            LOGGER.error("文件传输调用失败：诊断=%s", detail)
            raise TwainBackendError(
                "SCAN_FAILED",
                failure_message("SCAN_FAILED", detail),
                diagnostics=detail,
            ) from exc
        return TransferStatus(
            return_code=return_code,
            pending_count=pending_count,
        )

    def _end_file_transfer(self) -> int:
        """结束传输必须收到标准成功，特定待处理返回仅允许限时等待。"""

        constants = self._twain.constants
        deadline = time.monotonic() + self.END_TRANSFER_TIMEOUT
        while True:
            if self._stop_requested is not None and self._stop_requested():
                raise TwainScanStopped()
            pending = self._twain.structs.TW_PENDINGXFERS()
            return_code = int(
                self._source._call(
                    constants.DG_CONTROL,
                    constants.DAT_PENDINGXFERS,
                    constants.MSG_ENDXFER,
                    ctypes.byref(pending),
                    (0x8001,),
                )
            )
            count = int(pending.Count)
            LOGGER.info(
                "ENDXFER 返回：TWRC=%s，待传输数=%s，EOJ=%s",
                return_code,
                count,
                int(pending.EOJ),
            )
            if return_code == constants.TWRC_SUCCESS:
                if count == 0:
                    self._source._state = "enabled"
                return count
            detail = {
                "stage": "end_transfer",
                "twainReturnCode": return_code,
                "pendingCount": count,
            }
            status = self._twain.structs.TW_STATUS()
            try:
                self._source._call(
                    constants.DG_CONTROL,
                    constants.DAT_STATUS,
                    constants.MSG_GET,
                    ctypes.byref(status),
                )
                detail["twainConditionCode"] = int(status.ConditionCode)
            except Exception as exc:
                LOGGER.error(
                    "读取 ENDXFER 后状态失败：%s", describe_failure(exc, "end_transfer")
                )
            identity = getattr(self._source, "identity", {})
            can_wait = (
                return_code == 0x8001
                and count > 0
                and detail.get("twainConditionCode") == constants.TWCC_SUCCESS
                and "kodak" in str(identity.get("Manufacturer", "")).lower()
                and identity.get("ProductName") == "KODAK Scanner: i2000"
            )
            if not can_wait or time.monotonic() >= deadline:
                LOGGER.error("ENDXFER 未返回标准成功：诊断=%s", detail)
                raise TwainBackendError(
                    "SCAN_FAILED",
                    failure_message("SCAN_FAILED", detail),
                    diagnostics=detail,
                )
            self._pump_pending_events()
            time.sleep(0.1)

    def _pump_pending_events(self) -> bool:
        """结束传输等待期间继续泵送消息，不重新启用采集。"""

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        message = self._twain.structs.MSG()
        pointer_type = ctypes.POINTER(type(message))
        peek = user32.PeekMessageW
        peek.argtypes = [
            pointer_type,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        peek.restype = ctypes.c_int
        translate = user32.TranslateMessage
        translate.argtypes = [pointer_type]
        dispatch = user32.DispatchMessageW
        dispatch.argtypes = [pointer_type]
        dispatch.restype = ctypes.c_ssize_t
        ready = False
        for _ in range(64):
            if not peek(ctypes.byref(message), None, 0, 0, 1):
                break
            if message.message == 0x0012:
                raise TwainBackendError("SCANNER_OFFLINE", "TWAIN 消息窗口退出")
            result, event = self._source._process_event(message)
            if event == self.close_request_message:
                raise TwainBackendError("SCANNER_OFFLINE", "驱动请求关闭采集")
            if event == self.transfer_ready_message:
                ready = True
            if result == self._twain.constants.TWRC_NOTDSEVENT:
                translate(ctypes.byref(message))
                dispatch(ctypes.byref(message))
        return ready

    def finish_acquisition(self) -> None:
        """关闭当前采集状态，不弹出厂商界面。"""

        self._source.hide_ui()


class PytwainDsmAdapter:
    """使用固定版本 pytwain 读取完整 TWAIN Data Source 身份。"""

    def __init__(
        self,
        dsm_path: str | Path | None = None,
        *,
        parent_window: Any | None = None,
    ) -> None:
        import twain

        self._twain = twain
        self._dsm_path = _resolve_dsm_path(dsm_path)
        self._message_window: Any | None = None
        if parent_window is None:
            from app.scanner.twain_window import TwainMessageWindow

            self._message_window = TwainMessageWindow()
            parent_window = self._message_window.open()
        kwargs: dict[str, Any] = {}
        if self._dsm_path is not None:
            kwargs["dsm_name"] = str(self._dsm_path)
        try:
            self._manager: Any = twain.SourceManager(parent_window, **kwargs)
        except (OSError, twain.exceptions.SMLoadFileFailed) as exc:
            if self._message_window is not None:
                self._message_window.close()
                self._message_window = None
            raise TwainBackendError(
                "TWAIN_DSM_NOT_FOUND",
                "未找到或无法加载 64 位 TWAINDSM.DLL",
            ) from exc
        self._raw_identities: dict[str, Any] = {}
        self._source_handle: TwainSourceHandle | None = None

    def enumerate_sources(self) -> list[TwainSourceIdentity]:
        """枚举完整 `TW_IDENTITY`，不打开任何 Data Source。"""

        constants = self._twain.constants
        identity = self._twain.structs.TW_IDENTITY()
        try:
            result = self._manager._call(
                None,
                constants.DG_CONTROL,
                constants.DAT_IDENTITY,
                constants.MSG_GETFIRST,
                ctypes.byref(identity),
                expected_returns=(
                    constants.TWRC_SUCCESS,
                    constants.TWRC_ENDOFLIST,
                ),
            )
        except self._twain.exceptions.NoDataSourceError:
            return []

        sources: list[TwainSourceIdentity] = []
        self._raw_identities = {}
        while result != constants.TWRC_ENDOFLIST:
            converted = self._convert_identity(identity)
            sources.append(converted)
            self._raw_identities[converted.product_name] = identity
            identity = self._twain.structs.TW_IDENTITY()
            result = self._manager._call(
                None,
                constants.DG_CONTROL,
                constants.DAT_IDENTITY,
                constants.MSG_GETNEXT,
                ctypes.byref(identity),
                expected_returns=(
                    constants.TWRC_SUCCESS,
                    constants.TWRC_ENDOFLIST,
                ),
            )
        return sources

    def open_source(
        self,
        product_name: str,
        *,
        show_ui: bool = False,
    ) -> TwainSourceHandle:
        """无界面打开完整枚举身份对应的 Data Source。"""

        if show_ui:
            raise TwainBackendError(
                "TWAIN_UI_FORBIDDEN",
                "Capability 冒烟探测禁止打开厂商界面",
            )
        if self._source_handle is not None:
            raise TwainBackendError(
                "TWAIN_SOURCE_ALREADY_OPEN",
                "工作进程已经打开一个 Data Source",
            )
        if product_name not in self._raw_identities:
            self.enumerate_sources()
        identity = self._raw_identities.get(product_name)
        if identity is None:
            raise TwainBackendError(
                "TWAIN_SOURCE_NOT_FOUND",
                f"未找到 Data Source {product_name}",
            )

        source: Any | None = None
        try:
            # 不使用 pytwain.open_source 的残缺 TW_IDENTITY 构造，直接用
            # DSM 枚举得到的完整身份打开目标 Data Source。
            self._manager._open_ds(identity)
            source = self._twain.Source(self._manager, identity)
            self._manager._sources.add(source)
            capability_source = PytwainCapabilitySource(source, self._twain)
            transfer_source = PytwainFileTransferSource(
                source,
                self._manager,
                self._twain,
            )
            handle = TwainSourceHandle(
                identity=self._convert_identity(identity),
                capability_source=capability_source,
                transfer_source=transfer_source,
                dsm_path=self._dsm_path,
                source_identity=source.identity,
            )
            self._source_handle = handle
            return handle
        except TwainBackendError:
            raise
        except Exception as exc:
            if source is not None:
                try:
                    source.close()
                except Exception:
                    pass
            raise TwainBackendError(
                "TWAIN_SOURCE_OPEN_FAILED",
                f"无法打开 Data Source {product_name}",
            ) from exc

    def close_source(self) -> None:
        """关闭当前打开的 Data Source。"""

        handle = self._source_handle
        self._source_handle = None
        if handle is not None:
            handle.close()

    def close(self) -> None:
        """关闭 DSM，并让 pytwain 释放其 Data Source 句柄。"""

        self.close_source()
        manager = getattr(self, "_manager", None)
        try:
            if manager is not None:
                manager.close()
                self._manager = None
        finally:
            message_window = self._message_window
            self._message_window = None
            if message_window is not None:
                message_window.close()

    def _convert_identity(self, identity: Any) -> TwainSourceIdentity:
        return TwainSourceIdentity(
            source_id=int(identity.Id),
            manufacturer=self._decode_text(identity.Manufacturer),
            product_family=self._decode_text(identity.ProductFamily),
            product_name=self._decode_text(identity.ProductName),
            protocol_major=int(identity.ProtocolMajor),
            protocol_minor=int(identity.ProtocolMinor),
        )

    def _decode_text(self, value: Any) -> str:
        raw = bytes(value).split(b"\0", 1)[0]
        if not raw:
            return ""
        try:
            decoded = self._manager._decode(raw)
        except (AttributeError, UnicodeDecodeError):
            decoded = raw.decode(errors="replace")
        return decoded.strip()


class TwainBackend:
    """在工作进程内管理 DSM 并返回稳定的设备快照。"""

    def __init__(
        self,
        *,
        dsm_factory: DsmFactory | None = None,
        dsm_path: str | Path | None = None,
        parent_window: int | None = None,
    ) -> None:
        if dsm_factory is not None and (
            dsm_path is not None or parent_window is not None
        ):
            raise ValueError("dsm_factory 不能与 dsm_path 或 parent_window 同时指定")
        self._dsm_factory = dsm_factory or (
            lambda: PytwainDsmAdapter(
                dsm_path=dsm_path,
                parent_window=parent_window,
            )
        )
        self._dsm: DsmAdapter | None = None
        self._source_handle: TwainSourceHandle | None = None
        self._capability_service: CapabilityService | None = None

    def enumerate_devices(self) -> list[TwainDevice]:
        """加载 DSM 并枚举全部 Data Source，不自动打开设备。"""

        dsm = self._get_dsm()
        try:
            identities = dsm.enumerate_sources()
        except TwainBackendError:
            raise
        except Exception as exc:
            raise TwainBackendError(
                "TWAIN_SOURCE_ENUMERATION_FAILED",
                "TWAIN Data Source 枚举失败",
            ) from exc

        devices = [self._to_device(identity) for identity in identities]
        return sorted(devices, key=lambda device: device.device_id)

    def open_source(
        self,
        product_name: str,
        *,
        show_ui: bool = False,
    ) -> dict[str, Any]:
        """在工作进程内无界面打开一个目标 Data Source。"""

        if not product_name:
            raise TwainBackendError(
                "TWAIN_SOURCE_NOT_FOUND",
                "Data Source 产品名不能为空",
            )
        if show_ui:
            raise TwainBackendError(
                "TWAIN_UI_FORBIDDEN",
                "Capability 冒烟探测禁止打开厂商界面",
            )
        if self._source_handle is not None:
            raise TwainBackendError(
                "TWAIN_SOURCE_ALREADY_OPEN",
                "工作进程已经打开一个 Data Source",
            )

        dsm = self._get_dsm()
        try:
            handle = dsm.open_source(product_name, show_ui=False)
        except TwainBackendError:
            raise
        except Exception as exc:
            raise TwainBackendError(
                "TWAIN_SOURCE_OPEN_FAILED",
                f"无法打开 Data Source {product_name}",
            ) from exc
        self._source_handle = handle
        self._capability_service = None
        return handle.to_payload(show_ui=False)

    def query_capabilities(self) -> list[Any]:
        """查询当前打开 Data Source 的全部 Capability，不执行设置。"""

        handle = self._source_handle
        if handle is None:
            raise TwainBackendError(
                "TWAIN_SOURCE_NOT_OPEN",
                "查询 Capability 前必须先打开 Data Source",
            )
        service = CapabilityService(
            handle.capability_source,
            source_manufacturer=handle.identity.manufacturer,
            source_product_name=handle.identity.product_name,
        )
        self._capability_service = service
        return service.query_all()

    def resolve_capabilities(self, settings: Mapping[str, Any]) -> list[Any]:
        """应用固定配置后重新查询 Capability，不开始图像传输。"""

        if self._source_handle is None:
            raise TwainBackendError(
                "TWAIN_SOURCE_NOT_OPEN",
                "重新查询 Capability 前必须先打开 Data Source",
            )
        if settings:
            try:
                self._apply_fixed_settings(settings)
            except TwainBackendError:
                raise
            except Exception as exc:
                raise TwainBackendError(
                    "TWAIN_CAPABILITY_SET_FAILED",
                    "重新查询前的固定配置设置失败",
                ) from exc
        return self.query_capabilities()

    def scan_once(
        self,
        output_dir: str | Path,
        *,
        page_id: str = "page-1",
        settings: Mapping[str, Any] | None = None,
    ) -> FileTransferResult:
        """应用固定配置并扫描一面 JPEG。"""

        handle = self._source_handle
        transfer_source = handle.transfer_source if handle is not None else None
        if transfer_source is None:
            raise TwainBackendError(
                "TWAIN_SOURCE_NOT_OPEN",
                "扫描前必须先打开 Data Source",
            )

        configuration_results = (
            self._apply_fixed_settings(settings) if settings is not None else ()
        )
        transfer_ready = False

        def abort_transfer_if_needed() -> None:
            if not transfer_ready:
                return
            try:
                transfer_source.abort_transfer()
            except Exception:
                pass

        try:
            transfer_source.prepare_file_transfer()
            transfer_source.start_acquisition()
            event = transfer_source.wait_for_event()
            if event == transfer_source.close_request_message:
                raise TwainBackendError(
                    "SCANNER_OFFLINE",
                    "TWAIN Data Source 在传输前关闭了请求",
                )
            if event != transfer_source.transfer_ready_message:
                raise TwainBackendError(
                    "SCAN_FAILED",
                    f"收到未知 TWAIN 扫描事件: {event}",
                )
            transfer_ready = True
            transfer_source.read_extended_image_info()
            transfer_source.read_image_info()
            result = FileTransfer(output_dir).transfer_one(
                transfer_source,
                page_id=page_id,
            )
            if configuration_results:
                return FileTransferResult(
                    original_path=result.original_path,
                    size=result.size,
                    transfer_return_code=result.transfer_return_code,
                    pending_count=result.pending_count,
                    configuration_results=tuple(
                        item.to_payload() for item in configuration_results
                    ),
                )
            return result
        except TwainBackendError:
            abort_transfer_if_needed()
            raise
        except FileTransferError as exc:
            abort_transfer_if_needed()
            raise TwainBackendError("SCAN_FAILED", str(exc)) from exc
        except Exception as exc:
            abort_transfer_if_needed()
            raise TwainBackendError("SCAN_FAILED", "TWAIN扫描传输失败") from exc
        finally:
            try:
                transfer_source.finish_acquisition()
            except Exception:
                pass

    def scan_pages(
        self,
        output_dir: str | Path,
        *,
        page_id: str = "page-1",
        settings: Mapping[str, Any] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> Generator[FileTransferResult, None, None]:
        """一次启用 ADF，按待传输数量逐面输出，直到无纸或停止。"""

        handle = self._source_handle
        source = handle.transfer_source if handle is not None else None
        if source is None:
            raise TwainBackendError(
                "TWAIN_SOURCE_NOT_OPEN", "扫描前必须先打开 Data Source"
            )
        FileTransfer._validate_page_id(page_id)
        stop = should_stop or (lambda: False)
        if stop():
            raise TwainScanStopped()
        configuration = dict(settings or {})
        LOGGER.info("扫描阶段：query_capabilities")
        self.query_capabilities()
        requested = dict(
            self._fixed_capability_requests(
                {
                    key: value
                    for key, value in configuration.items()
                    if key not in {"outputDir", "pageId"}
                }
            )
        )
        snapshot = (
            {item.capability_id: item for item in self._capability_service.snapshot()}
            if self._capability_service is not None
            else {}
        )
        feeder = snapshot.get(CAP_FEEDERENABLED)
        adf = requested.get(
            CAP_FEEDERENABLED,
            feeder.current_value
            if feeder and isinstance(feeder.current_value, bool)
            else True,
        )
        transfer_count = requested.get(CAP_XFERCOUNT, -1) if adf else 1
        if (
            isinstance(transfer_count, bool)
            or not isinstance(transfer_count, int)
            or (transfer_count != -1 and not 1 <= transfer_count <= 32767)
        ):
            raise TwainBackendError(
                "TWAIN_CAPABILITY_SET_FAILED",
                "传输页数必须是 -1 或 1 至 32767 的整数",
                diagnostics={"stage": "set_capability", "capabilityId": CAP_XFERCOUNT},
            )
        results = self._apply_fixed_settings(
            configuration, transfer_count=transfer_count
        )
        if stop():
            raise TwainScanStopped()
        loaded = snapshot.get(CAP_FEEDERLOADED)
        if (
            adf
            and loaded
            and loaded.query_error is None
            and loaded.current_value is False
        ):
            return
        acquiring = False
        pending = False
        failed = False
        stage = "prepare_transfer"
        transferred = 0

        def start_session() -> None:
            nonlocal acquiring, pending, stage
            if stop():
                raise TwainScanStopped()
            stage = "prepare_transfer"
            source.prepare_file_transfer()
            acquiring = True
            stage = "enable_source"
            LOGGER.info("扫描阶段：%s", stage)
            source.start_acquisition()
            stage = "wait_event"
            LOGGER.info("扫描阶段：%s", stage)
            event = source.wait_for_event(should_stop=stop)
            LOGGER.info("收到 TWAIN 事件：%s", event)
            if event == source.close_request_message:
                raise TwainBackendError(
                    "SCANNER_OFFLINE", "TWAIN Data Source 关闭了扫描请求"
                )
            if event != source.transfer_ready_message:
                raise TwainBackendError("SCAN_FAILED", "收到未知 TWAIN 扫描事件")
            pending = True

        try:
            start_session()
            transfer = FileTransfer(output_dir)
            while True:
                if stop():
                    raise TwainScanStopped()
                stage = "read_extended_info"
                LOGGER.info("扫描阶段：%s", stage)
                source.read_extended_image_info()
                stage = "read_image_info"
                LOGGER.info("扫描阶段：%s", stage)
                source.read_image_info()
                stage = "transfer_file"
                LOGGER.info("扫描阶段：%s", stage)
                page = transfer.transfer_one(source, page_id=page_id)
                transferred += 1
                pending = page.pending_count != 0
                LOGGER.info(
                    "图像传输完成：本轮页数=%s，待传输数=%s",
                    transferred,
                    page.pending_count,
                )
                yield replace(
                    page,
                    configuration_results=tuple(item.to_payload() for item in results),
                )
                if not adf or (transfer_count > 0 and transferred >= transfer_count):
                    return
                if not pending:
                    return
                page_id = f"page-{uuid4().hex}"
        except TwainBackendError as exc:
            failed = True
            if not exc.diagnostics:
                exc.diagnostics = describe_failure(exc, stage)
            LOGGER.error(
                "扫描失败：错误码=%s，诊断=%s", exc.error_code, exc.diagnostics
            )
            raise
        except Exception as exc:
            cause: BaseException | None = exc
            while cause is not None:
                if isinstance(cause, TwainScanStopped):
                    failed = True
                    raise cause
                cause = cause.__cause__
            error_code = self._scan_error_code(exc)
            if adf and error_code == "ADF_EMPTY":
                return
            if error_code == "ADF_EMPTY":
                error_code = "SCAN_FAILED"
            failed = True
            detail = describe_failure(exc, stage)
            LOGGER.error("扫描失败：错误码=%s，诊断=%s", error_code, detail)
            raise TwainBackendError(
                error_code, failure_message(error_code, detail), diagnostics=detail
            ) from exc
        finally:
            cleanup_failure: TwainBackendError | None = None
            for needed, cleanup_stage, cleanup in (
                (pending, "reset_transfers", source.abort_transfer),
                (acquiring, "disable_source", source.finish_acquisition),
            ):
                if not needed:
                    continue
                LOGGER.info("扫描清理：%s", cleanup_stage)
                try:
                    cleanup()
                except Exception as exc:
                    detail = describe_failure(exc, cleanup_stage)
                    LOGGER.error("扫描清理失败：诊断=%s", detail)
                    if not failed and cleanup_failure is None:
                        cleanup_failure = TwainBackendError(
                            "SCAN_FAILED",
                            failure_message("SCAN_FAILED", detail),
                            diagnostics=detail,
                        )
            if cleanup_failure is not None:
                raise cleanup_failure

    @staticmethod
    def _scan_error_code(error: BaseException) -> str:
        """保留被文件传输层包装的真实设备错误类别。"""

        import twain

        while True:
            if isinstance(error, twain.exceptions.PaperJam):
                return "PAPER_JAM"
            if isinstance(
                error,
                (
                    twain.exceptions.CheckDeviceOnlineError,
                    twain.exceptions.NoDataSourceError,
                ),
            ):
                return "SCANNER_OFFLINE"
            if isinstance(error, twain.exceptions.UnknownError) and str(error) == (
                f"ConditionCode = {twain.constants.TWCC_NOMEDIA}"
            ):
                return "ADF_EMPTY"
            if error.__cause__ is None:
                return "SCAN_FAILED"
            error = error.__cause__

    def _apply_fixed_settings(
        self,
        settings: Mapping[str, Any],
        *,
        transfer_count: int = 1,
    ) -> tuple[Any, ...]:
        """按当前 Capability 快照设置显式传入的固定业务字段。"""

        capability_settings = {
            key: value
            for key, value in settings.items()
            if key not in {"outputDir", "pageId"}
        }
        service = self._capability_service
        if service is None:
            try:
                self.query_capabilities()
            except TwainBackendError:
                raise
            service = self._capability_service
        if service is None:
            raise TwainBackendError(
                "TWAIN_CAPABILITY_QUERY_FAILED",
                "无法创建 Capability 设置服务",
            )

        requests = self._fixed_capability_requests(capability_settings)
        requests_by_id: dict[int, Any] = {
            CAP_XFERCOUNT: transfer_count,
            CAP_AUTOSCAN: True,
            CAP_INDICATORS: False,
            ICAP_XFERMECH: TWSX_FILE,
            ICAP_IMAGEFILEFORMAT: TWFF_JFIF,
        }
        for capability_id, value in requests:
            if capability_id not in requests_by_id:
                requests_by_id[capability_id] = value
        handle = self._source_handle
        if (
            handle is not None
            and "kodak" in handle.identity.manufacturer.lower()
            and (handle.identity.product_name == "KODAK Scanner: i2000")
        ):
            supported = {item.capability_id for item in service.snapshot()}
            duplex = dict(requests).get(CAP_DUPLEXENABLED, False)
            page_count = (
                0
                if transfer_count == -1
                else ((transfer_count + 1) // 2 if duplex else transfer_count)
            )
            # i2000 私有控制，连续模式的真机语义仍需根据实际传输验证。
            for capability_id, value in (
                (0x8032, True),
                (0x8031, page_count),
            ):
                if capability_id in supported:
                    requests_by_id[capability_id] = value
        results = []
        try:
            for capability_id, value in requests_by_id.items():
                LOGGER.info("设置扫描参数：Capability=0x%04X", capability_id)
                results.append(service.set_capability(capability_id, value))
                LOGGER.info("扫描参数设置完成：Capability=0x%04X", capability_id)
        except Exception as exc:
            if isinstance(exc, TwainBackendError):
                raise
            error_code = getattr(exc, "error_code", "TWAIN_CAPABILITY_SET_FAILED")
            detail = describe_failure(exc, "set_capability", capability_id)
            LOGGER.error("扫描参数设置失败：错误码=%s，诊断=%s", error_code, detail)
            raise TwainBackendError(
                error_code, failure_message(error_code, detail), diagnostics=detail
            ) from exc
        return tuple(results)

    @staticmethod
    def _fixed_capability_requests(
        settings: Mapping[str, Any],
    ) -> list[tuple[int, Any]]:
        """把固定业务字段映射为标准 Capability 编号和值。"""

        constants = {
            "CAP_XFERCOUNT": CAP_XFERCOUNT,
            "CAP_AUTOSCAN": CAP_AUTOSCAN,
            "CAP_INDICATORS": CAP_INDICATORS,
            "CAP_FEEDERENABLED": CAP_FEEDERENABLED,
            "CAP_AUTOFEED": CAP_AUTOFEED,
            "CAP_DUPLEXENABLED": CAP_DUPLEXENABLED,
            "ICAP_XFERMECH": ICAP_XFERMECH,
            "ICAP_IMAGEFILEFORMAT": ICAP_IMAGEFILEFORMAT,
            "ICAP_PIXELTYPE": ICAP_PIXELTYPE,
            "ICAP_COMPRESSION": ICAP_COMPRESSION,
            "ICAP_BITDEPTH": ICAP_BITDEPTH,
            "ICAP_ORIENTATION": ICAP_ORIENTATION,
            "ICAP_FRAMES": ICAP_FRAMES,
            "ICAP_SUPPORTEDSIZES": ICAP_SUPPORTEDSIZES,
            "ICAP_XRESOLUTION": ICAP_XRESOLUTION,
            "ICAP_YRESOLUTION": ICAP_YRESOLUTION,
            "ICAP_JPEGQUALITY": ICAP_JPEGQUALITY,
        }
        aliases = {
            "pixelType": "ICAP_PIXELTYPE",
            "bitDepth": "ICAP_BITDEPTH",
            "orientation": "ICAP_ORIENTATION",
            "paperSize": "ICAP_SUPPORTEDSIZES",
            "jpegQuality": "ICAP_JPEGQUALITY",
            "compression": "ICAP_COMPRESSION",
            "xResolution": "ICAP_XRESOLUTION",
            "yResolution": "ICAP_YRESOLUTION",
        }
        requests: list[tuple[int, Any]] = []
        for key, value in settings.items():
            if key == "feedMode":
                mode = str(value).lower()
                if mode in {"flatbed", "flat", "平板"}:
                    requests.extend([(constants["CAP_FEEDERENABLED"], False)])
                elif mode in {"adf_simplex", "adfsimplex", "simplex", "adf单面"}:
                    requests.extend(
                        [
                            (constants["CAP_FEEDERENABLED"], True),
                            (constants["CAP_DUPLEXENABLED"], False),
                            (constants["CAP_AUTOFEED"], True),
                        ]
                    )
                elif mode in {"adf_duplex", "adfduplex", "duplex", "adf双面"}:
                    requests.extend(
                        [
                            (constants["CAP_FEEDERENABLED"], True),
                            (constants["CAP_DUPLEXENABLED"], True),
                            (constants["CAP_AUTOFEED"], True),
                        ]
                    )
                else:
                    raise ValueError(f"不支持的固定进纸模式 {value!r}")
                continue
            if key == "resolution":
                requests.extend(
                    [
                        (constants["ICAP_XRESOLUTION"], value),
                        (constants["ICAP_YRESOLUTION"], value),
                    ]
                )
                continue
            standard_name = aliases.get(key, key)
            if standard_name not in constants:
                raise ValueError(f"未知固定扫描配置字段 {key}")
            requests.append((constants[standard_name], value))
        return requests

    def close_source(self) -> None:
        """关闭当前打开的 Data Source。"""

        handle = self._source_handle
        self._source_handle = None
        dsm = self._dsm
        if dsm is not None:
            try:
                dsm.close_source()
                return
            except AttributeError:
                pass
        if handle is not None:
            handle.close()

    def close(self) -> None:
        """关闭当前 DSM 连接。"""

        self.close_source()
        dsm = self._dsm
        self._dsm = None
        if dsm is not None:
            dsm.close()

    def __enter__(self) -> TwainBackend:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def _get_dsm(self) -> DsmAdapter:
        if self._dsm is not None:
            return self._dsm
        try:
            self._dsm = self._dsm_factory()
        except TwainBackendError:
            raise
        except (ImportError, OSError) as exc:
            raise TwainBackendError(
                "TWAIN_DSM_NOT_FOUND",
                "未找到或无法加载 64 位 TWAINDSM.DLL",
            ) from exc
        return self._dsm

    @staticmethod
    def _to_device(identity: TwainSourceIdentity) -> TwainDevice:
        architecture = f"x{struct.calcsize('P') * 8}"
        stable_fields = {
            "architecture": architecture,
            "manufacturer": identity.manufacturer,
            "productFamily": identity.product_family,
            "productName": identity.product_name,
            "protocolMajor": identity.protocol_major,
            "protocolMinor": identity.protocol_minor,
            "sourceId": identity.source_id,
        }
        serialized = json.dumps(
            stable_fields,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        device_id = f"twain-{hashlib.sha256(serialized).hexdigest()}"
        return TwainDevice(
            device_id=device_id,
            manufacturer=identity.manufacturer,
            product_family=identity.product_family,
            product_name=identity.product_name,
            protocol_major=identity.protocol_major,
            protocol_minor=identity.protocol_minor,
            architecture=architecture,
        )
