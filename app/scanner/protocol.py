"""扫描工作进程 IPC 协议的版本和消息类型注册表。"""

from enum import StrEnum


PROTOCOL_VERSION = 1


class CommandType(StrEnum):
    """主进程可以发送给工作进程的命令类型。"""

    WORKER_INIT = "worker_init"
    ENUMERATE_DEVICES = "enumerate_devices"
    OPEN_SOURCE = "open_source"
    QUERY_CAPABILITIES = "query_capabilities"
    RESOLVE_CAPABILITIES = "resolve_capabilities"
    START_SCAN = "start_scan"
    STOP_SCAN = "stop_scan"
    CLOSE_SOURCE = "close_source"
    SHUTDOWN = "shutdown"


class EventType(StrEnum):
    """工作进程可以发送给主进程的事件类型。"""

    COMMAND_SUCCEEDED = "command_succeeded"
    COMMAND_FAILED = "command_failed"
    WORKER_READY = "worker_ready"
    DEVICE_LISTED = "device_listed"
    CAPABILITIES_QUERIED = "capabilities_queried"
    SCAN_STARTED = "scan_started"
    PAGE_STARTED = "page_started"
    PAGE_FILE_READY = "page_file_ready"
    SCAN_STOPPED = "scan_stopped"
    SCAN_COMPLETED = "scan_completed"
    SCAN_FAILED = "scan_failed"
    WORKER_HEARTBEAT = "worker_heartbeat"


COMMAND_TYPES = frozenset(item.value for item in CommandType)
EVENT_TYPES = frozenset(item.value for item in EventType)
MESSAGE_KINDS = frozenset({"command", "event"})

EVENTS_REQUIRING_COMMAND_ID = frozenset(
    {
        EventType.COMMAND_SUCCEEDED.value,
        EventType.COMMAND_FAILED.value,
        EventType.DEVICE_LISTED.value,
        EventType.CAPABILITIES_QUERIED.value,
        EventType.SCAN_STARTED.value,
        EventType.PAGE_STARTED.value,
        EventType.PAGE_FILE_READY.value,
        EventType.SCAN_STOPPED.value,
        EventType.SCAN_COMPLETED.value,
        EventType.SCAN_FAILED.value,
    }
)
EVENTS_REQUIRING_TASK_ID = frozenset(
    {
        EventType.SCAN_STARTED.value,
        EventType.PAGE_STARTED.value,
        EventType.PAGE_FILE_READY.value,
        EventType.SCAN_STOPPED.value,
        EventType.SCAN_COMPLETED.value,
        EventType.SCAN_FAILED.value,
    }
)
