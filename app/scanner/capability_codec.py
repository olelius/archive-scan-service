"""TWAIN Capability 容器、Item 类型和 JSON 模型编解码。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.models.schemas import CapabilityOperations, CapabilitySchema


# TWAIN 标准容器编号。
TWON_ARRAY = 3
TWON_ENUMERATION = 4
TWON_ONEVALUE = 5
TWON_RANGE = 6

TW_ARRAY = "TW_ARRAY"
TW_ENUMERATION = "TW_ENUMERATION"
TW_ONEVALUE = "TW_ONEVALUE"
TW_RANGE = "TW_RANGE"

# TWAIN Item 类型编号。
TWTY_INT8 = 0
TWTY_INT16 = 1
TWTY_INT32 = 2
TWTY_UINT8 = 3
TWTY_UINT16 = 4
TWTY_UINT32 = 5
TWTY_BOOL = 6
TWTY_FIX32 = 7
TWTY_FRAME = 8
TWTY_STR32 = 9
TWTY_STR64 = 10
TWTY_STR128 = 11
TWTY_STR255 = 12
TWTY_STR1024 = 13
TWTY_UNI512 = 14

# TWAIN Capability 操作位。
TWQC_GET = 0x0001
TWQC_SET = 0x0002
TWQC_GETDEFAULT = 0x0004
TWQC_GETCURRENT = 0x0008
TWQC_RESET = 0x0010

# TWAIN 返回码和标准 Capability 编号。
TWRC_SUCCESS = 0
TWRC_CHECKSTATUS = 2
CAP_XFERCOUNT = 0x0001
CAP_AUTHOR = 0x1000
CAP_CAPTION = 0x1001
CAP_FEEDERENABLED = 0x1002
CAP_FEEDERLOADED = 0x1003
CAP_SUPPORTEDCAPS = 0x1005
CAP_AUTOFEED = 0x1007
CAP_CLEARPAGE = 0x1008
CAP_FEEDPAGE = 0x1009
CAP_REWINDPAGE = 0x100A
CAP_INDICATORS = 0x100B
CAP_PAPERDETECTABLE = 0x100D
CAP_UICONTROLLABLE = 0x100E
CAP_DEVICEONLINE = 0x100F
CAP_AUTOSCAN = 0x1010
CAP_THUMBNAILSENABLED = 0x1011
CAP_DUPLEX = 0x1012
CAP_DUPLEXENABLED = 0x1013
CAP_CUSTOMDSDATA = 0x1015
CAP_SERIALNUMBER = 0x1024
CAP_DEVICEEVENT = 0x1022
CAP_SUPPORTEDCAPSEXT = 0x100C
CAP_CUSTOMBASE = 0x8000

ICAP_COMPRESSION = 0x0100
ICAP_PIXELTYPE = 0x0101
ICAP_UNITS = 0x0102
ICAP_XFERMECH = 0x0103
ICAP_AUTOBRIGHT = 0x1100
ICAP_BRIGHTNESS = 0x1101
ICAP_CONTRAST = 0x1103
ICAP_IMAGEFILEFORMAT = 0x110C
ICAP_BITDEPTH = 0x112B


class CapabilityMessage(StrEnum):
    """Capability 查询使用的 TWAIN 消息。"""

    GET = "MSG_GET"
    GET_CURRENT = "MSG_GETCURRENT"
    GET_DEFAULT = "MSG_GETDEFAULT"
    GETCURRENT = "MSG_GETCURRENT"
    GETDEFAULT = "MSG_GETDEFAULT"


_CONTAINER_BY_NUMBER = {
    TWON_ARRAY: TW_ARRAY,
    TWON_ENUMERATION: TW_ENUMERATION,
    TWON_ONEVALUE: TW_ONEVALUE,
    TWON_RANGE: TW_RANGE,
}
_CONTAINER_NAMES = {
    TW_ARRAY,
    TW_ENUMERATION,
    TW_ONEVALUE,
    TW_RANGE,
    "TWON_ARRAY",
    "TWON_ENUMERATION",
    "TWON_ONEVALUE",
    "TWON_RANGE",
}
_ITEM_TYPE_BY_NUMBER = {
    TWTY_INT8: "TWTY_INT8",
    TWTY_UINT8: "TWTY_UINT8",
    TWTY_INT16: "TWTY_INT16",
    TWTY_UINT16: "TWTY_UINT16",
    TWTY_INT32: "TWTY_INT32",
    TWTY_UINT32: "TWTY_UINT32",
    TWTY_BOOL: "TWTY_BOOL",
    TWTY_FRAME: "TWTY_FRAME",
    TWTY_STR32: "TWTY_STR32",
    TWTY_STR64: "TWTY_STR64",
    TWTY_STR128: "TWTY_STR128",
    TWTY_STR255: "TWTY_STR255",
    TWTY_FIX32: "TWTY_FIX32",
    TWTY_STR1024: "TWTY_STR1024",
    TWTY_UNI512: "TWTY_UNI512",
}
_STANDARD_CAPABILITIES: dict[int, tuple[str, str, str]] = {
    CAP_XFERCOUNT: ("CAP_XFERCOUNT", "传输页数", "控制本次传输的页面数量"),
    CAP_AUTHOR: ("CAP_AUTHOR", "作者", "图像作者或操作者"),
    CAP_CAPTION: ("CAP_CAPTION", "标题", "图像标题"),
    CAP_FEEDERENABLED: ("CAP_FEEDERENABLED", "进纸器启用", "是否启用自动进纸器"),
    CAP_FEEDERLOADED: ("CAP_FEEDERLOADED", "进纸器有纸", "进纸器中是否检测到纸张"),
    CAP_SUPPORTEDCAPS: ("CAP_SUPPORTEDCAPS", "支持的能力", "设备支持的 Capability 编号列表"),
    CAP_AUTOFEED: ("CAP_AUTOFEED", "自动进纸", "是否自动连续进纸"),
    CAP_CLEARPAGE: ("CAP_CLEARPAGE", "清除页面", "是否清除当前页面"),
    CAP_FEEDPAGE: ("CAP_FEEDPAGE", "进一页纸", "请求进一页纸"),
    CAP_REWINDPAGE: ("CAP_REWINDPAGE", "退回页面", "是否允许退回页面"),
    CAP_INDICATORS: ("CAP_INDICATORS", "设备指示器", "是否允许设备指示器工作"),
    CAP_PAPERDETECTABLE: ("CAP_PAPERDETECTABLE", "纸张可检测", "设备是否支持纸张检测"),
    CAP_UICONTROLLABLE: ("CAP_UICONTROLLABLE", "界面可控制", "应用是否可以控制设备界面"),
    CAP_DEVICEONLINE: ("CAP_DEVICEONLINE", "设备在线", "设备当前是否在线"),
    CAP_AUTOSCAN: ("CAP_AUTOSCAN", "自动扫描", "是否自动开始扫描"),
    CAP_THUMBNAILSENABLED: ("CAP_THUMBNAILSENABLED", "缩略图启用", "是否允许缩略图传输"),
    CAP_DUPLEX: ("CAP_DUPLEX", "双面能力", "设备是否支持双面扫描"),
    CAP_DUPLEXENABLED: ("CAP_DUPLEXENABLED", "双面启用", "是否启用双面扫描"),
    CAP_CUSTOMDSDATA: ("CAP_CUSTOMDSDATA", "自定义设备数据", "整块保存或恢复厂商设备数据"),
    CAP_SERIALNUMBER: ("CAP_SERIALNUMBER", "序列号", "设备序列号"),
    CAP_DEVICEEVENT: ("CAP_DEVICEEVENT", "设备事件", "设备事件通知能力"),
    CAP_SUPPORTEDCAPSEXT: ("CAP_SUPPORTEDCAPSEXT", "扩展支持的能力", "扩展 Capability 编号列表"),
    ICAP_COMPRESSION: ("ICAP_COMPRESSION", "压缩方式", "图像压缩方式"),
    ICAP_PIXELTYPE: ("ICAP_PIXELTYPE", "像素类型", "图像颜色和像素类型"),
    ICAP_UNITS: ("ICAP_UNITS", "度量单位", "图像尺寸和分辨率使用的单位"),
    ICAP_XFERMECH: ("ICAP_XFERMECH", "传输机制", "图像传输机制"),
    ICAP_AUTOBRIGHT: ("ICAP_AUTOBRIGHT", "自动亮度", "是否自动调整亮度"),
    ICAP_BRIGHTNESS: ("ICAP_BRIGHTNESS", "亮度", "图像亮度"),
    ICAP_CONTRAST: ("ICAP_CONTRAST", "对比度", "图像对比度"),
    ICAP_IMAGEFILEFORMAT: ("ICAP_IMAGEFILEFORMAT", "图像文件格式", "文件传输时的图像格式"),
    ICAP_BITDEPTH: ("ICAP_BITDEPTH", "位深度", "每像素的位数"),
}


def normalize_container_type(value: str | int) -> str:
    """将 TWAIN 数字或名称容器统一为项目使用的名称。"""

    if isinstance(value, bool):
        raise ValueError("容器类型不能是布尔值")
    if isinstance(value, int):
        try:
            return _CONTAINER_BY_NUMBER[value]
        except KeyError as exc:
            raise ValueError(f"未知 TWAIN 容器类型 {value}") from exc
    if not isinstance(value, str) or value not in _CONTAINER_NAMES:
        raise ValueError(f"未知 TWAIN 容器类型 {value!r}")
    return value.replace("TWON_", "TW_")


def normalize_item_type(value: str | int) -> str:
    """将 TWAIN Item 类型统一为名称，未知数字保留其原始编号。"""

    if isinstance(value, bool):
        raise ValueError("Item 类型不能是布尔值")
    if isinstance(value, int):
        return _ITEM_TYPE_BY_NUMBER.get(value, f"TWTY_UNKNOWN(0x{value:04X})")
    if not isinstance(value, str) or not value:
        raise ValueError("Item 类型必须是非空字符串或整数")
    return value


@dataclass(frozen=True, slots=True)
class RawCapability:
    """与 TWAIN 容器对应的、尚未映射成业务模型的值。"""

    container_type: str | int
    item_type: str | int
    value: Any = None
    values: tuple[Any, ...] | Sequence[Any] | None = None
    minimum: Any = None
    maximum: Any = None
    step: Any = None
    current: Any = None
    default: Any = None
    current_index: int | None = None
    default_index: int | None = None

    def __post_init__(self) -> None:
        if self.values is not None and not isinstance(self.values, tuple):
            object.__setattr__(self, "values", tuple(self.values))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> RawCapability:
        """兼容 pytwain/低层适配器返回的 snake_case 和 TWAIN 字段名。"""

        def read(*names: str, default: Any = None) -> Any:
            for name in names:
                if name in value:
                    return value[name]
            return default

        values = read("values", "Values", "items", "Items")
        return cls(
            container_type=read("container_type", "containerType", "ConType"),
            item_type=read("item_type", "itemType", "ItemType"),
            value=read("value", "Value"),
            values=values,
            minimum=read("minimum", "minimumValue", "MinValue"),
            maximum=read("maximum", "maximumValue", "MaxValue"),
            step=read("step", "stepSize", "StepSize"),
            current=read("current", "currentValue", "CurrentValue"),
            default=read("default", "defaultValue", "DefaultValue"),
            current_index=read("current_index", "currentIndex", "CurrentIndex"),
            default_index=read("default_index", "defaultIndex", "DefaultIndex"),
        )


def _coerce_raw(raw: RawCapability | Mapping[str, Any]) -> RawCapability:
    if isinstance(raw, RawCapability):
        return raw
    if isinstance(raw, Mapping):
        return RawCapability.from_mapping(raw)
    raise TypeError(f"不支持的 Capability 原始值类型 {type(raw).__name__}")


def _coerce_operations(value: CapabilityOperations | int) -> CapabilityOperations:
    if isinstance(value, CapabilityOperations):
        return value
    return CapabilityOperations.from_mask(value)


class CapabilityCodec:
    """解析四类 TWAIN Capability 容器并生成稳定业务模型。"""

    def decode_one_value(
        self,
        *,
        capability_id: int,
        item_type: str | int,
        current: Any,
        default: Any = None,
        operations: CapabilityOperations | int = 0,
        container_type: str | int = TW_ONEVALUE,
        source_manufacturer: str | None = None,
        source_product_name: str | None = None,
    ) -> CapabilitySchema:
        """解析 `TW_ONEVALUE`，保留原始 Item 类型和 Python 值类型。"""

        return self._build(
            capability_id=capability_id,
            container_type=container_type,
            item_type=item_type,
            operations=operations,
            current=current,
            default=default,
            source_manufacturer=source_manufacturer,
            source_product_name=source_product_name,
        )

    def decode_enumeration(
        self,
        *,
        capability_id: int,
        item_type: str | int,
        values: Sequence[Any],
        current_index: int | None = None,
        default_index: int | None = None,
        current: Any = None,
        default: Any = None,
        operations: CapabilityOperations | int = 0,
        container_type: str | int = TW_ENUMERATION,
        source_manufacturer: str | None = None,
        source_product_name: str | None = None,
    ) -> CapabilitySchema:
        """解析 `TW_ENUMERATION`，并将索引转换成实际枚举值。"""

        normalized_values = tuple(values)
        if current is None and current_index is not None:
            current = self._value_at_index(normalized_values, current_index, "current_index")
        if default is None and default_index is not None:
            default = self._value_at_index(normalized_values, default_index, "default_index")
        return self._build(
            capability_id=capability_id,
            container_type=container_type,
            item_type=item_type,
            operations=operations,
            current=current,
            default=default,
            values=normalized_values,
            source_manufacturer=source_manufacturer,
            source_product_name=source_product_name,
        )

    def decode_range(
        self,
        *,
        capability_id: int,
        item_type: str | int,
        minimum: Any,
        maximum: Any,
        step: Any,
        current: Any,
        default: Any,
        operations: CapabilityOperations | int = 0,
        container_type: str | int = TW_RANGE,
        source_manufacturer: str | None = None,
        source_product_name: str | None = None,
    ) -> CapabilitySchema:
        """解析 `TW_RANGE`，保留最小值、最大值、步长和当前/默认值。"""

        return self._build(
            capability_id=capability_id,
            container_type=container_type,
            item_type=item_type,
            operations=operations,
            current=current,
            default=default,
            minimum=minimum,
            maximum=maximum,
            step=step,
            source_manufacturer=source_manufacturer,
            source_product_name=source_product_name,
        )

    def decode_array(
        self,
        *,
        capability_id: int,
        item_type: str | int,
        values: Sequence[Any],
        current: Any = None,
        default: Any = None,
        operations: CapabilityOperations | int = 0,
        container_type: str | int = TW_ARRAY,
        source_manufacturer: str | None = None,
        source_product_name: str | None = None,
    ) -> CapabilitySchema:
        """解析 `TW_ARRAY`，不把数组中的 Item 强制转换成字符串。"""

        return self._build(
            capability_id=capability_id,
            container_type=container_type,
            item_type=item_type,
            operations=operations,
            current=current,
            default=default,
            values=tuple(values),
            source_manufacturer=source_manufacturer,
            source_product_name=source_product_name,
        )

    def decode(
        self,
        *,
        capability_id: int,
        raw: RawCapability | Mapping[str, Any],
        current: Any = None,
        default: Any = None,
        operations: CapabilityOperations | int = 0,
        source_manufacturer: str | None = None,
        source_product_name: str | None = None,
    ) -> CapabilitySchema:
        """按原始容器类型分派到四种解码器。"""

        value = _coerce_raw(raw)
        container_type = normalize_container_type(value.container_type)
        if container_type == TW_ONEVALUE:
            return self.decode_one_value(
                capability_id=capability_id,
                item_type=value.item_type,
                current=value.value if current is None else current,
                default=value.default if default is None else default,
                operations=operations,
                container_type=container_type,
                source_manufacturer=source_manufacturer,
                source_product_name=source_product_name,
            )
        if container_type == TW_ENUMERATION:
            values = value.values or ()
            return self.decode_enumeration(
                capability_id=capability_id,
                item_type=value.item_type,
                values=values,
                current_index=value.current_index,
                default_index=value.default_index,
                current=value.current if current is None else current,
                default=value.default if default is None else default,
                operations=operations,
                container_type=container_type,
                source_manufacturer=source_manufacturer,
                source_product_name=source_product_name,
            )
        if container_type == TW_RANGE:
            return self.decode_range(
                capability_id=capability_id,
                item_type=value.item_type,
                minimum=value.minimum,
                maximum=value.maximum,
                step=value.step,
                current=value.current if current is None else current,
                default=value.default if default is None else default,
                operations=operations,
                container_type=container_type,
                source_manufacturer=source_manufacturer,
                source_product_name=source_product_name,
            )
        return self.decode_array(
            capability_id=capability_id,
            item_type=value.item_type,
            values=value.values or (),
            current=value.current if current is None else current,
            default=value.default if default is None else default,
            operations=operations,
            container_type=container_type,
            source_manufacturer=source_manufacturer,
            source_product_name=source_product_name,
        )

    def error(
        self,
        *,
        capability_id: int,
        error_code: str,
        error_message: str,
        operations: CapabilityOperations | int = 0,
        source_manufacturer: str | None = None,
        source_product_name: str | None = None,
    ) -> CapabilitySchema:
        """为单项查询失败保留 Capability 编号和错误信息。"""

        standard_code, standard_name, description = _STANDARD_CAPABILITIES.get(
            capability_id,
            (None, None, None),
        )
        return CapabilitySchema(
            capability_id=capability_id,
            standard_name=standard_name,
            standard_code=standard_code,
            standard_description=description,
            custom=capability_id >= CAP_CUSTOMBASE,
            container_type=TW_ONEVALUE,
            item_type="TWTY_UNKNOWN",
            operations=_coerce_operations(operations),
            source_manufacturer=source_manufacturer,
            source_product_name=source_product_name,
            query_error=error_code,
            query_error_message=error_message,
        )

    @staticmethod
    def extract_values(raw: RawCapability | Mapping[str, Any]) -> tuple[Any, ...]:
        """提取 `CAP_SUPPORTEDCAPS` 等列表 Capability 的全部值。"""

        value = _coerce_raw(raw)
        if value.values is not None:
            return tuple(value.values)
        if isinstance(value.value, Sequence) and not isinstance(
            value.value, (str, bytes, bytearray)
        ):
            return tuple(value.value)
        raise ValueError("Capability 原始值不包含列表")

    @staticmethod
    def _value_at_index(values: tuple[Any, ...], index: int, field_name: str) -> Any:
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError(f"{field_name} 必须是整数")
        if index < 0 or index >= len(values):
            raise ValueError(f"{field_name} 超出枚举值范围")
        return values[index]

    @staticmethod
    def _build(
        *,
        capability_id: int,
        container_type: str | int,
        item_type: str | int,
        operations: CapabilityOperations | int,
        current: Any = None,
        default: Any = None,
        values: tuple[Any, ...] | None = None,
        minimum: Any = None,
        maximum: Any = None,
        step: Any = None,
        source_manufacturer: str | None = None,
        source_product_name: str | None = None,
    ) -> CapabilitySchema:
        normalized_container = normalize_container_type(container_type)
        normalized_item = normalize_item_type(item_type)
        standard_code, standard_name, description = _STANDARD_CAPABILITIES.get(
            capability_id,
            (None, None, None),
        )
        return CapabilitySchema(
            capability_id=capability_id,
            standard_name=standard_name,
            standard_code=standard_code,
            standard_description=description,
            custom=capability_id >= CAP_CUSTOMBASE,
            container_type=normalized_container,
            item_type=normalized_item,
            operations=_coerce_operations(operations),
            current=current,
            default=default,
            values=values,
            minimum=minimum,
            maximum=maximum,
            step=step,
            source_manufacturer=source_manufacturer,
            source_product_name=source_product_name,
        )


__all__ = [
    "CAP_AUTOFEED",
    "CAP_AUTOSCAN",
    "CAP_AUTHOR",
    "CAP_CAPTION",
    "CAP_CLEARPAGE",
    "CAP_CUSTOMBASE",
    "CAP_CUSTOMDSDATA",
    "CAP_DEVICEEVENT",
    "CAP_DEVICEONLINE",
    "CAP_DUPLEX",
    "CAP_DUPLEXENABLED",
    "CAP_FEEDERENABLED",
    "CAP_FEEDERLOADED",
    "CAP_SUPPORTEDCAPS",
    "CAP_SUPPORTEDCAPSEXT",
    "CapabilityCodec",
    "CapabilityMessage",
    "ICAP_AUTOBRIGHT",
    "ICAP_BITDEPTH",
    "ICAP_BRIGHTNESS",
    "ICAP_COMPRESSION",
    "ICAP_CONTRAST",
    "ICAP_IMAGEFILEFORMAT",
    "ICAP_PIXELTYPE",
    "ICAP_UNITS",
    "ICAP_XFERMECH",
    "RawCapability",
    "TWRC_CHECKSTATUS",
    "TWRC_SUCCESS",
    "TWQC_GET",
    "TWQC_GETCURRENT",
    "TWQC_GETDEFAULT",
    "TWQC_RESET",
    "TWQC_SET",
    "TWON_ARRAY",
    "TWON_ENUMERATION",
    "TWON_ONEVALUE",
    "TWON_RANGE",
    "TW_ARRAY",
    "TW_ENUMERATION",
    "TW_ONEVALUE",
    "TW_RANGE",
    "TWTY_BOOL",
    "TWTY_FIX32",
    "TWTY_FRAME",
    "TWTY_INT16",
    "TWTY_INT32",
    "TWTY_INT8",
    "TWTY_STR128",
    "TWTY_STR255",
    "TWTY_STR1024",
    "TWTY_STR32",
    "TWTY_STR64",
    "TWTY_UNI512",
    "TWTY_UINT16",
    "TWTY_UINT32",
    "TWTY_UINT8",
    "normalize_container_type",
    "normalize_item_type",
]
