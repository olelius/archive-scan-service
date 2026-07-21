"""扫描服务领域服务。"""

from .page_service import PageRegistrationError, PageService
from .thumbnail_service import ThumbnailError, ThumbnailService

__all__ = [
    "PageRegistrationError",
    "PageService",
    "ThumbnailError",
    "ThumbnailService",
]
