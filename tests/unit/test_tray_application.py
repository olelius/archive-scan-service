"""Task 12 托盘、单实例和有序退出测试。"""

from __future__ import annotations

from pathlib import Path
import os
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest


class MemoryMutexBackend:
    """只在单元测试中模拟 Windows 命名互斥体后端。"""

    def __init__(self) -> None:
        self._handles: dict[str, object] = {}
        self.closed: list[object] = []
        self.released: list[object] = []

    def create(self, name: str) -> tuple[object, bool]:
        if name in self._handles:
            return object(), True
        handle = object()
        self._handles[name] = handle
        return handle, False

    def release(self, handle: object) -> None:
        self.released.append(handle)
        for name, current in list(self._handles.items()):
            if current is handle:
                del self._handles[name]

    def close(self, handle: object) -> None:
        self.closed.append(handle)


class FakeGuard:
    def __init__(self, events: list[str], *, acquired: bool = True) -> None:
        self.events = events
        self.acquired = acquired
        self.release_count = 0

    def acquire(self) -> bool:
        self.events.append("mutex.acquire")
        return self.acquired

    def release(self) -> None:
        self.release_count += 1
        self.events.append("mutex.release")


class FakeContext:
    def __init__(self, events: list[str], *, ready: bool = True) -> None:
        self.events = events
        self.ready = ready
        self.close_count = 0

    def status(self) -> dict[str, object]:
        return {"ready": self.ready, "pid": 4242, "generation": 1}

    def close(self) -> None:
        self.close_count += 1
        self.events.append("context.close")


class FakeServer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.started = Event()
        self.should_exit = False
        self.force_exit = False
        self._stop = Event()

    def run(self) -> None:
        self.events.append("server.run")
        self.started.set()
        while not self.should_exit and not self.force_exit:
            self._stop.wait(0.01)
        self.events.append("server.return")


class FailingServer(FakeServer):
    def run(self) -> None:
        self.events.append("server.run")
        self.started.set()
        raise RuntimeError("server failed")


class SystemExitServer(FakeServer):
    def run(self) -> None:
        self.events.append("server.run")
        self.started.set()
        raise SystemExit(1)


class StuckServer(FakeServer):
    def run(self) -> None:
        self.events.append("server.run")
        self.started.set()
        while not self._stop.is_set():
            self._stop.wait(0.01)
        self.events.append("server.return")


class FakeIcon:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.application = None
        self.stop_count = 0

    def run(self) -> None:
        self.events.append("icon.run")
        assert self.application is not None
        assert self.application.server.started.wait(timeout=1)
        self.application.request_exit()

    def stop(self) -> None:
        self.stop_count += 1
        self.events.append("icon.stop")


def test_single_instance_guard_rejects_second_and_releases_first():
    from app.tray.application import SingleInstanceGuard

    backend = MemoryMutexBackend()
    name = f"Local\\ArchiveScanService-Test-{uuid4().hex}"
    first = SingleInstanceGuard(name=name, backend=backend)
    second = SingleInstanceGuard(name=name, backend=backend)

    assert first.acquire() is True
    assert second.acquire() is False

    second.release()
    first.release()
    assert len(backend.released) == 1
    assert len(backend.closed) == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows 命名互斥体只在 Windows 上验证")
def test_default_windows_mutex_rejects_second_instance():
    from app.tray.application import SingleInstanceGuard

    name = f"Local\\ArchiveScanService-Test-{uuid4().hex}"
    first = SingleInstanceGuard(name=name)
    second = SingleInstanceGuard(name=name)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
    finally:
        second.release()
        first.release()


def test_second_instance_does_not_start_server_or_icon(tmp_path: Path):
    from app.config import Settings
    from app.tray.application import TrayApplication

    events: list[str] = []
    guard = FakeGuard(events, acquired=False)

    def unexpected_server(_config):
        raise AssertionError("第二个实例不应创建 Server")

    def unexpected_icon(*_args):
        raise AssertionError("第二个实例不应创建 Icon")

    application = TrayApplication(
        settings=Settings(data_root=tmp_path),
        instance_guard=guard,
        server_factory=unexpected_server,
        icon_factory=unexpected_icon,
    )

    assert application.run() == 1
    assert events == ["mutex.acquire"]
    assert guard.release_count == 0


