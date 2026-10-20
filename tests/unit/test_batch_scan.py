"""ADF 连续传输、停止和已完成页面保留的针对性测试。"""

from pathlib import Path
from queue import Queue

import pytest
import twain

from app.scanner.file_transfer import TransferStatus
from app.scanner.twain_backend import TwainBackend, TwainBackendError


class BatchSource:
    transfer_ready_message = 257
    close_request_message = 258

    def __init__(self, pending_counts):
        self.pending_counts = iter(pending_counts)
        self.calls = []
        self.error = None

    def prepare_file_transfer(self):
        pass

    def start_acquisition(self):
        self.calls.append("start")

    def wait_for_event(self, *, should_stop=None):
        if should_stop and should_stop():
            from app.scanner.twain_backend import TwainScanStopped

            raise TwainScanStopped()
        return self.transfer_ready_message

    def read_extended_image_info(self):
        pass

    def read_image_info(self):
        pass

    def transfer_file(self, path, *, file_format):
        if self.error:
            raise self.error
        path.write_bytes(b"\xff\xd8test\xff\xd9")
        return TransferStatus(6, next(self.pending_counts))

    def abort_transfer(self):
        self.calls.append("reset")

    def finish_acquisition(self):
        self.calls.append("finish")


def batch_backend(monkeypatch, source):
    from app.scanner.twain_backend import TwainSourceHandle, TwainSourceIdentity

    backend = TwainBackend()
    backend._source_handle = TwainSourceHandle(
        identity=TwainSourceIdentity(
            1, "Eastman Kodak", "Imaging", "KODAK Scanner: i2000", 2, 4
        ),
        capability_source=object(),
        transfer_source=source,
    )
    monkeypatch.setattr(backend, "query_capabilities", lambda: [])
    monkeypatch.setattr(backend, "_apply_fixed_settings", lambda settings, **kwargs: ())
    return backend


def test_batch_transfers_every_pending_face_and_uses_unique_files(
    tmp_path, monkeypatch
):
    source = BatchSource([65535, 1, 0])
    backend = batch_backend(monkeypatch, source)

    pages = list(
        backend.scan_pages(
            tmp_path, page_id="page-000001", settings={"feedMode": "adf_duplex"}
        )
    )

    assert len(pages) == 3
    assert len({page.original_path for page in pages}) == 3
    assert pages[0].original_path.name == "page-000001.jpg"
    assert all(
        page.original_path.read_bytes() == b"\xff\xd8test\xff\xd9" for page in pages
    )
    assert source.calls == ["start", "finish"]
    assert not list(tmp_path.glob("*.part"))


def test_flatbed_remains_single_page_and_resets_unexpected_pending(
    tmp_path, monkeypatch
):
    source = BatchSource([1, 0])
    backend = batch_backend(monkeypatch, source)

    assert (
        len(list(backend.scan_pages(tmp_path, settings={"feedMode": "flatbed"}))) == 1
    )
    assert source.calls == ["start", "reset", "finish"]


def test_stop_between_pages_keeps_completed_file(tmp_path, monkeypatch):
    from app.scanner.twain_backend import TwainScanStopped

    source = BatchSource([65535, 0])
    backend = batch_backend(monkeypatch, source)
    stop = False
    pages = backend.scan_pages(
        tmp_path, settings={"feedMode": "adf_simplex"}, should_stop=lambda: stop
    )
    first = next(pages)
    stop = True

    with pytest.raises(TwainScanStopped):
        next(pages)
    assert first.original_path.is_file()
    assert source.calls == ["start", "reset", "finish"]


@pytest.mark.parametrize(
    "error,expected",
    [
        (twain.exceptions.PaperJam(), "PAPER_JAM"),
        (twain.exceptions.CheckDeviceOnlineError(), "SCANNER_OFFLINE"),
    ],
)
def test_failure_keeps_previous_pages_and_cleans_partial(
    tmp_path, monkeypatch, error, expected
):
    source = BatchSource([1])
    backend = batch_backend(monkeypatch, source)
    pages = backend.scan_pages(tmp_path, settings={"feedMode": "adf_simplex"})
    first = next(pages)
    source.error = error

    with pytest.raises(TwainBackendError) as failure:
        next(pages)
    assert failure.value.error_code == expected
    assert first.original_path.is_file()
    assert len(list(tmp_path.glob("*.jpg"))) == 1
    assert not list(tmp_path.glob("*.part"))
    assert source.calls == ["start", "reset", "finish"]


def test_explicit_no_media_is_normal_adf_completion(tmp_path, monkeypatch):
    source = BatchSource([])
    source.error = twain.exceptions.UnknownError("ConditionCode = 29")
    backend = batch_backend(monkeypatch, source)

    assert (
        list(backend.scan_pages(tmp_path, settings={"feedMode": "adf_simplex"})) == []
    )
    assert source.calls == ["start", "reset", "finish"]


def test_explicit_transfer_count_bounds_a_diagnostic_request(tmp_path, monkeypatch):
    source = BatchSource([65535, 65535, 0])
    backend = batch_backend(monkeypatch, source)
    assert (
        len(
            list(
                backend.scan_pages(
                    tmp_path,
                    settings={
                        "feedMode": "adf_simplex",
                        "CAP_XFERCOUNT": 1,
                    },
                )
            )
        )
        == 1
    )
    assert source.calls == ["start", "reset", "finish"]


def test_i2000_pages_share_one_native_acquisition_session(tmp_path, monkeypatch):
    source = BatchSource([1, 0])
    source.single_page_sessions = True
    backend = batch_backend(monkeypatch, source)
    counts = []
    monkeypatch.setattr(
        backend,
        "_apply_fixed_settings",
        lambda settings, *, transfer_count: counts.append(transfer_count) or (),
    )
    pages = list(
        backend.scan_pages(
            tmp_path, settings={"feedMode": "adf_simplex", "CAP_XFERCOUNT": 2}
        )
    )
    assert len(pages) == 2
    assert len({page.original_path for page in pages}) == 2
    assert counts == [2]
    assert source.calls == ["start", "finish"]


