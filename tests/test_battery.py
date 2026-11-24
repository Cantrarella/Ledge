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
    assert not svc._apple_known_addrs
    assert watcher_states[-1] is False
    svc.stop()


def test_apple_ble_review_fixes_pending_refresh_stale_side_and_no_cross_merge(monkeypatch):
    """回归：轮询中排队补刷、过期旧侧淘汰、已知地址上限、以及不串台合并到非 Apple 蓝牙耳机。"""
    import time
    from types import SimpleNamespace
    from PySide6 import QtWidgets
    from wisland.services import battery as bat_mod
    from wisland.services import battery_providers as bp

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    # 1) 非 Apple 蓝牙耳机与 AirPods 同时连接时，不得把 AirPods 电量串台合并到非 Apple 耳机上
    monkeypatch.setattr(
        bp,
        "read_bluetooth_batteries",
        lambda: [{"id": "bt:BTHENUM\\DEV_111122223333\\0", "name": "Sony WF-1000XM5 Earbuds", "percent": 65}],
    )
    snap = {"model": "AirPods Pro", "percent": 90, "charging": False, "parts": [("左", 90, False), ("右", 90, False)]}
    conn = [{"mac": "282D7FDFF047", "name": "鹅夹似鹅鸡", "pid": 0x2024}]
    res = bp.BluetoothBatteryProvider().discover_and_read(snap, connected_apple=conn)
    assert len(res) == 2
    by_name = {c.name: (c, r) for c, r in res}
    assert by_name["Sony WF-1000XM5 Earbuds"][0].brand == "Bluetooth"
    assert by_name["Sony WF-1000XM5 Earbuds"][1].percent == 65
    assert by_name["Sony WF-1000XM5 Earbuds"][1].parts == []
    assert by_name["鹅夹似鹅鸡"][0].brand == "Apple"
    assert by_name["鹅夹似鹅鸡"][1].percent == 90

    # 2) 新增型号映射（AirPods 4 / AirPods Max USB-C）
    assert bp.AppleAdvertParser.model_for_pid(0x2019) == "AirPods 4"
    assert bp.AppleAdvertParser.model_for_pid(0x201B) == "AirPods 4"
    assert bp.AppleAdvertParser.model_for_pid(0x201F) == "AirPods Max"

    # 3) _busy 期间触发 refresh() 会排队，在 _on_poll_done 后自动补刷一次
    svc = BatteryService()
    svc._started = True
    launched: list[bool] = []
    monkeypatch.setattr(bat_mod, "run_bg", lambda fn, on_done=None, on_error=None: launched.append(True))
    svc.refresh()
    assert len(launched) == 1 and svc._busy is True
    svc.refresh(force_discover=True)
    assert len(launched) == 1 and svc._refresh_pending is True and svc._refresh_pending_force is True
    svc._on_poll_done([])
    assert len(launched) == 2 and svc._refresh_pending is False

    # 4) 另一侧广播过期超过 _APPLE_SIDE_STALE_S 时自动淘汰旧充电盒/单耳数据，且已知地址不超过上限
    svc._connected_apple = conn
    svc._bound_apple_models = {"AirPods Pro"}
    old_ts = time.time() - (svc._APPLE_SIDE_STALE_S + 30.0)
    svc._apple_side_adv["left"] = (
        old_ts,
        {"model": "AirPods Pro", "percent": 90, "charging": False, "parts": [("左", 90, False), ("盒", 60, True)]},
    )
    right_only = bytes([0x07, 0x19, 0x01, 0x24, 0x20, 0x20, 0x8F, 0x0F])
    for i in range(10):
        args = SimpleNamespace(
            raw_signal_strength_in_dbm=-50,
            bluetooth_address=0xAA00 + i,
            advertisement=SimpleNamespace(manufacturer_data=[SimpleNamespace(company_id=0x004C, data=right_only)]),
        )
        svc._on_ble_adv(None, args)
    merged = svc._fresh_apple_adv()
    assert merged is not None
    assert merged["parts"] == [("右", 80, False)]
    assert len(svc._apple_known_addrs) <= svc._APPLE_MAX_KNOWN_ADDRS
    svc.stop()


