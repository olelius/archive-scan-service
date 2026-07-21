"""pytwain 文件扫描适配器测试。"""

from __future__ import annotations


def test_read_image_info_uses_source_image_info():
    import twain

    from app.scanner.twain_backend import PytwainFileTransferSource

    class FakeSource:
        image_info = {"ImageWidth": 100, "ImageLength": 200}

    adapter = PytwainFileTransferSource(FakeSource(), object(), twain)

    assert adapter.read_image_info() == {"ImageWidth": 100, "ImageLength": 200}


def test_read_extended_image_info_calls_twain_dat_extimageinfo():
    import twain

    from app.scanner.twain_backend import PytwainFileTransferSource

    class FakeSource:
        def __init__(self):
            self.calls = []

        def _call(self, dg, dat, msg, buf):
            self.calls.append((dg, dat, msg, buf))
            return twain.constants.TWRC_SUCCESS

    source = FakeSource()
    adapter = PytwainFileTransferSource(source, object(), twain)

    adapter.read_extended_image_info()

    assert source.calls[0][:3] == (
        twain.constants.DG_IMAGE,
        twain.constants.DAT_EXTIMAGEINFO,
        0x8005,
    )
    assert source.calls[0][3] is not None


def test_abort_transfer_resets_pending_transfers():
    import twain

    from app.scanner.twain_backend import PytwainFileTransferSource

    class FakeSource:
        def __init__(self):
            self.calls = []

        def _end_all_xfers(self):
            self.calls.append("reset")

    source = FakeSource()
    adapter = PytwainFileTransferSource(source, object(), twain)

    adapter.abort_transfer()

    assert source.calls == ["reset"]


def test_capability_source_reads_tw_status_condition_and_data():
    import ctypes
    import twain

    from app.scanner.twain_backend import PytwainCapabilitySource

    class FakeSource:
        def _call(self, dg, dat, msg, buf):
            status = ctypes.cast(
                buf,
                ctypes.POINTER(twain.structs.TW_STATUS),
            ).contents
            status.ConditionCode = 3
            status.Data = 7
            return twain.constants.TWRC_SUCCESS

    adapter = object.__new__(PytwainCapabilitySource)
    adapter._source = FakeSource()
    adapter._twain = twain

    assert adapter.get_status() == {"conditionCode": 3, "data": 7}
