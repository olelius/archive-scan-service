"""TWAIN Capability 查询、校验、设置和实际值回读服务。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from numbers import Real
from typing import Any, Protocol

from app.models.schemas import CapabilityOperations, CapabilitySchema, CapabilitySetResult
from app.scanner.capability_codec import (
    CAP_CUSTOMDSDATA,
    CAP_SUPPORTEDCAPS,
    CapabilityCodec,
    CapabilityMessage,
    RawCapability,
    TWRC_CHECKSTATUS,
    TWRC_SUCCESS,
    TW_ARRAY,
    TW_ENUMERATION,
    TW_ONEVALUE,
    TW_RANGE,
    TWTY_BOOL,
    TWTY_FIX32,
    TWTY_FRAME,
    TWTY_INT16,
    TWTY_INT32,
    TWTY_INT8,
    TWTY_STR128,
    TWTY_STR255,
    TWTY_STR1024,
    TWTY_STR32,
    TWTY_STR64,
    TWTY_UNI512,
    TWTY_UINT16,
    TWTY_UINT32,
    TWTY_UINT8,
    normalize_container_type,
    normalize_item_type,
)


class CapabilitySource(Protocol):
    """工作进程内 Data Source 的 Capability 最小边界。"""

    def get_capability(
        self,
        capability_id: int,
        message: CapabilityMessage,
    ) -> RawCapability | Mapping[str, Any]:
        """执行 `MSG_GET`、`MSG_GETCURRENT` 或 `MSG_GETDEFAULT`。"""

    def query_support(self, capability_id: int) -> int:
        """执行 `MSG_QUERYSUPPORT` 并返回 `TWQC_*` 位掩码。"""

    def set_capability(self, capability_id: int, item_type: str, value: Any) -> int | None:
        """用保留 Item 类型执行 `MSG_SET`，返回 TWAIN 返回码。"""


class CapabilityServiceError(RuntimeError):
    """Capability 服务对上层暴露的稳定错误。"""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class CapabilitySetError(ValueError):
    """Capability 设置前校验失败或驱动设置失败。"""

    def __init__(self, message: str, error_code: str = "TWAIN_CAPABILITY_SET_FAILED") -> None:
        super().__init__(message)
        self.error_code = error_code


class CapabilityService:
    """对一个已打开的 TWAIN Data Source 串行管理 Capability。"""

    def __init__(
        self,
        source: CapabilitySource,
        *,
        codec: CapabilityCodec | None = None,
        source_manufacturer: str | None = None,
        source_product_name: str | None = None,
    ) -> None:
        self._source = source
        self._codec = codec or CapabilityCodec()
        self._source_manufacturer = source_manufacturer or getattr(
            source, "source_manufacturer", None
        )
        self._source_product_name = source_product_name or getattr(
            source, "source_product_name", None
        )
        self._snapshot: dict[int, CapabilitySchema] = {}

    def query_all(self) -> list[CapabilitySchema]:
        """查询设备声明的全部 Capability，单项失败保留错误后继续。"""

        try:
            supported_raw = self._source.get_capability(
                CAP_SUPPORTEDCAPS,
                CapabilityMessage.GET,
            )
            supported_ids = self._normalize_supported_ids(
                self._codec.extract_values(supported_raw)
            )
        except Exception as exc:
            raise CapabilityServiceError(
                "TWAIN_CAPABILITY_QUERY_FAILED",
                "无法读取 CAP_SUPPORTEDCAPS",
            ) from exc

        result: list[CapabilitySchema] = []
        self._snapshot = {}
        for capability_id in supported_ids:
            item = self._query_one(capability_id)
            result.append(item)
            self._snapshot[capability_id] = item
        return result

    def query(self) -> list[CapabilitySchema]:
        """`query_all` 的简短别名。"""

        return self.query_all()

    def snapshot(self) -> tuple[CapabilitySchema, ...]:
        """返回最近一次成功查询到的 Capability 快照。"""

        return tuple(self._snapshot.values())

    def set_capability(self, capability_id: int, value: Any) -> CapabilitySetResult:
        """按最近一次查询快照校验并设置 Capability，然后回读实际值。"""

        capability = self._snapshot.get(capability_id)
        if capability is None:
            raise CapabilitySetError("只能设置本次查询结果中的 Capability")
        if capability.query_error is not None:
            raise CapabilitySetError("当前 Capability 查询失败，不能设置")
        if not capability.operations.set:
            raise CapabilitySetError("当前 Capability 不支持设置")
        if not capability.operations.get_current:
            raise CapabilitySetError("当前 Capability 不支持设置后的 GETCURRENT 回读")

        self._validate_value(capability, value)
        try:
            raw_status = self._source.set_capability(
                capability_id,
                capability.item_type,
                value,
            )
            status = self._status_code(raw_status)
        except Exception as exc:
            raise CapabilitySetError(
                f"Capability {capability.capability_hex} 设置失败"
            ) from exc

        if status not in (TWRC_SUCCESS, TWRC_CHECKSTATUS):
            raise CapabilitySetError(
                f"Capability {capability.capability_hex} 设置返回异常状态 {status}"
            )

        try:
            current_raw = self._source.get_capability(
                capability_id,
                CapabilityMessage.GET_CURRENT,
            )
            actual = self._extract_current(current_raw)
        except Exception as exc:
            raise CapabilitySetError(
                f"Capability {capability.capability_hex} 设置后无法读取实际值"
            ) from exc

        return CapabilitySetResult(
            capability_id=capability_id,
            item_type=capability.item_type,
            requested=value,
            actual=actual,
            check_status=status == TWRC_CHECKSTATUS,
        )

    def _query_one(self, capability_id: int) -> CapabilitySchema:
        operations = CapabilityOperations()
        try:
            raw_mask = self._source.query_support(capability_id)
            operations = CapabilityOperations.from_mask(raw_mask)
            raw_get = (
                self._source.get_capability(capability_id, CapabilityMessage.GET)
                if operations.get
                else None
            )
            raw_current = (
                self._source.get_capability(
                    capability_id,
                    CapabilityMessage.GET_CURRENT,
                )
                if operations.get_current
                else None
            )
            raw_default = (
                self._source.get_capability(
                    capability_id,
                    CapabilityMessage.GET_DEFAULT,
                )
                if operations.get_default
                else None
            )
            base = raw_get or raw_current or raw_default
            if base is None:
                raise ValueError("Capability 没有可读取的容器")
            return self._codec.decode(
                capability_id=capability_id,
                raw=base,
                current=(
                    self._extract_current(raw_current)
                    if raw_current is not None
                    else None
                ),
                default=(
                    self._extract_default(raw_default)
                    if raw_default is not None
                    else None
                ),
                operations=operations,
                source_manufacturer=self._source_manufacturer,
                source_product_name=self._source_product_name,
            )
        except Exception as exc:
            detail = str(exc) or type(exc).__name__
            result = self._codec.error(
                capability_id=capability_id,
                error_code="TWAIN_CAPABILITY_QUERY_FAILED",
                error_message=f"Capability {capability_id:#06x} 查询失败: {detail}",
                operations=operations,
                source_manufacturer=self._source_manufacturer,
                source_product_name=self._source_product_name,
            )
            raw_container = getattr(exc, "container_type", None)
            raw_item_type = getattr(exc, "item_type", None)
            if raw_container is not None and raw_item_type is not None:
                result = replace(
                    result,
                    container_type=normalize_container_type(raw_container),
                    item_type=normalize_item_type(raw_item_type),
                )
            return result

    @staticmethod
    def _normalize_supported_ids(values: Sequence[Any]) -> list[int]:
        result: list[int] = []
        seen: set[int] = set()
        for value in values:
            try:
                capability_id = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("CAP_SUPPORTEDCAPS 包含非整数 Capability 编号") from exc
            if isinstance(value, bool) or capability_id < 0 or capability_id > 0xFFFF:
                raise ValueError("CAP_SUPPORTEDCAPS 包含无效 Capability 编号")
            if capability_id not in seen:
                result.append(capability_id)
                seen.add(capability_id)
        return result

    @staticmethod
    def _status_code(value: int | None | Any) -> int:
        if value is None:
            return TWRC_SUCCESS
        if isinstance(value, bool):
            raise ValueError("TWAIN 返回码不能是布尔值")
        if isinstance(value, int):
            return value
        for name in ("return_code", "status_code", "status"):
            status = getattr(value, name, None)
            if isinstance(status, int) and not isinstance(status, bool):
                return status
        raise ValueError("TWAIN 设置返回值不是整数返回码")

    @staticmethod
    def _raw(raw: RawCapability | Mapping[str, Any]) -> RawCapability:
        if isinstance(raw, RawCapability):
            return raw
        return RawCapability.from_mapping(raw)

    @classmethod
    def _extract_current(cls, raw: RawCapability | Mapping[str, Any]) -> Any:
        value = cls._raw(raw)
        container_type = normalize_container_type(value.container_type)
        if container_type == TW_ONEVALUE:
            return value.value
        if container_type == TW_RANGE:
            return value.current
        if container_type == TW_ENUMERATION:
            if value.current is not None:
                return value.current
            if value.current_index is not None and value.values is not None:
                return tuple(value.values)[value.current_index]
            return None
        return value.current if value.current is not None else value.values

    @classmethod
    def _extract_default(cls, raw: RawCapability | Mapping[str, Any]) -> Any:
        value = cls._raw(raw)
        container_type = normalize_container_type(value.container_type)
        if container_type == TW_ONEVALUE:
            return value.value
        if container_type == TW_RANGE:
            return value.default
        if container_type == TW_ENUMERATION:
            if value.default is not None:
                return value.default
            if value.default_index is not None and value.values is not None:
                return tuple(value.values)[value.default_index]
            return None
        return value.default if value.default is not None else value.values

    def _validate_value(self, capability: CapabilitySchema, value: Any) -> None:
        if capability.capability_id == CAP_CUSTOMDSDATA:
            if not isinstance(value, (bytes, bytearray)):
                raise CapabilitySetError("CAP_CUSTOMDSDATA 只能整块保存或恢复二进制数据")
            return
        container_type = capability.container_type
        if container_type == TW_ENUMERATION:
            self._validate_item_type(capability.item_type, value)
            if capability.values is not None and value not in capability.values:
                raise CapabilitySetError("设置值不在驱动返回的枚举值集合中")
            return
        if container_type == TW_RANGE:
            self._validate_range(capability, value)
            return
        if container_type == TW_ARRAY:
            if not isinstance(value, (list, tuple)):
                raise CapabilitySetError("TW_ARRAY 设置值必须是数组")
            for item in value:
                self._validate_item_type(capability.item_type, item)
            return
        self._validate_item_type(capability.item_type, value)

    @staticmethod
    def _validate_range(capability: CapabilitySchema, value: Any) -> None:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise CapabilitySetError("TW_RANGE 设置值必须是数字")
        if capability.minimum is not None and value < capability.minimum:
            raise CapabilitySetError("TW_RANGE 设置值小于最小值")
        if capability.maximum is not None and value > capability.maximum:
            raise CapabilitySetError("TW_RANGE 设置值大于最大值")
        if capability.minimum is not None and capability.step not in (None, 0):
            quotient = (value - capability.minimum) / capability.step
            if abs(quotient - round(quotient)) > 1e-9:
                raise CapabilitySetError("TW_RANGE 设置值不符合步长")

    @staticmethod
    def _validate_item_type(item_type: str, value: Any) -> None:
        integer_types = {
            TWTY_INT8,
            TWTY_UINT8,
            TWTY_INT16,
            TWTY_UINT16,
            TWTY_INT32,
            TWTY_UINT32,
        }
        if item_type == TWTY_BOOL:
            valid = isinstance(value, bool)
        elif item_type in integer_types:
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif item_type == TWTY_FIX32:
            valid = isinstance(value, Real) and not isinstance(value, bool)
        elif item_type == TWTY_FRAME:
            valid = (
                isinstance(value, (tuple, list))
                and len(value) == 4
                and all(isinstance(item, Real) and not isinstance(item, bool) for item in value)
            )
        elif item_type in {
            TWTY_STR32,
            TWTY_STR64,
            TWTY_STR128,
            TWTY_STR255,
            TWTY_STR1024,
            TWTY_UNI512,
        }:
            valid = isinstance(value, str)
        else:
            valid = True
        if not valid:
            raise CapabilitySetError(f"设置值与 Item 类型 {item_type} 不匹配")


__all__ = [
    "CapabilityService",
    "CapabilityServiceError",
    "CapabilitySetError",
    "CapabilitySource",
]
