"""仅验证失败诊断、错误回传与日志接线，不连接设备。"""

from types import SimpleNamespace

import pytest
import twain


def test_capability_failure_keeps_id_stage_and_twain_condition():
    from app.scanner.capability_service import CapabilitySetError
    from app.scanner.twain_backend import TwainBackend, TwainBackendError

    class FailedSettings:
        def set_capability(self, capability_id, value):
            try:
                raise twain.exceptions.SequenceError()
            except twain.exceptions.SequenceError as cause:
                raise CapabilitySetError("设置失败") from cause

    backend = TwainBackend()
    backend._capability_service = FailedSettings()
    with pytest.raises(TwainBackendError) as failure:
        backend._apply_fixed_settings({}, transfer_count=-1)
    assert failure.value.diagnostics == {
        "stage": "set_capability",
        "capabilityId": 1,
        "exceptionType": "SequenceError",
        "twainConditionCode": twain.constants.TWCC_SEQERROR,
    }
    assert "0x0001" in str(failure.value)


def test_gateway_keeps_safe_failure_details_and_discards_source_cache():
    from app.api.dependencies import WorkerGateway
    from app.worker.messages import EventMessage

    gateway = WorkerGateway(
        supervisor=object(),
        task_service=object(),
        page_service=object(),
        event_hub=object(),
    )
    gateway._open_device_id = "device-1"
    captured = []
    gateway._fail_task_from_worker = lambda *args: captured.append(args)
    gateway._handle_scan_failed(
        EventMessage(
            event_type="scan_failed",
            command_id="cmd-1",
            task_id="task-1",
            payload={
                "errorCode": "TWAIN_CAPABILITY_SET_FAILED",
                "errorMessage": "不应直接透传的内部文本",
                "diagnostics": {
                    "stage": "set_capability",
                    "capabilityId": 1,
                    "twainConditionCode": 11,
                },
            },
        )
    )
    assert "0x0001" in captured[0][2]
    assert "11" in captured[0][2]
    assert gateway._open_device_id is None


def test_api_diagnostics_only_contains_whitelisted_safe_fields():
    from app.api.dependencies import WorkerGatewayError
    from app.errors import api_error_from_domain

    error = WorkerGatewayError(
        "TWAIN_CAPABILITY_SET_FAILED",
        "内部错误",
        diagnostics={
            "stage": "set_capability",
            "capabilityId": 1,
            "exceptionType": "SequenceError",
            "twainConditionCode": 11,
            "path": "D:/private/scan.jpg",
            "stack": "内部堆栈",
        },
    )
    result = api_error_from_domain(error)
    assert result.data["diagnostics"] == {
        "stage": "set_capability",
        "capabilityId": 1,
        "exceptionType": "SequenceError",
        "twainConditionCode": 11,
    }
    assert "private" not in str(result.data)


def test_tray_main_configures_logging_before_starting(monkeypatch):
    import app.tray.application as module

    calls = []
    configuration = object()
    monkeypatch.setattr(module, "Settings", lambda: configuration)
    monkeypatch.setattr(
        module, "configure_logging", lambda settings: calls.append(("logs", settings))
    )
    monkeypatch.setattr(
        module,
        "TrayApplication",
        lambda **kwargs: SimpleNamespace(
            run=lambda: calls.append(("run", kwargs["settings"])) or 0
        ),
    )
    assert module.main() == 0
    assert calls == [("logs", configuration), ("run", configuration)]


def test_worker_entry_configures_logging_before_runtime(monkeypatch):
    import app.worker.process as module

    calls = []
    monkeypatch.setattr(
        module, "configure_logging", lambda settings: calls.append("logs")
    )
    monkeypatch.setattr(
        module,
        "WorkerProcess",
        lambda *args, **kwargs: SimpleNamespace(run=lambda: calls.append("runtime")),
    )
    module.worker_process_entry(object(), object(), "worker-1", 0.5)
    assert calls == ["logs", "runtime"]


def test_resolve_failure_keeps_diagnostics_and_closes_source():
    from queue import Queue
    from app.scanner.twain_backend import TwainBackendError
    from app.worker.messages import CommandMessage, decode_message
    from app.worker.process import _handle_command

    calls = []

    class FailedRuntime:
        def resolve_capabilities(self, settings):
            raise TwainBackendError(
                "TWAIN_CAPABILITY_SET_FAILED",
                "参数设置失败",
                diagnostics={"stage": "set_capability", "capabilityId": 1},
            )

        def close_source(self):
            calls.append("closed")

    queue = Queue()
    _handle_command(
        CommandMessage(
            command_id="cmd-1",
            message_type="resolve_capabilities",
            payload={"settings": {"resolution": 300}},
        ),
        queue,
        active_scan=None,
        runtime=FailedRuntime(),
    )
    event = decode_message(queue.get_nowait())
    assert event.payload["diagnostics"]["capabilityId"] == 1
    assert calls == ["closed"]


