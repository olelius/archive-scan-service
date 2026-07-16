"""扫描工作进程生命周期集成测试。"""

from __future__ import annotations

import time

import pytest


@pytest.fixture
def supervisor():
    from app.worker.supervisor import WorkerSupervisor

    instance = WorkerSupervisor()
    try:
        yield instance
    finally:
        instance.shutdown()


def _wait_for_event(supervisor, event_type: str, timeout: float = 5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = supervisor.wait_for_event(timeout=0.2)
        if getattr(event, "event_type", None) == event_type:
            return event
    raise AssertionError(f"未在 {timeout} 秒内收到 {event_type} 事件")


def test_supervisor_starts_worker_and_emits_ready(supervisor):
    process = supervisor.start()

    event = supervisor.wait_until_ready(timeout=10)

    assert event.event_type == "worker_ready"
    assert event.pid == process.pid
    assert event.generation == 1


def test_supervisor_restarts_crashed_idle_worker(supervisor):
    first_process = supervisor.start()
    first_ready = supervisor.wait_until_ready(timeout=10)

    supervisor.force_terminate_for_test()

    second_ready = supervisor.wait_until_ready(timeout=10)

    assert second_ready.event_type == "worker_ready"
    assert second_ready.pid != first_process.pid
    assert second_ready.pid != first_ready.pid
    assert second_ready.generation == 2


def test_supervisor_reports_worker_unavailable_when_scan_worker_crashes(supervisor):
    supervisor.start()
    supervisor.wait_until_ready(timeout=10)

    from app.worker.messages import ScanCommand

    supervisor.send(
        ScanCommand(
            command_id="cmd-scan-1",
            task_id="task-1",
            device_id="device-1",
            settings={},
        )
    )
    _wait_for_event(supervisor, "scan_started")

    supervisor.force_terminate_for_test()

    unavailable = _wait_for_event(supervisor, "worker_unavailable", timeout=10)
    assert unavailable.error_code == "WORKER_UNAVAILABLE"
    assert unavailable.task_id == "task-1"
    assert unavailable.command_id == "cmd-scan-1"
    assert unavailable.pid is not None

    replacement = supervisor.wait_until_ready(timeout=10)
    assert replacement.pid != unavailable.pid


def test_supervisor_shutdown_reaps_worker(supervisor):
    process = supervisor.start()
    supervisor.wait_until_ready(timeout=10)

    supervisor.shutdown()

    process.join(timeout=5)
    assert not process.is_alive()
