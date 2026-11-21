# -*- coding: utf-8 -*-
"""设备电量设置页：配置监控开关、轮询间隔、低电量提醒阈值与已发现设备别名/隐藏管理。"""
from __future__ import annotations

from PySide6 import QtWidgets

from ...core.config import ConfigDraft
from ..theme import G, W
from ..widgets import Label, LineEdit, PillButton, SettingRow, SpinBox, Switch, hbox, vbox
from .base import SettingsPage


class BatterySettingsPage(SettingsPage):
    key = "battery"
    title = "设备电量"
    glyph = G.BOLT

    def __init__(self, ctx=None, parent=None):
        super().__init__(ctx, parent, lazy=True)
        self.svc = getattr(ctx, "battery", None) if ctx else None
        self._aliases: dict[str, str] = {}
        self._hidden: set[str] = set()
        self._dev_controls: list[tuple[str, LineEdit, Switch]] = []

    def _build_ui(self):
        self.sw_enabled = Switch()
        self.sp_interval = SpinBox(15, 300, 15, suffix=" 秒")
        self.sw_low_notify = Switch()
        self.sp_low_thresh = SpinBox(5, 50, 5, suffix=" %")
        self.sw_full_notify = Switch()

        self.btn_scan = PillButton("立即扫描", G.REFRESH, height=28)
        self.btn_scan.clicked.connect(self._on_scan)

        self.dev_list_box = QtWidgets.QWidget()
        self.dev_list_lay = vbox(spacing=8, margins=(0, 0, 0, 0))
        self.dev_list_box.setLayout(self.dev_list_lay)

        self.finish_layout(
            self.section("无线设备电量监控", "支持 ROG Delta II、迈从 K99 V2、罗技 HID++、通用 HID 耳机、XInput / PS 手柄、AirPods 与系统蓝牙"),
            SettingRow("启用设备电量监控", "关闭后停止后台扫描并隐藏当前设备电量数据", self.sw_enabled),
            SettingRow("后台刷新间隔", "每隔指定秒数扫描一次设备电量（15–300 秒，默认 60 秒）", self.sp_interval),

            self.section("电量提醒", "每个周期仅提醒一次，充电时静音低电提醒，电量恢复后重置"),
            SettingRow("开启低电量轻提示", "电量低于阈值时在灵动岛收起态弹出提醒", self.sw_low_notify),
            SettingRow("低电量提醒阈值", "范围 5%–50%，默认 20%", self.sp_low_thresh),
            SettingRow("开启充满电轻提示", "设备从充电状态充至 100% 时在灵动岛收起态弹出提醒", self.sw_full_notify),

            self.section("已发现设备", "可为设备设置短别名或在页面中隐藏不需要显示的设备"),
            SettingRow("扫描无线外设", "支持 2.4G 接收器、USB/蓝牙手柄及已配对蓝牙设备", self.btn_scan),
            self.dev_list_box,
        )

        if self.svc is not None:
            self.svc.changed.connect(self._rebuild_device_rows)
            self.svc.busy_changed.connect(self._on_busy_changed)

    def _on_busy_changed(self, busy: bool):
        self.btn_scan.setEnabled(not busy)

    def _on_scan(self):
        if self.svc is not None:
            self.svc.refresh()

    def _sync_dev_draft_state(self):
        for dev_id, ed_alias, sw_vis in self._dev_controls:
            txt = ed_alias.text().strip()
            if txt:
                self._aliases[dev_id] = txt
            else:
                self._aliases.pop(dev_id, None)
            if sw_vis.isChecked():
                self._hidden.discard(dev_id)
            else:
                self._hidden.add(dev_id)

    def _on_dev_item_edited(self, *_args):
        self._sync_dev_draft_state()
        self.changed.emit()

    def _rebuild_device_rows(self):
        self._sync_dev_draft_state()
        self._dev_controls.clear()
        while self.dev_list_lay.count():
            item = self.dev_list_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        devs = self.svc.devices(include_hidden=True) if self.svc is not None else []
        if not devs:
            empty = SettingRow("暂未扫描到无线外设", "请确认无线接收器已插入或蓝牙设备已连接后点击「立即扫描」")
            self.dev_list_lay.addWidget(empty)
            return

        for d in devs:
            status = "休眠" if d.sleeping else ("充电中" if d.charging else "在线" if d.online else "未响应")
            pct_str = f"{d.percent}%" if d.percent is not None else "--"
            sub = f"{d.provider} · {d.detail or '无线'} · {pct_str}（{status}）"

            ed = LineEdit(d.name, width=130)
            ed.setText(self._aliases.get(d.id, d.alias))
            ed.setToolTip("自定义短别名（留空则显示原名称）")

            sw_show = Switch()
            sw_show.setChecked(d.id not in self._hidden)
            sw_show.setToolTip("是否在设备电量页面显示该设备")

            ed.textChanged.connect(self._on_dev_item_edited)
            sw_show.toggled.connect(self._on_dev_item_edited)
            self._dev_controls.append((d.id, ed, sw_show))

            ctrl = QtWidgets.QWidget()
            ctrl.setLayout(hbox(ed, Label("显示", 12, W.Normal, 2), sw_show, spacing=8, margins=(0, 0, 0, 0)))
            self.dev_list_lay.addWidget(SettingRow(d.name, sub, ctrl))

    def load_from_draft(self, draft: ConfigDraft):
        self.clear_errors()
        self.sw_enabled.setChecked(bool(draft.get("battery.enabled", True)))
        self.sp_interval.setValue(int(draft.get("battery.poll_interval_s", 60) or 60))
        self.sw_low_notify.setChecked(bool(draft.get("battery.low_notify", True)))
        self.sp_low_thresh.setValue(int(draft.get("battery.low_threshold", 20) or 20))
        self.sw_full_notify.setChecked(bool(draft.get("battery.full_notify", True)))

        raw_aliases = draft.get("battery.aliases", {})
        self._aliases = {str(k): str(v) for k, v in raw_aliases.items()} if isinstance(raw_aliases, dict) else {}
        raw_hidden = draft.get("battery.hidden", [])
        self._hidden = set(str(x) for x in raw_hidden) if isinstance(raw_hidden, list) else set()
        self._rebuild_device_rows()

    def save_to_draft(self, draft: ConfigDraft):
        self._sync_dev_draft_state()
        draft.set("battery.enabled", self.sw_enabled.isChecked())
        draft.set("battery.poll_interval_s", int(self.sp_interval.value()))
        draft.set("battery.low_notify", self.sw_low_notify.isChecked())
        draft.set("battery.low_threshold", int(self.sp_low_thresh.value()))
        draft.set("battery.full_notify", self.sw_full_notify.isChecked())
        draft.set("battery.aliases", dict(self._aliases))
        draft.set("battery.hidden", sorted(self._hidden))
