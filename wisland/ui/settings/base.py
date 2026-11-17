# -*- coding: utf-8 -*-
"""设置页面基类。"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ...core.config import ConfigDraft
from ..theme import W, theme, ui_font
from ..widgets import ComboBox, DoubleSpinBox, LineEdit, PasswordEdit, ScrollArea, SettingRow, SpinBox, Switch, vbox


class SettingsPage(ScrollArea):
    """单个分类设置页：自带滚动容器（底部 24px 内边距），通过 load_from_draft / save_to_draft 与事务草稿交互。"""
    changed = QtCore.Signal()
    key: str = ""
    title: str = ""
    glyph: str = ""

    def __init__(self, ctx=None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._sec_titles: list[QtWidgets.QLabel] = []
        self._sec_descs: list[QtWidgets.QLabel] = []
        self._rows: dict[str, SettingRow] = {}
        theme.changed.connect(self._style_sections)

    def section(self, title: str, desc: str = "") -> QtWidgets.QLayout:
        lbl = QtWidgets.QLabel(title, self.inner)
        lbl.setFont(ui_font(14.5, W.DemiBold))
        lbl.setWordWrap(True)
        self._sec_titles.append(lbl)
        if desc:
            sub = QtWidgets.QLabel(desc, self.inner)
            sub.setFont(ui_font(12, W.Normal))
            sub.setWordWrap(True)
            self._sec_descs.append(sub)
            self._style_sections()
            return vbox(lbl, sub, spacing=2, margins=(4, 10, 4, 2))
        self._style_sections()
        return vbox(lbl, spacing=2, margins=(4, 10, 4, 2))

    def _style_sections(self):
        tc = theme.qss_color(theme.text())
        sc = theme.qss_color(theme.text3())
        for lbl in self._sec_titles:
            lbl.setStyleSheet(f"QLabel{{color:{tc};background:transparent;}}")
        for sub in self._sec_descs:
            sub.setStyleSheet(f"QLabel{{color:{sc};background:transparent;}}")

    def register_row(self, key: str, row: SettingRow) -> SettingRow:
        self._rows[key] = row
        return row

    def clear_errors(self):
        for row in self._rows.values():
            row.set_error("")

    def finish_layout(self, *widgets):
        # 滚动容器底部保留 24px 内边距，确保最后一组卡片可完整滚到底部操作栏上方
        lay = vbox(*widgets, None, spacing=10, margins=(20, 12, 20, 24))
        self.inner.setLayout(lay)
        self._hook_change_signals()

    def _hook_change_signals(self):
        for w in self.inner.findChildren(QtWidgets.QWidget):
            if isinstance(w, Switch):
                w.toggled.connect(lambda _=False: self.changed.emit())
            elif isinstance(w, ComboBox):
                w.value_changed.connect(lambda _=None: self.changed.emit())
            elif isinstance(w, (SpinBox, DoubleSpinBox)):
                w.valueChanged.connect(lambda _=None: self.changed.emit())
            elif isinstance(w, LineEdit):
                w.textChanged.connect(lambda _="": self.changed.emit())
            elif isinstance(w, PasswordEdit):
                w.textChanged.connect(lambda _="": self.changed.emit())

    def load_from_draft(self, draft: ConfigDraft):
        pass

    def save_to_draft(self, draft: ConfigDraft):
        pass
