import platform
import struct


def test_python_runtime_is_64_bit_windows():
    assert platform.system() == "Windows"
    assert struct.calcsize("P") * 8 == 64


def test_pytwain_can_be_imported():
    import twain

    assert twain is not None