def test_tray_menu_shows_status_and_opens_data_directory(tmp_path: Path):
    from app.config import Settings
    from app.tray.application import TrayApplication

    events: list[str] = []
    context = FakeContext(events)
    application = TrayApplication(
        settings=Settings(data_root=tmp_path),
        application=SimpleNamespace(state=SimpleNamespace(context=context)),
        open_directory=lambda path: events.append(f"open:{path}"),
    )

    labels = [item.text for item in application.build_menu()]

    assert labels == ["服务状态：正常（Worker 4242）", "打开数据目录", "退出"]
    application.open_data_directory()
    assert events == [f"open:{tmp_path.resolve()}"]


def test_tray_exit_stops_server_before_context_and_mutex(tmp_path: Path):
    from app.config import Settings
    from app.tray.application import TrayApplication

    events: list[str] = []
    context = FakeContext(events)
    server = FakeServer(events)
    icon = FakeIcon(events)
    guard = FakeGuard(events)
    application = TrayApplication(
        settings=Settings(data_root=tmp_path),
        application=SimpleNamespace(state=SimpleNamespace(context=context)),
        instance_guard=guard,
        server_factory=lambda _config: server,
        icon_factory=lambda *_args: icon,
    )
    icon.application = application

    assert application.run() == 0

    assert events.index("server.return") < events.index("context.close")
    assert events.index("context.close") < events.index("mutex.release")
    assert server.should_exit is True
    assert context.close_count == 1
    assert guard.release_count == 1
    assert icon.stop_count == 1

    application.request_exit()
    application.shutdown()
    assert context.close_count == 1
    assert guard.release_count == 1
    assert icon.stop_count == 1


def test_unready_worker_status_is_visible(tmp_path: Path):
    from app.config import Settings
    from app.tray.application import TrayApplication

    context = FakeContext([], ready=False)
    application = TrayApplication(
        settings=Settings(data_root=tmp_path),
        application=SimpleNamespace(state=SimpleNamespace(context=context)),
    )

    assert application.status_text() == "服务状态：工作进程未就绪"


def test_server_thread_failure_returns_error_and_releases_resources(tmp_path: Path):
    from app.config import Settings
    from app.tray.application import TrayApplication

    events: list[str] = []
    context = FakeContext(events)
    server = FailingServer(events)
    icon = FakeIcon(events)
    guard = FakeGuard(events)
    application = TrayApplication(
        settings=Settings(data_root=tmp_path),
        application=SimpleNamespace(state=SimpleNamespace(context=context)),
        instance_guard=guard,
        server_factory=lambda _config: server,
        icon_factory=lambda *_args: icon,
    )
    icon.application = application

    assert application.run() == 1
    assert context.close_count == 1
    assert guard.release_count == 1


def test_server_system_exit_returns_error_and_releases_resources(tmp_path: Path):
    from app.config import Settings
    from app.tray.application import TrayApplication

    events: list[str] = []
    context = FakeContext(events)
    server = SystemExitServer(events)
    icon = FakeIcon(events)
    guard = FakeGuard(events)
    application = TrayApplication(
        settings=Settings(data_root=tmp_path),
        application=SimpleNamespace(state=SimpleNamespace(context=context)),
        instance_guard=guard,
        server_factory=lambda _config: server,
        icon_factory=lambda *_args: icon,
    )
    icon.application = application

    assert application.run() == 1
    assert context.close_count == 1
    assert guard.release_count == 1


def test_shutdown_keeps_mutex_when_server_thread_remains_alive(tmp_path: Path):
    from app.config import Settings
    from app.tray.application import TrayApplication

    events: list[str] = []
    context = FakeContext(events)
    server = StuckServer(events)
    icon = FakeIcon(events)
    guard = FakeGuard(events)
    application = TrayApplication(
        settings=Settings(data_root=tmp_path),
        application=SimpleNamespace(state=SimpleNamespace(context=context)),
        instance_guard=guard,
        server_factory=lambda _config: server,
        icon_factory=lambda *_args: icon,
        shutdown_timeout=0.01,
    )
    icon.application = application

    assert application.run() == 0
    assert context.close_count == 0
    assert guard.release_count == 0

    server._stop.set()
    assert application._server_thread is not None
    application._server_thread.join(timeout=1)
    assert not application._server_thread.is_alive()
