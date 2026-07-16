"""SQLite schema 版本迁移。"""

from __future__ import annotations

import sqlite3


CURRENT_SCHEMA_VERSION = 1

_SCHEMA_V1 = """
BEGIN;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE scan_task (
    task_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'CREATED', 'SCANNING', 'STOPPING', 'STOPPED',
            'COMPLETED', 'FAILED', 'CANCELLED'
        )
    ),
    error_code TEXT,
    error_message TEXT,
    device_snapshot_json TEXT,
    capability_snapshot_json TEXT,
    scan_params_snapshot_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE scan_page (
    page_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    original_path TEXT NOT NULL,
    thumbnail_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    file_size INTEGER NOT NULL CHECK (file_size >= 0),
    width INTEGER,
    height INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES scan_task(task_id) ON DELETE CASCADE,
    UNIQUE (task_id, sequence)
);

CREATE INDEX scan_task_status_idx ON scan_task(status);
CREATE INDEX scan_page_task_sequence_idx ON scan_page(task_id, sequence);

INSERT INTO schema_version (version, applied_at)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

COMMIT;
"""


def apply_migrations(connection: sqlite3.Connection) -> None:
    """将数据库迁移到当前 schema 版本。"""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM schema_version"
    ).fetchone()
    current_version = int(row["version"] if row is not None else 0)

    if current_version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库 schema 版本 {current_version} 高于程序支持的版本 "
            f"{CURRENT_SCHEMA_VERSION}"
        )
    if current_version == CURRENT_SCHEMA_VERSION:
        return
    if current_version != 0:
        raise RuntimeError(f"不支持从 schema 版本 {current_version} 迁移")

    connection.executescript(_SCHEMA_V1)
