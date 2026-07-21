"""扫描任务生命周期、状态转换和单活动互斥测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def repository_bundle(tmp_path: Path):
    from app.repositories.database import Database
    from app.repositories.task_repository import TaskRepository

    database = Database(tmp_path / "metadata.db")
    tasks = TaskRepository(database)
    yield tasks, database
    database.close()


def _service(tasks):
    from app.services.task_service import TaskService

    return TaskService(tasks)


def test_start_scan_moves_created_task_to_scanning_and_saves_settings(
    repository_bundle,
):
    tasks, _ = repository_bundle
    service = _service(tasks)
    service.create("task-1", "device-1")

    updated = service.start_scan(
        "task-1",
        {"resolution": 300, "duplex": True},
    )

    assert updated.status.value == "SCANNING"
    assert json.loads(updated.scan_params_snapshot_json or "") == {
        "duplex": True,
        "resolution": 300,
    }


def test_second_scan_is_rejected(repository_bundle):
    from app.services.task_service import ScannerBusyError

    tasks, _ = repository_bundle
    service = _service(tasks)
    service.create("task-1", "device-1")
    service.create("task-2", "device-1")
    service.start_scan("task-1", {})

    with pytest.raises(ScannerBusyError) as exc_info:
        service.start_scan("task-2", {})

    assert exc_info.value.error_code == "SCANNER_BUSY"
    assert exc_info.value.active_task_id == "task-1"


def test_two_service_instances_share_database_activity_guard(repository_bundle):
    from app.services.task_service import ScannerBusyError

    tasks, _ = repository_bundle
    first = _service(tasks)
    second = _service(tasks)
    first.create("task-1", "device-1")
    second.create("task-2", "device-1")
    first.start_scan("task-1", {})

    with pytest.raises(ScannerBusyError):
        second.start_scan("task-2", {})


def test_stop_scan_requires_stopping_event_before_stopped(repository_bundle):
    tasks, _ = repository_bundle
    service = _service(tasks)
    service.create("task-1", "device-1")
    service.start_scan("task-1", {})

    stopping = service.stop_scan("task-1")
    stopped = service.mark_stopped("task-1")

    assert stopping.status.value == "STOPPING"
    assert stopped.status.value == "STOPPED"


@pytest.mark.parametrize("terminal_action", ["complete_scan", "fail_scan"])
def test_active_scan_can_complete_or_fail(repository_bundle, terminal_action: str):
    tasks, _ = repository_bundle
    service = _service(tasks)
    service.create("task-1", "device-1")
    service.start_scan("task-1", {})

    if terminal_action == "complete_scan":
        result = service.complete_scan("task-1")
        assert result.error_code is None
        assert result.status.value == "COMPLETED"
    else:
        result = service.fail_scan("task-1", "PAPER_JAM", "扫描仪卡纸")
        assert result.status.value == "FAILED"
        assert result.error_code == "PAPER_JAM"
        assert result.error_message == "扫描仪卡纸"


def test_invalid_transition_is_rejected(repository_bundle):
    from app.services.task_service import TaskStateError

    tasks, _ = repository_bundle
    service = _service(tasks)
    service.create("task-1", "device-1")

    with pytest.raises(TaskStateError) as exc_info:
        service.complete_scan("task-1")

    assert exc_info.value.error_code == "TASK_STATE_INVALID"
    assert exc_info.value.current_status == "CREATED"


@pytest.mark.parametrize("status", ["STOPPED", "FAILED", "COMPLETED"])
def test_stopped_failed_and_completed_tasks_can_resume(repository_bundle, status: str):
    tasks, _ = repository_bundle
    service = _service(tasks)
    service.create("task-1", "device-1")
    service.start_scan("task-1", {})

    if status == "STOPPED":
        service.stop_scan("task-1")
        service.mark_stopped("task-1")
    elif status == "FAILED":
        service.fail_scan("task-1", "SCAN_FAILED", "驱动异常")
    else:
        service.complete_scan("task-1")

    resumed = service.start_scan("task-1", {"resolution": 200})

    assert resumed.status.value == "SCANNING"
    assert resumed.error_code is None
    assert resumed.error_message is None


def test_cancelled_task_is_terminal(repository_bundle):
    from app.services.task_service import TaskStateError

    tasks, _ = repository_bundle
    service = _service(tasks)
    service.create("task-1", "device-1")

    cancelled = service.cancel_scan("task-1")
    assert cancelled.status.value == "CANCELLED"

    with pytest.raises(TaskStateError):
        service.start_scan("task-1", {})


def test_duplicate_task_is_rejected(repository_bundle):
    from app.services.task_service import TaskAlreadyExistsError

    tasks, _ = repository_bundle
    service = _service(tasks)
    service.create("task-1", "device-1")

    with pytest.raises(TaskAlreadyExistsError):
        service.create("task-1", "device-2")


def test_task_lifecycle_services_are_exported_from_services_package():
    from app.services import RecoveryService, TaskService

    assert TaskService.__name__ == "TaskService"
    assert RecoveryService.__name__ == "RecoveryService"
