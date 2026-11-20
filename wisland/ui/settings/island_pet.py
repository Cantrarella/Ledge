# -*- coding: utf-8 -*-
"""岛体与表情设置页：停靠边、沿边位置、屏幕选择、收起态视图、时间样式配色、轮换间隔、延迟与媒体联动。"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ...core.config import ConfigDraft
from ..clock_face import (
    CLOCK_COLORS,
    CLOCK_GRADIENT_PRESETS,
    CLOCK_STYLES,
    measure_clock,
    measure_temperature,
    normalize_hex_color,
    paint_clock,
    paint_temperature,
    paint_weather_icon,
    resolve_clock_palette,
    resolve_weather_colors,
)
from ..theme import G, theme
from ..widgets import ComboBox, SettingRow, SpinBox, Switch
from .base import SettingsPage

Qt = QtCore.Qt


class _ColorSwatchButton(QtWidgets.QPushButton):
    """圆角色块按钮，点击弹出系统调色板。"""

    color_changed = QtCore.Signal(str)

    def __init__(self, initial: str = "#FFBF5E", label_prefix: str = "", parent=None):
        super().__init__(parent)
        self._hex = normalize_hex_color(initial, "#FFBF5E")
        self._prefix = label_prefix
        self.setFixedHeight(30)
        self.setMinimumWidth(96)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._pick_color)
        self._refresh_style()

    def hex_color(self) -> str:
        return self._hex

    def set_hex_color(self, value: str):
        self._hex = normalize_hex_color(value, "#FFBF5E")
        self._refresh_style()

    def _refresh_style(self):
        c = QtGui.QColor(self._hex)
        lum = 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()
        fg = "#14161C" if lum > 0.56 else "#F6F8FC"
        text = f"{self._prefix}{self._hex}" if self._prefix else self._hex
        self.setText(text)
        self.setStyleSheet(
            f"QPushButton {{ background: {self._hex}; color: {fg}; border: 1px solid rgba(128,128,128,0.45); "
            f"border-radius: 7px; padding: 0 10px; font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ border: 1px solid {fg}; }}"
        )

    def _pick_color(self):
        chosen = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._hex), self, "选择时间颜色")
        if chosen.isValid():
            self.set_hex_color(chosen.name(QtGui.QColor.NameFormat.HexRgb).upper())
            self.color_changed.emit(self._hex)


class _ClockPreviewPill(QtWidgets.QWidget):
    """设置页内嵌胶囊时间实时预览控件。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(176, 34)
        self._style = "minimal"
        self._color = "white"
        self._custom_hex = "#FFBF5E"
        self._gradient = ["#5AE0FF", "#FF6BA8"]
        self._show_weather = True

    def update_preview(
        self,
        style: str,
        color: str,
        custom_hex: str,
        gradient: list[str],
        show_weather: bool = True,
    ):
        self._style = style
        self._color = color
        self._custom_hex = custom_hex
        self._gradient = list(gradient)
        self._show_weather = bool(show_weather)
        self.update()

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        rect = QtCore.QRectF(1, 1, self.width() - 2, self.height() - 2)
        bg = QtGui.QColor(14, 15, 20, 240) if theme.dark else QtGui.QColor(242, 245, 250, 245)
        border = QtGui.QColor(255, 255, 255, 36) if theme.dark else QtGui.QColor(20, 24, 34, 34)
        p.setBrush(bg)
        p.setPen(QtGui.QPen(border, 1.0))
        p.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)

        sample_cover = QtGui.QColor("#78A6FF") if self._color == "cover" else None
        palette = resolve_clock_palette(
            self._color,
            theme,
            custom_hex=self._custom_hex,
            gradient_stops=self._gradient,
            cover_color=sample_cover,
        )
        clock_w = measure_clock(self._style, "15:25")["total_w"]
        if not self._show_weather:
            cr = QtCore.QRectF(rect.center().x() - clock_w * 0.5, rect.y(), clock_w + 1.0, rect.height())
            paint_clock(p, cr, "15:25", self._style, palette, dark=theme.dark, alpha=1.0)
        else:
            temp_txt = "19°"
            temp_w = measure_temperature(self._style, temp_txt)["total_w"]
            gap = 14.0
            total_w = clock_w + gap + 15.0 + 1.5 + temp_w
            gx = rect.center().x() - total_w * 0.5
            cr = QtCore.QRectF(gx, rect.y(), clock_w + 1.0, rect.height())
            paint_clock(p, cr, "15:25", self._style, palette, dark=theme.dark, alpha=1.0)
            dot_col, icon_col, temp_col = resolve_weather_colors(self._color, theme, palette, alpha=1.0)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(dot_col)
            p.drawEllipse(QtCore.QPointF(gx + clock_w + gap * 0.44, rect.center().y()), 1.1, 1.1)
            paint_weather_icon(p, QtCore.QRectF(gx + clock_w + gap, rect.y(), 15.0, rect.height()), "partly", icon_col)
            paint_temperature(p, QtCore.QRectF(gx + clock_w + gap + 16.5, rect.y(), temp_w + 1.0, rect.height()), temp_txt, self._style, temp_col)
        p.end()


