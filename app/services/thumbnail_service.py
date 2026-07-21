"""使用 Pillow 生成不修改原图的 JPEG 缩略图。"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps


class ThumbnailError(RuntimeError):
    """缩略图生成失败。"""


class ThumbnailService:
    """读取原图并以临时文件原子生成 JPEG 预览图。"""

    def __init__(
        self,
        max_size: tuple[int, int] = (320, 320),
        quality: int = 75,
    ) -> None:
        if (
            len(max_size) != 2
            or any(isinstance(item, bool) or item <= 0 for item in max_size)
        ):
            raise ValueError("max_size 必须包含两个正整数")
        if isinstance(quality, bool) or not 1 <= quality <= 100:
            raise ValueError("quality 必须在 1 到 100 之间")
        self._max_size = (int(max_size[0]), int(max_size[1]))
        self._quality = quality

    def create(self, source: str | Path, destination: str | Path) -> Path:
        """生成缩略图并返回最终路径，原图始终只读。"""

        source_path = Path(source)
        destination_path = Path(destination)
        partial_path = destination_path.parent / (
            f".{destination_path.name}.{uuid4().hex}.part"
        )

        try:
            if not source_path.is_file():
                raise ThumbnailError("原图文件不存在")
            if source_path.resolve() == destination_path.resolve():
                raise ThumbnailError("缩略图路径不能与原图相同")

            destination_path.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(source_path) as original:
                original.load()
                image = ImageOps.exif_transpose(original).copy()
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            image.thumbnail(self._max_size, Image.Resampling.LANCZOS)
            image.save(
                partial_path,
                format="JPEG",
                quality=self._quality,
                optimize=True,
            )
            image.close()
            os.replace(partial_path, destination_path)
        except ThumbnailError:
            self._remove_partial(partial_path)
            raise
        except Exception as exc:
            self._remove_partial(partial_path)
            raise ThumbnailError("缩略图生成失败") from exc

        return destination_path

    @staticmethod
    def _remove_partial(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = ["ThumbnailError", "ThumbnailService"]
