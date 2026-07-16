"""扫描服务领域枚举。"""

from enum import StrEnum


class TaskStatus(StrEnum):
    """扫描任务状态。"""

    CREATED = "CREATED"
    SCANNING = "SCANNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
