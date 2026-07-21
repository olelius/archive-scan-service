"""页面缩略图服务测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image


def _create_jpeg(
    path: Path,
    *,
    size: tuple[int, int] = (1200, 800),
    orientation: int | None = None,
) -> None:
    image = Image.new("RGB", size, (30, 90, 150))
    if orientation is not None:
        exif = image.getexif()
        exif[274] = orientation
        image.save(path, format="JPEG", exif=exif)
    else:
        image.save(path, format="JPEG")


def test_thumbnail_generation_does_not_modify_original(tmp_path: Path):
    from app.services.thumbnail_service import ThumbnailService

    original = tmp_path / "original.jpg"
    thumbnail_path = tmp_path / "thumbnail.jpg"
    _create_jpeg(original)
    before = hashlib.sha256(original.read_bytes()).hexdigest()

    result = ThumbnailService(max_size=(320, 320), quality=75).create(
        original,
        thumbnail_path,
    )

    after = hashlib.sha256(original.read_bytes()).hexdigest()
    assert result == thumbnail_path
    assert result.exists()
    assert after == before

    with Image.open(result) as thumbnail:
        assert thumbnail.format == "JPEG"
        assert thumbnail.width <= 320
        assert thumbnail.height <= 320
        assert thumbnail.size == (320, 213)


def test_thumbnail_uses_exif_orientation_without_rewriting_original(tmp_path: Path):
    from app.services.thumbnail_service import ThumbnailService

    original = tmp_path / "portrait.jpg"
    thumbnail_path = tmp_path / "portrait-thumbnail.jpg"
    _create_jpeg(original, size=(100, 200), orientation=6)
    before = hashlib.sha256(original.read_bytes()).hexdigest()

    ThumbnailService(max_size=(320, 320)).create(original, thumbnail_path)

    with Image.open(thumbnail_path) as thumbnail:
        assert thumbnail.size == (200, 100)
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before


def test_thumbnail_generation_rejects_invalid_image_without_partial_output(
    tmp_path: Path,
):
    from app.services.thumbnail_service import ThumbnailError, ThumbnailService

    original = tmp_path / "broken.jpg"
    thumbnail_path = tmp_path / "thumbnail.jpg"
    original.write_bytes(b"not a jpeg")

    with pytest.raises(ThumbnailError):
        ThumbnailService().create(original, thumbnail_path)

    assert not thumbnail_path.exists()
    assert not list(tmp_path.glob(".thumbnail.*.part"))
