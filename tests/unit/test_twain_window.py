"""TWAIN 隐藏 Windows 消息窗口测试。"""

from __future__ import annotations

import os

import pytest


@pytest.mark.skipif(os.name != "nt", reason="TWAIN 消息窗口只在 Windows 上运行")
def test_message_window_exposes_hidden_tk_parent_window():
    from app.scanner.twain_window import TwainMessageWindow

    window = TwainMessageWindow()
    parent = window.open()
    try:
        assert hasattr(parent, "winfo_id")
        assert parent.winfo_id() > 0
        assert not bool(parent.winfo_viewable())
        assert window.hwnd == parent.winfo_id()
    finally:
        window.close()


def test_message_window_close_is_idempotent():
    from app.scanner.twain_window import TwainMessageWindow

    window = TwainMessageWindow()
    window.close()
    window.close()
