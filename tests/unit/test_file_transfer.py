"""TWAIN 文件传输和 JPEG 原子落盘测试。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class FakeTransferStatus:
    return_code: int
    pending_count: int


class FakeSource:
    def __init__(
        self,
        payload: bytes,
        *,
        return_code: int = 6,
        pending_count: int = 0,
    ) -> None:
        self.payload = payload
        self.return_code = return_code
        self.pending_count = pending_count
        self.prepared = False
        self.calls: list[tuple[Path, int]] = []

    def prepare_file_transfer(self) -> None:
        self.prepared = True

    def transfer_file(self, path: Path, *, file_format: int) -> FakeTransferStatus:
        self.calls.append((path, file_format))
        path.write_bytes(self.payload)
        return FakeTransferStatus(self.return_code, self.pending_count)


def test_completed_transfer_is_atomically_renamed(tmp_path: Path):
    from app.scanner.file_transfer import FileTransfer

    source = FakeSource(b"\xff\xd8fake-jpeg\xff\xd9")

    result = FileTransfer(tmp_path).transfer_one(source, page_id="page-1")

    assert source.prepared is True
    assert result.original_path == tmp_path / "page-1.jpg"
    assert result.original_path.read_bytes().startswith(b"\xff\xd8")
    assert result.original_path.read_bytes().endswith(b"\xff\xd9")
    assert result.size == len(b"\xff\xd8fake-jpeg\xff\xd9")
    assert result.transfer_return_code == 6
    assert result.pending_count == 0
    assert source.calls[0][0].suffix == ".part"
    assert source.calls[0][1] == 4
    assert not list(tmp_path.rglob("*.part"))


def test_incomplete_jpeg_is_removed_and_rejected(tmp_path: Path):
    from app.scanner.file_transfer import FileTransfer, FileTransferError

    source = FakeSource(b"\xff\xd8incomplete")

    with pytest.raises(FileTransferError, match="JPEG"):
        FileTransfer(tmp_path).transfer_one(source, page_id="page-1")

    assert not (tmp_path / "page-1.jpg").exists()
    assert not list(tmp_path.rglob("*.part"))


def test_non_xferdone_status_is_rejected(tmp_path: Path):
    from app.scanner.file_transfer import FileTransfer, FileTransferError

    source = FakeSource(b"\xff\xd8cancelled\xff\xd9", return_code=3)

    with pytest.raises(FileTransferError, match="传输结束码"):
        FileTransfer(tmp_path).transfer_one(source, page_id="page-1")

    assert not (tmp_path / "page-1.jpg").exists()
    assert not list(tmp_path.rglob("*.part"))


def test_invalid_page_id_is_rejected_before_driver_call(tmp_path: Path):
    from app.scanner.file_transfer import FileTransfer

    source = FakeSource(b"\xff\xd8jpeg\xff\xd9")

    with pytest.raises(ValueError, match="page_id"):
        FileTransfer(tmp_path).transfer_one(source, page_id="../outside")

    assert source.calls == []
