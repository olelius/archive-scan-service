from pathlib import Path

import pytest


@pytest.fixture
def repository_bundle(tmp_path: Path):
    from app.repositories.database import Database
    from app.repositories.page_repository import PageRepository
    from app.repositories.task_repository import TaskRepository

    database = Database(tmp_path / "metadata.db")
    yield TaskRepository(database), PageRepository(database), database
    database.close()


def test_database_initializes_schema_and_write_safety(repository_bundle):
    _, _, database = repository_bundle

    foreign_keys = database.connection.execute("PRAGMA foreign_keys").fetchone()[0]
    journal_mode = database.connection.execute("PRAGMA journal_mode").fetchone()[0]
    busy_timeout = database.connection.execute("PRAGMA busy_timeout").fetchone()[0]
    schema_version = database.connection.execute(
        "SELECT version FROM schema_version"
    ).fetchone()[0]

    assert foreign_keys == 1
    assert journal_mode.lower() == "wal"
    assert busy_timeout == 5_000
    assert schema_version == 1


def test_task_create_get_and_update_status(repository_bundle):
    tasks, _, _ = repository_bundle

    created = tasks.create("task-1", "device-1")
    loaded = tasks.get("task-1")

    assert created.task_id == "task-1"
    assert created.device_id == "device-1"
    assert created.status.value == "CREATED"
    assert loaded == created

    updated = tasks.update_status(
        "task-1",
        "SCANNING",
        error_code="",
        error_message=None,
    )

    assert updated.status.value == "SCANNING"
    assert updated.error_code is None
    assert tasks.get("task-1").status.value == "SCANNING"


def test_page_deletion_does_not_renumber_remaining_pages(repository_bundle):
    tasks, pages, _ = repository_bundle
    tasks.create("task-1", "device-1")
    pages.create("page-1", "task-1", 1, "a.jpg", "a-thumb.jpg", "hash-a", 101)
    pages.create("page-2", "task-1", 2, "b.jpg", "b-thumb.jpg", "hash-b", 202)

    assert pages.delete("task-1", "page-1") is True

    remaining = pages.list_by_task("task-1")
    assert [(item.page_id, item.sequence) for item in remaining] == [("page-2", 2)]


def test_pages_can_be_loaded_after_a_sequence(repository_bundle):
    tasks, pages, _ = repository_bundle
    tasks.create("task-1", "device-1")
    pages.create("page-1", "task-1", 1, "a.jpg", "a-thumb.jpg", "hash-a", 101)
    pages.create("page-2", "task-1", 2, "b.jpg", "b-thumb.jpg", "hash-b", 202)
    pages.create("page-3", "task-1", 3, "c.jpg", "c-thumb.jpg", "hash-c", 303)

    loaded = pages.list_by_task("task-1", after_sequence=1)

    assert [item.page_id for item in loaded] == ["page-2", "page-3"]


def test_next_page_sequence_is_not_reused_after_deleting_last_page(
    repository_bundle,
):
    tasks, pages, _ = repository_bundle
    tasks.create("task-1", "device-1")
    pages.create("page-1", "task-1", 1, "a.jpg", "a-thumb.jpg", "hash-a", 101)
    pages.create("page-2", "task-1", 2, "b.jpg", "b-thumb.jpg", "hash-b", 202)

    pages.delete("task-1", "page-2")

    assert pages.next_sequence("task-1") == 3


def test_page_sequence_cannot_reuse_a_deleted_value(repository_bundle):
    tasks, pages, _ = repository_bundle
    tasks.create("task-1", "device-1")
    pages.create("page-1", "task-1", 1, "a.jpg", "a-thumb.jpg", "hash-a", 101)
    pages.create("page-2", "task-1", 2, "b.jpg", "b-thumb.jpg", "hash-b", 202)
    pages.delete("task-1", "page-2")

    with pytest.raises(ValueError):
        pages.create(
            "page-2-reused",
            "task-1",
            2,
            "b-reused.jpg",
            "b-reused-thumb.jpg",
            "hash-b-reused",
            303,
        )


@pytest.mark.parametrize(
    ("original_path", "thumbnail_path"),
    [
        ("C:/outside.jpg", "thumbnail.jpg"),
        ("original.jpg", "../outside-thumbnail.jpg"),
    ],
)
def test_page_paths_must_be_relative_without_parent_traversal(
    repository_bundle,
    original_path: str,
    thumbnail_path: str,
):
    tasks, pages, _ = repository_bundle
    tasks.create("task-1", "device-1")

    with pytest.raises(ValueError):
        pages.create(
            "page-1",
            "task-1",
            1,
            original_path,
            thumbnail_path,
            "hash-a",
            101,
        )


def test_deleting_task_cascades_to_pages(repository_bundle):
    tasks, pages, _ = repository_bundle
    tasks.create("task-1", "device-1")
    pages.create("page-1", "task-1", 1, "a.jpg", "a-thumb.jpg", "hash-a", 101)

    assert tasks.delete("task-1") is True
    assert tasks.get("task-1") is None
    assert pages.list_by_task("task-1") == []
