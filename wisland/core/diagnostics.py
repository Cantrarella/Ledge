# -*- coding: utf-8 -*-
"""崩溃日志捕获与脱敏诊断包导出工具。"""
from __future__ import annotations

import faulthandler
import os
import platform
import re
import sys
import threading
import time
import traceback
import zipfile
from pathlib import Path

from PySide6 import QtCore, QtGui

from .branding import APP_VERSION_TAG, DISPLAY_NAME

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_USER_PATH_RE = re.compile(r"([A-Za-z]:\\Users\\)([^\\/\s]+)", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|cookie|password|secret|authorization)\b\s*[:=]\s*([^\s,;]+)"
)

_CRASH_STATE = {
    "installed": False,
    "fault_fp": None,
}


def sanitize_text(text: str) -> str:
    """对路径用户名、邮箱、Token 等敏感片段进行脱敏处理。"""
    out = str(text or "")
    out = _USER_PATH_RE.sub(r"\1<USER>", out)
    out = _EMAIL_RE.sub("<EMAIL>", out)
    out = _SECRET_RE.sub(lambda m: f"{m.group(1)}=<REDACTED>", out)
    return out


def rotate_log_if_needed(log_path: Path, max_bytes: int = 2 * 1024 * 1024) -> None:
    """当主日志文件超过阈值时轮转为 `.1` 备份，避免无限增长。"""
    try:
        if log_path.is_file() and log_path.stat().st_size > max_bytes:
            bak = log_path.with_suffix(log_path.suffix + ".1")
            if bak.exists():
                bak.unlink()
            log_path.replace(bak)
    except Exception:  # noqa: BLE001
        pass


def _prune_old_crashes(data_dir: Path, keep: int = 10) -> None:
    """仅保留最近 keep 份 Python 崩溃日志。"""
    try:
        files = sorted(
            data_dir.glob("crash-2*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in files[keep:]:
            old.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def write_crash_report(
    data_dir: Path,
    log_path: Path,
    exc_type,
    exc_value,
    exc_tb,
    context: str = "main",
) -> Path | None:
    """将未捕获异常堆栈写入独立崩溃日志并追加到主日志。"""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        crash_file = data_dir / f"crash-{stamp}.log"
        tb_txt = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        header = (
            f"=== {DISPLAY_NAME} {APP_VERSION_TAG} Crash Report ({context}) ===\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"OS: {platform.platform()}\n"
            f"Python: {sys.version.splitlines()[0]}\n"
            f"Frozen: {getattr(sys, 'frozen', False)}\n\n"
        )
        body = sanitize_text(header + tb_txt)
        crash_file.write_text(body, encoding="utf-8")
        with open(log_path, "a", encoding="utf-8") as fp:
            fp.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] UNCAUGHT ({context}):\n{body}\n")
        _prune_old_crashes(data_dir, keep=10)
        return crash_file
    except Exception:  # noqa: BLE001
        return None


def setup_crash_logging(data_dir: Path, log_path: Path) -> None:
    """启用原生 faulthandler 与 Python 主/子线程未捕获异常落盘。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    rotate_log_if_needed(log_path)
    if _CRASH_STATE["installed"]:
        return
    _CRASH_STATE["installed"] = True

    try:
        fault_path = data_dir / "crash-native.log"
        rotate_log_if_needed(fault_path, max_bytes=512 * 1024)
        fp = open(fault_path, "a", encoding="utf-8")
        _CRASH_STATE["fault_fp"] = fp
        faulthandler.enable(file=fp, all_threads=True)
    except Exception:  # noqa: BLE001
        pass

    prev_sys_hook = sys.excepthook
    prev_thr_hook = getattr(threading, "excepthook", None)

    def _sys_hook(exc_type, exc_value, exc_tb):
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            write_crash_report(data_dir, log_path, exc_type, exc_value, exc_tb, context="main")
        if callable(prev_sys_hook):
            prev_sys_hook(exc_type, exc_value, exc_tb)

    def _thr_hook(args):
        if not issubclass(args.exc_type, (KeyboardInterrupt, SystemExit)):
            tname = getattr(args.thread, "name", "thread")
            write_crash_report(data_dir, log_path, args.exc_type, args.exc_value, args.exc_traceback, context=f"thread:{tname}")
        if callable(prev_thr_hook):
            prev_thr_hook(args)

    sys.excepthook = _sys_hook
    if hasattr(threading, "excepthook"):
        threading.excepthook = _thr_hook


def collect_diagnostics_summary(cfg_data: dict | None, data_dir: Path) -> str:
    """生成不含任何邮箱、密钥、Cookie 或本地敏感路径的诊断摘要文本。"""
    cfg_data = cfg_data or {}
    isl = cfg_data.get("island") or {}
    pages = cfg_data.get("pages") or {}
    sub = cfg_data.get("subtitle") or {}

    screens_info: list[str] = []
    if QtGui.QGuiApplication.instance() is not None:
        for i, scr in enumerate(QtGui.QGuiApplication.screens()):
            g = scr.geometry()
            screens_info.append(f"#{i}: {g.width()}x{g.height()} (dpr={scr.devicePixelRatio():.2f})")

    crashes = sorted(
        list(data_dir.glob("crash-*.log")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:5]

    lines = [
        f"{DISPLAY_NAME} Diagnostics Summary",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"App Version: {APP_VERSION_TAG}",
        f"Frozen: {bool(getattr(sys, 'frozen', False))}",
        f"OS: {platform.platform()}",
        f"Python: {sys.version.splitlines()[0]}",
        f"Qt / PySide6: {QtCore.qVersion()}",
        f"Screens: {', '.join(screens_info) if screens_info else 'N/A'}",
        "",
        "[Config Snapshot (Sanitized)]",
        f"schema_version: {cfg_data.get('schema_version')}",
        f"island.theme: {isl.get('theme')} | edge: {isl.get('edge')} | compact_view: {isl.get('compact_view')}",
        f"island.clock_style: {isl.get('clock_style')} | clock_color: {isl.get('clock_color')} | compact_weather: {isl.get('compact_weather')}",
        f"pages.order: {pages.get('order')}",
        f"subtitle.enabled: {sub.get('enabled')} | source: {sub.get('source')}",
        f"crash_logs: {[p.name for p in crashes]}",
    ]
    return sanitize_text("\n".join(lines) + "\n")


def export_diagnostics_zip(dest_zip: Path, cfg_data: dict | None, data_dir: Path) -> Path:
    """将脱敏后的诊断摘要、主日志尾部及崩溃日志导出为 zip（绝不包含 config.json 与凭据）。"""
    dest_zip = Path(dest_zip)
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    summary = collect_diagnostics_summary(cfg_data, data_dir)

    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("diagnostics.txt", summary.encode("utf-8"))
        main_log = data_dir / "wisland.log"
        if main_log.is_file():
            raw = main_log.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
            zf.writestr("wisland.tail.log", sanitize_text("\n".join(raw)).encode("utf-8"))
        for cf in sorted(data_dir.glob("crash-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
            txt = cf.read_text(encoding="utf-8", errors="replace")
            zf.writestr(cf.name, sanitize_text(txt).encode("utf-8"))
    return dest_zip
