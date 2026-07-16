import platform
import struct
import sys


def test_python_runtime_is_exactly_3_12_13():
    assert sys.version_info[:3] == (3, 12, 13)


def test_python_runtime_is_64_bit_windows():
    assert platform.system() == "Windows"
    assert struct.calcsize("P") * 8 == 64


def test_pytwain_can_be_imported():
    import twain

    assert twain is not None
