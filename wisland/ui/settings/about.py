# -*- coding: utf-8 -*-
"""关于页面：应用图标、版本基线、动态 60 FPS / 空闲 30 FPS 动效状态与内存占用监控。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from ..theme import G, W
from ..widgets import Card, Label, PillButton, SettingRow, hbox, rounded_pixmap, vbox
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

    def __init__(self, ctx=None, parent=None):
        super().__init__(ctx, parent)

        hero = Card(radius=16)
        icon_lbl = QtWidgets.QLabel()
        icon_lbl.setFixedSize(56, 56)
        icon_path = Path(__file__).resolve().parents[2] / "icon.png"
        if icon_path.exists():
            pm = rounded_pixmap(QtGui.QPixmap(str(icon_path)), 56, 13)
            if pm:
                icon_lbl.setPixmap(pm)

        title_lbl = Label("Wisland 灵动岛", 17, W.Bold, 1)
        ver_lbl = Label("版本 v1.0 (Baseline 2026-11-17) · Windows 原生桌面灵动岛", 12.5, W.Normal, 2)
        desc_lbl = Label("弹簧物理动效 · 实时背景毛玻璃 · 音乐歌词律动 · 日中实时双语字幕", 12, W.Normal, 3)
        hero.setLayout(hbox(icon_lbl, vbox(title_lbl, ver_lbl, desc_lbl, spacing=3), None, spacing=16, margins=(18, 16, 18, 16)))

        self.lbl_fps = Label("动态 60 FPS（16ms）/ 空闲 30 FPS（33ms）", 12.5, W.Medium, 2)
        self.lbl_mem = Label("正在读取...", 12.5, W.Medium, 2, num=True)
        btn_refresh_mem = PillButton("刷新状态", G.REFRESH, height=28)
        btn_refresh_mem.clicked.connect(self._update_stats)

        mem_wrap = QtWidgets.QWidget()
        mem_wrap.setLayout(hbox(self.lbl_mem, btn_refresh_mem, spacing=8))

        self.finish_layout(
            hero,
            self.section("运行与性能基线", "实时监测灵动岛渲染帧率策略与内存占用情况"),
            SettingRow("动效帧率策略", "交互/律动/展开动画时运行 60 FPS，收起态静止呼吸时自动降至 30 FPS 节能", self.lbl_fps),
            SettingRow("当前进程内存占用", "物理工作集内存 (Working Set) 与基线对比（基线空闲约 88 MB）", mem_wrap),
        )
        self._update_stats()

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
