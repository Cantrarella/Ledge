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
    # BLE watcher 按系统已连接 Apple 蓝牙状态按需启停，不在 start() 无条件空跑
    assert calls["watcher_start"] == 0

    svc.stop()
    assert not svc._timer.isActive()
    assert calls["watcher_stop"] >= 1


def test_battery_alias_clear_persists_after_commit(tmp_path):
    """清空无线设备别名并提交草稿后，旧别名不得被 Config.validate() 重新合并回来。"""
    from wisland.core.config import Config

    cm = Config(tmp_path / "config.json")
    draft = cm.create_draft()
    draft.set("battery.aliases", {"dev_1": "耳机A", "dev_2": "键盘B"})
    ok, errs = cm.commit_draft(draft, strict=True)
    assert ok and not errs
    assert cm.get("battery.aliases") == {"dev_1": "耳机A", "dev_2": "键盘B"}

    # 清空 dev_1，只保留 dev_2，再全部清空
    draft2 = cm.create_draft()
    draft2.set("battery.aliases", {"dev_2": "键盘B"})
    ok2, errs2 = cm.commit_draft(draft2, strict=True)
    assert ok2 and not errs2
    assert cm.get("battery.aliases") == {"dev_2": "键盘B"}

    draft3 = cm.create_draft()
    draft3.set("battery.aliases", {})
    ok3, errs3 = cm.commit_draft(draft3, strict=True)
    assert ok3 and not errs3
    assert cm.get("battery.aliases") == {}
    cm2 = Config(tmp_path / "config.json")
    assert cm2.get("battery.aliases") == {}


def test_apple_ble_ignored_when_not_connected_to_windows(monkeypatch):
    """未连接本机 Windows 蓝牙时，附近孤立的 AirPods BLE 广播不得显示为在线设备；已连接时正常显示系统设备名。"""
    from wisland.services import battery_providers as bp

    monkeypatch.setattr(bp, "read_bluetooth_batteries", lambda: [])
    snap = {"model": "AirPods Pro", "percent": 80, "charging": False, "parts": [("右", 80, False)]}
    prov = bp.BluetoothBatteryProvider()

    # 断开本机连接 -> 不显示
    monkeypatch.setattr(bp, "get_connected_apple_bluetooth_devices", lambda: [])
    monkeypatch.setattr(bp, "has_connected_apple_bluetooth", lambda: False)
    assert prov.discover_and_read(snap, connected_apple=[]) == []

    # 已连接本机（PID 0x2024 <-> 0x2420 AirPods Pro）-> 正常显示并优先采用系统设备名
    conn = [{"mac": "282D7FDFF047", "name": "鹅夹似鹅鸡", "pid": 0x2024}]
    assert bp.AppleAdvertParser.model_for_pid(0x2024) == "AirPods Pro"
    res = prov.discover_and_read(snap, connected_apple=conn)
    assert len(res) == 1
    assert res[0][0].name == "鹅夹似鹅鸡"
    assert res[0][1].percent == 80


def test_apple_ble_state_manager_side_merge_and_disconnect_cleanup(monkeypatch):
    """参考 AirPodsDesktop：左/右耳分侧广播合并，按已连接型号过滤，断开时立刻清状态并停扫。"""
    from types import SimpleNamespace
    from PySide6 import QtWidgets
    from wisland.services import battery as bat_mod

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    svc = BatteryService()
    svc._started = True
    watcher_states: list[bool] = []
    monkeypatch.setattr(svc, "_start_apple_watcher", lambda: watcher_states.append(True))
    monkeypatch.setattr(svc, "_stop_apple_watcher", lambda: watcher_states.append(False))
    monkeypatch.setattr(bat_mod, "enumerate_hid", lambda: [])

    # 1) 模拟已连接 AirPods Pro（PID 0x2024）
    conn_state = [[{"mac": "282D7FDFF047", "name": "鹅夹似鹅鸡", "pid": 0x2024}]]
    monkeypatch.setattr(bat_mod, "get_connected_apple_bluetooth_devices", lambda: conn_state[0])
    monkeypatch.setattr(svc._bt_provider, "discover_and_read", lambda snap, connected_apple=None: [])

    svc._poll_sync(force_discover=True)
    app.processEvents()
    assert watcher_states[-1] is True
    assert svc._bound_apple_models == {"AirPods Pro"}

    def _mk_args(raw: bytes, rssi: int = -55, addr: int = 0x112233445566):
        mdata = SimpleNamespace(company_id=0x004C, data=raw)
        return SimpleNamespace(
            raw_signal_strength_in_dbm=rssi,
            bluetooth_address=addr,
            advertisement=SimpleNamespace(manufacturer_data=[mdata]),
        )

    # 2) 不同型号广播（AirPods 0x0220）应被过滤
    other_adv = bytes([0x07, 0x19, 0x01, 0x02, 0x20, 0x00, 0x99, 0x05])
    svc._on_ble_adv(None, _mk_args(other_adv, rssi=-45, addr=0x999999))
    assert svc._fresh_apple_adv() is None

    # 3) 右侧广播只含右耳 80%（左耳 0xF 不可用）+ 随后左侧广播含左耳 90% & 充电盒 60% -> 合并保留全部部位
    right_only = bytes([0x07, 0x19, 0x01, 0x24, 0x20, 0x20, 0x8F, 0x0F])  # flipped=False -> side=right, high=right=8, low=left=15
    svc._on_ble_adv(None, _mk_args(right_only, rssi=-52, addr=0xAAA1))
    snap1 = svc._fresh_apple_adv()
    assert snap1 is not None and ("右", 80, False) in snap1["parts"]

    left_and_case = bytes([0x07, 0x19, 0x01, 0x24, 0x20, 0x00, 0x9F, 0x46])  # flipped=True -> side=left, left=9, right=15, case=6 chg
    svc._on_ble_adv(None, _mk_args(left_and_case, rssi=-50, addr=0xAAA2))
    snap2 = svc._fresh_apple_adv()
    assert snap2 is not None
    assert ("左", 90, False) in snap2["parts"]
    assert ("右", 80, False) in snap2["parts"]
    assert ("盒", 60, True) in snap2["parts"]
    assert snap2["percent"] == 80

    # 4) 已锁定地址即使信号偏弱 (-90 dBm) 仍可更新
    right_drop = bytes([0x07, 0x19, 0x01, 0x24, 0x20, 0x20, 0x7F, 0x0F])  # right=70%
    svc._on_ble_adv(None, _mk_args(right_drop, rssi=-90, addr=0xAAA1))
    snap3 = svc._fresh_apple_adv()
    assert snap3 is not None and ("右", 70, False) in snap3["parts"]

    # 5) 断开系统蓝牙连接 -> 立即清空缓存并停止 BLE 扫描
    conn_state[0] = []
    svc._poll_sync(force_discover=False)
    app.processEvents()
    assert svc._fresh_apple_adv() is None
    assert svc._apple_known_addrs == set()
    assert watcher_states[-1] is False
    svc.stop()
