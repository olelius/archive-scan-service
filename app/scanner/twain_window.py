"""TWAIN 工作进程使用的隐藏 Windows 消息窗口。"""

from __future__ import annotations

import os
from typing import Any


class TwainMessageWindow:
    """创建一个不显示界面的 Tk 根窗口供 TWAIN DSM 投递事件。"""

    def __init__(self) -> None:
        self._root: Any | None = None

    @property
    def hwnd(self) -> int:
        if self._root is None:
            raise RuntimeError("TWAIN消息窗口尚未创建")
        return int(self._root.winfo_id())

    def open(self) -> Any:
        if self._root is not None:
            return self._root
        if os.name != "nt":
            raise RuntimeError("TWAIN消息窗口只支持 Windows")
        try:
            from tkinter import Tk

            root = Tk()
        except Exception as exc:
            raise RuntimeError("无法创建 TWAIN 隐藏 Tk 消息窗口") from exc
        root.withdraw()
        self._root = root
        return root

    def close(self) -> None:
        root = self._root
        if root is None:
            return
        try:
            root.destroy()
        finally:
            self._root = None

    def __enter__(self) -> TwainMessageWindow:
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()


__all__ = ["TwainMessageWindow"]
