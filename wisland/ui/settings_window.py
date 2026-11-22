# -*- coding: utf-8 -*-
"""独立 Qt 设置窗口：单实例、左侧分类导航、右侧顶栏 + 滚动设置卡片 + 独立底部操作栏。"""
from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from ..core.branding import DISPLAY_NAME, SETTINGS_TITLE
from ..core.config import ConfigDraft, cfg
from ..core.memory import trim_working_set
from .settings import (
    AboutSettingsPage,
    BatterySettingsPage,
    DataSettingsPage,
    GeneralSettingsPage,
    IslandPetSettingsPage,
    MailAccountsSettingsPage,
    NotifySettingsPage,
    PagesNavSettingsPage,
    QuotaSettingsPage,
    SubtitlesSettingsPage,
    ToolsLifeSettingsPage,
    WeatherSettingsPage,
)
from .theme import G, W, bind_theme_context, icon_font, settings_theme, ui_font
from .widgets import ConfirmDialog, Hoverable, Label, PillButton, hbox, painter, rounded_pixmap, vbox

Qt = QtCore.Qt

_TOOLTIP_MARK_START = "/* WISLAND_SETTINGS_TOOLTIP_START */"
_TOOLTIP_MARK_END = "/* WISLAND_SETTINGS_TOOLTIP_END */"


def _strip_tooltip_block(qss: str) -> str:
    """移除已有的设置窗口 Tooltip 标记段，保留其他全局样式不变。"""
    s = qss or ""
    while _TOOLTIP_MARK_START in s and _TOOLTIP_MARK_END in s:
        i0 = s.index(_TOOLTIP_MARK_START)
        i1 = s.index(_TOOLTIP_MARK_END, i0) + len(_TOOLTIP_MARK_END)
        s = (s[:i0].rstrip("\n") + "\n" + s[i1:].lstrip("\n")).strip("\n")
    return s


def _build_tooltip_block(dark: bool) -> str:
    if dark:
        bg, fg, bd = "#242630", "#F4F6FB", "rgba(255,255,255,36)"
    else:
        bg, fg, bd = "#FFFFFF", "#181A20", "rgba(0,0,0,28)"
    return (
        f"{_TOOLTIP_MARK_START}\n"
        "QTipLabel, QToolTip {\n"
        f"    background-color: {bg};\n"
        f"    color: {fg};\n"
        f"    border: 1px solid {bd};\n"
        "    border-radius: 6px;\n"
        "    padding: 4px 8px;\n"
        "}\n"
        f"{_TOOLTIP_MARK_END}"
    )


class _SettingsTooltipFilter(QtCore.QObject):
    """仅对设置窗口触发的 QTipLabel 局部应用主题样式，避免调用 QApplication.setStyleSheet 引发全应用重排卡顿。"""

    def __init__(self, win: SettingsWindow):
        super().__init__(win)
        self.win = win
        self._last_dark: bool | None = None
        self._applying: bool = False

    def _belongs_to_settings(self) -> bool:
        if not self.win.isVisible():
            return False
        w = QtWidgets.QApplication.widgetAt(QtGui.QCursor.pos())
        if w is not None:
            return w.window() is self.win
        return self.win.isActiveWindow()

    def apply_to_tip(self, tip: QtWidgets.QWidget, force: bool = False):
        if self._applying:
            return
        self._applying = True
        try:
            if self._belongs_to_settings():
                dark = bool(settings_theme.dark)
                if force or self._last_dark != dark or _TOOLTIP_MARK_START not in tip.styleSheet():
                    tip.setStyleSheet(_build_tooltip_block(dark))
                    self._last_dark = dark
            elif _TOOLTIP_MARK_START in tip.styleSheet():
                tip.setStyleSheet("")
                self._last_dark = None
        finally:
            self._applying = False

    def eventFilter(self, obj, ev):
        if ev.type() in (QtCore.QEvent.Type.Polish, QtCore.QEvent.Type.Show, QtCore.QEvent.Type.StyleChange):
            if isinstance(obj, QtWidgets.QWidget) and obj.metaObject().className() == "QTipLabel":
                self.apply_to_tip(obj)
        return False


LEAF_TO_GROUP: dict[str, tuple[str, str]] = {
    "general": ("appearance", "general"),
    "island_pet": ("appearance", "island_pet"),
    "pages_nav": ("pages", "pages_nav"),
    "notify": ("messages", "notify"),
    "mail_accounts": ("messages", "mail_accounts"),
    "weather": ("weather", "weather"),
    "quota": ("ai_content", "quota"),
    "subtitles": ("ai_content", "subtitles"),
    "battery": ("battery", "battery"),
    "tools_life": ("tools_life", "tools_life"),
    "data": ("data_about", "data"),
    "about": ("data_about", "about"),
}


