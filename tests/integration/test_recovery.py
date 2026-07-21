"""服务重启后的扫描任务恢复测试。"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def recovery_context(tmp_path: Path):
    from app.repositories.database import Database
    from app.repositories.page_repository import PageRepository
    from app.repositories.task_repository import TaskRepository

    database = Database(tmp_path / "metadata.db")
    tasks = TaskRepository(database)
    pages = PageRepository(database)
    task_dir = tmp_path / "tasks" / "task-1"
    original = task_dir / "originals" / "page-000001.jpg"
    thumbnail = task_dir / "thumbnails" / "page-000001.jpg"
    original.parent.mkdir(parents=True)
    thumbnail.parent.mkdir(parents=True)
    original.write_bytes(b"original-jpeg-placeholder")
    thumbnail.write_bytes(b"thumbnail-jpeg-placeholder")
    yield {
        "database": database,
        "tasks": tasks,
        "pages": pages,
        "original": original,
        "thumbnail": thumbnail,
    }
    database.close()


@pytest.mark.parametrize("stale_status", ["SCANNING", "STOPPING"])
def test_recovery_stops_stale_tasks_and_preserves_pages_and_files(
    recovery_context,
    stale_status: str,
):
    from app.models.enums import TaskStatus
    from app.services.recovery_service import RecoveryService

    tasks = recovery_context["tasks"]
    pages = recovery_context["pages"]
    tasks.create("task-1", "device-1", status=stale_status)
    pages.create(
        "page-000001",
        "task-1",
        1,
        "originals/page-000001.jpg",
        "thumbnails/page-000001.jpg",
        "sha256-before-recovery",
        24,
    )

    recovered = RecoveryService(tasks).recover()

    assert [item.task_id for item in recovered] == ["task-1"]
    task = tasks.get("task-1")
    assert task is not None
    assert task.status is TaskStatus.STOPPED
    assert task.error_code == "SERVICE_RESTARTED"
    assert task.error_message == "服务重启后停止未完成扫描"
    assert [(page.page_id, page.sequence) for page in pages.list_by_task("task-1")] == [
        ("page-000001", 1)
    ]
    assert recovery_context["original"].read_bytes() == b"original-jpeg-placeholder"
    assert recovery_context["thumbnail"].read_bytes() == b"thumbnail-jpeg-placeholder"


def test_recovery_leaves_non_active_tasks_unchanged(recovery_context):
    from app.services.recovery_service import RecoveryService

    tasks = recovery_context["tasks"]
    tasks.create("task-1", "device-1", status="COMPLETED")
    tasks.create("task-2", "device-2", status="FAILED")

    recovered = RecoveryService(tasks).recover()

    assert [(item.task_id, item.status.value) for item in recovered] == [
        ("task-1", "COMPLETED"),
        ("task-2", "FAILED"),
    ]
    assert tasks.get("task-1").error_code is None
    assert tasks.get("task-2").error_code is None
