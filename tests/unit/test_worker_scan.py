"""Worker 一次扫描的事件链路测试。"""

from __future__ import annotations

from pathlib import Path
from queue import Queue


def _scan_command(output_dir: Path):
    from app.worker.messages import ScanCommand

    return ScanCommand(
        command_id="cmd-scan-1",
        task_id="task-1",
        device_id="device-1",
        settings={"outputDir": str(output_dir), "pageId": "page-1"},
    )


def test_start_scan_emits_file_ready_and_completed_events(tmp_path: Path):
    from app.scanner.file_transfer import FileTransferResult
    from app.worker.messages import decode_message
    from app.worker.process import _handle_command

    class FakeRuntime:
        def __init__(self):
            self.calls: list[tuple[str, dict]] = []

        def scan_once(self, device_id: str, settings: dict):
            self.calls.append((device_id, settings))
            return FileTransferResult(
                original_path=tmp_path / "page-1.jpg",
                size=128,
                transfer_return_code=6,
                pending_count=0,
                configuration_results=(
                    {"capabilityId": 0x1118, "readbackUnavailable": True},
                ),
            )

    command = _scan_command(tmp_path)
    runtime = FakeRuntime()
    event_queue = Queue()

    active_scan, should_exit = _handle_command(
        command,
        event_queue,
        active_scan=None,
        runtime=runtime,
    )

    events = [decode_message(event_queue.get_nowait()) for _ in range(3)]

    assert active_scan is None
    assert should_exit is False
    assert runtime.calls == [("device-1", command.settings)]
    assert [event.event_type for event in events] == [
        "scan_started",
        "page_file_ready",
        "scan_completed",
    ]
    assert events[1].payload["path"] == str(tmp_path / "page-1.jpg")
    assert events[1].payload["transferReturnCode"] == 6
    assert events[1].payload["pendingCount"] == 0
    assert events[1].payload["configurationResults"][0]["readbackUnavailable"] is True
    assert events[2].payload["pageCount"] == 1


def test_start_scan_emits_scan_failed_when_runtime_fails(tmp_path: Path):
    from app.scanner.twain_backend import TwainBackendError
    from app.worker.messages import decode_message
    from app.worker.process import _handle_command

    class FakeRuntime:
        def scan_once(self, device_id: str, settings: dict):
            raise TwainBackendError("SCANNER_OFFLINE", "设备已断开")

    event_queue = Queue()

    active_scan, should_exit = _handle_command(
        _scan_command(tmp_path),
        event_queue,
        active_scan=None,
        runtime=FakeRuntime(),
    )

    events = [decode_message(event_queue.get_nowait()) for _ in range(2)]

    assert active_scan is None
    assert should_exit is False
    assert [event.event_type for event in events] == ["scan_started", "scan_failed"]
    assert events[1].payload["errorCode"] == "SCANNER_OFFLINE"


def test_start_scan_keeps_existing_lifecycle_behavior_without_output_dir():
    from app.worker.messages import ScanCommand
    from app.worker.process import _handle_command

    command = ScanCommand(
        command_id="cmd-scan-2",
        task_id="task-2",
        device_id="device-1",
        settings={},
    )
    event_queue = Queue()

    active_scan, should_exit = _handle_command(
        command,
        event_queue,
        active_scan=None,
        runtime=object(),
    )

    assert active_scan == command
    assert should_exit is False
