# -*- coding: utf-8 -*-
"""独立 Qt 设置窗口：单实例、左侧分类导航、右侧顶栏 + 滚动设置卡片 + 独立底部操作栏。"""
from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from ..core.config import ConfigDraft, cfg
from .settings import (
    AboutSettingsPage,
    DataSettingsPage,
    GeneralSettingsPage,
    IslandPetSettingsPage,
    MailAccountsSettingsPage,
    NotifySettingsPage,
    PagesNavSettingsPage,
    SubtitlesSettingsPage,
    ToolsLifeSettingsPage,
    WeatherSettingsPage,
)
from .theme import G, W, icon_font, theme, ui_font
from .widgets import ConfirmDialog, Hoverable, Label, PillButton, hbox, painter, rounded_pixmap, vbox

Qt = QtCore.Qt


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
            sel_bg = QtGui.QColor(theme.on_color())
            sel_bg.setAlphaF(0.18 if theme.dark else 0.14)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(sel_bg)
            p.drawRoundedRect(r, 10, 10)
            # 左侧 3px 强调线
            bar = QtCore.QRectF(r.left() + 3, r.center().y() - 8, 3.0, 16)
            p.setBrush(theme.on_color())
            p.drawRoundedRect(bar, 1.5, 1.5)
        elif self._hover.x > 0.01:
            bg = QtGui.QColor(theme.hover())
            bg.setAlphaF(bg.alphaF() * self._hover.x)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, 10, 10)

        if self.hasFocus():
            p.setPen(QtGui.QPen(theme.on_color(), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 10, 10)

        fg = theme.on_color() if (self.selected and not theme.dark) else (theme.text(0.98) if self.selected else theme.text2(0.92))
        p.setPen(fg)
        p.setFont(icon_font(15))
        p.drawText(QtCore.QRectF(r.left() + 14, r.top(), 20, r.height()), Qt.AlignmentFlag.AlignCenter, self.glyph)
        p.setFont(ui_font(13.5, W.DemiBold if self.selected else W.Normal))
        p.drawText(QtCore.QRectF(r.left() + 40, r.top(), r.width() - 48, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.title)


class SettingsWindow(QtWidgets.QWidget):
    """独立普通窗口的 Wisland 设置中心（单实例，关闭窗口不退出主程序）。"""
    _instance: SettingsWindow | None = None
    saved = QtCore.Signal()

    def __init__(self, ctx=None, config_obj=None, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.ctx = ctx
        self.cfg_obj = config_obj or cfg
        self._loading = False
        self._saving = False
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setWindowTitle("Wisland 设置")
        self.resize(820, 600)
        self.setMinimumSize(640, 480)

        icon_path = Path(__file__).resolve().parents[1] / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        self.draft: ConfigDraft = self.cfg_obj.create_draft()
        self._baseline_dict: dict = self.draft.to_dict()

        # ---- 左侧导航栏（小型 logo + Wisland / 副标题“设置”） ----
        self.sidebar = QtWidgets.QFrame(self)
        self.sidebar.setFixedWidth(188)

        logo_lbl = QtWidgets.QLabel()
        logo_lbl.setFixedSize(28, 28)
        if icon_path.exists():
            pm = rounded_pixmap(QtGui.QPixmap(str(icon_path)), 28, 7)
            if pm:
                logo_lbl.setPixmap(pm)
        brand_title = Label("Wisland", 15, W.Bold, 1)
        brand_sub = Label("设置", 11.5, W.Normal, 3)
        brand_box = hbox(logo_lbl, vbox(brand_title, brand_sub, spacing=0), None, spacing=10, margins=(8, 4, 6, 10))

        self.pages = [
            GeneralSettingsPage(ctx, self),
            IslandPetSettingsPage(ctx, self),
            PagesNavSettingsPage(ctx, self),
            WeatherSettingsPage(ctx, self),
            NotifySettingsPage(ctx, self),
            MailAccountsSettingsPage(ctx, self),
            SubtitlesSettingsPage(ctx, self),
            ToolsLifeSettingsPage(ctx, self),
            DataSettingsPage(ctx, self, on_reload_draft=self._load_all_from_draft),
            AboutSettingsPage(ctx, self),
        ]

        self.nav_buttons: dict[str, _CategoryButton] = {}
        nav_items = [brand_box]
        for pg in self.pages:
            btn = _CategoryButton(pg.key, pg.glyph, pg.title, self.sidebar)
            btn.clicked.connect(lambda k=pg.key: self.switch_category(k))
            pg.changed.connect(self._on_any_page_changed)
            self.nav_buttons[pg.key] = btn
            nav_items.append(btn)
        nav_items.append(None)
        self.sidebar.setLayout(vbox(*nav_items, spacing=4, margins=(12, 14, 12, 14)))

        # ---- 右侧纵向三段式：顶部标题栏 + 可伸缩 QStackedWidget(QScrollArea) + 独立底部操作栏 ----
        self.header = QtWidgets.QFrame(self)
        self.header.setFixedHeight(50)
        self.header_title = Label("常规", 16, W.Bold, 1)
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

        self.footer = QtWidgets.QFrame(self)
        self.footer.setFixedHeight(58)
        self.footer.setLayout(hbox((self.status_lbl, 1), self.btn_cancel, self.btn_save, spacing=10, margins=(20, 10, 20, 12)))

        right_lay = vbox(self.header, (self.stack, 1), self.footer, spacing=0, margins=(0, 0, 0, 0))
        self.setLayout(hbox(self.sidebar, (right_lay, 1), spacing=0, margins=(0, 0, 0, 0)))

        theme.changed.connect(self._apply_window_style)
        self._apply_window_style()
        self.switch_category("general")
        self._load_all_from_draft()

    @classmethod
    def open_instance(cls, ctx=None, category: str | None = None) -> SettingsWindow:
        if cls._instance is None:
            cls._instance = cls(ctx=ctx)
        else:
            cls._instance.ctx = ctx
            cls._instance.reload_draft_from_config()
        if category:
            cls._instance.switch_category(category)
        cls._instance.show()
        cls._instance.raise_()
        cls._instance.activateWindow()
        return cls._instance

    def _apply_window_style(self):
        if theme.dark:
            win_bg = "#181920"
            side_bg = "#13141a"
            head_bg = "#181920"
            foot_bg = "#15161c"
            sep = "rgba(255,255,255,18)"
        else:
            win_bg = "#f3f5f9"
            side_bg = "#eaedf4"
            head_bg = "#f3f5f9"
            foot_bg = "#edf0f6"
            sep = "rgba(0,0,0,16)"
        self.setStyleSheet(f"SettingsWindow{{background:{win_bg};}}")
        self.sidebar.setStyleSheet(f"QFrame{{background:{side_bg};border-right:1px solid {sep};}}")
        self.header.setStyleSheet(f"QFrame{{background:{head_bg};border-bottom:1px solid {sep};}}")
        self.footer.setStyleSheet(f"QFrame{{background:{foot_bg};border-top:1px solid {sep};}}")
        self.dirty_lbl.set_color(theme.on_color())
        self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        bg = QtGui.QColor("#181920" if theme.dark else "#f3f5f9")
        p.fillRect(self.rect(), bg)

    def switch_category(self, key: str):
        for i, pg in enumerate(self.pages):
            on = (pg.key == key)
            if pg.key in self.nav_buttons:
                self.nav_buttons[pg.key].set_selected(on)
            if on:
                self.header_title.setText(pg.title)
                self.stack.setCurrentIndex(i)
                if pg.key == "about":
                    pg.load_from_draft(self.draft)

    def reload_draft_from_config(self):
        """放弃未保存草稿，重新从当前生效配置创建新草稿并刷新控件。"""
        self.draft = self.cfg_obj.create_draft()
        self._baseline_dict = self.draft.to_dict()
        self.status_lbl.setText("")
        self._load_all_from_draft()

    def _load_all_from_draft(self):
        self._loading = True
        try:
            for pg in self.pages:
                pg.load_from_draft(self.draft)
            self._baseline_dict = self.collect_to_draft().to_dict()
        finally:
            self._loading = False
        self._update_dirty_state()

    def collect_to_draft(self) -> ConfigDraft:
        for pg in self.pages:
            pg.save_to_draft(self.draft)
        return self.draft

    def is_dirty(self) -> bool:
        self.collect_to_draft()
        gen_pg = self.pages[0]
        auto_dirty = gen_pg.autostart_dirty() if isinstance(gen_pg, GeneralSettingsPage) else False
        return (self.draft.to_dict() != self._baseline_dict) or auto_dirty

    def _on_any_page_changed(self):
        if self._loading or self._saving:
            return
        self._update_dirty_state()

    def _update_dirty_state(self):
        if self._saving:
            return
        dirty = self.is_dirty()
        if dirty:
            self.dirty_lbl.set_color(theme.on_color())
            self.dirty_lbl.setText("● 有未保存修改")
            self.btn_save.setEnabled(True)
            self.btn_save.set_text("保存设置")
        else:
            self.dirty_lbl.setText("")
            self.btn_save.setEnabled(False)
            self.btn_save.set_text("保存设置")

    def _route_field_errors(self, errors: list[str]):
        for pg in self.pages:
            pg.clear_errors()
        pg_map = {pg.key: pg for pg in self.pages}
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

    def save_settings(self) -> bool:
        """收集草稿 -> 显示保存中禁止重复点击 -> 严格校验 -> 原子写入 -> 平滑重建导航（不关闭设置窗口）。"""
        if self._saving:
            return False
        self._saving = True
        self.btn_save.set_text("正在保存…")
        self.btn_save.setEnabled(False)
        QtWidgets.QApplication.processEvents()

        try:
            self.collect_to_draft()
            mail_pg = next((pg for pg in self.pages if isinstance(pg, MailAccountsSettingsPage)), None)
            if mail_pg is not None:
                cred_ok, cred_err = mail_pg.commit_credentials()
                if not cred_ok:
                    self.status_lbl.set_color(theme.up())
                    self.status_lbl.setText(f"保存失败：{cred_err}")
                    self.switch_category("mail_accounts")
                    return False

            ok, errors = self.cfg_obj.commit_draft(self.draft, strict=True)
            if not ok:
                err_msg = errors[0] if errors else "配置校验未通过"
                self._route_field_errors(errors)
                self.status_lbl.set_color(theme.up())
                self.status_lbl.setText(f"保存失败：{err_msg}")
                return False

            if mail_pg is not None:
                mail_pg.finalize_commit()

            for pg in self.pages:
                pg.clear_errors()

            gen_page = self.pages[0]
            if isinstance(gen_page, GeneralSettingsPage):
                gen_page.apply_autostart_if_changed()

            self.draft = self.cfg_obj.create_draft()
            self._baseline_dict = self.collect_to_draft().to_dict()
            self.status_lbl.set_color(theme.down())
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
        mail_pg = next((pg for pg in self.pages if isinstance(pg, MailAccountsSettingsPage)), None)
        if mail_pg is not None:
            mail_pg.discard_pending()
        self.draft = self.cfg_obj.create_draft()
        self._baseline_dict = self.draft.to_dict()
        self.status_lbl.setText("")
        self._load_all_from_draft()
        self.close()
        return True

    def closeEvent(self, e):
        mail_pg = next((pg for pg in self.pages if isinstance(pg, MailAccountsSettingsPage)), None)
        if mail_pg is not None:
            mail_pg.discard_pending()
        super().closeEvent(e)
