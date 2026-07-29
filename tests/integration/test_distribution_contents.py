"""Task 13 便携式 Windows 交付目录内容测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def distribution_root() -> Path:
    configured_root = os.environ.get("ARCHIVE_SCAN_DELIVERY_ROOT")
    if configured_root:
        return Path(configured_root).resolve()
    return Path(__file__).resolve().parents[2] / "ArchiveScanService"


def test_distribution_contains_runtime_and_deployment_files(
    distribution_root: Path,
):
    required_paths = (
        "runtime/python.exe",
        "runtime/pythonw.exe",
        "app",
        "run.py",
        "启动服务.cmd",
        "启动服务-调试.cmd",
        "部署流程.md",
        "故障排查.md",
        "版本信息.txt",
        "requirements.lock",
        "source-offer.txt",
        "licenses/GPL-2.0.txt",
        "licenses/THIRD-PARTY-NOTICES.md",
    )

    missing_paths = [
        relative_path
        for relative_path in required_paths
        if not (distribution_root / relative_path).exists()
    ]

    assert not missing_paths, (
        f"交付目录缺少文件或目录: {', '.join(missing_paths)}; "
        f"交付根目录: {distribution_root}"
    )


def test_distribution_has_no_development_path_or_editable_install(
    distribution_root: Path,
):
    editable_files = list(
        (distribution_root / "runtime" / "Lib" / "site-packages").glob(
            "__editable__*"
        )
    )
    site_packages = distribution_root / "runtime" / "Lib" / "site-packages"
    runtime_text_files = list(site_packages.rglob("*.pth")) + list(
        site_packages.rglob("direct_url.json")
    )
    conda_meta_history = distribution_root / "runtime" / "conda-meta" / "history"
    if conda_meta_history.is_file():
        runtime_text_files.append(conda_meta_history)
    development_root = str(Path(__file__).resolve().parents[2]).lower()
    development_path_hits = []
    for path in runtime_text_files:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if development_root in content:
            development_path_hits.append(str(path))

    assert not editable_files, f"交付运行时包含 editable 安装文件: {editable_files}"
    assert not development_path_hits, (
        f"交付运行时仍引用开发机路径: {development_path_hits}"
    )


def test_distribution_does_not_bundle_twain_dsm(
    distribution_root: Path,
):
    bundled_dsm = [
        path
        for path in distribution_root.rglob("TWAINDSM.DLL")
        if path.is_file()
    ]

    assert not bundled_dsm, f"交付目录不应擅自捆绑 TWAINDSM.DLL: {bundled_dsm}"
