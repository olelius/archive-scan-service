"""工作进程命令和事件消息协议测试。"""

import json
from pathlib import Path

import pytest


def test_scan_command_round_trip():
    from app.worker.messages import ScanCommand, decode_message, encode_message

    command = ScanCommand(
        command_id="cmd-1",
        task_id="task-1",
        device_id="device-1",
        settings={"ICAP_XRESOLUTION": 300},
    )

    decoded = decode_message(encode_message(command))

    assert decoded == command


def test_encoded_message_has_version_and_discriminator():
    from app.worker.messages import ScanCommand, encode_message

    envelope = json.loads(
        encode_message(
            ScanCommand(
                command_id="cmd-1",
                task_id="task-1",
                device_id="device-1",
                settings={},
            )
        )
    )

    assert envelope == {
        "version": 1,
        "kind": "command",
        "type": "start_scan",
        "commandId": "cmd-1",
        "taskId": "task-1",
        "payload": {"deviceId": "device-1", "settings": {}},
    }


def test_all_supported_command_types_round_trip():
    from app.scanner.protocol import COMMAND_TYPES
    from app.worker.messages import (
        CommandMessage,
        ScanCommand,
        decode_message,
        encode_message,
    )

    for message_type in COMMAND_TYPES:
        if message_type == "start_scan":
            command = ScanCommand(
                command_id="cmd-start_scan",
                task_id="task-1",
                device_id="device-1",
                settings={},
            )
        else:
            command = CommandMessage(
                command_id=f"cmd-{message_type}",
                message_type=message_type,
                payload={},
            )

        assert decode_message(encode_message(command)) == command


def test_all_supported_event_types_round_trip():
    from app.scanner.protocol import EVENT_TYPES
    from app.worker.messages import EventMessage, decode_message, encode_message

    for event_type in EVENT_TYPES:
        payload = (
            {"path": "tasks/task-1/page-1.jpg"}
            if event_type == "page_file_ready"
            else {"status": "ok"}
        )
        event = EventMessage(
            event_type=event_type,
            command_id="cmd-1",
            task_id="task-1",
            payload=payload,
        )

        assert decode_message(encode_message(event)) == event


def test_command_result_event_requires_command_id():
    from app.worker.messages import EventMessage, MessageEncodeError, encode_message

    event = EventMessage(event_type="command_failed", payload={})

    with pytest.raises(MessageEncodeError):
        encode_message(event)


def test_scan_lifecycle_event_requires_command_and_task_ids():
    from app.worker.messages import EventMessage, MessageEncodeError, encode_message

    missing_command_id = EventMessage(event_type="scan_started", task_id="task-1")
    missing_task_id = EventMessage(event_type="scan_started", command_id="cmd-1")

    with pytest.raises(MessageEncodeError):
        encode_message(missing_command_id)
    with pytest.raises(MessageEncodeError):
        encode_message(missing_task_id)


@pytest.mark.parametrize(
    "event_type,payload",
    [("command_failed", {}), ("scan_started", {})],
)
def test_decoding_event_without_required_context_is_rejected(event_type, payload):
    from app.worker.messages import InvalidMessageError, decode_message

    message = json.dumps(
        {
            "version": 1,
            "kind": "event",
            "type": event_type,
            "payload": payload,
        }
    )

    with pytest.raises(InvalidMessageError):
        decode_message(message)


def test_worker_lifecycle_event_can_exist_without_command_or_task_ids():
    from app.worker.messages import EventMessage, decode_message, encode_message

    event = EventMessage(event_type="worker_ready", payload={})

    assert decode_message(encode_message(event)) == event


def test_page_file_ready_requires_a_non_empty_path():
    from app.worker.messages import EventMessage, MessageEncodeError, encode_message

    event = EventMessage(
        event_type="page_file_ready",
        command_id="cmd-1",
        task_id="task-1",
        payload={},
    )

    with pytest.raises(MessageEncodeError):
        encode_message(event)