def test_apple_fast_connection_probe_starts_and_stops_watcher_without_waiting_poll(monkeypatch):
    """快速蓝牙探测：连上/断开 AirPods 时无需等 60 秒轮询，立刻启停 BLE 扫描并触发刷新。"""
    from PySide6 import QtWidgets

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    svc = BatteryService()
    svc._started = True
    watcher_states: list[bool] = []
    refreshes: list[bool] = []
    monkeypatch.setattr(svc, "_start_apple_watcher", lambda: watcher_states.append(True))
    monkeypatch.setattr(svc, "_stop_apple_watcher", lambda: watcher_states.append(False))
    monkeypatch.setattr(svc, "refresh", lambda force_discover=False: refreshes.append(force_discover))

    # 1) 状态无变化（仍为空）-> 不重复触发刷新
    svc._on_apple_probe_done([])
    assert watcher_states == []
    assert refreshes == []

    # 2) 检测到连上 AirPods Pro -> 立刻开启 BLE 扫描并刷新
    conn = [{"mac": "282D7FDFF047", "name": "鹅夹似鹅鸡", "pid": 0x2024}]
    svc._on_apple_probe_done(conn)
    assert watcher_states == [True]
    assert len(refreshes) == 1
    assert svc._bound_apple_models == {"AirPods Pro"}

    # 3) 状态未变 -> 不重复触发刷新
    svc._on_apple_probe_done(conn)
    assert len(refreshes) == 1

    # 4) 检测到断开 -> 立刻清空缓存、停扫并刷新
    svc._apple_adv = (1.0, {"model": "AirPods Pro", "percent": 80, "charging": False, "parts": []})
    svc._on_apple_probe_done([])
    assert watcher_states[-1] is False
    assert len(refreshes) == 2
    assert svc._fresh_apple_adv() is None
    svc.stop()


def test_apple_ble_restart_cancel_switch_pair_and_query_error_guard(monkeypatch):
    """回归：断开/禁用后延迟重启不得空扫；切换同型号 AirPods 清空旧缓存并丢弃旧会话广播；枚举失败不假断连。"""
    from types import SimpleNamespace
    from PySide6 import QtWidgets

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    svc = BatteryService()
    svc._started = True
    starts: list[bool] = []
    stops: list[bool] = []
    monkeypatch.setattr(svc, "_start_apple_watcher", lambda: starts.append(True))
    monkeypatch.setattr(svc, "refresh", lambda force_discover=False: None)

    conn_a = [{"mac": "111111111111", "name": "AirPods A", "pid": 0x2024}]
    conn_b = [{"mac": "222222222222", "name": "AirPods B", "pid": 0x2024}]
    svc._on_apple_probe_done((True, conn_a))
    assert len(starts) == 1

    # 1) 扫描器意外停止安排 2 秒延迟重启 -> 期间断开 -> 到期回调不得重新启动扫描
    fake_w = SimpleNamespace(stop=lambda: stops.append(True))
    svc._watcher = fake_w
    svc._on_ble_stopped_main(fake_w)
    assert svc._ble_restart_timer.isActive() is True

    svc._on_apple_probe_done((True, []))
    assert svc._ble_restart_timer.isActive() is False
    svc._try_restart_apple_watcher()
    assert len(starts) == 1

    # 1b) 再次安排重启 -> 期间用户停止/关闭服务 -> 到期回调同样不得重启
    svc._on_apple_probe_done((True, conn_a))
    assert len(starts) == 2
    svc._watcher = fake_w
    svc._on_ble_stopped_main(fake_w)
    assert svc._ble_restart_timer.isActive() is True
    svc.stop()
    assert svc._ble_restart_timer.isActive() is False
    svc._try_restart_apple_watcher()
    assert len(starts) == 2

    # 2) 快速切换两副同型号 AirPods（A -> B，中间未经过空列表）-> 生产代码自动清空 A 缓存、重启扫描并使旧回调失效
    svc._started = True
    svc._on_apple_probe_done((True, conn_a))
    old_gen = svc._watcher_gen
    left_a = bytes([0x07, 0x19, 0x01, 0x24, 0x20, 0x00, 0x9F, 0x46])
    args_a = SimpleNamespace(
        raw_signal_strength_in_dbm=-50,
        bluetooth_address=0xAAA1,
        advertisement=SimpleNamespace(manufacturer_data=[SimpleNamespace(company_id=0x004C, data=left_a)]),
    )
    svc._on_ble_adv(None, args_a, watcher_gen=old_gen)
    assert svc._fresh_apple_adv() is not None
    assert 0xAAA1 in svc._apple_known_addrs

    starts_before_switch = len(starts)
    svc._watcher = fake_w
    stops_before_switch = len(stops)
    svc._on_apple_probe_done((True, conn_b))
    assert svc._fresh_apple_adv() is None
    assert not svc._apple_known_addrs
    assert len(stops) == stops_before_switch + 1
    assert len(starts) == starts_before_switch + 1
    assert svc._watcher_gen > old_gen

    # 不手动修改 _watcher_gen：直接投递切换前捕获的 old_gen 回调，必须被丢弃且 B 的缓存保持为空
    svc._on_ble_adv(None, args_a, watcher_gen=old_gen)
    assert svc._fresh_apple_adv() is None

    # 3) 从底层模拟 SetupAPI 句柄有效但 SetupDiEnumDeviceInfo 中途报错（非 ERROR_NO_MORE_ITEMS）-> 不假断连
    import ctypes
    from wisland.services import hid_native as hn

    cur_gen = svc._watcher_gen
    svc._on_ble_adv(None, args_a, watcher_gen=cur_gen)
    assert svc._fresh_apple_adv() is not None

    enum_err = [5]  # ERROR_ACCESS_DENIED
    destroyed = []

    class _FakeSetupApi:
        def SetupDiGetClassDevsW(self, *_a):
            return 0x1234

        def SetupDiEnumDeviceInfo(self, *_a):
            ctypes.set_last_error(enum_err[0])
            return 0

        def SetupDiDestroyDeviceInfoList(self, h):
            destroyed.append(h)
            return 1

    monkeypatch.setattr(hn, "_setupapi", _FakeSetupApi())
    ok_err, devs_err = hn.query_connected_apple_bluetooth_devices()
    assert ok_err is False and devs_err == [] and destroyed == [0x1234]

    svc._on_apple_probe_done(svc._query_connected_apple_safe())
    assert svc._connected_apple == conn_b
    assert svc._fresh_apple_adv() is not None

    # 正常枚举结束（ERROR_NO_MORE_ITEMS = 259）返回 (True, []) -> 立即清空缓存
    enum_err[0] = hn._ERROR_NO_MORE_ITEMS
    ok_empty, devs_empty = hn.query_connected_apple_bluetooth_devices()
    assert ok_empty is True and devs_empty == []

    svc._on_apple_probe_done(svc._query_connected_apple_safe())
    assert svc._connected_apple == []
    assert svc._fresh_apple_adv() is None
    svc.stop()


