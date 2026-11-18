# -*- coding: utf-8 -*-
"""天气设置页：默认城市、Open-Meteo / OpenWeather API Key、更新间隔与保存城市列表。"""
from __future__ import annotations

from PySide6 import QtWidgets

from ...core.config import ConfigDraft
from ..theme import G, W
from ..widgets import Card, IconButton, Label, LineEdit, PasswordEdit, PillButton, SettingRow, SpinBox, hbox, vbox
from .base import SettingsPage


class WeatherSettingsPage(SettingsPage):
    key = "weather"
    title = "天气"
    glyph = G.SUN

    def __init__(self, ctx=None, parent=None):
        super().__init__(ctx, parent)
        self._cities: list[dict] = []

        self.ed_city = LineEdit("例如：上海 / 北京 / 东京", width=196)
        self.ed_api_key = PasswordEdit("粘贴 API Key（可选）", width=196)
        self.sp_interval = SpinBox(1, 1440, 5, suffix=" 分钟", width=196)

        self.btn_refresh = PillButton("立即刷新天气", G.REFRESH, height=30)
        self.btn_refresh.clicked.connect(self._on_refresh_now)

        self.row_city = self.register_row(
            "weather.city",
            SettingRow("默认城市", "输入中文或英文城市名（修改保存后将重新解析城市坐标并更新天气）", self.ed_city),
        )
        self.row_api = self.register_row(
            "weather.api_key",
            SettingRow("OpenWeather API Key", "留空时使用免费的 Open-Meteo 服务", self.ed_api_key),
        )
        self.row_interval = self.register_row(
            "weather.interval_min",
            SettingRow("自动更新间隔", "后台定时拉取最新实时气温与天气预报的间隔时间", self.sp_interval),
        )

        self.cities_card = Card(radius=14)
        self.cities_lay = vbox(spacing=6, margins=(14, 12, 14, 12))
        self.cities_card.setLayout(self.cities_lay)

        self.finish_layout(
            self.section("天气源与城市", "收起态与天气页展示的默认城市及气象接口配置"),
            self.row_city,
            self.row_api,
            self.row_interval,
            SettingRow("手动同步", "立即向气象服务器发起一次刷新请求", self.btn_refresh),
            self.section("已保存城市列表", "在展开态「天气」页中可切换查看的常用城市（首项同步显示在收起态）"),
            self.cities_card,
        )

    def _on_refresh_now(self):
        if self.ctx and getattr(self.ctx, "weather", None):
            self.ctx.weather.refresh()
            self.btn_refresh.set_text("已触发刷新")

    def _rebuild_cities_ui(self):
        while self.cities_lay.count():
            item = self.cities_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        if not self._cities:
            self.cities_lay.addWidget(Label("暂无已保存的多城市记录（可在展开态天气页搜索添加）", 12, W.Normal, 3))
            return
        for idx, c in enumerate(self._cities):
            row = QtWidgets.QWidget()
            name = str(c.get("name") or "未知城市").strip()
            region = str(c.get("region") or c.get("admin1") or c.get("country") or "").strip()
            display_txt = f"{idx + 1}. {name} · {region}" if region and region != name else f"{idx + 1}. {name}"
            lbl = Label(display_txt, 12.5, W.Medium, 1)
            lat, lon = c.get("lat"), c.get("lon")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                tip = f"经纬度：{lat:.4f}, {lon:.4f}"
                lbl.setToolTip(tip)
                row.setToolTip(tip)
            btn_del = IconButton(G.TRASH, 26, 12, "移除该城市")
            btn_del.clicked.connect(lambda _=False, i=idx: self._remove_city(i))
            row.setLayout(hbox(lbl, None, btn_del, spacing=8, margins=(4, 2, 4, 2)))
            self.cities_lay.addWidget(row)

    def _remove_city(self, idx: int):
        if 0 <= idx < len(self._cities):
            self._cities.pop(idx)
            self._rebuild_cities_ui()
            self.changed.emit()

    def load_from_draft(self, draft: ConfigDraft):
        self.clear_errors()
        self.ed_city.setText(str(draft.get("weather.city", "上海") or ""))
        self.ed_api_key.setText(str(draft.get("weather.api_key", "") or ""))
        self.sp_interval.setValue(int(draft.get("weather.interval_min", 15) or 15))
        self._cities = list(draft.get("weather.cities") or [])
        self.btn_refresh.set_text("立即刷新天气")
        self._rebuild_cities_ui()

    def save_to_draft(self, draft: ConfigDraft):
        old_city = draft.get("weather.city", "")
        new_city = self.ed_city.text().strip()
        draft.set("weather.city", new_city)
        if new_city != old_city:
            draft.set("weather.lat", None)
            draft.set("weather.lon", None)
        draft.set("weather.api_key", self.ed_api_key.text().strip())
        draft.set("weather.interval_min", int(self.sp_interval.value()))
        draft.set("weather.cities", list(self._cities))
