"""TWAIN DSM 加载、Data Source 身份读取和设备枚举。"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
import struct
import sys
from typing import Any, Protocol

from app.scanner.capability_codec import (
    CapabilityMessage,
    RawCapability,
    TW_ARRAY,
    TW_ENUMERATION,
    TW_ONEVALUE,
    TW_RANGE,
)
from app.scanner.capability_service import CapabilityService


class TwainBackendError(RuntimeError):
    """TWAIN 后端对上层暴露的稳定错误。"""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


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
                item_type = int(ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint16))[0])
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


class PytwainDsmAdapter:
    """使用固定版本 pytwain 读取完整 TWAIN Data Source 身份。"""

    def __init__(self, dsm_path: str | Path | None = None) -> None:
        import twain

        self._twain = twain
        self._dsm_path = _resolve_dsm_path(dsm_path)
        kwargs: dict[str, Any] = {}
        if self._dsm_path is not None:
            kwargs["dsm_name"] = str(self._dsm_path)
        try:
            self._manager: Any = twain.SourceManager(0, **kwargs)
        except (OSError, twain.exceptions.SMLoadFileFailed) as exc:
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
            handle = TwainSourceHandle(
                identity=self._convert_identity(identity),
                capability_source=capability_source,
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
        if manager is not None:
            manager.close()
            self._manager = None

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
    ) -> None:
        if dsm_factory is not None and dsm_path is not None:
            raise ValueError("dsm_factory 和 dsm_path 不能同时指定")
        self._dsm_factory = dsm_factory or (
            lambda: PytwainDsmAdapter(dsm_path=dsm_path)
        )
        self._dsm: DsmAdapter | None = None
        self._source_handle: TwainSourceHandle | None = None

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
        return handle.to_payload(show_ui=False)

    def query_capabilities(self) -> list[Any]:
        """查询当前打开 Data Source 的全部 Capability，不执行设置。"""

        handle = self._source_handle
        if handle is None:
            raise TwainBackendError(
                "TWAIN_SOURCE_NOT_OPEN",
                "查询 Capability 前必须先打开 Data Source",
            )
        return CapabilityService(
            handle.capability_source,
            source_manufacturer=handle.identity.manufacturer,
            source_product_name=handle.identity.product_name,
        ).query_all()

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
