# -*- coding: utf-8 -*-
"""关于页面：应用图标、版本号、软件更新检查、崩溃诊断导出与性能监控。"""
from __future__ import annotations

import ctypes
import os
import threading
import webbrowser
from ctypes import wintypes
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from ...core.branding import ABOUT_TITLE, APP_VERSION_TAG, RELEASES_URL
from ...core.config import DATA_DIR, cfg
from ...core.diagnostics import export_diagnostics_zip
from ...services.update import check_latest_release
from ..theme import G, W
from ..widgets import Card, ConfirmDialog, Label, PillButton, SettingRow, hbox, rounded_pixmap, vbox
from .base import SettingsPage


def get_process_memory_mb() -> tuple[float, float]:
    """通过 Windows PSAPI 获取当前进程 WorkingSet 与 PrivateBytes (MB)。"""
    try:
        class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]
        pmc = PROCESS_MEMORY_COUNTERS_EX()
        pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
        hproc = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(hproc, ctypes.byref(pmc), pmc.cb):
            return round(pmc.WorkingSetSize / (1024 * 1024), 1), round(pmc.PrivateUsage / (1024 * 1024), 1)
    except Exception:  # noqa: BLE001
        pass
    return 0.0, 0.0


class AboutSettingsPage(SettingsPage):
    key = "about"
    title = "关于"
    glyph = G.PULSE

    _update_checked = QtCore.Signal(dict)

    def __init__(self, ctx=None, parent=None):
        super().__init__(ctx, parent)
        self._latest_release_url = RELEASES_URL
        self._checking_update = False
        self._update_checked.connect(self._on_update_result)

        hero = Card(radius=16)
        icon_lbl = QtWidgets.QLabel()
        icon_lbl.setFixedSize(56, 56)
        icon_path = Path(__file__).resolve().parents[2] / "icon.png"
        if icon_path.exists():
            pm = rounded_pixmap(QtGui.QPixmap(str(icon_path)), 56, 13)
            if pm:
                icon_lbl.setPixmap(pm)

        title_lbl = Label(ABOUT_TITLE, 17, W.Bold, 1)
        ver_lbl = Label(f"版本 {APP_VERSION_TAG} · Windows 原生桌面灵动岛", 12.5, W.Normal, 2)
        desc_lbl = Label("弹簧物理动效 · 实时背景毛玻璃 · 音乐歌词律动 · 日中实时双语字幕", 12, W.Normal, 3)
        hero.setLayout(hbox(icon_lbl, vbox(title_lbl, ver_lbl, desc_lbl, spacing=3), None, spacing=16, margins=(18, 16, 18, 16)))

        self.lbl_fps = Label("动态 60 FPS（16ms）/ 空闲 30 FPS（33ms）", 12.5, W.Medium, 2)
        self.lbl_mem = Label("正在读取...", 12.5, W.Medium, 2, num=True)
        btn_refresh_mem = PillButton("刷新状态", G.REFRESH, height=28)
        btn_refresh_mem.clicked.connect(self._update_stats)

        mem_wrap = QtWidgets.QWidget()
        mem_wrap.setLayout(hbox(self.lbl_mem, btn_refresh_mem, spacing=8))

        self.lbl_update = Label(f"当前版本 {APP_VERSION_TAG}（未检查）", 12.5, W.Medium, 2)
        self.btn_check_update = PillButton("检查更新", G.REFRESH, height=28)
        self.btn_check_update.clicked.connect(self._start_update_check)
        self.btn_open_release = PillButton("前往下载", G.OPEN, height=28)
        self.btn_open_release.clicked.connect(lambda: webbrowser.open(self._latest_release_url))
        self.btn_open_release.setVisible(False)
        update_wrap = QtWidgets.QWidget()
        update_wrap.setLayout(hbox(self.lbl_update, self.btn_check_update, self.btn_open_release, spacing=8))

        btn_export_diag = PillButton("导出诊断包...", G.OPEN, height=28)
        btn_export_diag.clicked.connect(self._export_diagnostics)
        btn_open_logs = PillButton("打开日志目录", G.OPEN, height=28)
        btn_open_logs.clicked.connect(lambda: os.startfile(str(DATA_DIR)))  # noqa: S606
        diag_wrap = QtWidgets.QWidget()
        diag_wrap.setLayout(hbox(btn_export_diag, btn_open_logs, spacing=8))

        self.finish_layout(
            hero,
            self.section("软件更新", "主动连接 GitHub Releases 检查新版本（不后台静默上传任何本机数据）"),
            SettingRow("版本检查", f"对比 GitHub 仓库最新 Release（当前 {APP_VERSION_TAG}）", update_wrap),
            self.section("崩溃日志与诊断", "导出脱敏运行环境与崩溃日志，自动排除邮箱、密钥和本地密码等敏感数据"),
            SettingRow("诊断信息与日志", str(DATA_DIR), diag_wrap),
            self.section("运行与性能基线", "实时监测灵动岛渲染帧率策略与内存占用情况"),
            SettingRow("动效帧率策略", "交互/律动/展开动画时运行 60 FPS，收起态静止呼吸时自动降至 30 FPS 节能", self.lbl_fps),
            SettingRow("当前进程内存占用", "物理工作集内存 (Working Set) 与基线对比（基线空闲约 88 MB）", mem_wrap),
        )
        self._update_stats()

    def _start_update_check(self):
        if self._checking_update:
            return
        self._checking_update = True
        self.btn_check_update.setEnabled(False)
        self.lbl_update.setText("正在连接 GitHub 检查更新...")

        def _worker():
            res = check_latest_release()
            self._update_checked.emit(res)

        threading.Thread(target=_worker, name="wisland-update-check", daemon=True).start()

    def _on_update_result(self, res: dict):
        self._checking_update = False
        self.btn_check_update.setEnabled(True)
        self._latest_release_url = str(res.get("release_url") or RELEASES_URL)
        if not res.get("ok"):
            self.lbl_update.setText(str(res.get("error") or "检查更新失败"))
            self.btn_open_release.setVisible(False)
            return
        if res.get("has_update"):
            latest = res.get("latest_version") or ""
            self.lbl_update.setText(f"发现新版本 {latest}")
            self.btn_open_release.setVisible(True)
        else:
            self.lbl_update.setText(str(res.get("message") or f"当前已是最新版本 ({APP_VERSION_TAG})"))
            self.btn_open_release.setVisible(False)

    def _export_diagnostics(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "导出脱敏诊断包", f"ledge-diagnostics-{APP_VERSION_TAG}.zip", "ZIP 压缩包 (*.zip)"
        )
        if not path:
            return
        try:
            export_diagnostics_zip(Path(path), cfg.data, DATA_DIR)
            ConfirmDialog.info(self.window(), "导出成功", f"诊断包已保存至：\n{path}\n\n已自动脱敏处理，不含密码与凭据。")
        except Exception as e:  # noqa: BLE001
            ConfirmDialog.info(self.window(), "导出失败", f"导出诊断包失败：{e}")

    def _update_stats(self):
        ws_mb, _ = get_process_memory_mb()
        isl = getattr(self.ctx, "island", None) if self.ctx else None
        if isl is not None:
            iv = isl.timer.interval()
            active = isl.timer.isActive()
            fps_now = f"当前计时器 {round(1000 / max(1, iv))} FPS ({iv}ms)" if active else "空闲静止（按需唤醒 / 60-30 FPS）"
            self.lbl_fps.setText(f"动态 60 FPS / 空闲 30 FPS（{fps_now}）")
        if ws_mb > 0:
            self.lbl_mem.setText(f"{ws_mb:.1f} MB (基线 ~88.1 MB)")
        else:
            self.lbl_mem.setText("正常")

    def load_from_draft(self, draft):
        self._update_stats()
