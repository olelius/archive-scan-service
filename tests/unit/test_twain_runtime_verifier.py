from scripts.verify_twain_runtime import _candidate_dsm_paths


def test_dsm_search_excludes_32_bit_syswow64(monkeypatch, tmp_path):
    system_root = tmp_path / "Windows"
    system32 = system_root / "System32"
    syswow64 = system_root / "SysWOW64"
    system32.mkdir(parents=True)
    syswow64.mkdir()
    system32_dsm = system32 / "TWAINDSM.DLL"
    syswow64_dsm = syswow64 / "TWAINDSM.DLL"
    system32_dsm.touch()
    syswow64_dsm.touch()

    monkeypatch.setenv("SystemRoot", str(system_root))
    monkeypatch.setenv("PATH", "")

    candidates = _candidate_dsm_paths()

    assert system32_dsm in candidates
    assert syswow64_dsm not in candidates