class _SubTabButton(Hoverable):
    """组合页顶部二级标签按钮：紧凑胶囊态，不触发保存、不改变窗口尺寸。"""

    def __init__(self, key: str, title: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.title = title
        self.selected = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        fm = QtGui.QFontMetrics(ui_font(12.5, W.DemiBold))
        self.setFixedSize(max(68, fm.horizontalAdvance(title) + 26), 28)

    def set_selected(self, on: bool):
        if self.selected != on:
            self.selected = on
            self.update()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(e)

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.selected:
            bg = QtGui.QColor(settings_theme.on_color())
            bg.setAlphaF(0.20 if settings_theme.dark else 0.14)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, 8, 8)
            fg = settings_theme.text(0.98) if settings_theme.dark else settings_theme.on_color()
        else:
            if self._hover.x > 0.01:
                bg = QtGui.QColor(settings_theme.hover())
                bg.setAlphaF(bg.alphaF() * self._hover.x)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(bg)
                p.drawRoundedRect(r, 8, 8)
            fg = settings_theme.text2(0.92)
        if self.hasFocus():
            p.setPen(QtGui.QPen(settings_theme.on_color(), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 8, 8)
        p.setPen(fg)
        p.setFont(ui_font(12.5, W.DemiBold if self.selected else W.Normal))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.title)


class _PanelFrame(QtWidgets.QWidget):
    """自绘背景与 1px 边线的轻量面板容器，避免 setStyleSheet 触发全树 QSS 解析。"""

    def __init__(self, role: str, parent=None):
        super().__init__(parent)
        self._role = role

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        dark = settings_theme.dark
        sep = QtGui.QColor(255, 255, 255, 18) if dark else QtGui.QColor(0, 0, 0, 16)
        if self._role == "sidebar":
            p.fillRect(self.rect(), QtGui.QColor("#13141a" if dark else "#eaedf4"))
            p.fillRect(self.width() - 1, 0, 1, self.height(), sep)
        elif self._role == "header":
            p.fillRect(self.rect(), QtGui.QColor("#181920" if dark else "#f3f5f9"))
            p.fillRect(0, self.height() - 1, self.width(), 1, sep)
        else:
            p.fillRect(self.rect(), QtGui.QColor("#15161c" if dark else "#edf0f6"))
            p.fillRect(0, 0, self.width(), 1, sep)


class _TabBarFrame(QtWidgets.QWidget):
    """二级标签栏容器：自绘 1px 底部分隔线，避免 QFrame setStyleSheet 触发全树样式重算。"""

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        sep = QtGui.QColor(255, 255, 255, 14) if settings_theme.dark else QtGui.QColor(0, 0, 0, 12)
        p.fillRect(0, self.height() - 1, self.width(), 1, sep)


class SettingsGroupPage(QtWidgets.QWidget):
    """一级组合设置页：固定二级标签栏 + QStackedWidget 承载原有独立滚动的叶子页面。"""
    leaf_changed = QtCore.Signal(str)

    def __init__(
        self,
        key: str,
        glyph: str,
        title: str,
        tabs: list[tuple[str, QtWidgets.QWidget]],
        parent=None,
    ):
        super().__init__(parent)
        self.key = key
        self.glyph = glyph
        self.title = title
        self._theme_context = settings_theme
        self.sub_pages: list[QtWidgets.QWidget] = [pg for _, pg in tabs]
        self._leaf_keys: list[str] = [pg.key for pg in self.sub_pages]  # type: ignore[attr-defined]
        self._tab_buttons: dict[str, _SubTabButton] = {}

        self.tab_bar = _TabBarFrame(self)
        self.tab_bar.setFixedHeight(44)
        tab_items: list = []
        for tab_title, pg in tabs:
            lkey = pg.key  # type: ignore[attr-defined]
            btn = _SubTabButton(lkey, tab_title, self.tab_bar)
            btn.clicked.connect(lambda k=lkey: self.show_leaf(k))
            self._tab_buttons[lkey] = btn
            tab_items.append(btn)
        tab_items.append(None)
        self.tab_bar.setLayout(hbox(*tab_items, spacing=8, margins=(20, 8, 20, 6)))

        self.sub_stack = QtWidgets.QStackedWidget(self)
        for pg in self.sub_pages:
            self.sub_stack.addWidget(pg)

        self.setLayout(vbox(self.tab_bar, (self.sub_stack, 1), spacing=0, margins=(0, 0, 0, 0)))
        settings_theme.changed.connect(self.tab_bar.update)
        if self._leaf_keys:
            self.show_leaf(self._leaf_keys[0])

    def current_leaf(self) -> QtWidgets.QWidget:
        return self.sub_stack.currentWidget()

    def show_leaf(self, key: str) -> bool:
        if key not in self._leaf_keys:
            return False
        idx = self._leaf_keys.index(key)
        self.sub_stack.setCurrentIndex(idx)
        for k, btn in self._tab_buttons.items():
            btn.set_selected(k == key)
        self.leaf_changed.emit(key)
        return True


