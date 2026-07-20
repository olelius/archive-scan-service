"""真实 Windows TWAIN Capability 只读冒烟探测。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest


def _wait_for_command_event(supervisor, command_id: str, event_type: str):
    for _ in range(100):
        event = supervisor.wait_for_event(timeout=1.0)
        if event is None:
            continue
        if getattr(event, "command_id", None) != command_id:
            continue
        if getattr(event, "event_type", None) == event_type:
            return event
        if getattr(event, "event_type", None) == "command_failed":
            pytest.fail(
                f"命令 {command_id} 失败：{event.payload.get('errorCode')} "
                f"{event.payload.get('errorMessage')}"
            )
    pytest.fail(f"等待命令 {command_id} 的 {event_type} 事件超时")


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_TWAIN_CAPABILITY_MANUAL") != "1",
    reason="设置 RUN_TWAIN_CAPABILITY_MANUAL=1 后才连接真实 TWAIN 设备探测",
)
def test_real_twain_capabilities_are_read_only_and_recorded(tmp_path: Path):
    from app.worker.messages import CommandMessage
    from app.worker.supervisor import WorkerSupervisor

    expected_product = os.environ.get(
        "EXPECTED_TWAIN_PRODUCT",
        "KODAK Scanner: i2000",
    )
    supervisor = WorkerSupervisor(
        heartbeat_interval=0.1,
        monitor_poll_interval=0.02,
        shutdown_timeout=10.0,
    )
    supervisor.start()
    supervisor.wait_until_ready(timeout=10.0)
    try:
        open_command = CommandMessage(
            command_id="manual-open-1",
            message_type="open_source",
            payload={"productName": expected_product, "showUi": False},
        )
        supervisor.send(open_command)
        opened = _wait_for_command_event(
            supervisor,
            open_command.command_id,
            "command_succeeded",
        )
        assert opened.payload["productName"] == expected_product
        assert opened.payload["showUi"] is False
        assert opened.payload["architecture"] == "x64"

        query_command = CommandMessage(
            command_id="manual-query-1",
            message_type="query_capabilities",
        )
        supervisor.send(query_command)
        queried = _wait_for_command_event(
            supervisor,
            query_command.command_id,
            "capabilities_queried",
        )
        capabilities = queried.payload["capabilities"]
        assert queried.payload["count"] == len(capabilities)
        assert capabilities
        for capability in capabilities:
            assert isinstance(capability["capabilityId"], int)
            assert capability["containerType"]
            assert capability["itemType"]
            assert "operations" in capability
            assert "currentValue" in capability
            assert "defaultValue" in capability

        close_command = CommandMessage(
            command_id="manual-close-1",
            message_type="close_source",
        )
        supervisor.send(close_command)
        _wait_for_command_event(
            supervisor,
            close_command.command_id,
            "command_succeeded",
        )

        record: dict[str, Any] = {
            "task": "Task 7.5",
            "readOnly": True,
            "physicalDevice": os.environ.get(
                "EXPECTED_TWAIN_PHYSICAL_DEVICE",
                "KODAK i2400",
            ),
            "driverEntry": os.environ.get(
                "EXPECTED_TWAIN_DRIVER",
                "kds_i2000.inf",
            ),
            "source": opened.payload,
            "capabilityCount": len(capabilities),
            "capabilities": capabilities,
        }
        record_path_value = os.environ.get("TWAIN_CAPABILITY_RECORD_PATH")
        if record_path_value:
            record_path = Path(record_path_value)
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record_path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        else:
            print(json.dumps(record, ensure_ascii=False, indent=2))
    finally:
        supervisor.shutdown()
