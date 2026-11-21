# -*- coding: utf-8 -*-
"""无线设备电量监控单元测试（协议解析、低电防抖、页面与设置页草稿）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wisland.core.config import DEFAULTS, ConfigDraft, cfg  # noqa: E402
from wisland.services.battery import BatteryDevice, BatteryService  # noqa: E402
from wisland.services.battery_providers import (  # noqa: E402
    AppleAdvertParser,
    AsusHeadsetProvider,
    MchoseProvider,
    PlayStationProvider,
)
from wisland.ui.page_registry import PageRegistry  # noqa: E402
from wisland.ui.pages.battery import BatteryPage  # noqa: E402
from wisland.ui.settings.battery import BatterySettingsPage  # noqa: E402


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])


def test_asus_delta2_protocol():
    ok_pkt = bytes([0xCC, 0x12, 0x07, 0x00, 0x00, 0x05, 0x4D, 0x14, 0x01]) + b"\x00" * 55
    assert AsusHeadsetProvider.parse_battery(ok_pkt) == 0x4D
    nak_pkt = bytes([0xCC, 0xFF, 0xAA, 0x00, 0x00, 0x00, 0x50]) + b"\x00" * 57
    assert AsusHeadsetProvider.parse_battery(nak_pkt) is None
    chg_pkt = bytes([0xCC, 0x12, 0x08, 0x00, 0x00, 0x01]) + b"\x00" * 58
    assert AsusHeadsetProvider.parse_charging(chg_pkt) is True


def test_mchose_k99_protocol_and_echo_reject():
    pkt = bytearray(20)
    pkt[0], pkt[1], pkt[2], pkt[3], pkt[4] = 0x13, 0x4A, 0x01, 0x00, 0x02
    pkt[5] = 88
    pkt[6] = 0x10  # charging
    pkt[19] = MchoseProvider.checksum(pkt)
    assert MchoseProvider.parse_battery(bytes(pkt)) == (88, True)

    echo = bytearray(20)
    echo[0], echo[1], echo[2], echo[3], echo[4] = 0x13, 0x4A, 0x01, 0x00, 0x00
    echo[19] = MchoseProvider.checksum(echo)
    assert MchoseProvider.parse_battery(bytes(echo)) is None


def test_playstation_protocol():
    ds_usb = bytearray(64)
    ds_usb[0] = 0x01
    ds_usb[53] = 0x14  # state=1(charging), level=4 -> 45%
    assert PlayStationProvider.parse_report(0x0CE6, bytes(ds_usb), False) == (45, True)

    ds_bt = bytearray(78)
    ds_bt[0] = 0x31
    ds_bt[54] = 0x08  # discharging, level=8 -> 85%
    assert PlayStationProvider.parse_report(0x0CE6, bytes(ds_bt), True) == (85, False)

    ds4_usb = bytearray(64)
    ds4_usb[0] = 0x01
    ds4_usb[30] = 0x06
    assert PlayStationProvider.parse_report(0x09CC, bytes(ds4_usb), False) == (65, False)


def test_apple_airpods_advert_parser():
    # Apple proximity pairing: type=0x07, len=0x19, model=0x1420 (AirPods Pro)
    # flipped=(byte5 & 0x20)==0 -> byte5=0x00; byte6=(8<<4)|7 -> L=80%, R=70%; byte7=(0x04<<4)|6 -> Case=60%, CaseCharging=True
    adv = bytes([0x07, 0x19, 0x01, 0x14, 0x20, 0x00, 0x87, 0x46])
    res = AppleAdvertParser.parse(adv)
    assert res is not None
    assert res["model"] == "AirPods Pro"
    assert res["percent"] == 70
    assert ("左", 80, False) in res["parts"]
    assert ("右", 70, False) in res["parts"]
    assert ("盒", 60, True) in res["parts"]


def test_low_battery_debounce():
    _app()
    svc = BatteryService.__new__(BatteryService)
    QtCore.QObject.__init__(svc)
    svc._low_alerted = set()
    alerts = []
    svc.low_battery.connect(lambda i, n, p, k: alerts.append((i, n, p, k)))

    d_low = BatteryDevice("d1", "GPW3", "", "Logitech", "mouse", "Logitech HID++", percent=15, charging=False, online=True)
    svc._check_low_battery([d_low])
    svc._check_low_battery([d_low])
    assert len(alerts) == 1

    # 充电时静音并在电量回升 >= thresh + 5 后重置
    d_chg = BatteryDevice("d1", "GPW3", "", "Logitech", "mouse", "Logitech HID++", percent=18, charging=True, online=True)
    svc._check_low_battery([d_chg])
    svc._check_low_battery([d_low])
    assert len(alerts) == 2


def test_full_battery_notify_debounce():
    _app()
    svc = BatteryService.__new__(BatteryService)
    QtCore.QObject.__init__(svc)
    svc._low_alerted = set()
    svc._seen_charging = set()
    svc._full_alerted = set()
    full_alerts = []
    svc.full_battery.connect(lambda i, n, k: full_alerts.append((i, n, k)))

    # 1) 从未见过充电状态，一开机 100% 不误报
    d_wired = BatteryDevice("d2", "Delta II", "", "ASUS", "headset", "ROG", percent=100, charging=False, online=True)
    svc._check_low_battery([d_wired])
    assert len(full_alerts) == 0

    # 2) 充电至 100% 触发一次，不重复弹
    d_chg_80 = BatteryDevice("d2", "Delta II", "", "ASUS", "headset", "ROG", percent=80, charging=True, online=True)
    d_chg_100 = BatteryDevice("d2", "Delta II", "", "ASUS", "headset", "ROG", percent=100, charging=True, online=True)
    svc._check_low_battery([d_chg_80])
    svc._check_low_battery([d_chg_100])
    svc._check_low_battery([d_chg_100])
    assert len(full_alerts) == 1

    # 3) 用电降回 <=94% 后，下一轮充电至 100% 可再次触发
    d_use = BatteryDevice("d2", "Delta II", "", "ASUS", "headset", "ROG", percent=90, charging=False, online=True)
    svc._check_low_battery([d_use])
    svc._check_low_battery([d_chg_100])
    assert len(full_alerts) == 2


def test_battery_page_and_settings_draft():
    _app()
    fake_devs = [
        BatteryDevice("logi-1", "Logitech GPW3", "GPW3", "Logitech", "mouse", "Logitech HID++", percent=82, charging=False, online=True, detail="HID++ 2.0"),
        BatteryDevice("mchose-1", "MCHOSE K99 V2", "", "MCHOSE", "keyboard", "MCHOSE HID", percent=23, charging=False, online=True, detail="2.4G"),
        BatteryDevice("rog-1", "ROG Delta II", "", "ASUS ROG", "headset", "ROG Audio", percent=64, charging=True, online=True, detail="2.4G"),
        BatteryDevice("bt-1", "AirPods Pro", "", "Apple", "earbuds", "Windows Bluetooth", percent=70, charging=False, online=True, detail="左80% 右70% 盒60%"),
    ]

    class _FakeSvc(QtCore.QObject):
        changed = QtCore.Signal()
        busy_changed = QtCore.Signal(bool)

        def devices(self, include_hidden=False):
            return list(fake_devs)

        def refresh(self):
            pass

    ctx = SimpleNamespace(
        battery=_FakeSvc(),
        media=None, audio=None, windows=None, notify=None, weather=None,
        lyrics=None, mailcheck=None, mail=None, mail_monitor=None, quota=None,
        shelf=None, clipboard=None, todo=None, water=None, health=None, live=None,
    )

    pg = BatteryPage(ctx)
    assert pg.SIZE == (419, 176)
    img = QtGui.QImage(419, 176, QtGui.QImage.Format_ARGB32)
    pg.render(img)

    spg = BatterySettingsPage(ctx)
    draft = ConfigDraft(cfg.data)
    spg.load_from_draft(draft)
    spg.sp_low_thresh.setValue(25)
    spg.save_to_draft(draft)
    assert draft.get("battery.low_threshold") == 25

    reg = PageRegistry()
    reg.register(pg)
    reg.sync_from_config(cfg)
    assert "battery" in reg.registered_keys()
    assert "battery" in DEFAULTS["pages"]["order"]


def test_battery_service_lifecycle_deferred_start(monkeypatch):
    """BatteryService 构造时不抢跑扫描/定时器，需显式 start() 后才启动，stop() 可干净停止。"""
    from PySide6 import QtWidgets
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    calls = {"refresh": 0, "watcher_start": 0, "watcher_stop": 0}
    monkeypatch.setattr(BatteryService, "refresh", lambda self, force_discover=False: calls.__setitem__("refresh", calls["refresh"] + 1))
    monkeypatch.setattr(BatteryService, "_start_apple_watcher", lambda self: calls.__setitem__("watcher_start", calls["watcher_start"] + 1))
    monkeypatch.setattr(BatteryService, "_stop_apple_watcher", lambda self: calls.__setitem__("watcher_stop", calls["watcher_stop"] + 1))

    svc = BatteryService()
    assert not svc._timer.isActive()
    assert calls["refresh"] == 0
    assert calls["watcher_start"] == 0

    svc.apply_config()
    assert not svc._timer.isActive()
    assert calls["refresh"] == 0

    svc.start()
    assert svc._timer.isActive()
    assert calls["refresh"] == 1
    assert calls["watcher_start"] == 1

    svc.stop()
    assert not svc._timer.isActive()
    assert calls["watcher_stop"] >= 1
