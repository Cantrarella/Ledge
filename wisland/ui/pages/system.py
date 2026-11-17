# -*- coding: utf-8 -*-
"""右键弹出菜单样式与外部点击自动关闭。"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..theme import theme

Qt = QtCore.Qt


def _close_on_outside_click(m: QtWidgets.QMenu):
    """灵动岛不抢焦点，Qt 的"点外面关闭菜单"不会触发；这里轮询鼠标键，点到菜单外就关闭。"""
    import ctypes
    u32 = ctypes.WinDLL("user32")
    st = {"armed": False}
    t = QtCore.QTimer(m)
    t.setInterval(25)

    def down() -> bool:
        return any(u32.GetAsyncKeyState(k) & 0x8000 for k in (0x01, 0x02, 0x04))

    def tick():
        if not m.isVisible():
            t.stop()
            return
        if not st["armed"]:
            st["armed"] = not down()          # 先等打开菜单的那次按键松开
            return
        if down():
            pos = QtGui.QCursor.pos()
            menus = [w for w in QtWidgets.QApplication.topLevelWidgets()
                     if isinstance(w, QtWidgets.QMenu) and w.isVisible()]
            if not any(w.geometry().contains(pos) for w in menus):
                m.close()
                t.stop()
    t.timeout.connect(tick)
    m.aboutToShow.connect(lambda: (st.update(armed=False), t.start()))
    m.aboutToHide.connect(t.stop)


def styled_menu(parent=None) -> QtWidgets.QMenu:
    m = QtWidgets.QMenu(parent)
    _close_on_outside_click(m)
    m.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    m.setWindowFlag(Qt.WindowType.FramelessWindowHint)
    m.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint)
    bg, fg, sel = theme.menu_qss()
    m.setStyleSheet(
        f"QMenu{{background:{bg};border-radius:14px;padding:6px;color:{fg};font-family:'HarmonyOS Sans','Microsoft YaHei UI';font-size:13px}}"
        f"QMenu::item{{padding:6px 22px 6px 14px;border-radius:9px}}"
        f"QMenu::item:selected{{background:{sel}}}"
        f"QMenu::item:disabled{{color:gray}}"
        "QMenu::separator{height:6px;background:transparent}"
        "QMenu::indicator{width:0}")
    return m
