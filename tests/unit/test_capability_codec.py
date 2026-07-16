"""TWAIN Capability 容器编解码测试。"""

from __future__ import annotations

import base64

import pytest


@pytest.fixture
def codec():
    from app.scanner.capability_codec import CapabilityCodec

    return CapabilityCodec()


def test_private_range_capability_preserves_number_and_item_type(codec):
    result = codec.decode_range(
        capability_id=0x8001,
        item_type="TWTY_FIX32",
        minimum=100,
        maximum=600,
        step=100,
        current=300,
        default=300,
    )

    assert result.capability_id == 0x8001
    assert result.standard_name is None
    assert result.custom is True
    assert result.capability_hex == "0x8001"
    assert result.container_type == "TW_RANGE"
    assert result.item_type == "TWTY_FIX32"
    assert result.current == 300
    assert result.minimum == 100
    assert result.maximum == 600
    assert result.step == 100


def test_one_value_maps_standard_capability_and_operations(codec):
    from app.scanner.capability_codec import CAP_FEEDERENABLED, TWQC_GET, TWQC_GETCURRENT, TWQC_SET

    result = codec.decode_one_value(
        capability_id=CAP_FEEDERENABLED,
        item_type="TWTY_BOOL",
        current=True,
        default=False,
        operations=TWQC_GET | TWQC_GETCURRENT | TWQC_SET,
    )

    assert result.standard_name == "进纸器启用"
    assert result.custom is False
    assert result.item_type == "TWTY_BOOL"
    assert result.current is True
    assert result.default is False
    assert result.operations.get is True
    assert result.operations.get_current is True
    assert result.operations.set is True
    assert result.operations.get_default is False

    payload = result.to_payload()
    assert payload["capabilityName"] == "进纸器启用"
    assert payload["currentValue"] is True
    assert payload["operations"] == {
        "get": True,
        "set": True,
        "getCurrent": True,
        "getDefault": False,
        "reset": False,
    }


def test_enumeration_preserves_allowed_values_and_indexes(codec):
    from app.scanner.capability_codec import TW_ENUMERATION

    result = codec.decode_enumeration(
        capability_id=0x8002,
        item_type="TWTY_UINT16",
        values=[0, 1, 2],
        current_index=2,
        default_index=1,
    )

    assert result.container_type == TW_ENUMERATION
    assert result.item_type == "TWTY_UINT16"
    assert result.values == (0, 1, 2)
    assert result.current == 2
    assert result.default == 1


def test_array_preserves_values_without_treating_them_as_strings(codec):
    result = codec.decode_array(
        capability_id=0x8003,
        item_type="TWTY_FRAME",
        values=[(0.0, 0.0, 8.5, 11.0)],
    )

    assert result.container_type == "TW_ARRAY"
    assert result.item_type == "TWTY_FRAME"
    assert result.values == ((0.0, 0.0, 8.5, 11.0),)
    assert result.values[0][2] == 8.5


def test_numeric_twain_container_and_item_types_are_normalized(codec):
    from app.scanner.capability_codec import TWON_ONEVALUE, TWTY_UINT16

    result = codec.decode_one_value(
        capability_id=0x8004,
        item_type=TWTY_UINT16,
        current=300,
        container_type=TWON_ONEVALUE,
    )

    assert result.container_type == "TW_ONEVALUE"
    assert result.item_type == "TWTY_UINT16"
    assert result.current == 300


def test_numeric_item_type_values_match_installed_pytwain_constants(codec):
    import twain

    result = codec.decode_one_value(
        capability_id=0x8006,
        item_type=twain.constants.TWTY_UINT16,
        current=300,
    )

    assert result.item_type == "TWTY_UINT16"


def test_custom_binary_value_is_base64_encoded_only_at_payload_boundary(codec):
    result = codec.decode_one_value(
        capability_id=0x1015,
        item_type="TWTY_UINT8",
        current=b"\x00\xffcustom",
    )

    assert result.current == b"\x00\xffcustom"
    assert result.to_payload()["currentValue"] == base64.b64encode(
        b"\x00\xffcustom"
    ).decode("ascii")
    assert result.to_payload()["valueEncoding"] == "base64"


def test_invalid_enumeration_index_is_rejected(codec):
    with pytest.raises(ValueError, match="current_index"):
        codec.decode_enumeration(
            capability_id=0x8005,
            item_type="TWTY_UINT16",
            values=[1, 2],
            current_index=2,
            default_index=0,
        )
