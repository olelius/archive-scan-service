"""工作进程命令和事件的可判别 JSON 消息模型。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from typing import Any, TypeAlias

from app.scanner.protocol import (
    CommandType,
    COMMAND_TYPES,
    EventType,
    EVENT_TYPES,
    EVENTS_REQUIRING_COMMAND_ID,
    EVENTS_REQUIRING_TASK_ID,
    MESSAGE_KINDS,
    PROTOCOL_VERSION,
)


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class MessageError(ValueError):
    """消息协议错误的基类。"""


class InvalidMessageError(MessageError):
    """消息结构或字段值不符合当前协议。"""


class MessageEncodeError(MessageError):
    """消息不能编码为标准 JSON。"""


class UnsupportedVersionError(MessageError):
    """消息使用了当前实现不支持的协议版本。"""


class UnknownMessageTypeError(MessageError):
    """消息类型不在当前协议注册表中。"""


UnsupportedProtocolVersionError = UnsupportedVersionError

__all__ = [
    "Command",
    "CommandMessage",
    "CommandType",
    "Event",
    "EventMessage",
    "EventType",
    "InvalidMessageError",
    "JsonValue",
    "Message",
    "MessageEncodeError",
    "MessageError",
    "ScanCommand",
    "StartScanCommand",
    "UnknownMessageTypeError",
    "UnsupportedProtocolVersionError",
    "UnsupportedVersionError",
    "WorkerCommand",
    "WorkerEvent",
    "decode_message",
    "encode_message",
]


def _copy_payload(
    payload: Mapping[str, JsonValue],
    *,
    error_type: type[MessageError],
) -> dict[str, JsonValue]:
    if not isinstance(payload, Mapping):
        raise error_type("payload 必须是 JSON 对象")

    copied: dict[str, JsonValue] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise error_type("payload 的字段名必须是字符串")
        copied[key] = value
    return copied


def _validate_json_value(value: Any, *, path: str = "payload") -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            raise MessageEncodeError(f"{path} 不能包含 NaN 或无穷浮点数")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise MessageEncodeError(f"{path} 的字段名必须是字符串")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise MessageEncodeError(f"{path} 包含不可序列化的 {type(value).__name__}")


def _validate_version_value(version: int, *, error_type: type[MessageError]) -> None:
    if isinstance(version, bool) or not isinstance(version, int):
        raise error_type("version 必须是整数")


def _validate_identifier(
    value: str,
    *,
    field_name: str,
    error_type: type[MessageError],
) -> None:
    if not isinstance(value, str) or not value:
        raise error_type(f"{field_name} 必须是非空字符串")


def _validate_event_context(
    event_type: str,
    command_id: str | None,
    task_id: str | None,
    *,
    error_type: type[MessageError],
) -> None:
    if event_type in EVENTS_REQUIRING_COMMAND_ID and command_id is None:
        raise error_type(f"{event_type} 必须包含 command_id")
    if event_type in EVENTS_REQUIRING_TASK_ID and task_id is None:
        raise error_type(f"{event_type} 必须包含 task_id")


def _validate_event_payload(
    event_type: str,
    payload: Mapping[str, JsonValue],
    *,
    error_type: type[MessageError],
) -> None:
    if event_type == "page_file_ready":
        path = payload.get("path")
        if not isinstance(path, str) or not path:
            raise error_type("page_file_ready 必须包含非空字符串 path")


@dataclass(frozen=True, slots=True)
class ScanCommand:
    """启动扫描命令的强类型模型。"""

    command_id: str
    task_id: str
    device_id: str
    settings: Mapping[str, JsonValue]
    version: int = PROTOCOL_VERSION
    message_type: str = field(default="start_scan", init=False)

    def __post_init__(self) -> None:
        _validate_identifier(
            self.command_id,
            field_name="command_id",
            error_type=MessageEncodeError,
        )
        _validate_identifier(
            self.task_id,
            field_name="task_id",
            error_type=MessageEncodeError,
        )
        _validate_identifier(
            self.device_id,
            field_name="device_id",
            error_type=MessageEncodeError,
        )
        _validate_version_value(self.version, error_type=MessageEncodeError)
        object.__setattr__(
            self,
            "settings",
            _copy_payload(self.settings, error_type=MessageEncodeError),
        )


@dataclass(frozen=True, slots=True)
class CommandMessage:
    """除启动扫描外的通用命令模型。"""

    command_id: str
    message_type: str
    task_id: str | None = None
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _validate_identifier(
            self.command_id,
            field_name="command_id",
            error_type=MessageEncodeError,
        )
        if self.task_id is not None:
            _validate_identifier(
                self.task_id,
                field_name="task_id",
                error_type=MessageEncodeError,
            )
        _validate_version_value(self.version, error_type=MessageEncodeError)
        object.__setattr__(
            self,
            "message_type",
            str(self.message_type),
        )
        object.__setattr__(
            self,
            "payload",
            _copy_payload(self.payload, error_type=MessageEncodeError),
        )


@dataclass(frozen=True, slots=True)
class EventMessage:
    """工作进程事件的通用模型。"""

    event_type: str
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    command_id: str | None = None
    task_id: str | None = None
    version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.command_id is not None:
            _validate_identifier(
                self.command_id,
                field_name="command_id",
                error_type=MessageEncodeError,
            )
        if self.task_id is not None:
            _validate_identifier(
                self.task_id,
                field_name="task_id",
                error_type=MessageEncodeError,
            )
        _validate_version_value(self.version, error_type=MessageEncodeError)
        object.__setattr__(self, "event_type", str(self.event_type))
        object.__setattr__(
            self,
            "payload",
            _copy_payload(self.payload, error_type=MessageEncodeError),
        )


Message: TypeAlias = ScanCommand | CommandMessage | EventMessage
Command = CommandMessage
Event = EventMessage
WorkerCommand = CommandMessage
WorkerEvent = EventMessage
StartScanCommand = ScanCommand


def _build_envelope(message: Message) -> dict[str, JsonValue]:
    if isinstance(message, ScanCommand):
        kind = "command"
        message_type = message.message_type
        command_id = message.command_id
        task_id = message.task_id
        payload: Mapping[str, JsonValue] = {
            "deviceId": message.device_id,
            "settings": message.settings,
        }
        version = message.version
    elif isinstance(message, CommandMessage):
        kind = "command"
        message_type = message.message_type
        if message_type == "start_scan":
            raise MessageEncodeError(
                "start_scan 必须使用 ScanCommand 模型"
            )
        command_id = message.command_id
        task_id = message.task_id
        payload = message.payload
        version = message.version
    elif isinstance(message, EventMessage):
        kind = "event"
        message_type = message.event_type
        command_id = message.command_id
        task_id = message.task_id
        payload = message.payload
        version = message.version
    else:
        raise MessageEncodeError(
            f"不支持编码 {type(message).__name__} 类型的消息"
        )

    _validate_version_value(version, error_type=MessageEncodeError)
    if version != PROTOCOL_VERSION:
        raise UnsupportedVersionError(f"不支持协议版本 {version}")
    if kind not in MESSAGE_KINDS:
        raise MessageEncodeError(f"不支持消息方向 {kind}")
    if kind == "command" and message_type not in COMMAND_TYPES:
        raise UnknownMessageTypeError(f"未知命令类型 {message_type}")
    if kind == "event" and message_type not in EVENT_TYPES:
        raise UnknownMessageTypeError(f"未知事件类型 {message_type}")
    if kind == "event":
        _validate_event_context(
            message_type,
            command_id,
            task_id,
            error_type=MessageEncodeError,
        )
    if not isinstance(payload, Mapping):
        raise MessageEncodeError("payload 必须是 JSON 对象")
    payload_dict = _copy_payload(payload, error_type=MessageEncodeError)
    _validate_json_value(payload_dict)
    if kind == "event":
        _validate_event_payload(
            message_type,
            payload_dict,
            error_type=MessageEncodeError,
        )

    envelope: dict[str, JsonValue] = {
        "version": version,
        "kind": kind,
        "type": message_type,
        "payload": payload_dict,
    }
    if command_id is not None:
        envelope["commandId"] = command_id
    if task_id is not None:
        envelope["taskId"] = task_id
    return envelope


def encode_message(message: Message) -> str:
    """将消息编码成 UTF-8 可传输的标准 JSON 字符串。"""

    envelope = _build_envelope(message)
    try:
        return json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise MessageEncodeError(f"消息无法编码为 JSON: {exc}") from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON 不允许常量 {value}")


def _decode_payload(payload: object) -> dict[str, JsonValue]:
    if not isinstance(payload, dict):
        raise InvalidMessageError("payload 必须是 JSON 对象")
    copied = _copy_payload(payload, error_type=InvalidMessageError)
    try:
        _validate_decoded_json_value(copied)
    except ValueError as exc:
        raise InvalidMessageError(str(exc)) from exc
    return copied


def _validate_decoded_json_value(value: Any, *, path: str = "payload") -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"{path} 不能包含 NaN 或无穷浮点数")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_decoded_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} 的字段名必须是字符串")
            _validate_decoded_json_value(item, path=f"{path}.{key}")
        return
    raise ValueError(f"{path} 包含非法 JSON 值")


def _read_optional_identifier(
    envelope: Mapping[str, object],
    key: str,
) -> str | None:
    value = envelope.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise InvalidMessageError(f"{key} 必须是非空字符串或 null")
    return value


def decode_message(raw: str | bytes | bytearray) -> Message:
    """校验并解码一条 JSON 消息。"""

    if not isinstance(raw, (str, bytes, bytearray)):
        raise InvalidMessageError("消息必须是字符串或 UTF-8 字节串")
    try:
        decoded = json.loads(raw, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise InvalidMessageError(f"消息不是合法 JSON: {exc}") from exc

    if not isinstance(decoded, dict):
        raise InvalidMessageError("消息顶层必须是 JSON 对象")

    allowed_keys = {
        "version",
        "kind",
        "type",
        "commandId",
        "taskId",
        "payload",
    }
    unknown_keys = set(decoded) - allowed_keys
    if unknown_keys:
        names = ", ".join(sorted(str(item) for item in unknown_keys))
        raise InvalidMessageError(f"消息包含未知字段: {names}")

    version = decoded.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise InvalidMessageError("version 必须是整数")
    if version != PROTOCOL_VERSION:
        raise UnsupportedVersionError(f"不支持协议版本 {version}")

    message_type = decoded.get("type")
    if not isinstance(message_type, str) or not message_type:
        raise InvalidMessageError("type 必须是非空字符串")
    is_command = message_type in COMMAND_TYPES
    is_event = message_type in EVENT_TYPES
    if not is_command and not is_event:
        raise UnknownMessageTypeError(f"未知消息类型 {message_type}")

    if "kind" not in decoded:
        raise InvalidMessageError("消息必须包含 kind")
    expected_kind = "command" if is_command else "event"
    kind = decoded["kind"]
    if not isinstance(kind, str) or kind not in MESSAGE_KINDS:
        raise InvalidMessageError("kind 必须是 command 或 event")
    if kind != expected_kind:
        raise InvalidMessageError(
            f"消息 kind={kind!r} 与 type={message_type!r} 不匹配"
        )

    command_id = _read_optional_identifier(decoded, "commandId")
    task_id = _read_optional_identifier(decoded, "taskId")
    if is_command and command_id is None:
        raise InvalidMessageError("命令必须包含 commandId")
    if is_event:
        _validate_event_context(
            message_type,
            command_id,
            task_id,
            error_type=InvalidMessageError,
        )

    payload = _decode_payload(decoded.get("payload"))
    if is_event:
        _validate_event_payload(
            message_type,
            payload,
            error_type=InvalidMessageError,
        )
    if message_type == "start_scan":
        if set(payload) != {"deviceId", "settings"}:
            raise InvalidMessageError(
                "start_scan 的 payload 必须只包含 deviceId 和 settings"
            )
        device_id = payload["deviceId"]
        settings = payload["settings"]
        if not isinstance(device_id, str) or not device_id:
            raise InvalidMessageError("start_scan.deviceId 必须是非空字符串")
        if not isinstance(settings, dict):
            raise InvalidMessageError("start_scan.settings 必须是 JSON 对象")
        if task_id is None:
            raise InvalidMessageError("start_scan 必须包含 taskId")
        return ScanCommand(
            command_id=command_id,
            task_id=task_id,
            device_id=device_id,
            settings=settings,
            version=version,
        )

    if is_command:
        return CommandMessage(
            command_id=command_id,
            message_type=message_type,
            task_id=task_id,
            payload=payload,
            version=version,
        )
    return EventMessage(
        event_type=message_type,
        command_id=command_id,
        task_id=task_id,
        payload=payload,
        version=version,
    )
