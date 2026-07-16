"""工作进程设备枚举命令测试。"""

from __future__ import annotations

from queue import Queue


def test_twain_runtime_constructs_backend_in_worker(monkeypatch):
    import app.worker.process as worker_process
    from app.worker.process import TwainRuntime

    instances = []

    class FakeBackend:
        def __init__(self):
            instances.append(self)

        def close(self):
            return None

    monkeypatch.setattr(worker_process, "TwainBackend", FakeBackend)

    runtime = TwainRuntime()

    assert len(instances) == 1
    runtime.close()


def test_enumerate_devices_emits_device_listed_events():
    from app.scanner.twain_backend import TwainDevice
    from app.worker.messages import CommandMessage, decode_message
    from app.worker.process import _handle_command

    class FakeRuntime:
        def __init__(self):
            self.calls = 0

        def enumerate_devices(self):
            self.calls += 1
            return [
                TwainDevice(
                    device_id="twain-device-1",
                    manufacturer="KODAK",
                    product_family="i2000",
                    product_name="KODAK i2600 Scanner",
                    protocol_major=1,
                    protocol_minor=0,
                    architecture="x64",
                )
            ]

    runtime = FakeRuntime()
    event_queue = Queue()
    command = CommandMessage(
        command_id="cmd-enumerate-1",
        message_type="enumerate_devices",
    )

    active_scan, should_exit = _handle_command(
        command,
        event_queue,
        active_scan=None,
        runtime=runtime,
    )

    listed = decode_message(event_queue.get_nowait())
    succeeded = decode_message(event_queue.get_nowait())

    assert active_scan is None
    assert should_exit is False
    assert runtime.calls == 1
    assert listed.event_type == "device_listed"
    assert listed.command_id == "cmd-enumerate-1"
    assert listed.payload["productName"] == "KODAK i2600 Scanner"
    assert succeeded.event_type == "command_succeeded"
    assert succeeded.payload == {"count": 1}


def test_enumerate_devices_is_rejected_during_scan():
    from app.worker.messages import CommandMessage, ScanCommand, decode_message
    from app.worker.process import _handle_command

    class FakeRuntime:
        def enumerate_devices(self):
            raise AssertionError("扫描中不应调用设备枚举")

    event_queue = Queue()
    command = CommandMessage(
        command_id="cmd-enumerate-2",
        message_type="enumerate_devices",
    )
    active_scan = ScanCommand(
        command_id="cmd-scan-1",
        task_id="task-1",
        device_id="device-1",
        settings={},
    )

    result_scan, should_exit = _handle_command(
        command,
        event_queue,
        active_scan=active_scan,
        runtime=FakeRuntime(),
    )
    failed = decode_message(event_queue.get_nowait())

    assert result_scan == active_scan
    assert should_exit is False
    assert failed.event_type == "command_failed"
    assert failed.payload["errorCode"] == "SCANNER_BUSY"