def test_apple_ble_start_failure_schedules_retry_and_probe_self_heals(monkeypatch):
    """当蓝牙适配器报“设备未就绪”导致 w.start() 失败时：首次 2s 快速重试，连续失败指数退避且心跳不抢跑，恢复后重置计数。"""
    import sys
    from types import ModuleType, SimpleNamespace
    from PySide6 import QtWidgets

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    svc = BatteryService()
    svc._started = True
    conn = [{"mac": "282D7FDFF047", "name": "鹅夹似鹅鸡", "pid": 0x2024}]
    svc._connected_apple = list(conn)
    monkeypatch.setattr(svc, "refresh", lambda force_discover=False: None)

    fail_left = [3]
    start_calls = [0]

    class _FakeWatcher:
        def __init__(self):
            self.scanning_mode = None
            self.advertisement_filter = SimpleNamespace(byte_patterns=[])
            self.status = 1

        def add_received(self, cb):
            self._rx = cb

        def add_stopped(self, cb):
            self._st = cb

        def start(self):
            start_calls[0] += 1
            if fail_left[0] > 0:
                fail_left[0] -= 1
                raise OSError("device not ready")

        def stop(self):
            pass

    class _FakeDataWriter:
        def write_bytes(self, _b):
            pass

        def detach_buffer(self):
            return b""

    adv_mod = ModuleType("winrt.windows.devices.bluetooth.advertisement")
    adv_mod.BluetoothLEAdvertisementBytePattern = lambda *a: a
    adv_mod.BluetoothLEAdvertisementWatcher = _FakeWatcher
    adv_mod.BluetoothLEScanningMode = SimpleNamespace(PASSIVE=0)
    streams_mod = ModuleType("winrt.windows.storage.streams")
    streams_mod.DataWriter = _FakeDataWriter
    for name, mod in [
        ("winrt.windows.devices.bluetooth.advertisement", adv_mod),
        ("winrt.windows.storage.streams", streams_mod),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)

    # 1) 连续 3 次 w.start() 失败 -> 间隔依次 2000 -> 4000 -> 8000ms
    svc._start_apple_watcher()
    assert svc._watcher is None and svc._ble_restart_timer.isActive() and svc._ble_restart_timer.interval() == 2000
    svc._try_restart_apple_watcher()
    assert svc._watcher is None and svc._ble_restart_timer.interval() == 4000
    svc._try_restart_apple_watcher()
    assert svc._watcher is None and svc._ble_restart_timer.interval() == 8000

    # 2) 在退避窗口期内即使 timer 被停，5 秒心跳也遵守退避时间不抢跑
    svc._ble_restart_timer.stop()
    calls_before_probe = start_calls[0]
    svc._on_apple_probe_done((True, conn))
    assert start_calls[0] == calls_before_probe

    # 3) 第 4 次恢复成功 -> 重置退避计数
    svc._try_restart_apple_watcher()
    assert svc._watcher is not None
    assert svc._ble_retry_fail_count == 0
    assert svc._ble_next_retry_at == 0.0

    # 4) 模拟 WinRT 每次回调传入不同的 sender 包装对象，只要 watcher_gen 匹配仍必须正常接收广播
    cur_gen = svc._watcher_gen
    svc._bound_apple_models = {"AirPods Pro"}
    right_pkt = bytes([0x07, 0x19, 0x01, 0x24, 0x20, 0x20, 0x8F, 0x0F])
    args = SimpleNamespace(
        raw_signal_strength_in_dbm=-45,
        bluetooth_address=0xBB01,
        advertisement=SimpleNamespace(manufacturer_data=[SimpleNamespace(company_id=0x004C, data=right_pkt)]),
    )
    svc._on_ble_adv(object(), args, watcher_gen=cur_gen)
    snap = svc._fresh_apple_adv()
    assert snap is not None and ("右", 80, False) in snap["parts"]
    svc.stop()
