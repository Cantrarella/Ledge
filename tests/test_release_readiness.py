# -*- coding: utf-8 -*-
"""正式发版前验证：版本号、更新检查、崩溃诊断、配置升级兼容与彻底卸载模拟。"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import urllib.error
import zipfile
from io import BytesIO
from pathlib import Path
from unittest import mock

import pytest
from PySide6 import QtWidgets

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wisland.core.branding import APP_VERSION, APP_VERSION_TAG
from wisland.core.config import Config, validate_config
from wisland.core.diagnostics import (
    collect_diagnostics_summary,
    export_diagnostics_zip,
    sanitize_text,
    write_crash_report,
)
from wisland.services.update import check_latest_release, is_newer_version, parse_semver
from wisland.ui.settings.about import AboutSettingsPage


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def test_version_consistency_across_code_and_rc(qapp):
    assert APP_VERSION == "1.0.11"
    assert APP_VERSION_TAG == "v1.0.11"

    ver_file = Path(__file__).resolve().parents[1] / "build" / "version_info.txt"
    txt = ver_file.read_text(encoding="utf-8")
    expected_rc = f"{APP_VERSION}.0"
    assert f"u'{expected_rc}'" in txt

    page = AboutSettingsPage()
    assert APP_VERSION_TAG in page.lbl_update.text()


def test_update_check_semver_and_responses():
    assert parse_semver("v1.0.0") > parse_semver("v1.0.0-beta.1")
    assert is_newer_version("v1.0.1", "1.0.0") is True
    assert is_newer_version("v1.1.0", "1.0.0") is True
    assert is_newer_version("v1.0.0", "1.0.0") is False
    assert is_newer_version("v0.9.9", "1.0.0") is False
    assert is_newer_version("v1.0.0-rc1", "1.0.0") is False

    # 发现新版本响应
    fake_body = json.dumps({
        "tag_name": "v1.2.0",
        "html_url": "https://github.com/Cantrarella/Ledge/releases/tag/v1.2.0",
        "body": "Release notes",
        "published_at": "2026-11-20T00:00:00Z",
    }).encode("utf-8")

    class _FakeResp(BytesIO):
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    with mock.patch("urllib.request.urlopen", return_value=_FakeResp(fake_body)):
        res = check_latest_release(current_version="1.0.0")
        assert res["ok"] is True
        assert res["has_update"] is True
        assert res["latest_version"] == "v1.2.0"

    # 404 暂无 Release
    with mock.patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.HTTPError("url", 404, "Not Found", {}, None),
    ):
        res404 = check_latest_release(current_version="1.0.0")
        assert res404["ok"] is True
        assert res404["has_update"] is False

    # 网络异常优雅降级
    with mock.patch("urllib.request.urlopen", side_effect=OSError("timeout")):
        res_err = check_latest_release(current_version="1.0.0")
        assert res_err["ok"] is False
        assert "检查更新失败" in res_err["error"]


def test_crash_logging_and_sanitized_diagnostics(tmp_path: Path, qapp):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "wisland.log"

    # 模拟敏感路径、邮箱与 Token 脱敏
    raw = r"Error for user C:\Users\Alice\Desktop\secret.txt mail=alice@example.com api_key=sk-12345678"
    cleaned = sanitize_text(raw)
    assert "Alice" not in cleaned
    assert "alice@example.com" not in cleaned
    assert "sk-12345678" not in cleaned

    # 写主日志、崩溃日志与不应导出的隐私文件
    log_path.write_text(raw + "\n", encoding="utf-8")
    (data_dir / "accounts.json").write_text('{"secret": "123"}', encoding="utf-8")
    try:
        raise RuntimeError("boom alice@example.com")
    except RuntimeError as exc:
        cf = write_crash_report(data_dir, log_path, type(exc), exc, exc.__traceback__, context="test")
        assert cf is not None and cf.is_file()
        assert "alice@example.com" not in cf.read_text(encoding="utf-8")

    summary = collect_diagnostics_summary({"schema_version": 2, "island": {"theme": "dark"}}, data_dir)
    assert APP_VERSION_TAG in summary

    out_zip = tmp_path / "diag.zip"
    export_diagnostics_zip(out_zip, {"schema_version": 2}, data_dir)
    assert out_zip.is_file()
    with zipfile.ZipFile(out_zip) as zf:
        names = zf.namelist()
        assert "diagnostics.txt" in names
        assert "wisland.tail.log" in names
        assert "accounts.json" not in names
        assert "config.json" not in names
        tail = zf.read("wisland.tail.log").decode("utf-8")
        assert "Alice" not in tail
        assert "alice@example.com" not in tail
        assert "sk-12345678" not in tail


def test_legacy_config_upgrade_compatibility(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    legacy = {
        "island": {
            "theme": "sky",
            "edge": "bottom",
            "pinned": True,
        },
        "custom_user_extra": {"keep": 42},
    }
    cfg_file.write_text(json.dumps(legacy), encoding="utf-8")

    store = Config(path=cfg_file)
    data = store.data
    ok, errs, _ = validate_config(data, strict=True)
    assert ok is True and errs == []
    assert data["schema_version"] == 2
    assert data["island"]["theme"] == "sky"
    assert data["island"]["edge"] == "bottom"
    assert data["island"]["clock_style"] == "minimal"
    assert data["island"]["clock_color"] == "white"
    assert data["island"]["compact_weather"] is True
    assert data["custom_user_extra"] == {"keep": 42}


def test_clean_uninstall_simulation(tmp_path: Path):
    install_dir = tmp_path / "Ledge"
    (install_dir / "_internal").mkdir(parents=True)
    (install_dir / "data").mkdir(parents=True)
    (install_dir / "Ledge.exe").write_bytes(b"MZ")
    (install_dir / "config.json").write_text("{}", encoding="utf-8")
    (install_dir / "data" / "wisland.log").write_text("log", encoding="utf-8")
    (install_dir / "data" / "crash-20261120-000000.log").write_text("crash", encoding="utf-8")

    shutil.rmtree(install_dir)
    assert not install_dir.exists()
