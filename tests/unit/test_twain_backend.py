"""TWAIN DSM 和扫描仪枚举测试。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class FakeDsm:
    """不加载真实 DSM 的测试替身。"""

    identities: list[object]
    enumerate_calls: int = 0
    open_source_calls: int = 0
    close_calls: int = 0

    def enumerate_sources(self) -> list[object]:
        self.enumerate_calls += 1
        return list(self.identities)

    def open_source(self, product_name: str) -> None:
        self.open_source_calls += 1

    def close(self) -> None:
        self.close_calls += 1


@pytest.fixture
def fake_identity():
    from app.scanner.twain_backend import TwainSourceIdentity

    return TwainSourceIdentity(
        source_id=17,
        manufacturer="KODAK",
        product_family="Document Imaging",
        product_name="KODAK Scanner: i2000",
        protocol_major=1,
        protocol_minor=0,
    )


def test_enumerate_devices_returns_stable_ids(fake_identity):
    from app.scanner.twain_backend import TwainBackend

    fake_dsm = FakeDsm([fake_identity])
    backend = TwainBackend(dsm_factory=lambda: fake_dsm)

    first = backend.enumerate_devices()
    second = backend.enumerate_devices()

    assert len(first) == 1
    assert first == second
    assert first[0].device_id
    assert first[0].manufacturer == "KODAK"
    assert first[0].product_family == "Document Imaging"
    assert first[0].product_name == "KODAK Scanner: i2000"
    assert first[0].protocol_major == 1
    assert first[0].protocol_minor == 0
    assert first[0].architecture == "x64"
    assert fake_dsm.open_source_calls == 0
    assert fake_dsm.enumerate_calls == 2


def test_device_id_changes_for_different_source_identity(fake_identity):
    from app.scanner.twain_backend import TwainBackend, TwainSourceIdentity

    another_identity = TwainSourceIdentity(
        source_id=18,
        manufacturer=fake_identity.manufacturer,
        product_family=fake_identity.product_family,
        product_name=fake_identity.product_name,
        protocol_major=fake_identity.protocol_major,
        protocol_minor=fake_identity.protocol_minor,
    )

    first = TwainBackend(dsm_factory=lambda: FakeDsm([fake_identity])).enumerate_devices()
    second = TwainBackend(
        dsm_factory=lambda: FakeDsm([another_identity])
    ).enumerate_devices()

    assert first[0].device_id != second[0].device_id


def test_enumerate_devices_returns_empty_when_no_source_exists():
    from app.scanner.twain_backend import TwainBackend

    fake_dsm = FakeDsm([])

    devices = TwainBackend(dsm_factory=lambda: fake_dsm).enumerate_devices()

    assert devices == []
    assert fake_dsm.open_source_calls == 0


def test_missing_dsm_returns_twain_dsm_not_found():
    from app.scanner.twain_backend import TwainBackend, TwainBackendError

    def missing_dsm():
        raise OSError("TWAINDSM.DLL not found")

    with pytest.raises(TwainBackendError) as error:
        TwainBackend(dsm_factory=missing_dsm).enumerate_devices()

    assert error.value.error_code == "TWAIN_DSM_NOT_FOUND"


def test_close_releases_dsm():
    from app.scanner.twain_backend import TwainBackend

    fake_dsm = FakeDsm([])
    backend = TwainBackend(dsm_factory=lambda: fake_dsm)
    backend.enumerate_devices()

    backend.close()

    assert fake_dsm.close_calls == 1


def test_file_transfer_source_prepare_is_idempotent_after_configuration():
    import twain

    from app.scanner.twain_backend import PytwainFileTransferSource

    class FakeSource:
        def __init__(self):
            self.calls = []

        def set_capability(self, capability_id, item_type, value):
            self.calls.append((capability_id, item_type, value))

    source = FakeSource()
    adapter = PytwainFileTransferSource(source, object(), twain)

    adapter.prepare_file_transfer()
    adapter.prepare_file_transfer()

    assert source.calls == []


def test_backend_scan_once_runs_hidden_file_transfer_lifecycle(tmp_path):
    from app.scanner.file_transfer import TransferStatus
    from app.scanner.twain_backend import (
        TwainBackend,
        TwainSourceHandle,
        TwainSourceIdentity,
    )

    class FakeCapabilitySource:
        def close(self):
            return None

    class FakeTransferSource:
        transfer_ready_message = 257
        close_request_message = 258

        def __init__(self):
            self.calls = []

        def prepare_file_transfer(self):
            self.calls.append("prepare")

        def start_acquisition(self):
            self.calls.append("start")

        def wait_for_event(self):
            self.calls.append("wait")
            return self.transfer_ready_message

        def read_extended_image_info(self):
            self.calls.append("extended")

        def read_image_info(self):
            self.calls.append("image")

        def transfer_file(self, path, *, file_format):
            self.calls.append(("transfer", path, file_format))
            path.write_bytes(b"\xff\xd8fake\xff\xd9")
            return TransferStatus(return_code=6, pending_count=0)

        def abort_transfer(self):
            self.calls.append("abort")

        def finish_acquisition(self):
            self.calls.append("finish")

    transfer = FakeTransferSource()
    handle = TwainSourceHandle(
        identity=TwainSourceIdentity(
            source_id=1,
            manufacturer="KODAK",
            product_family="Document Imaging",
            product_name="KODAK Scanner: i2000",
            protocol_major=2,
            protocol_minor=4,
        ),
        capability_source=FakeCapabilitySource(),
        transfer_source=transfer,
    )

    class FakeDsm:
        def open_source(self, product_name, *, show_ui=False):
            assert product_name == "KODAK Scanner: i2000"
            assert show_ui is False
            return handle

        def close_source(self):
            return None

        def close(self):
            return None

    backend = TwainBackend(dsm_factory=lambda: FakeDsm())
    backend.open_source("KODAK Scanner: i2000", show_ui=False)

    result = backend.scan_once(tmp_path, page_id="page-1")

    assert result.original_path == tmp_path / "page-1.jpg"
    assert result.original_path.read_bytes() == b"\xff\xd8fake\xff\xd9"
    assert transfer.calls[:6] == [
        "prepare",
        "start",
        "wait",
        "extended",
        "image",
        "prepare",
    ]
    assert transfer.calls[-1] == "finish"


def test_backend_applies_fixed_standard_setting_without_operation_bit(tmp_path):
    from app.scanner.capability_codec import CapabilityMessage, RawCapability, TWQC_GET
    from app.scanner.file_transfer import TransferStatus
    from app.scanner.twain_backend import (
        TwainBackend,
        TwainSourceHandle,
        TwainSourceIdentity,
    )

    class FakeCapabilitySource:
        source_manufacturer = "KODAK"
        source_product_name = "KODAK Scanner: i2000"

        def __init__(self):
            self.set_calls = []

        def get_capability(self, capability_id, message):
            if capability_id == 0x1005:
                return RawCapability(
                    container_type="TW_ARRAY",
                    item_type="TWTY_UINT16",
                    values=(0x0001, 0x100B, 0x1010, 0x0103, 0x110C, 0x1118),
                )
            assert message == CapabilityMessage.GET
            if capability_id in {0x0001}:
                return RawCapability(
                    container_type="TW_ONEVALUE",
                    item_type="TWTY_INT16",
                    value=-1,
                )
            if capability_id in {0x100B, 0x1010}:
                return RawCapability(
                    container_type="TW_ONEVALUE",
                    item_type="TWTY_BOOL",
                    value=False,
                )
            if capability_id in {0x0103, 0x110C}:
                return RawCapability(
                    container_type="TW_ENUMERATION",
                    item_type="TWTY_UINT16",
                    values=(0, 1, 4),
                    current_index=0,
                    default_index=0,
                )
            return RawCapability(
                container_type="TW_RANGE",
                item_type="TWTY_FIX32",
                minimum=100,
                maximum=600,
                step=100,
                current=300,
                default=300,
            )

        def query_support(self, capability_id):
            return TWQC_GET

        def set_capability(self, capability_id, item_type, value):
            self.set_calls.append((capability_id, item_type, value))
            return 0

        def close(self):
            return None

    class FakeTransferSource:
        transfer_ready_message = 257
        close_request_message = 258

        def prepare_file_transfer(self):
            return None

        def start_acquisition(self):
            return None

        def wait_for_event(self):
            return self.transfer_ready_message

        def read_extended_image_info(self):
            return None

        def read_image_info(self):
            return None

        def transfer_file(self, path, *, file_format):
            path.write_bytes(b"\xff\xd8fixed-config\xff\xd9")
            return TransferStatus(return_code=6, pending_count=0)

        def abort_transfer(self):
            return None

        def finish_acquisition(self):
            return None

    capability = FakeCapabilitySource()
    handle = TwainSourceHandle(
        identity=TwainSourceIdentity(
            source_id=1,
            manufacturer="KODAK",
            product_family="Document Imaging",
            product_name="KODAK Scanner: i2000",
            protocol_major=2,
            protocol_minor=4,
        ),
        capability_source=capability,
        transfer_source=FakeTransferSource(),
    )

    class FakeDsm:
        def open_source(self, product_name, *, show_ui=False):
            assert show_ui is False
            return handle

        def close_source(self):
            return None

        def close(self):
            return None

    backend = TwainBackend(dsm_factory=lambda: FakeDsm())
    backend.open_source("KODAK Scanner: i2000", show_ui=False)
    result = backend.scan_once(
        tmp_path,
        page_id="page-1",
        settings={"xResolution": 300},
    )

    assert capability.set_calls == [
        (0x0001, "TWTY_INT16", 1),
        (0x1010, "TWTY_BOOL", True),
        (0x100B, "TWTY_BOOL", False),
        (0x0103, "TWTY_UINT16", 1),
        (0x110C, "TWTY_UINT16", 4),
        (0x1118, "TWTY_FIX32", 300),
    ]
    assert [item["capabilityId"] for item in result.configuration_results] == [
        0x0001,
        0x1010,
        0x100B,
        0x0103,
        0x110C,
        0x1118,
    ]
    assert all(
        item["readbackUnavailable"] is True
        for item in result.configuration_results
    )


def test_fixed_business_fields_map_to_standard_capabilities():
    from app.scanner.twain_backend import TwainBackend

    assert TwainBackend._fixed_capability_requests(
        {
            "feedMode": "adf_duplex",
            "resolution": 300,
            "jpegQuality": 85,
        }
    ) == [
        (0x1002, True),
        (0x1013, True),
        (0x1007, True),
        (0x1118, 300),
        (0x1119, 300),
        (0x1143, 85),
    ]