class IslandPetSettingsPage(SettingsPage):
    key = "island_pet"
    title = "岛体与表情"
    glyph = G.HOME

    def __init__(self, ctx=None, parent=None):
        super().__init__(ctx, parent)

        self.cb_edge = ComboBox([
            ("top", "顶部边缘"),
            ("bottom", "底部边缘"),
            ("left", "左侧边缘"),
            ("right", "右侧边缘"),
        ], width=196)
        self.sp_pos = SpinBox(0, 100, 5, suffix=" %", width=196)
        self.cb_screen = ComboBox(width=196)
        self._populate_screens()

        self.cb_compact = ComboBox([
            ("clock", "时间与天气"),
            ("pet", "灵动小表情"),
            ("music", "音乐播放"),
        ], width=196)
        self.sp_cycle = SpinBox(0, 3600, 5, suffix=" 秒", width=196)

        self.cb_clock_style = ComboBox(
            [(item["key"], item["label"]) for item in CLOCK_STYLES],
            width=196,
        )
        self.cb_clock_color = ComboBox(
            [(item["key"], item["label"]) for item in CLOCK_COLORS],
            width=196,
        )
        self.sw_compact_weather = Switch()
        self.clock_preview = _ClockPreviewPill()
        self.btn_custom_color = _ColorSwatchButton("#FFBF5E")

        # 渐变色控件：常用预设 + 起止色块
        self.grad_box = QtWidgets.QWidget()
        gl = QtWidgets.QHBoxLayout(self.grad_box)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(6)
        self.cb_grad_preset = ComboBox(
            [(i, name) for i, (name, _stops) in enumerate(CLOCK_GRADIENT_PRESETS)],
            width=110,
        )
        self.btn_grad_start = _ColorSwatchButton("#5AE0FF")
        self.btn_grad_end = _ColorSwatchButton("#FF6BA8")
        self.btn_grad_start.setMinimumWidth(84)
        self.btn_grad_end.setMinimumWidth(84)
        gl.addWidget(self.cb_grad_preset)
        gl.addWidget(self.btn_grad_start)
        gl.addWidget(self.btn_grad_end)

        self.row_custom_color = SettingRow("自定义时间颜色", "点击色块从调色板选择时间文字颜色", self.btn_custom_color)
        self.row_gradient_color = SettingRow("渐变起止颜色", "选择预设渐变或分别调整左/右端点颜色", self.grad_box)

        self.sp_hover_ms = SpinBox(0, 5000, 20, suffix=" ms", width=196)
        self.sp_collapse_ms = SpinBox(0, 10000, 50, suffix=" ms", width=196)
        self.sp_blur_fps = SpinBox(1, 60, 1, suffix=" FPS", width=196)

        self.sw_media_auto = Switch()
        self.sw_media_lyrics = Switch()
        self.sw_netease_log = Switch()

        self.finish_layout(
            self.section("停靠与位置", "调整灵动岛吸附的显示器、屏幕边缘及沿边偏移（也可直接按住灵动岛拖拽）"),
            SettingRow("停靠屏幕边缘", "灵动岛默认贴附的屏幕边缘方向", self.cb_edge),
            SettingRow("沿边位置比例", "50% 为正中，0% 靠近左/上端，100% 靠近右/下端", self.sp_pos),
            SettingRow("目标显示器", "选择灵动岛停靠所在的显示器屏幕", self.cb_screen),
            self.section("收起态与小表情", "设置收起胶囊显示的常驻视图与自动轮换节奏（收起态滚轮也可随时切换）"),
            SettingRow("收起态当前视图", "灵动岛收起时默认展示的内容样式", self.cb_compact),
            SettingRow("时间天气 / 小表情轮换间隔", "每隔指定秒数在时间天气与小表情间自动切换（设为 0 秒则不自动轮换）", self.sp_cycle),
            self.section("时间字体与颜色", "选择收起态数字的字体风格与主题自适应配色方案（移植自 BrimDeck）"),
            SettingRow("效果预览", "按当前风格与颜色实时预览收起态时间效果", self.clock_preview),
            SettingRow("时间字体风格", "固定内置 7 种精选数字字体（不含任意系统字体）", self.cb_clock_style),
            SettingRow("时间配色方案", "支持主题默认色、精选单色、跟随封面、自定义及水平渐变", self.cb_clock_color),
            SettingRow("收起态显示天气", "关闭后收起态胶囊仅居中显示纯净大时间", self.sw_compact_weather),
            self.row_custom_color,
            self.row_gradient_color,
            self.section("动效响应与毛玻璃", "岛体动效保持动态 60 FPS、空闲 30 FPS；此处可微调悬停延迟与背景模糊采样率"),
            SettingRow("悬停展开延迟", "鼠标移入收起态灵动岛后触发自动展开的等待毫秒数", self.sp_hover_ms),
            SettingRow("移出收起延迟", "鼠标离开展开态灵动岛后自动收起的缓冲毫秒数", self.sp_collapse_ms),
            SettingRow("毛玻璃背景采样率", "展开态身后画面模糊纹理的每秒更新次数（建议 6~15 FPS）", self.sp_blur_fps),
            self.section("音乐与歌词联动", "控制系统媒体播放及网易云音乐与收起态胶囊的联动行为"),
            SettingRow("播放时自动切到音乐视图", "检测到音乐播放时收起态自动显示封面、律动波形与歌词，暂停 8 秒后切回", self.sw_media_auto),
            SettingRow("收起态显示实时歌词", "音乐播放时在收起态胶囊右侧滚动显示当前同步歌词", self.sw_media_lyrics),
            SettingRow("读取网易云本地日志", "从网易云客户端本地日志获取精准播放状态与进度", self.sw_netease_log),
        )

        self.cb_clock_style.value_changed.connect(lambda *_: self._refresh_clock_controls())
        self.cb_clock_color.value_changed.connect(lambda *_: self._refresh_clock_controls())
        self.sw_compact_weather.toggled.connect(lambda *_: self._refresh_clock_controls())
        self.btn_custom_color.color_changed.connect(lambda *_: self._refresh_clock_controls())
        self.btn_grad_start.color_changed.connect(lambda *_: self._refresh_clock_controls())
        self.btn_grad_end.color_changed.connect(lambda *_: self._refresh_clock_controls())
        self.cb_grad_preset.value_changed.connect(self._on_gradient_preset)
        self._refresh_clock_controls()

    def _on_gradient_preset(self, idx):
        if isinstance(idx, int) and 0 <= idx < len(CLOCK_GRADIENT_PRESETS):
            stops = CLOCK_GRADIENT_PRESETS[idx][1]
            self.btn_grad_start.set_hex_color(stops[0])
            self.btn_grad_end.set_hex_color(stops[1])
            self._refresh_clock_controls()

    def _refresh_clock_controls(self):
        color_key = str(self.cb_clock_color.current_value() or "white")
        self.row_custom_color.setVisible(color_key == "custom")
        self.row_gradient_color.setVisible(color_key == "gradient")
        self.clock_preview.update_preview(
            style=str(self.cb_clock_style.current_value() or "minimal"),
            color=color_key,
            custom_hex=self.btn_custom_color.hex_color(),
            gradient=[self.btn_grad_start.hex_color(), self.btn_grad_end.hex_color()],
            show_weather=self.sw_compact_weather.isChecked(),
        )

    def _populate_screens(self):
        screens = QtGui.QGuiApplication.screens()
        items = []
        for idx, scr in enumerate(screens):
            g = scr.geometry()
            name = scr.name() or f"显示器 {idx + 1}"
            items.append((idx, f"{idx}: {name} ({g.width()}×{g.height()})"))
        if not items:
            items = [(0, "0: 主显示器")]
        self.cb_screen.set_items(items)

    def load_from_draft(self, draft: ConfigDraft):
        self._populate_screens()
        self.cb_edge.set_value(draft.get("island.edge", "top"))
        pos_frac = float(draft.get("island.pos", 0.5) or 0.5)
        self.sp_pos.setValue(round(pos_frac * 100))
        self.cb_screen.set_value(int(draft.get("island.screen", 0) or 0))
        self.cb_compact.set_value(draft.get("island.compact_view", "clock"))
        self.sp_cycle.setValue(int(draft.get("island.auto_cycle_s", 30) or 0))
        self.cb_clock_style.set_value(draft.get("island.clock_style", "minimal"))
        self.cb_clock_color.set_value(draft.get("island.clock_color", "white"))
        self.sw_compact_weather.setChecked(bool(draft.get("island.compact_weather", True)))
        self.btn_custom_color.set_hex_color(str(draft.get("island.clock_custom_color", "#FFBF5E")))
        grad = draft.get("island.clock_gradient", ["#5AE0FF", "#FF6BA8"])
        if isinstance(grad, list) and len(grad) >= 2:
            self.btn_grad_start.set_hex_color(str(grad[0]))
            self.btn_grad_end.set_hex_color(str(grad[1]))
        else:
            self.btn_grad_start.set_hex_color("#5AE0FF")
            self.btn_grad_end.set_hex_color("#FF6BA8")
        self._refresh_clock_controls()
        self.sp_hover_ms.setValue(int(draft.get("island.hover_expand_ms", 120) or 120))
        self.sp_collapse_ms.setValue(int(draft.get("island.collapse_delay_ms", 320) or 320))
        self.sp_blur_fps.setValue(int(float(draft.get("island.blur_fps", 8) or 8)))
        self.sw_media_auto.setChecked(bool(draft.get("media.auto_show", True)))
        self.sw_media_lyrics.setChecked(bool(draft.get("media.lyrics", True)))
        self.sw_netease_log.setChecked(bool(draft.get("media.netease_log", True)))

    def save_to_draft(self, draft: ConfigDraft):
        draft.set("island.edge", self.cb_edge.current_value() or "top")
        draft.set("island.pos", round(self.sp_pos.value() / 100.0, 4))
        scr_val = self.cb_screen.current_value()
        draft.set("island.screen", int(scr_val) if scr_val is not None else 0)
        draft.set("island.compact_view", self.cb_compact.current_value() or "clock")
        draft.set("island.auto_cycle_s", int(self.sp_cycle.value()))
        draft.set("island.clock_style", self.cb_clock_style.current_value() or "minimal")
        draft.set("island.clock_color", self.cb_clock_color.current_value() or "white")
        draft.set("island.compact_weather", self.sw_compact_weather.isChecked())
        draft.set("island.clock_custom_color", self.btn_custom_color.hex_color())
        draft.set("island.clock_gradient", [self.btn_grad_start.hex_color(), self.btn_grad_end.hex_color()])
        draft.set("island.hover_expand_ms", int(self.sp_hover_ms.value()))
        draft.set("island.collapse_delay_ms", int(self.sp_collapse_ms.value()))
        draft.set("island.blur_fps", int(self.sp_blur_fps.value()))
        draft.set("media.auto_show", self.sw_media_auto.isChecked())
        draft.set("media.lyrics", self.sw_media_lyrics.isChecked())
        draft.set("media.netease_log", self.sw_netease_log.isChecked())
