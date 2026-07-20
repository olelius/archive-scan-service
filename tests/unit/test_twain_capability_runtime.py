"""真实 Capability 运行时边界的单元测试。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from queue import Queue


@dataclass
class FakeCapabilitySource:
    source_manufacturer: str = "KODAK"
    source_product_name: str = "KODAK Scanner: i2000"
    closed: bool = False

    def query_support(self, capability_id: int) -> int:
        from app.scanner.capability_codec import TWQC_GET, TWQC_GETCURRENT

        return TWQC_GET | TWQC_GETCURRENT

    def get_capability(self, capability_id, message):
        from app.scanner.capability_codec import (
            CAP_SUPPORTEDCAPS,
            CapabilityMessage,
            RawCapability,
            TW_ARRAY,
            TW_ONEVALUE,
            TWTY_BOOL,
            TWTY_UINT16,
        )

        if capability_id == CAP_SUPPORTEDCAPS:
            return RawCapability(
                container_type=TW_ARRAY,
                item_type=TWTY_UINT16,
                values=(0x1002,),
            )
        if message == CapabilityMessage.GET:
            return RawCapability(
                container_type=TW_ONEVALUE,
                item_type=TWTY_BOOL,
                value=True,
            )
        return RawCapability(
            container_type=TW_ONEVALUE,
            item_type=TWTY_BOOL,
            value=True,
        )

    def close(self) -> None:
        self.closed = True


def _source_handle():
    from app.scanner.twain_backend import TwainSourceHandle, TwainSourceIdentity

    return TwainSourceHandle(
        identity=TwainSourceIdentity(
            source_id=1,
            manufacturer="KODAK",
            product_family="Document Imaging",
            product_name="KODAK Scanner: i2000",
            protocol_major=1,
            protocol_minor=0,
        ),
        capability_source=FakeCapabilitySource(),
        dsm_path=Path(r"C:\Windows\System32\TWAINDSM.dll"),
        source_identity={"Id": 1, "ProductName": "KODAK Scanner: i2000"},
    )


def test_backend_opens_queries_and_closes_source_without_ui():
    from app.scanner.twain_backend import TwainBackend

    handle = _source_handle()

    class FakeDsm:
        def open_source(self, product_name: str, *, show_ui: bool = False):
            assert product_name == "KODAK Scanner: i2000"
            assert show_ui is False
            return handle

        def close_source(self):
            handle.close()

        def close(self):
            return None

    backend = TwainBackend(dsm_factory=lambda: FakeDsm())
    source = backend.open_source("KODAK Scanner: i2000", show_ui=False)
    capabilities = backend.query_capabilities()

    assert source["productName"] == "KODAK Scanner: i2000"
    assert source["showUi"] is False
    assert source["dsmPath"].endswith("TWAINDSM.dll")
    assert capabilities[0].capability_id == 0x1002
    assert capabilities[0].current is True

    backend.close_source()
    assert handle.capability_source.closed is True


def test_pytwain_range_values_keep_signed_int32_semantics():
    import twain

    from app.scanner.twain_backend import PytwainCapabilitySource

    adapter = object.__new__(PytwainCapabilitySource)
    adapter._twain = twain
    adapter._source = None

    assert adapter._range_value(twain.constants.TWTY_INT32, 0xFFFFFF9C) == -100


def test_worker_capability_commands_are_read_only_and_serialized():
    from app.models.schemas import CapabilityOperations, CapabilitySchema
    from app.worker.messages import CommandMessage, decode_message
    from app.worker.process import _handle_command

    class FakeRuntime:
        def __init__(self):
            self.calls = []

        def open_source(self, product_name: str, *, show_ui: bool = False):
            self.calls.append(("open", product_name, show_ui))
            return {"productName": product_name, "showUi": show_ui}

        def query_capabilities(self):
            self.calls.append(("query",))
            return [
                CapabilitySchema(
                    capability_id=0x1002,
                    standard_name="进纸器启用",
                    standard_code="CAP_FEEDERENABLED",
                    standard_description="是否启用自动进纸器",
                    custom=False,
                    container_type="TW_ONEVALUE",
                    item_type="TWTY_BOOL",
                    operations=CapabilityOperations(get=True),
                    current=True,
                )
            ]

        def close_source(self):
            self.calls.append(("close",))

    runtime = FakeRuntime()
    events = Queue()
    active_scan = None

    for command in (
        CommandMessage(
            command_id="open-1",
            message_type="open_source",
            payload={"productName": "KODAK Scanner: i2000", "showUi": False},
        ),
        CommandMessage(command_id="query-1", message_type="query_capabilities"),
        CommandMessage(command_id="close-1", message_type="close_source"),
    ):
        active_scan, should_exit = _handle_command(
            command,
            events,
            active_scan=active_scan,
            runtime=runtime,
        )
        assert should_exit is False

    decoded = [decode_message(events.get_nowait()) for _ in range(4)]
    capability_event = next(
        event for event in decoded if event.event_type == "capabilities_queried"
    )

    assert runtime.calls == [
        ("open", "KODAK Scanner: i2000", False),
        ("query",),
        ("close",),
    ]
    assert capability_event.payload["count"] == 1
    assert capability_event.payload["capabilities"][0]["itemType"] == "TWTY_BOOL"


def test_worker_rejects_ui_enabled_capability_probe():
    from app.worker.messages import CommandMessage, decode_message
    from app.worker.process import _handle_command

    class FakeRuntime:
        def open_source(self, product_name: str, *, show_ui: bool = False):
            raise AssertionError("禁止 UI 时不应打开 Data Source")

    events = Queue()
    active_scan, should_exit = _handle_command(
        CommandMessage(
            command_id="open-ui-1",
            message_type="open_source",
            payload={"productName": "KODAK Scanner: i2000", "showUi": True},
        ),
        events,
        active_scan=None,
        runtime=FakeRuntime(),
    )

    failed = decode_message(events.get_nowait())
    assert active_scan is None
    assert should_exit is False
    assert failed.event_type == "command_failed"
    assert failed.payload["errorCode"] == "TWAIN_UI_FORBIDDEN"
