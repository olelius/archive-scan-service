"""页面原图登记、摘要和缩略图编排测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image


def _create_jpeg(path: Path, *, size: tuple[int, int] = (1200, 800)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (40, 100, 160)).save(path, format="JPEG")


@pytest.fixture
def page_context(tmp_path: Path):
    from app.repositories.database import Database
    from app.repositories.page_repository import PageRepository
    from app.repositories.task_repository import TaskRepository

    tasks_root = tmp_path / "tasks"
    task_dir = tasks_root / "task-1"
    originals_dir = task_dir / "originals"
    originals_dir.mkdir(parents=True)

    database = Database(tmp_path / "metadata.db")
    task_repository = TaskRepository(database)
    page_repository = PageRepository(database)
    task_repository.create("task-1", "device-1")
    yield {
        "database": database,
        "tasks": task_repository,
        "pages": page_repository,
        "tasks_root": tasks_root,
        "task_dir": task_dir,
        "originals": originals_dir,
    }
    database.close()


def _page_file_ready(path: Path):
    from app.worker.messages import EventMessage

    return EventMessage(
        event_type="page_file_ready",
        command_id="command-1",
        task_id="task-1",
        payload={"path": str(path)},
    )


def _build_service(context, **kwargs):
    from app.services.page_service import PageService

    return PageService(
        task_repository=context["tasks"],
        page_repository=context["pages"],
        tasks_root=context["tasks_root"],
        **kwargs,
    )


def test_register_page_calculates_metadata_creates_thumbnail_and_publishes_event(
    page_context,
):
    source = page_context["originals"] / "page-000001.jpg"
    _create_jpeg(source, size=(1200, 800))
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    published: list[dict[str, object]] = []
    service = _build_service(page_context)

    record = service.handle_page_file_ready(
        _page_file_ready(source),
        publish=published.append,
    )

    assert record.page_id == "page-000001"
    assert record.task_id == "task-1"
    assert record.sequence == 1
    assert record.original_path == "originals/page-000001.jpg"
    assert record.thumbnail_path == "thumbnails/page-000001.jpg"
    assert record.sha256 == before
    assert record.file_size == source.stat().st_size
    assert (page_context["task_dir"] / record.thumbnail_path).exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before

    with Image.open(page_context["task_dir"] / record.thumbnail_path) as thumbnail:
        assert thumbnail.format == "JPEG"
        assert thumbnail.width <= 320
        assert thumbnail.height <= 320

    assert published[0]["event"] == "page_completed"
    assert published[0]["taskId"] == "task-1"
    assert published[0]["data"] == {
        "pageId": "page-000001",
        "sequence": 1,
        "originalPath": "originals/page-000001.jpg",
        "thumbnailPath": "thumbnails/page-000001.jpg",
        "sha256": before,
        "fileSize": source.stat().st_size,
        "width": 1200,
        "height": 800,
    }


def test_register_page_uses_monotonic_sequence_and_stable_page_id(page_context):
    first = page_context["originals"] / "page-000001.jpg"
    second = page_context["originals"] / "page-000002.jpg"
    _create_jpeg(first)
    _create_jpeg(second)
    service = _build_service(page_context)

    first_record = service.handle_page_file_ready(_page_file_ready(first))
    second_record = service.handle_page_file_ready(_page_file_ready(second))

    assert (first_record.page_id, first_record.sequence) == ("page-000001", 1)
    assert (second_record.page_id, second_record.sequence) == ("page-000002", 2)
    assert page_context["pages"].next_sequence("task-1") == 3


def test_register_page_rejects_file_outside_task_originals(page_context):
    from app.services.page_service import PageRegistrationError

    outside = page_context["tasks_root"] / "outside.jpg"
    _create_jpeg(outside)
    published: list[dict[str, object]] = []
    service = _build_service(page_context)

    with pytest.raises(PageRegistrationError, match="任务原图目录"):
        service.handle_page_file_ready(
            _page_file_ready(outside),
            publish=published.append,
        )

    assert page_context["pages"].list_by_task("task-1") == []
    assert published == []


def test_thumbnail_failure_keeps_original_and_records_recoverable_error(
    page_context,
):
    from app.services.page_service import PageRegistrationError

    class FailingThumbnailService:
        def create(self, source: Path, destination: Path) -> Path:
            raise RuntimeError("磁盘空间不足")

    source = page_context["originals"] / "page-000001.jpg"
    _create_jpeg(source)
    before = source.read_bytes()
    published: list[dict[str, object]] = []
    service = _build_service(
        page_context,
        thumbnail_service=FailingThumbnailService(),
    )

    with pytest.raises(PageRegistrationError, match="缩略图"):
        service.handle_page_file_ready(
            _page_file_ready(source),
            publish=published.append,
        )

    assert source.read_bytes() == before
    assert page_context["pages"].list_by_task("task-1") == []
    assert published == []
    task = page_context["tasks"].get("task-1")
    assert task is not None
    assert task.error_code == "THUMBNAIL_FAILED"


def test_database_failure_keeps_original_and_removes_unregistered_thumbnail(
    page_context,
    monkeypatch,
):
    from app.services.page_service import PageRegistrationError

    source = page_context["originals"] / "page-000001.jpg"
    _create_jpeg(source)
    before = source.read_bytes()

    def fail_create(*args, **kwargs):
        raise RuntimeError("数据库写入失败")

    monkeypatch.setattr(page_context["pages"], "create", fail_create)
    service = _build_service(page_context)

    with pytest.raises(PageRegistrationError, match="页面记录"):
        service.handle_page_file_ready(_page_file_ready(source))

    assert source.read_bytes() == before
    assert not (page_context["task_dir"] / "thumbnails/page-000001.jpg").exists()
    assert page_context["pages"].list_by_task("task-1") == []
    task = page_context["tasks"].get("task-1")
    assert task is not None
    assert task.error_code == "PAGE_PERSIST_FAILED"
    assert task.error_message == "页面记录写入失败"