def test_page_file_ready_round_trip_preserves_path():
    from app.worker.messages import EventMessage, decode_message, encode_message

    event = EventMessage(
        event_type="page_file_ready",
        command_id="cmd-1",
        task_id="task-1",
        payload={"path": "tasks/task-1/page-1.jpg"},
    )

    assert decode_message(encode_message(event)) == event


def test_non_finite_json_number_is_rejected():
    from app.worker.messages import InvalidMessageError, decode_message

    message = (
        '{"version":1,"kind":"event","type":"worker_ready",'
        '"payload":{"value":1e400}}'
    )

    with pytest.raises(InvalidMessageError):
        decode_message(message)


def test_unknown_protocol_version_is_rejected():
    from app.worker.messages import UnsupportedVersionError, decode_message

    message = json.dumps(
        {
            "version": 99,
            "kind": "command",
            "type": "start_scan",
            "commandId": "cmd-1",
            "taskId": "task-1",
            "payload": {"deviceId": "device-1", "settings": {}},
        }
    )

    with pytest.raises(UnsupportedVersionError):
        decode_message(message)


def test_unknown_message_type_is_rejected():
    from app.worker.messages import UnknownMessageTypeError, decode_message

    message = json.dumps(
        {
            "version": 1,
            "kind": "command",
            "type": "not_supported",
            "commandId": "cmd-1",
            "payload": {},
        }
    )

    with pytest.raises(UnknownMessageTypeError):
        decode_message(message)


def test_message_kind_must_match_registered_type():
    from app.worker.messages import InvalidMessageError, decode_message

    message = json.dumps(
        {
            "version": 1,
            "kind": "event",
            "type": "start_scan",
            "commandId": "cmd-1",
            "taskId": "task-1",
            "payload": {"deviceId": "device-1", "settings": {}},
        }
    )

    with pytest.raises(InvalidMessageError):
        decode_message(message)


def test_message_kind_is_required():
    from app.worker.messages import InvalidMessageError, decode_message

    message = json.dumps(
        {
            "version": 1,
            "type": "worker_ready",
            "payload": {},
        }
    )

    with pytest.raises(InvalidMessageError):
        decode_message(message)


def test_non_string_message_kind_is_rejected_as_protocol_error():
    from app.worker.messages import InvalidMessageError, decode_message

    message = json.dumps(
        {
            "version": 1,
            "kind": [],
            "type": "worker_ready",
            "payload": {},
        }
    )

    with pytest.raises(InvalidMessageError):
        decode_message(message)


@pytest.mark.parametrize(
    "value",
    [Path("page.jpg"), b"jpeg-bytes", object()],
)
def test_non_json_payload_values_are_rejected(value):
    from app.worker.messages import CommandMessage, MessageEncodeError, encode_message

    command = CommandMessage(
        command_id="cmd-1",
        message_type="stop_scan",
        payload={"value": value},
    )

    with pytest.raises(MessageEncodeError):
        encode_message(command)


def test_invalid_json_and_missing_required_fields_are_rejected():
    from app.worker.messages import InvalidMessageError, decode_message

    with pytest.raises(InvalidMessageError):
        decode_message("not-json")

    with pytest.raises(InvalidMessageError):
        decode_message(
            json.dumps(
                {
                    "version": 1,
                    "kind": "command",
                    "type": "start_scan",
                    "payload": {},
                }
            )
        )


def test_scan_command_requires_json_object_settings():
    from app.worker.messages import InvalidMessageError, decode_message

    message = json.dumps(
        {
            "version": 1,
            "kind": "command",
            "type": "start_scan",
            "commandId": "cmd-1",
            "taskId": "task-1",
            "payload": {"deviceId": "device-1", "settings": [300]},
        }
    )

    with pytest.raises(InvalidMessageError):
        decode_message(message)


def test_start_scan_requires_the_typed_scan_command_model():
    from app.worker.messages import CommandMessage, MessageEncodeError, encode_message

    command = CommandMessage(
        command_id="cmd-1",
        message_type="start_scan",
        task_id="task-1",
        payload={"deviceId": "device-1", "settings": {}},
    )

    with pytest.raises(MessageEncodeError):
        encode_message(command)
