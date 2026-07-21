"""扫描页面 SQLite 仓储。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from app.models.records import ScanPageRecord

from .database import Database


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _normalize_relative_path(value: str) -> str:
    """校验并规范化任务目录内的相对路径。"""

    path = Path(value)
    if (
        not value
        or path == Path(".")
        or path.is_absolute()
        or path.anchor
        or ".." in path.parts
    ):
        raise ValueError("页面文件路径必须是任务目录内的相对路径")
    return path.as_posix()


class PageRepository:
    """提供页面记录的事务读写操作。"""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create(
        self,
        page_id: str,
        task_id: str,
        sequence: int,
        original_path: str,
        thumbnail_path: str,
        sha256: str,
        file_size: int,
        *,
        width: int | None = None,
        height: int | None = None,
        created_at: str | None = None,
    ) -> ScanPageRecord:
        timestamp = created_at or _utc_now()
        normalized_original_path = _normalize_relative_path(original_path)
        normalized_thumbnail_path = _normalize_relative_path(thumbnail_path)
        with self._database.transaction() as connection:
            task_row = connection.execute(
                """
                SELECT last_page_sequence
                FROM scan_task
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise KeyError(f"任务不存在：{task_id}")
            if sequence <= task_row["last_page_sequence"]:
                raise ValueError("页面 sequence 必须严格递增且不能复用")
            connection.execute(
                """
                INSERT INTO scan_page (
                    page_id,
                    task_id,
                    sequence,
                    original_path,
                    thumbnail_path,
                    sha256,
                    file_size,
                    width,
                    height,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    page_id,
                    task_id,
                    sequence,
                    normalized_original_path,
                    normalized_thumbnail_path,
                    sha256,
                    file_size,
                    width,
                    height,
                    timestamp,
                ),
            )
            connection.execute(
                """
                UPDATE scan_task
                SET last_page_sequence = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (sequence, timestamp, task_id),
            )
        record = self.get(task_id, page_id)
        if record is None:
            raise RuntimeError(f"页面 {page_id} 写入后无法读取")
        return record

    def get(self, task_id: str, page_id: str) -> ScanPageRecord | None:
        with self._database.lock:
            row = self._database.connection.execute(
                """
                SELECT *
                FROM scan_page
                WHERE task_id = ? AND page_id = ?
                """,
                (task_id, page_id),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def list_by_task(
        self,
        task_id: str,
        *,
        after_sequence: int | None = None,
    ) -> list[ScanPageRecord]:
        if after_sequence is None:
            with self._database.lock:
                rows = self._database.connection.execute(
                    """
                    SELECT *
                    FROM scan_page
                    WHERE task_id = ?
                    ORDER BY sequence
                    """,
                    (task_id,),
                ).fetchall()
        else:
            with self._database.lock:
                rows = self._database.connection.execute(
                    """
                    SELECT *
                    FROM scan_page
                    WHERE task_id = ? AND sequence > ?
                    ORDER BY sequence
                    """,
                    (task_id, after_sequence),
                ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def next_sequence(self, task_id: str) -> int:
        with self._database.lock:
            row = self._database.connection.execute(
                """
                SELECT last_page_sequence + 1 AS next_sequence
                FROM scan_task
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"任务不存在：{task_id}")
        return int(row["next_sequence"])

    def delete(self, task_id: str, page_id: str) -> bool:
        with self._database.transaction() as connection:
            cursor = connection.execute(
                """
                DELETE FROM scan_page
                WHERE task_id = ? AND page_id = ?
                """,
                (task_id, page_id),
            )
        return cursor.rowcount == 1

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ScanPageRecord:
        return ScanPageRecord(
            page_id=row["page_id"],
            task_id=row["task_id"],
            sequence=row["sequence"],
            original_path=row["original_path"],
            thumbnail_path=row["thumbnail_path"],
            sha256=row["sha256"],
            file_size=row["file_size"],
            created_at=row["created_at"],
            width=row["width"],
            height=row["height"],
        )
