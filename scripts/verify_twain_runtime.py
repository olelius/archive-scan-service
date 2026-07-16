"""验证当前 Python 进程和 TWAIN 运行时的基础条件。"""

from __future__ import annotations

import ctypes.util
import os
import platform
import struct
import sys
from pathlib import Path


def _configure_utf8_output() -> None:
    """让 Windows 终端按 UTF-8 输出中文诊断信息。"""

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


def _candidate_dsm_paths() -> list[Path]:
    """返回不修改系统的 TWAINDSM.DLL 候选路径。"""

    candidates: list[Path] = []
    system_root = os.environ.get("SystemRoot")
    if system_root:
        # 64 位进程下，System32 是 64 位系统目录；SysWOW64 只包含 32 位 DLL。
        candidates.append(Path(system_root) / "System32" / "TWAINDSM.DLL")

    executable_dir = Path(sys.executable).resolve().parent
    candidates.append(executable_dir / "TWAINDSM.DLL")
    candidates.append(Path.cwd() / "TWAINDSM.DLL")

    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry:
            candidates.append(Path(entry) / "TWAINDSM.DLL")

    library_name = ctypes.util.find_library("TWAINDSM")
    if library_name:
        library_path = Path(library_name)
        if library_path.is_absolute():
            candidates.append(library_path)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate).lower()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(candidate)
    return unique


def main() -> int:
    """打印运行时信息，并在基础条件不满足时返回非零值。"""

    _configure_utf8_output()
    failures: list[str] = []
    pointer_bits = struct.calcsize("P") * 8
    print(f"操作系统：{platform.system()}")
    print(f"Python版本：{platform.python_version()}")
    print(f"Python路径：{Path(sys.executable).resolve()}")
    print(f"进程位数：{pointer_bits} 位")

    if platform.system() != "Windows":
        failures.append("当前操作系统不是 Windows")
    if pointer_bits != 64:
        failures.append("当前 Python 进程不是 64 位")

    try:
        import twain
    except ImportError as exc:
        print(f"pytwain 导入：失败（{exc}）")
        failures.append("pytwain 导入失败")
    else:
        print(f"pytwain 导入：成功（{Path(twain.__file__).resolve()}）")

    dsm_paths = [path for path in _candidate_dsm_paths() if path.is_file()]
    if dsm_paths:
        print("TWAINDSM.DLL：找到")
        for path in dsm_paths:
            print(f"  - {path.resolve()}")
    else:
        print("TWAINDSM.DLL：未找到")
        failures.append("未找到 64 位 TWAINDSM.DLL")

    if not failures:
        from app.scanner.twain_backend import TwainBackend, TwainBackendError

        try:
            with TwainBackend() as backend:
                devices = backend.enumerate_devices()
        except TwainBackendError as exc:
            print(f"TWAIN 设备枚举：失败（{exc.error_code}）")
            failures.append(exc.error_code)
        else:
            if devices:
                print(f"TWAIN 设备枚举：找到 {len(devices)} 个 Data Source")
                for device in devices:
                    print(
                        "  - "
                        f"{device.product_name} / {device.product_family} / "
                        f"{device.architecture} / "
                        f"{device.device_id}"
                    )
            else:
                print("TWAIN 设备枚举：未找到 Data Source")
                failures.append("TWAIN_SOURCE_NOT_FOUND")

    if failures:
        print("验证结论：失败")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("验证结论：通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