def test_cleanup_failure_does_not_hide_initial_scan_error(tmp_path, monkeypatch):
    source = BatchSource([])
    backend = batch_backend(monkeypatch, source)

    def wait_failure(**kwargs):
        raise twain.exceptions.OperationError()

    def cleanup_failure():
        raise twain.exceptions.SequenceError()

    monkeypatch.setattr(source, "wait_for_event", wait_failure)
    monkeypatch.setattr(source, "finish_acquisition", cleanup_failure)
    with pytest.raises(TwainBackendError) as failure:
        list(backend.scan_pages(tmp_path, settings={"feedMode": "adf_simplex"}))
    assert failure.value.diagnostics["stage"] == "wait_event"
    assert failure.value.diagnostics["exceptionType"] == "OperationError"


def test_stop_inside_file_transfer_remains_stopped_not_failed(tmp_path, monkeypatch):
    from app.scanner.twain_backend import TwainScanStopped

    source = BatchSource([])
    source.error = TwainScanStopped()
    backend = batch_backend(monkeypatch, source)
    with pytest.raises(TwainScanStopped):
        list(backend.scan_pages(tmp_path, settings={"feedMode": "adf_simplex"}))


@pytest.mark.parametrize(
    "mode,count,kodak_count,pages",
    [("adf_simplex", -1, 0, 2), ("adf_duplex", -1, 0, 2), ("flatbed", 1, 1, 1)],
)
def test_real_setting_path_removes_both_single_page_limits(
    tmp_path, monkeypatch, mode, count, kodak_count, pages
):
    from types import SimpleNamespace

    source = BatchSource([1, 0])
    backend = batch_backend(monkeypatch, source)
    monkeypatch.setattr(
        backend,
        "_apply_fixed_settings",
        TwainBackend._apply_fixed_settings.__get__(backend),
    )

    class SettingsService:
        def __init__(self):
            self.values = {}

        def snapshot(self):
            return [SimpleNamespace(capability_id=cap) for cap in (0x8031, 0x8032)]

        def set_capability(self, cap, value):
            self.values[cap] = value
            return SimpleNamespace(
                to_payload=lambda: {"capabilityId": cap, "requested": value}
            )

    service = SettingsService()
    backend._capability_service = service

    assert len(list(backend.scan_pages(tmp_path, settings={"feedMode": mode}))) == pages
    assert service.values[0x0001] == count
    assert service.values[0x8031] == kodak_count
    assert service.values[0x8032] is True


def test_empty_feeder_completes_without_starting_acquisition(tmp_path, monkeypatch):
    from types import SimpleNamespace

    source = BatchSource([])
    backend = batch_backend(monkeypatch, source)
    backend._capability_service = SimpleNamespace(
        snapshot=lambda: [
            SimpleNamespace(
                capability_id=0x1003,
                current_value=False,
                query_error=None,
            )
        ]
    )

    assert (
        list(backend.scan_pages(tmp_path, settings={"feedMode": "adf_simplex"})) == []
    )
    assert source.calls == []


def test_event_wait_can_stop_without_any_windows_message():
    from app.scanner.twain_backend import PytwainFileTransferSource, TwainScanStopped

    adapter = PytwainFileTransferSource(object(), object(), twain)
    with pytest.raises(TwainScanStopped):
        adapter.wait_for_event(should_stop=lambda: True)


@pytest.mark.parametrize("action", [None, "stop_scan", "shutdown"])
def test_worker_streams_pages_and_handles_scan_control(tmp_path, monkeypatch, action):
    from app.worker.messages import (
        CommandMessage,
        ScanCommand,
        decode_message,
        encode_message,
    )
    from app.worker.process import TwainRuntime, _handle_command, _ScanControl

    source = BatchSource([65535, 65535, 0])
    backend = batch_backend(monkeypatch, source)
    runtime = object.__new__(TwainRuntime)
    runtime._backend = backend
    commands, events = Queue(), Queue()
    command = ScanCommand(
        command_id="scan-1",
        task_id="task-1",
        device_id="device-1",
        settings={"outputDir": str(tmp_path), "feedMode": "adf_simplex"},
    )
    control = _ScanControl(commands, events, command, heartbeat_interval=0.5)
    transfer = source.transfer_file

    def transfer_and_stop(path, *, file_format):
        status = transfer(path, file_format=file_format)
        if action:
            commands.put(
                encode_message(
                    CommandMessage(
                        command_id="control-1", message_type=action, task_id="task-1"
                    )
                )
            )
        return status

    monkeypatch.setattr(source, "transfer_file", transfer_and_stop)
    active, exiting = _handle_command(
        command, events, active_scan=None, runtime=runtime, scan_control=control
    )
    received = [decode_message(events.get_nowait()) for _ in range(events.qsize())]

    assert active is None and exiting is (action == "shutdown")
    if action:
        expected = ["scan_started", "page_file_ready", "scan_stopped"]
        if action == "shutdown":
            expected.append("command_succeeded")
        assert [event.event_type for event in received] == expected
        assert received[2].payload["pageCount"] == 1
    else:
        assert [event.event_type for event in received] == ["scan_started"] + [
            "page_file_ready"
        ] * 3 + ["scan_completed"]
        assert received[-1].payload["pageCount"] == 3
        assert received[-1].payload["pendingCount"] == 0
    assert received[1].payload["pageId"] == Path(received[1].payload["path"]).stem
