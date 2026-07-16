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
