"""扫描服务持久化模型。"""

from .enums import TaskStatus
from .records import ScanPageRecord, ScanTaskRecord

__all__ = ["ScanPageRecord", "ScanTaskRecord", "TaskStatus"]
