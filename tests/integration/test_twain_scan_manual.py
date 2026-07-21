"""真实 Windows TWAIN 一次 JPEG 扫描测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _wait_for_event(supervisor, command_id: str, event_type: str):
    for _ in range(100):
        event = supervisor.wait_for_event(timeout=1.0)
        if event is None:
            continue
        if getattr(event, "command_id", None) != command_id:
            continue
        if getattr(event, "event_type", None) == event_type:
            return event
        if getattr(event, "event_type", None) in {"command_failed", "scan_failed"}:
            pytest.fail(
                f"命令 {command_id} 失败：{event.payload.get('errorCode')} "
                f"{event.payload.get('errorMessage')}"
            )
    pytest.fail(f"等待命令 {command_id} 的 {event_type} 事件超时")


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_TWAIN_SCAN_MANUAL") != "1",
    reason="设置 RUN_TWAIN_SCAN_MANUAL=1 后才连接真实 TWAIN 设备扫描",
)
def test_real_twain_scan_returns_end_signal_and_jpeg():
    from app.worker.messages import CommandMessage, ScanCommand
    from app.worker.supervisor import WorkerSupervisor, WorkerSupervisorError

    expected_product = os.environ.get(
        "EXPECTED_TWAIN_PRODUCT",
        "KODAK Scanner: i2000",
    )
    output_dir = Path(
        os.environ.get(
            "TWAIN_SCAN_OUTPUT_DIR",
            str(Path(__file__).resolve().parents[2] / "scan-test-output"),
        )
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    page_id = "manual-page-1"

    supervisor = WorkerSupervisor(
        heartbeat_interval=0.1,
        monitor_poll_interval=0.02,
        shutdown_timeout=10.0,
    )
    supervisor.start()
    supervisor.wait_until_ready(timeout=10.0)
    opened = False
    try:
        enumerate_command = CommandMessage(
            command_id="manual-enumerate-scan-1",
            message_type="enumerate_devices",
        )
        supervisor.send(enumerate_command)
        listed = _wait_for_event(
            supervisor,
            enumerate_command.command_id,
            "device_listed",
        )
        _wait_for_event(
            supervisor,
            enumerate_command.command_id,
            "command_succeeded",
        )
        assert listed.payload["productName"] == expected_product
        device_id = listed.payload["deviceId"]

        open_command = CommandMessage(
            command_id="manual-open-scan-1",
            message_type="open_source",
            payload={"productName": expected_product, "showUi": False},
        )
        supervisor.send(open_command)
        opened_event = _wait_for_event(
            supervisor,
            open_command.command_id,
            "command_succeeded",
        )
        opened = True
        assert opened_event.payload["productName"] == expected_product
        assert opened_event.payload["showUi"] is False

        scan_command = ScanCommand(
            command_id="manual-scan-1",
            task_id="manual-task-1",
            device_id=device_id,
            settings={"outputDir": str(output_dir), "pageId": page_id},
        )
        supervisor.send(scan_command)
        page_ready = _wait_for_event(
            supervisor,
            scan_command.command_id,
            "page_file_ready",
        )
        completed = _wait_for_event(
            supervisor,
            scan_command.command_id,
            "scan_completed",
        )

        image_path = Path(page_ready.payload["path"])
        image_bytes = image_path.read_bytes()
        assert image_path == output_dir / f"{page_id}.jpg"
        assert image_bytes.startswith(b"\xff\xd8")
        assert image_bytes.endswith(b"\xff\xd9")
        assert len(image_bytes) > 4
        assert page_ready.payload["transferReturnCode"] == 6
        assert page_ready.payload["pendingCount"] == 0
        assert completed.payload["transferReturnCode"] == 6
        assert completed.payload["pendingCount"] == 0
        print(
            {
                "source": expected_product,
                "path": str(image_path),
                "size": len(image_bytes),
                "transferReturnCode": page_ready.payload["transferReturnCode"],
                "pendingCount": completed.payload["pendingCount"],
            }
        )
    finally:
        try:
            if opened:
                close_command = CommandMessage(
                    command_id="manual-close-scan-1",
                    message_type="close_source",
                )
                try:
                    supervisor.send(close_command)
                    _wait_for_event(
                        supervisor,
                        close_command.command_id,
                        "command_succeeded",
                    )
                except WorkerSupervisorError:
                    supervisor.force_terminate_for_test()
        finally:
            supervisor.shutdown()
