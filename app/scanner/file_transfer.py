"""TWAIN 文件模式的一次 JPEG 传输和原子落盘。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import twain


class FileTransferSource(Protocol):
    """一次文件传输所需的 Data Source 最小接口。"""

    def prepare_file_transfer(self) -> None:
        """准备一次文件传输。"""

    def transfer_file(
        self,
        path: Path,
        *,
        file_format: int,
    ) -> "TransferStatus":
        """把当前已就绪页面传输到文件并返回 TWAIN 状态。"""


@dataclass(frozen=True, slots=True)
class TransferStatus:
    """一次图像文件传输及 `MSG_ENDXFER` 的结果。"""

    return_code: int
    pending_count: int


@dataclass(frozen=True, slots=True)
class FileTransferResult:
    """已完成页面的最终文件和 TWAIN 传输结果。"""

    original_path: Path
    size: int
    transfer_return_code: int
    pending_count: int
    configuration_results: tuple[dict[str, object], ...] = ()


class FileTransferError(RuntimeError):
    """文件传输或原图完整性校验失败。"""


class FileTransfer:
    """把一个已就绪的 TWAIN 页面原子保存为 JPEG。"""

    def __init__(self, output_dir: str | Path) -> None:
        self._output_dir = Path(output_dir)

    def transfer_one(
        self,
        source: FileTransferSource,
        *,
        page_id: str,
    ) -> FileTransferResult:
        """传输一面页面，确认 JPEG 完整后原子改名。"""

        self._validate_page_id(page_id)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        final_path = self._output_dir / f"{page_id}.jpg"
        part_path = self._output_dir / f".{page_id}.{uuid4().hex}.part"
        moved = False

        try:
            source.prepare_file_transfer()
            status = source.transfer_file(
                part_path,
                file_format=twain.constants.TWFF_JFIF,
            )
            return_code = int(status.return_code)
            pending_count = int(status.pending_count)
            if return_code != int(twain.constants.TWRC_XFERDONE):
                raise FileTransferError(
                    f"TWAIN传输结束码不是 TWRC_XFERDONE: {return_code}"
                )

            size = self._validate_jpeg(part_path)
            os.replace(part_path, final_path)
            moved = True
            return FileTransferResult(
                original_path=final_path,
                size=size,
                transfer_return_code=return_code,
                pending_count=pending_count,
            )
        except FileTransferError:
            raise
        except Exception as exc:
            raise FileTransferError("TWAIN文件传输失败") from exc
        finally:
            if not moved:
                self._remove_partial(part_path)

    @staticmethod
    def _validate_page_id(page_id: str) -> None:
        if not isinstance(page_id, str):
            raise ValueError("page_id 必须是单段非空标识")
        if not page_id or Path(page_id).name != page_id or page_id in {".", ".."}:
            raise ValueError("page_id 必须是单段非空标识")

    @staticmethod
    def _validate_jpeg(path: Path) -> int:
        try:
            size = path.stat().st_size
            if size < 4:
                raise FileTransferError("传输文件不是完整 JPEG")
            with path.open("rb") as stream:
                head = stream.read(2)
                stream.seek(-2, os.SEEK_END)
                tail = stream.read(2)
        except FileTransferError:
            raise
        except (FileNotFoundError, OSError) as exc:
            raise FileTransferError("TWAIN未生成传输文件") from exc
        if head != b"\xff\xd8" or tail != b"\xff\xd9":
            raise FileTransferError("传输文件不是完整 JPEG")
        return size

    @staticmethod
    def _remove_partial(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = [
    "FileTransfer",
    "FileTransferError",
    "FileTransferResult",
    "FileTransferSource",
    "TransferStatus",
]
