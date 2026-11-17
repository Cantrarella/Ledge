# -*- coding: utf-8 -*-
from __future__ import annotations

from PySide6 import QtWidgets


class Page(QtWidgets.QWidget):
    key = "page"
    glyph = ""
    title = "页面"
    height = 300
    panel_width: int | None = None
    panel_height: int | None = None

    def __init__(self, ctx=None):
        super().__init__()
        self.ctx = ctx
        self.island = None
        self.active = False

    def preferred_panel_width(self) -> int | None:
        return self.panel_width

    def preferred_panel_height(self) -> int | None:
        return self.panel_height

    def preferred_height(self) -> int:
        return self.height

    def set_height(self, h: int):
        if h != self.height:
            self.height = h
            if self.island is not None:
                self.island.page_size_changed()

    def on_show(self):
        self.active = True

    def on_hide(self):
        self.active = False

    def want_focus(self):
        if self.island is not None:
            self.island.request_focus()
