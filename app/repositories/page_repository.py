"""扫描页面 SQLite 仓储。"""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from app.models.records import ScanPageRecord

from .database import Database


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


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
        with self._database.transaction() as connection:
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
                    original_path,
                    thumbnail_path,
                    sha256,
                    file_size,
                    width,
                    height,
                    timestamp,
                ),
            )
        record = self.get(task_id, page_id)
        if record is None:
            raise RuntimeError(f"页面 {page_id} 写入后无法读取")
        return record

    def get(self, task_id: str, page_id: str) -> ScanPageRecord | None:
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
        row = self._database.connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
            FROM scan_page
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
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
