"""Capability 查询与设置服务测试。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest


from app.scanner.capability_codec import (
    CAP_FEEDERENABLED,
    CAP_SUPPORTEDCAPS,
    CapabilityMessage,
    RawCapability,
    TWRC_CHECKSTATUS,
    TWQC_GET,
    TWQC_GETCURRENT,
    TWQC_GETDEFAULT,
    TWQC_SET,
)


@dataclass
class FakeSource:
    supported: list[int]
    containers: dict[tuple[int, CapabilityMessage], RawCapability]
    support_masks: dict[int, int]
    source_manufacturer: str = "KODAK"
    source_product_name: str = "KODAK Scanner: i2000"
    get_calls: list[tuple[int, CapabilityMessage]] = field(default_factory=list)
    query_support_calls: list[int] = field(default_factory=list)
    set_calls: list[tuple[int, str, object]] = field(default_factory=list)
    set_status: int = 0

    def get_capability(
        self,
        capability_id: int,
        message: CapabilityMessage,
    ) -> RawCapability:
        self.get_calls.append((capability_id, message))
        if capability_id == CAP_SUPPORTEDCAPS:
            return RawCapability(
                container_type="TW_ARRAY",
                item_type="TWTY_UINT16",
                values=tuple(self.supported),
            )
        return self.containers[(capability_id, message)]

    def query_support(self, capability_id: int) -> int:
        self.query_support_calls.append(capability_id)
        return self.support_masks[capability_id]

    def set_capability(self, capability_id: int, item_type: str, value: object) -> int:
        self.set_calls.append((capability_id, item_type, value))
        return self.set_status


def _source_for_query() -> FakeSource:
    supported = [CAP_FEEDERENABLED, 0x8001, 0x8002, 0x8003, 0x8004]
    get = CapabilityMessage.GET
    current = CapabilityMessage.GET_CURRENT
    default = CapabilityMessage.GET_DEFAULT
    containers = {
        (CAP_FEEDERENABLED, get): RawCapability(
            container_type="TW_ONEVALUE",
            item_type="TWTY_BOOL",
            value=True,
        ),
        (CAP_FEEDERENABLED, current): RawCapability(
            container_type="TW_ONEVALUE",
            item_type="TWTY_BOOL",
            value=True,
        ),
        (CAP_FEEDERENABLED, default): RawCapability(
            container_type="TW_ONEVALUE",
            item_type="TWTY_BOOL",
            value=False,
        ),
        (0x8001, get): RawCapability(
            container_type="TW_RANGE",
            item_type="TWTY_FIX32",
            minimum=100,
            maximum=600,
            step=100,
            current=300,
            default=300,
        ),
        (0x8001, current): RawCapability(
            container_type="TW_ONEVALUE",
            item_type="TWTY_FIX32",
            value=300,
        ),
        (0x8001, default): RawCapability(
            container_type="TW_ONEVALUE",
            item_type="TWTY_FIX32",
            value=300,
        ),
        (0x8002, get): RawCapability(
            container_type="TW_ENUMERATION",
            item_type="TWTY_UINT16",
            values=(0, 1, 2),
            current_index=1,
            default_index=0,
        ),
        (0x8002, current): RawCapability(
            container_type="TW_ONEVALUE",
            item_type="TWTY_UINT16",
            value=1,
        ),
        (0x8002, default): RawCapability(
            container_type="TW_ONEVALUE",
            item_type="TWTY_UINT16",
            value=0,
        ),
        (0x8003, get): RawCapability(
            container_type="TW_ARRAY",
            item_type="TWTY_UINT16",
            values=(10, 20, 30),
        ),
        (0x8003, current): RawCapability(
            container_type="TW_ARRAY",
            item_type="TWTY_UINT16",
            values=(10, 20, 30),
        ),
        (0x8003, default): RawCapability(
            container_type="TW_ARRAY",
            item_type="TWTY_UINT16",
            values=(10, 20, 30),
        ),
        (0x8004, get): RawCapability(
            container_type="TW_ONEVALUE",
            item_type="TWTY_UINT16",
            value=1,
        ),
    }
    support_masks = {
        CAP_FEEDERENABLED: TWQC_GET | TWQC_GETCURRENT | TWQC_GETDEFAULT | TWQC_SET,
        0x8001: TWQC_GET | TWQC_GETCURRENT | TWQC_GETDEFAULT | TWQC_SET,
        0x8002: TWQC_GET | TWQC_GETCURRENT | TWQC_GETDEFAULT | TWQC_SET,
        0x8003: TWQC_GET | TWQC_GETCURRENT | TWQC_GETDEFAULT,
        0x8004: TWQC_GET,
    }
    return FakeSource(supported, containers, support_masks)


def test_query_all_reads_supported_caps_first_and_returns_all_container_types():
    from app.scanner.capability_service import CapabilityService

    source = _source_for_query()
    result = CapabilityService(source).query_all()

    assert [item.capability_id for item in result] == [
        CAP_FEEDERENABLED,
        0x8001,
        0x8002,
        0x8003,
        0x8004,
    ]
    assert source.get_calls[0] == (CAP_SUPPORTEDCAPS, CapabilityMessage.GET)
    assert source.query_support_calls == [
        CAP_FEEDERENABLED,
        0x8001,
        0x8002,
        0x8003,
        0x8004,
    ]
    assert result[0].standard_name == "进纸器启用"
    assert result[0].source_manufacturer == "KODAK"
    assert result[1].container_type == "TW_RANGE"
    assert result[2].current == 1
    assert result[2].values == (0, 1, 2)
    assert result[3].container_type == "TW_ARRAY"
    assert result[4].operations.set is False


def test_query_error_is_retained_for_one_capability_without_aborting_list():
    from app.scanner.capability_service import CapabilityService

    source = _source_for_query()
    source.containers.pop((0x8002, CapabilityMessage.GET))

    result = CapabilityService(source).query_all()

    failed = next(item for item in result if item.capability_id == 0x8002)
    assert failed.query_error == "TWAIN_CAPABILITY_QUERY_FAILED"
    assert "0x8002" in (failed.query_error_message or "")
    assert [item.capability_id for item in result][-1] == 0x8004


def test_set_rejects_capability_not_in_current_snapshot():
    from app.scanner.capability_service import CapabilitySetError, CapabilityService

    service = CapabilityService(_source_for_query())
    service.query_all()

    with pytest.raises(CapabilitySetError, match="本次查询结果"):
        service.set_capability(0x8999, 1)


def test_set_allows_driver_operation_mask_without_set_bit():
    from app.scanner.capability_service import CapabilityService

    source = _source_for_query()
    service = CapabilityService(source)
    service.query_all()

    result = service.set_capability(0x8004, 2)

    assert result.requested == 2
    assert result.actual is None
    assert result.status_code == 0
    assert result.readback_unavailable is True
    assert result.to_payload()["readbackUnavailable"] is True
    assert source.set_calls == [(0x8004, "TWTY_UINT16", 2)]


def test_set_rejects_enumeration_value_outside_driver_values():
    from app.scanner.capability_service import CapabilitySetError, CapabilityService

    service = CapabilityService(_source_for_query())
    service.query_all()

    with pytest.raises(CapabilitySetError, match="枚举值"):
        service.set_capability(0x8002, 9)


def test_set_rejects_range_value_outside_step():
    from app.scanner.capability_service import CapabilitySetError, CapabilityService

    service = CapabilityService(_source_for_query())
    service.query_all()

    with pytest.raises(CapabilitySetError, match="步长"):
        service.set_capability(0x8001, 350)


def test_set_reads_back_actual_value_after_checkstatus():
    from app.scanner.capability_service import CapabilityService

    source = _source_for_query()
    source.set_status = TWRC_CHECKSTATUS
    source.containers[(0x8001, CapabilityMessage.GET_CURRENT)] = RawCapability(
        container_type="TW_ONEVALUE",
        item_type="TWTY_FIX32",
        value=400,
    )
    service = CapabilityService(source)
    service.query_all()

    result = service.set_capability(0x8001, 300)

    assert result.requested == 300
    assert result.actual == 400
    assert result.check_status is True
    assert source.set_calls == [(0x8001, "TWTY_FIX32", 300)]
    assert source.get_calls[-1] == (0x8001, CapabilityMessage.GET_CURRENT)


def test_set_returns_actual_value_when_driver_adjusts_request():
    from app.scanner.capability_service import CapabilityService

    source = _source_for_query()
    source.containers[(0x8001, CapabilityMessage.GET_CURRENT)] = RawCapability(
        container_type="TW_ONEVALUE",
        item_type="TWTY_FIX32",
        value=300,
    )
    service = CapabilityService(source)
    service.query_all()

    result = service.set_capability(0x8001, 300)

    assert result.requested == 300
    assert result.actual == 300
    assert result.check_status is False


def test_custom_ds_data_is_set_as_one_binary_block():
    from app.scanner.capability_service import CapabilitySetError, CapabilityService

    current = CapabilityMessage.GET_CURRENT
    source = FakeSource(
        supported=[0x1015],
        containers={
            (0x1015, CapabilityMessage.GET): RawCapability(
                container_type="TW_ONEVALUE",
                item_type="TWTY_UINT8",
                value=b"old",
            ),
            (0x1015, current): RawCapability(
                container_type="TW_ONEVALUE",
                item_type="TWTY_UINT8",
                value=b"new",
            ),
        },
        support_masks={0x1015: TWQC_GET | TWQC_GETCURRENT | TWQC_SET},
    )
    service = CapabilityService(source)
    service.query_all()

    result = service.set_capability(0x1015, b"new")

    assert result.actual == b"new"
    assert source.set_calls == [(0x1015, "TWTY_UINT8", b"new")]
    with pytest.raises(CapabilitySetError, match="整块"):
        service.set_capability(0x1015, "new")
