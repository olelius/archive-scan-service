"""TWAIN DSM 加载、Data Source 身份读取和设备枚举。"""

from __future__ import annotations

import ctypes
import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import struct
from typing import Any, Protocol


class TwainBackendError(RuntimeError):
    """TWAIN 后端对上层暴露的稳定错误。"""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


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


class DsmAdapter(Protocol):
    """TWAIN DSM 的最小可替换边界。"""

    def enumerate_sources(self) -> Sequence[TwainSourceIdentity]:
        """返回 DSM 当前注册的全部 Data Source 身份。"""

    def close(self) -> None:
        """关闭 Data Source Manager。"""


DsmFactory = Callable[[], DsmAdapter]


class PytwainDsmAdapter:
    """使用固定版本 pytwain 读取完整 TWAIN Data Source 身份。"""

    def __init__(self, dsm_path: str | Path | None = None) -> None:
        import twain

        self._twain = twain
        kwargs: dict[str, Any] = {}
        if dsm_path is not None:
            kwargs["dsm_name"] = str(Path(dsm_path))
        try:
            self._manager: Any = twain.SourceManager(0, **kwargs)
        except (OSError, twain.exceptions.SMLoadFileFailed) as exc:
            raise TwainBackendError(
                "TWAIN_DSM_NOT_FOUND",
                "未找到或无法加载 64 位 TWAINDSM.DLL",
            ) from exc

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
        while result != constants.TWRC_ENDOFLIST:
            sources.append(self._convert_identity(identity))
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

    def close(self) -> None:
        """关闭 DSM，并让 pytwain 释放其 Data Source 句柄。"""

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

    def close(self) -> None:
        """关闭当前 DSM 连接。"""

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