def test_file_setup_failure_keeps_actual_twain_return_code(tmp_path):
    from app.scanner.twain_backend import PytwainFileTransferSource, TwainBackendError

    class Source:
        @property
        def file_xfer_params(self):
            return None

        @file_xfer_params.setter
        def file_xfer_params(self, value):
            raise RuntimeError("Unexpected result: 2")

    adapter = PytwainFileTransferSource(Source(), object(), twain)
    with pytest.raises(TwainBackendError) as failure:
        adapter.transfer_file(tmp_path / "temporary.part", file_format=4)
    assert failure.value.diagnostics["stage"] == "setup_file_transfer"
    assert failure.value.diagnostics["twainReturnCode"] == 2


def test_unknown_end_transfer_return_is_reported_with_pending_and_status():
    import ctypes
    from app.scanner.twain_backend import PytwainFileTransferSource, TwainBackendError

    class Source:
        def _call(self, dg, dat, msg, buf, expected_returns=()):
            if dat == twain.constants.DAT_PENDINGXFERS:
                value = ctypes.cast(
                    buf, ctypes.POINTER(twain.structs.TW_PENDINGXFERS)
                ).contents
                value.Count = 1
                return 0x8001
            if dat == twain.constants.DAT_STATUS:
                ctypes.cast(
                    buf, ctypes.POINTER(twain.structs.TW_STATUS)
                ).contents.ConditionCode = 0
                return 0
            raise AssertionError("非预期调用")

    adapter = PytwainFileTransferSource(Source(), object(), twain)
    with pytest.raises(TwainBackendError) as failure:
        adapter._end_file_transfer()
    assert failure.value.diagnostics["twainReturnCode"] == 0x8001
    assert failure.value.diagnostics["pendingCount"] == 1
    assert failure.value.diagnostics["twainConditionCode"] == 0


@pytest.mark.parametrize(
    "manufacturer,condition,count",
    [
        ("Eastman Kodak", 0, 1),
        ("Other", 0, 1),
        ("Eastman Kodak", 11, 1),
        ("Eastman Kodak", 0, 0),
    ],
)
def test_nonstandard_end_return_never_silently_succeeds(
    manufacturer, condition, count, monkeypatch
):
    import ctypes
    from app.scanner.twain_backend import PytwainFileTransferSource, TwainBackendError

    class Source:
        identity = {"Manufacturer": manufacturer, "ProductName": "KODAK Scanner: i2000"}

        def _call(self, dg, dat, msg, buf, expected_returns=()):
            if dat == twain.constants.DAT_PENDINGXFERS:
                ctypes.cast(
                    buf, ctypes.POINTER(twain.structs.TW_PENDINGXFERS)
                ).contents.Count = count
                return 0x8001
            ctypes.cast(
                buf, ctypes.POINTER(twain.structs.TW_STATUS)
            ).contents.ConditionCode = condition
            return 0

    adapter = PytwainFileTransferSource(Source(), object(), twain)
    monkeypatch.setattr(adapter, "END_TRANSFER_TIMEOUT", 0)
    with pytest.raises(TwainBackendError):
        adapter._end_file_transfer()


def test_i2000_end_transfer_waits_for_standard_success(monkeypatch):
    import ctypes
    from app.scanner.twain_backend import PytwainFileTransferSource

    class Source:
        identity = {
            "Manufacturer": "Eastman Kodak",
            "ProductName": "KODAK Scanner: i2000",
        }
        calls = 0

        def _call(self, dg, dat, msg, buf, expected_returns=()):
            if dat == twain.constants.DAT_PENDINGXFERS:
                self.calls += 1
                ctypes.cast(
                    buf, ctypes.POINTER(twain.structs.TW_PENDINGXFERS)
                ).contents.Count = 1
                return 0x8001 if self.calls == 1 else 0
            ctypes.cast(
                buf, ctypes.POINTER(twain.structs.TW_STATUS)
            ).contents.ConditionCode = 0
            return 0

    adapter = PytwainFileTransferSource(Source(), object(), twain)
    monkeypatch.setattr(adapter, "_pump_pending_events", lambda: False)
    assert adapter._end_file_transfer() == 1
    assert adapter._source.calls == 2