class _CategoryButton(Hoverable):
    """左侧分类导航项：选中态采用低饱和主色背景 + 左侧 3px 强调线，与普通悬停态明显区分。"""
    def __init__(self, key: str, glyph: str, title: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.glyph = glyph
        self.title = title
        self.selected = False
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(38)

    def set_selected(self, on: bool):
        if on != self.selected:
            self.selected = on
            self.update()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            e.accept()
            return
        super().keyPressEvent(e)

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self.selected:
            sel_bg = QtGui.QColor(settings_theme.on_color())
            sel_bg.setAlphaF(0.18 if settings_theme.dark else 0.14)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(sel_bg)
            p.drawRoundedRect(r, 10, 10)
            # 左侧 3px 强调线
            bar = QtCore.QRectF(r.left() + 3, r.center().y() - 8, 3.0, 16)
            p.setBrush(settings_theme.on_color())
            p.drawRoundedRect(bar, 1.5, 1.5)
        elif self._hover.x > 0.01:
            bg = QtGui.QColor(settings_theme.hover())
            bg.setAlphaF(bg.alphaF() * self._hover.x)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, 10, 10)

        if self.hasFocus():
            p.setPen(QtGui.QPen(settings_theme.on_color(), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 10, 10)

        fg = (
            settings_theme.on_color()
            if (self.selected and not settings_theme.dark)
            else (settings_theme.text(0.98) if self.selected else settings_theme.text2(0.92))
        )
        p.setPen(fg)
        p.setFont(icon_font(15))
        p.drawText(QtCore.QRectF(r.left() + 14, r.top(), 20, r.height()), Qt.AlignmentFlag.AlignCenter, self.glyph)
        p.setFont(ui_font(13.5, W.DemiBold if self.selected else W.Normal))
        p.drawText(QtCore.QRectF(r.left() + 40, r.top(), r.width() - 48, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.title)


class SettingsWindow(QtWidgets.QWidget):
    """独立普通窗口的 Wisland 设置中心（单实例，关闭窗口不退出主程序）。"""
    _instance: SettingsWindow | None = None
    _idle_release_ms: int = 20_000
    saved = QtCore.Signal()

    def __init__(self, ctx=None, config_obj=None, parent=None, initial_category: str = "general"):
        super().__init__(parent, Qt.WindowType.Window)
        self.ctx = ctx
        self.cfg_obj = config_obj or cfg
        self._loading = False
        self._saving = False
        self._idle_release_timer = QtCore.QTimer(self)
        self._idle_release_timer.setSingleShot(True)
        self._idle_release_timer.timeout.connect(self._on_idle_release)
        self.setUpdatesEnabled(False)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setWindowTitle(SETTINGS_TITLE)
        self.resize(820, 600)
        self.setMinimumSize(640, 480)

        icon_path = Path(__file__).resolve().parents[1] / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        self.draft: ConfigDraft = self.cfg_obj.create_draft()
        self._baseline_dict: dict = self.draft.to_dict()

        # 先同步设置窗口主题，再构建子页面与绑定上下文，避免初始化阶段重复整树刷样式
        settings_theme.set_mode(self.cfg_obj.get("settings_window.theme", "light"))

        # ---- 左侧导航栏（小型 logo + Wisland / 副标题“设置”） ----
        self.sidebar = _PanelFrame("sidebar", self)
        self.sidebar.setObjectName("settingsSidebar")
        self.sidebar.setFixedWidth(188)

        logo_lbl = QtWidgets.QLabel()
        logo_lbl.setFixedSize(28, 28)
        if icon_path.exists():
            pm = rounded_pixmap(QtGui.QPixmap(str(icon_path)), 28, 7)
            if pm:
                logo_lbl.setPixmap(pm)
        brand_title = Label(DISPLAY_NAME, 15, W.Bold, 1)
        brand_sub = Label("设置", 11.5, W.Normal, 3)
        brand_box = hbox(logo_lbl, vbox(brand_title, brand_sub, spacing=0), None, spacing=10, margins=(8, 4, 6, 10))

        gen_pg = GeneralSettingsPage(ctx, self)
        pet_pg = IslandPetSettingsPage(ctx, self)
        nav_pg = PagesNavSettingsPage(ctx, self)
        notify_pg = NotifySettingsPage(ctx, self)
        mail_pg = MailAccountsSettingsPage(ctx, self)
        weather_pg = WeatherSettingsPage(ctx, self)
        quota_pg = QuotaSettingsPage(ctx, self)
        bat_pg = BatterySettingsPage(ctx, self)
        sub_pg = SubtitlesSettingsPage(ctx, self)
        tl_pg = ToolsLifeSettingsPage(ctx, self)
        data_pg = DataSettingsPage(ctx, self, on_reload_draft=self._load_all_from_draft)
        about_pg = AboutSettingsPage(ctx, self)

        self.leaf_pages = [
            gen_pg,
            pet_pg,
            nav_pg,
            notify_pg,
            mail_pg,
            weather_pg,
            quota_pg,
            bat_pg,
            sub_pg,
            tl_pg,
            data_pg,
            about_pg,
        ]
        for lpg in self.leaf_pages:
            lpg.changed.connect(self._on_any_page_changed)

        grp_appearance = SettingsGroupPage(
            "appearance",
            G.SLIDERS,
            "外观与岛体",
            [("常规", gen_pg), ("岛体与表情", pet_pg)],
            self,
        )
        grp_messages = SettingsGroupPage(
            "messages",
            G.MAIL,
            "消息与邮件",
            [("通知", notify_pg), ("邮件账号", mail_pg)],
            self,
        )
        grp_ai = SettingsGroupPage(
            "ai_content",
            G.CPU,
            "AI 与字幕",
            [("AI 额度", quota_pg), ("实时字幕", sub_pg)],
            self,
        )
        grp_data_about = SettingsGroupPage(
            "data_about",
            G.GEAR,
            "数据与关于",
            [("数据维护", data_pg), ("关于", about_pg)],
            self,
        )
        grp_data_about.leaf_changed.connect(
            lambda k: about_pg.load_from_draft(self.draft) if k == "about" else None
        )

        # 一级导航固定 8 项：外观与岛体、页面与导航、消息与邮件、天气、AI 与字幕、设备电量、工具与生活、数据与关于
        self._group_specs: list[tuple[str, str, str, QtWidgets.QWidget]] = [
            ("appearance", G.SLIDERS, "外观与岛体", grp_appearance),
            ("pages", G.APPS, "页面与导航", nav_pg),
            ("messages", G.MAIL, "消息与邮件", grp_messages),
            ("weather", G.SUN, "天气", weather_pg),
            ("ai_content", G.CPU, "AI 与字幕", grp_ai),
            ("battery", G.BOLT, "设备电量", bat_pg),
            ("tools_life", G.CLIP, "工具与生活", tl_pg),
            ("data_about", G.GEAR, "数据与关于", grp_data_about),
        ]
        self.pages = [pg for _, _, _, pg in self._group_specs]

        self.nav_buttons: dict[str, _CategoryButton] = {}
        nav_items = [brand_box]
        for gkey, gglyph, gtitle, _pg in self._group_specs:
            btn = _CategoryButton(gkey, gglyph, gtitle, self.sidebar)
            btn.clicked.connect(lambda k=gkey: self.switch_category(k))
            self.nav_buttons[gkey] = btn
            nav_items.append(btn)
        nav_items.append(None)
        self.sidebar.setLayout(vbox(*nav_items, spacing=4, margins=(12, 14, 12, 14)))

        # ---- 右侧纵向三段式：顶部标题栏 + 可伸缩 QStackedWidget(QScrollArea) + 独立底部操作栏 ----
        self.header = _PanelFrame("header", self)
        self.header.setObjectName("settingsHeader")
        self.header.setFixedHeight(50)
        self.header_title = Label("外观与岛体", 16, W.Bold, 1)
        self.dirty_lbl = Label("", 12, W.DemiBold, 2)
        self.header.setLayout(hbox(self.header_title, None, self.dirty_lbl, spacing=10, margins=(24, 10, 24, 8)))

        self.stack = QtWidgets.QStackedWidget(self)
        for pg in self.pages:
            self.stack.addWidget(pg)

        self.status_lbl = Label("", 12, W.Medium, 2)
        self.btn_cancel = PillButton("取消", height=34)
        self.btn_cancel.clicked.connect(lambda: self.cancel_and_close(prompt_if_dirty=self.isVisible()))

        self.btn_save = PillButton("保存设置", G.CHECK, height=34, primary=True)
        self.btn_save.clicked.connect(self.save_settings)

        self.footer = _PanelFrame("footer", self)
        self.footer.setObjectName("settingsFooter")
        self.footer.setFixedHeight(58)
        self.footer.setLayout(hbox((self.status_lbl, 1), self.btn_cancel, self.btn_save, spacing=10, margins=(20, 10, 20, 12)))

        self._theme_context = settings_theme
        self._tooltip_filter: _SettingsTooltipFilter | None = None
        self._tooltip_qss_active: bool = False
        right_lay = vbox(self.header, (self.stack, 1), self.footer, spacing=0, margins=(0, 0, 0, 0))
        self.setLayout(hbox(self.sidebar, (right_lay, 1), spacing=0, margins=(0, 0, 0, 0)))

        bind_theme_context(self, settings_theme)
        settings_theme.changed.connect(self._apply_window_style)
        self._apply_window_style()
        self.switch_category(initial_category or "general")
        self._load_all_from_draft()
        self.setUpdatesEnabled(True)

    @classmethod
    def open_instance(
        cls,
        ctx=None,
        category: str | None = None,
        account_id: str | None = None,
    ) -> SettingsWindow:
        if cls._instance is None:
            cls._instance = cls(ctx=ctx, initial_category=category or "general")
        else:
            cls._instance.ctx = ctx
            cls._instance.reload_draft_from_config()
            if category:
                cls._instance.switch_category(category)
        if category == "quota" and account_id:
            quota_pg = next(
                (pg for pg in cls._instance.leaf_pages if isinstance(pg, QuotaSettingsPage)),
                None,
            )
            if quota_pg is not None:
                quota_pg.focus_account(account_id)
        cls._instance.show()
        cls._instance.raise_()
        cls._instance.activateWindow()
        return cls._instance

    def _apply_tooltip_style(self):
        app = QtWidgets.QApplication.instance()
        if app is None or not self._tooltip_qss_active or self._tooltip_filter is None:
            return
        for tl in app.topLevelWidgets():
            if tl.metaObject().className() == "QTipLabel":
                self._tooltip_filter.apply_to_tip(tl, force=True)

    def _install_tooltip_style(self):
        app = QtWidgets.QApplication.instance()
        if app is None or self._tooltip_qss_active:
            return
        if self._tooltip_filter is None:
            self._tooltip_filter = _SettingsTooltipFilter(self)
        app.installEventFilter(self._tooltip_filter)
        self._tooltip_qss_active = True
        self._apply_tooltip_style()

    def _restore_tooltip_style(self):
        if not self._tooltip_qss_active:
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            if self._tooltip_filter is not None:
                try:
                    app.removeEventFilter(self._tooltip_filter)
                except Exception:
                    pass
            for tl in app.topLevelWidgets():
                if tl.metaObject().className() == "QTipLabel" and _TOOLTIP_MARK_START in tl.styleSheet():
                    tl.setStyleSheet("")
        self._tooltip_qss_active = False

    def showEvent(self, e):
        self._idle_release_timer.stop()
        self._install_tooltip_style()
        super().showEvent(e)

    def hideEvent(self, e):
        self._restore_tooltip_style()
        super().hideEvent(e)
        if SettingsWindow._instance is self and self._idle_release_ms > 0:
            self._idle_release_timer.start(self._idle_release_ms)

    def _on_idle_release(self):
        if SettingsWindow._instance is not self or self.isVisible() or self._saving:
            return
        try:
            if self.is_dirty():
                return
        except Exception:
            return
        SettingsWindow._instance = None
        self.deleteLater()
        QtCore.QTimer.singleShot(120, lambda: trim_working_set(collect_gc=True))

    def _apply_window_style(self):
        self.dirty_lbl.set_color(settings_theme.on_color())
        self._apply_tooltip_style()
        self.sidebar.update()
        self.header.update()
        self.footer.update()
        self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        bg = QtGui.QColor("#181920" if settings_theme.dark else "#f3f5f9")
        p.fillRect(self.rect(), bg)

    def switch_category(self, key: str):
        group_key, leaf_key = LEAF_TO_GROUP.get(key, (key, ""))
        for i, (gkey, _gglyph, gtitle, pg) in enumerate(self._group_specs):
            on = (gkey == group_key)
            if gkey in self.nav_buttons:
                self.nav_buttons[gkey].set_selected(on)
            if on:
                self.header_title.setText(gtitle)
                self.stack.setCurrentIndex(i)
                if isinstance(pg, SettingsGroupPage):
                    if leaf_key:
                        pg.show_leaf(leaf_key)
                    cur = pg.current_leaf()
                    if hasattr(cur, "ensure_built"):
                        cur.ensure_built()  # type: ignore[attr-defined]
                else:
                    if hasattr(pg, "ensure_built"):
                        pg.ensure_built()  # type: ignore[attr-defined]
                    if leaf_key == "about" or getattr(pg, "key", "") == "about":
                        pg.load_from_draft(self.draft)  # type: ignore[attr-defined]

    def reload_draft_from_config(self):
        """放弃未保存草稿，重新从当前生效配置创建新草稿并刷新控件。"""
        self.draft = self.cfg_obj.create_draft()
        self._baseline_dict = self.draft.to_dict()
        self.status_lbl.setText("")
        self._load_all_from_draft()

    def _load_all_from_draft(self):
        self._loading = True
        try:
            for pg in self.leaf_pages:
                if getattr(pg, "_built", True):
                    pg.load_from_draft(self.draft)
                else:
                    pg.defer_load_draft(self.draft)  # type: ignore[attr-defined]
            self._baseline_dict = self.collect_to_draft().to_dict()
        finally:
            self._loading = False
        self._update_dirty_state(collect=False)

    def collect_to_draft(self) -> ConfigDraft:
        for pg in self.leaf_pages:
            if getattr(pg, "_built", True):
                pg.save_to_draft(self.draft)
        return self.draft

    def is_dirty(self, collect: bool = True) -> bool:
        if collect:
            self.collect_to_draft()
        gen_pg = self.leaf_pages[0]
        auto_dirty = (
            gen_pg.autostart_dirty()
            if (isinstance(gen_pg, GeneralSettingsPage) and getattr(gen_pg, "_built", True))
            else False
        )
        quota_pg = next((pg for pg in self.leaf_pages if isinstance(pg, QuotaSettingsPage)), None)
        quota_pending = (
            quota_pg.has_pending_changes()
            if (quota_pg is not None and getattr(quota_pg, "_built", True))
            else False
        )
        sub_pg = next((pg for pg in self.leaf_pages if isinstance(pg, SubtitlesSettingsPage)), None)
        subtitle_pending = (
            sub_pg.has_pending_credentials()
            if (sub_pg is not None and getattr(sub_pg, "_built", True))
            else False
        )
        return (self.draft.to_dict() != self._baseline_dict) or auto_dirty or quota_pending or subtitle_pending

    def _on_any_page_changed(self):
        if self._loading or self._saving:
            return
        self._update_dirty_state()

    def _update_dirty_state(self, collect: bool = True):
        if self._saving:
            return
        dirty = self.is_dirty(collect=collect)
        if dirty:
            self.dirty_lbl.set_color(settings_theme.on_color())
            self.dirty_lbl.setText("● 有未保存修改")
            self.btn_save.setEnabled(True)
            self.btn_save.set_text("保存设置")
        else:
            self.dirty_lbl.setText("")
            self.btn_save.setEnabled(False)
            self.btn_save.set_text("保存设置")

    def _route_field_errors(self, errors: list[str]):
        for pg in self.leaf_pages:
            pg.clear_errors()
        pg_map = {pg.key: pg for pg in self.leaf_pages}
        gen_pg: GeneralSettingsPage = pg_map["general"]  # type: ignore[assignment]
        wth_pg: WeatherSettingsPage = pg_map["weather"]  # type: ignore[assignment]
        nav_pg: PagesNavSettingsPage = pg_map["pages_nav"]  # type: ignore[assignment]
        tl_pg: ToolsLifeSettingsPage = pg_map["tools_life"]  # type: ignore[assignment]
        for err in errors:
            if "热键" in err:
                gen_pg.row_hotkey.set_error(err)
                self.switch_category("general")
            elif "可见页面" in err:
                nav_pg._show_tip(err)
                self.switch_category("pages_nav")
            elif "提醒结束时间" in err or "提醒开始小时" in err or "提醒结束小时" in err:
                tl_pg.row_window.set_error(err)
                self.switch_category("tools_life")
            elif "午休时间" in err:
                tl_pg.row_lunch_time.set_error(err)
                self.switch_category("tools_life")
            elif "城市" in err:
                wth_pg.row_city.set_error(err)
                self.switch_category("weather")
            elif "天气刷新间隔" in err:
                wth_pg.row_interval.set_error(err)
                self.switch_category("weather")
            elif "AI 额度" in err:
                self.switch_category("quota")

    def save_settings(self) -> bool:
        """收集草稿 -> 显示保存中禁止重复点击 -> 严格校验 -> 原子写入 -> 平滑重建导航（不关闭设置窗口）。"""
        if self._saving:
            return False
        self._saving = True
        self.btn_save.set_text("正在保存…")
        self.btn_save.setEnabled(False)
        QtWidgets.QApplication.processEvents()

        try:
            import copy
            self.collect_to_draft()
            pre_ok, pre_errors, _ = self.cfg_obj.validate(self.draft, strict=True)
            if not pre_ok:
                err_msg = pre_errors[0] if pre_errors else "配置校验未通过"
                self._route_field_errors(pre_errors)
                self.status_lbl.set_color(settings_theme.up())
                self.status_lbl.setText(f"保存失败：{err_msg}")
                return False

            mail_pg = next((pg for pg in self.leaf_pages if isinstance(pg, MailAccountsSettingsPage)), None)
            quota_pg = next((pg for pg in self.leaf_pages if isinstance(pg, QuotaSettingsPage)), None)
            sub_pg = next((pg for pg in self.leaf_pages if isinstance(pg, SubtitlesSettingsPage)), None)

            def _rollback_creds(*pages) -> bool:
                all_ok = True
                for pg in pages:
                    if pg is not None and hasattr(pg, "rollback_credentials"):
                        try:
                            if not pg.rollback_credentials():
                                all_ok = False
                        except Exception:  # noqa: BLE001
                            all_ok = False
                return all_ok

            sub_written = False
            if sub_pg is not None and sub_pg.has_pending_credentials():
                sub_ok, sub_err = sub_pg.commit_credentials()
                if not sub_ok:
                    self.status_lbl.set_color(settings_theme.up())
                    self.status_lbl.setText(f"保存失败：{sub_err}")
                    self.switch_category("subtitles")
                    return False
                sub_written = True
            mail_written = False
            if mail_pg is not None:
                cred_ok, cred_err = mail_pg.commit_credentials()
                if not cred_ok:
                    rb_ok = _rollback_creds(sub_pg if sub_written else None)
                    self.status_lbl.set_color(settings_theme.up())
                    msg = f"保存失败：{cred_err}"
                    if not rb_ok:
                        msg += "（且部分凭据回滚失败，请检查系统凭据管理器）"
                    self.status_lbl.setText(msg)
                    self.switch_category("mail_accounts")
                    return False
                mail_written = True
            if quota_pg is not None:
                q_ok, q_err = quota_pg.commit_credentials()
                if not q_ok:
                    rb_ok = _rollback_creds(mail_pg if mail_written else None, sub_pg if sub_written else None)
                    self.status_lbl.set_color(settings_theme.up())
                    msg = f"保存失败：{q_err}"
                    if not rb_ok:
                        msg += "（且部分凭据回滚失败，请检查系统凭据管理器）"
                    self.status_lbl.setText(msg)
                    self.switch_category("quota")
                    return False

            prev_snapshot = copy.deepcopy(getattr(self.cfg_obj, "data", {}))
            quota_mgr = getattr(quota_pg, "_mgr", None) if quota_pg is not None else None
            if quota_mgr is not None and hasattr(quota_mgr, "set_commit_sync_suppressed"):
                quota_mgr.set_commit_sync_suppressed(True)
            try:
                try:
                    ok, errors = self.cfg_obj.commit_draft(self.draft, strict=True, emit_changed=False)
                except Exception as exc:  # noqa: BLE001
                    ok, errors = False, [f"写入配置文件失败：{type(exc).__name__}"]
                if not ok:
                    err_msg = errors[0] if errors else "配置校验未通过"
                    rb_ok = _rollback_creds(mail_pg if mail_written else None, sub_pg if sub_written else None)
                    self._route_field_errors(errors)
                    self.status_lbl.set_color(settings_theme.up())
                    msg = f"保存失败：{err_msg}"
                    if not rb_ok:
                        msg += "（且部分凭据回滚失败，请检查系统凭据管理器）"
                    self.status_lbl.setText(msg)
                    return False

                if quota_pg is not None:
                    fin_ok, fin_err = quota_pg.finalize_commit()
                    if not fin_ok:
                        cfg_rb_ok = True
                        if isinstance(prev_snapshot, dict) and prev_snapshot:
                            try:
                                cfg_rb_ok, _ = self.cfg_obj.commit_draft(prev_snapshot, strict=False, emit_changed=False)
                            except Exception:  # noqa: BLE001
                                cfg_rb_ok = False
                        cred_rb_ok = _rollback_creds(mail_pg if mail_written else None, sub_pg if sub_written else None)
                        self.status_lbl.set_color(settings_theme.up())
                        msg = f"保存失败：{fin_err or '会话凭据保存失败，未写入系统凭据管理器'}"
                        if not (cfg_rb_ok and cred_rb_ok):
                            msg += "（且部分凭据或配置回滚失败，请检查系统凭据管理器）"
                        self.status_lbl.setText(msg)
                        self.switch_category("quota")
                        return False

                if mail_pg is not None:
                    mail_res = mail_pg.finalize_commit()
                    if isinstance(mail_res, tuple):
                        mail_del_ok, mail_del_err = bool(mail_res[0]), str(mail_res[1] or "")
                    else:
                        mail_del_ok, mail_del_err = True, ""
                    if not mail_del_ok:
                        cfg_rb_ok = True
                        if isinstance(prev_snapshot, dict) and prev_snapshot:
                            try:
                                cfg_rb_ok, _ = self.cfg_obj.commit_draft(prev_snapshot, strict=False, emit_changed=False)
                            except Exception:  # noqa: BLE001
                                cfg_rb_ok = False
                        cred_rb_ok = _rollback_creds(
                            quota_pg if quota_pg is not None else None,
                            mail_pg if mail_written else None,
                            sub_pg if sub_written else None,
                        )
                        self.status_lbl.set_color(settings_theme.up())
                        msg = f"保存失败：{mail_del_err or '清理已删除邮箱凭据失败'}"
                        if not (cfg_rb_ok and cred_rb_ok):
                            msg += "（且部分凭据或配置回滚失败，请检查系统凭据管理器）"
                        self.status_lbl.setText(msg)
                        self.switch_category("mail_accounts")
                        return False
            finally:
                if quota_mgr is not None and hasattr(quota_mgr, "set_commit_sync_suppressed"):
                    quota_mgr.set_commit_sync_suppressed(False)

            self.cfg_obj.changed.emit()
            if getattr(self.ctx, "battery", None) is not None:
                self.ctx.battery.apply_config()

            for pg in self.leaf_pages:
                pg.clear_errors()

            gen_page = self.leaf_pages[0]
            if isinstance(gen_page, GeneralSettingsPage):
                gen_page.apply_autostart_if_changed()

            settings_theme.set_mode(self.cfg_obj.get("settings_window.theme", "light"))
            self.draft = self.cfg_obj.create_draft()
            self._baseline_dict = self.collect_to_draft().to_dict()
            self.status_lbl.set_color(settings_theme.down())
            self.status_lbl.setText("✓ 设置已保存并生效")
            self.saved.emit()
            return True
        finally:
            self._saving = False
            self._update_dirty_state()

    def cancel_and_close(self, prompt_if_dirty: bool = False) -> bool:
        """取消修改：有未保存修改且交互点击时弹出“放弃未保存修改？”确认；确认后丢弃草稿并关闭。"""
        if prompt_if_dirty and self.is_dirty():
            if not ConfirmDialog.ask(
                self,
                "放弃未保存修改？",
                "当前有未保存的设置更改，确定要放弃这些修改并关闭窗口吗？",
                confirm_text="放弃修改",
            ):
                return False
        mail_pg = next((pg for pg in self.leaf_pages if isinstance(pg, MailAccountsSettingsPage)), None)
        if mail_pg is not None:
            mail_pg.discard_pending()
        quota_pg = next((pg for pg in self.leaf_pages if isinstance(pg, QuotaSettingsPage)), None)
        if quota_pg is not None:
            quota_pg.discard_pending()
        self.draft = self.cfg_obj.create_draft()
        self._baseline_dict = self.draft.to_dict()
        self.status_lbl.setText("")
        self._load_all_from_draft()
        self.close()
        return True

    def closeEvent(self, e):
        self._restore_tooltip_style()
        mail_pg = next((pg for pg in self.leaf_pages if isinstance(pg, MailAccountsSettingsPage)), None)
        if mail_pg is not None:
            mail_pg.discard_pending()
        quota_pg = next((pg for pg in self.leaf_pages if isinstance(pg, QuotaSettingsPage)), None)
        if quota_pg is not None:
            quota_pg.discard_pending()
        super().closeEvent(e)
