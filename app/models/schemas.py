"""扫描服务对外使用的 Capability 数据模型。"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


def _json_safe(value: Any) -> Any:
    """将 TWAIN 值转换成 JSON 可传输的值，二进制只在边界处编码。"""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    raise TypeError(f"Capability 值不能编码为 JSON: {type(value).__name__}")


def _contains_bytes(value: Any) -> bool:
    if isinstance(value, bytes):
        return True
    if isinstance(value, Mapping):
        return any(_contains_bytes(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_bytes(item) for item in value)
    return False


@dataclass(frozen=True, slots=True)
class CapabilityOperations:
    """Capability 支持的 TWAIN 操作位。"""

    get: bool = False
    set: bool = False
    get_current: bool = False
    get_default: bool = False
    reset: bool = False

    @classmethod
    def from_mask(cls, mask: int) -> CapabilityOperations:
        """按 `TWQC_*` 位掩码创建操作模型。"""

        if isinstance(mask, bool) or not isinstance(mask, int) or mask < 0:
            raise ValueError("Capability 操作位必须是非负整数")
        return cls(
            get=bool(mask & 0x0001),
            set=bool(mask & 0x0002),
            get_current=bool(mask & 0x0008),
            get_default=bool(mask & 0x0004),
            reset=bool(mask & 0x0010),
        )

    @property
    def bitmask(self) -> int:
        """返回原始 TWAIN 操作位掩码。"""

        return (
            (0x0001 if self.get else 0)
            | (0x0002 if self.set else 0)
            | (0x0008 if self.get_current else 0)
            | (0x0004 if self.get_default else 0)
            | (0x0010 if self.reset else 0)
        )

    def to_payload(self) -> dict[str, bool]:
        """转换为 API/IPC 使用的 camelCase 对象。"""

        return {
            "get": self.get,
            "set": self.set,
            "getCurrent": self.get_current,
            "getDefault": self.get_default,
            "reset": self.reset,
        }


@dataclass(frozen=True, slots=True)
class CapabilitySchema:
    """一次设备 Capability 查询得到的完整快照。"""

    capability_id: int
    standard_name: str | None
    custom: bool
    container_type: str
    item_type: str
    operations: CapabilityOperations
    current: Any = None
    default: Any = None
    values: tuple[Any, ...] | None = None
    minimum: Any = None
    maximum: Any = None
    step: Any = None
    standard_code: str | None = None
    standard_description: str | None = None
    source_manufacturer: str | None = None
    source_product_name: str | None = None
    query_error: str | None = None
    query_error_message: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.capability_id, bool) or not isinstance(
            self.capability_id, int
        ):
            raise ValueError("capability_id 必须是整数")
        if self.capability_id < 0 or self.capability_id > 0xFFFF:
            raise ValueError("capability_id 必须位于 0 到 65535 之间")
        if self.values is not None and not isinstance(self.values, tuple):
            object.__setattr__(self, "values", tuple(self.values))

    @property
    def capability_hex(self) -> str:
        """返回稳定的十六进制 Capability 编号。"""

        return f"0x{self.capability_id:04X}"

    @property
    def capability_name(self) -> str | None:
        """兼容 API 文档中的 capabilityName 命名。"""

        return self.standard_name

    @property
    def current_value(self) -> Any:
        """兼容 API 文档中的 currentValue 命名。"""

        return self.current

    @property
    def default_value(self) -> Any:
        """兼容 API 文档中的 defaultValue 命名。"""

        return self.default

    def to_payload(self) -> dict[str, Any]:
        """转换为不含 TWAIN 对象和 ctypes 值的 JSON 对象。"""

        current_has_bytes = _contains_bytes(self.current)
        default_has_bytes = _contains_bytes(self.default)
        values_has_bytes = _contains_bytes(self.values)
        payload: dict[str, Any] = {
            "capabilityId": self.capability_id,
            "capabilityHex": self.capability_hex,
            "capabilityName": self.standard_name,
            "custom": self.custom,
            "containerType": self.container_type,
            "itemType": self.item_type,
            "operations": self.operations.to_payload(),
            "operationMask": self.operations.bitmask,
            "currentValue": _json_safe(self.current),
            "defaultValue": _json_safe(self.default),
            "values": _json_safe(self.values),
            "source": {
                "manufacturer": self.source_manufacturer,
                "productName": self.source_product_name,
            },
        }
        if self.standard_code is not None:
            payload["standardCode"] = self.standard_code
        if self.standard_description is not None:
            payload["description"] = self.standard_description
        if self.container_type == "TW_RANGE":
            payload.update(
                {
                    "minimum": _json_safe(self.minimum),
                    "maximum": _json_safe(self.maximum),
                    "step": _json_safe(self.step),
                }
            )
        if current_has_bytes or default_has_bytes or values_has_bytes:
            payload["valueEncoding"] = "base64"
        if self.query_error is not None:
            payload["queryError"] = self.query_error
            payload["queryErrorMessage"] = self.query_error_message
        return payload


@dataclass(frozen=True, slots=True)
class CapabilitySetResult:
    """Capability 设置后的请求值、状态和值回读结果。"""

    capability_id: int
    item_type: str
    requested: Any
    actual: Any = None
    check_status: bool = False
    status_code: int = 0
    readback_unavailable: bool = False

    def to_payload(self) -> dict[str, Any]:
        """转换为 API/IPC 使用的 JSON 对象。"""

        return {
            "capabilityId": self.capability_id,
            "itemType": self.item_type,
            "requestedValue": _json_safe(self.requested),
            "actualValue": _json_safe(self.actual),
            "checkStatus": self.check_status,
            "statusCode": self.status_code,
            "readbackUnavailable": self.readback_unavailable,
        }


__all__ = [
    "CapabilityOperations",
    "CapabilitySchema",
    "CapabilitySetResult",
]
