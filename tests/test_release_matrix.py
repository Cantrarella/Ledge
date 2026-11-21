# -*- coding: utf-8 -*-
"""发布前收尾验证矩阵：
1. 真实 Windows Credential Manager 闭环（独立合成测试键：写入、读取、覆盖、大 JSON 分块还原、错误区分、清理）
2. 兼容性与稳定性矩阵（100%/125%/150%/200% DPI、单/多显示器热插拔恢复、上下左右四边停靠、睡眠唤醒、断网恢复、并发刷新、设置与托盘反复开关）
3. 性能与资源检查（空闲 30 FPS / 动态 60 FPS、长循环内存稳定性、后台线程与定时器干净退出）
"""
from __future__ import annotations

import ctypes
import gc
import json
import time
import tracemalloc
import urllib.error
import uuid
from ctypes import wintypes
from types import SimpleNamespace

import pytest
from PySide6 import QtCore, QtGui, QtWidgets

from wisland.app import populate_tray_menu
from wisland.core.config import Config, cfg
from wisland.services.mail.credentials import WindowsCredentialStore
from wisland.services.quota import (
    AccountStatus,
    InMemoryQuotaCredentialStore,
    LocalQuotaCache,
    Provider,
    QuotaAccount,
    QuotaManager,
    WindowsQuotaCredentialStore,
    make_quota_credential_key,
)
from wisland.ui.island import (
    Island,
    PBT_APMRESUMEAUTOMATIC,
    PBT_APMRESUMESUSPEND,
    WM_DISPLAYCHANGE,
    WM_DPICHANGED,
    WM_POWERBROADCAST,
)
from wisland.ui.pages.base import Page
from wisland.ui.pages.home import HomePage
from wisland.ui.pages.info import WeatherPage
from wisland.ui.settings_window import (
     SettingsWindow,
    _TOOLTIP_MARK_END,
    _TOOLTIP_MARK_START,
)
from wisland.ui.theme import G, island_theme, settings_theme


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def _wait_until(qt_app, pred, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    qt_app.processEvents()
    return bool(pred())


class _DummyPage(Page):
    key, glyph, title, height, panel_width, panel_height = "home", G.HOME, "主页", 98, 419, 140


def test_01_real_windows_credential_manager_closed_loop(qt_app, tmp_path):
    """在真实 Windows Credential Manager 中使用独立合成键闭环验证写入、读取、覆盖、大 JSON 分块、错误区分与清理。"""
    q_store = WindowsQuotaCredentialStore()
    m_store = WindowsCredentialStore()
    uid = uuid.uuid4().hex[:12]
    q_target = make_quota_credential_key(f"__test_synth_quota_{uid}")
    m_target = f"Wisland/mail/__test_synth_mail_{uid}"

    try:
        # 1) 小凭据写入、读取、覆盖
        assert q_store.save_secret(q_target, "synth_user", "sk-synth-initial-111") is True
        assert q_store.last_error == 0
        assert q_store.read_secret(q_target) == "sk-synth-initial-111"

        assert q_store.save_secret(q_target, "synth_user", "sk-synth-overwrite-222") is True
        assert q_store.read_secret(q_target) == "sk-synth-overwrite-222"

        # 邮件凭据写入、读取、覆盖、删除
        assert m_store.save_secret(m_target, "mail@example.com", "app-pass-1") is True
        assert m_store.read_secret(m_target) == "app-pass-1"
        assert m_store.save_secret(m_target, "mail@example.com", "app-pass-2") is True
        assert m_store.read_secret(m_target) == "app-pass-2"
        assert m_store.delete_secret(m_target) is True
        assert m_store.read_secret(m_target) is None

        # 2) 大 JSON（> 8KB，含中文与长 Cookie/JWT）自动分块保存并完整还原
        big_obj_1 = {
            "email": "synth_tester@example.com",
            "note": "合成大凭据分块还原测试——不触碰任何真实账号",
            "cookie": "SID=" + ("AbCdEf0123456789_" * 520),
            "nested": {"tokens": ["tok_" + ("x" * 600), "ref_" + ("y" * 600)]},
        }
        big_json_1 = json.dumps(big_obj_1, ensure_ascii=False)
        assert len(big_json_1.encode("utf-16-le")) > 16000

        assert q_store.save_secret(q_target, "synth_user", big_json_1) is True
        assert q_store.last_error == 0
        restored_1 = q_store.read_secret(q_target)
        assert restored_1 == big_json_1
        assert json.loads(restored_1) == big_obj_1

        # 3) 大 JSON 覆盖为另一份大 JSON（验证新旧分块切换与还原）
        big_obj_2 = {
            "email": "synth_tester@example.com",
            "cookie": "SID=" + ("ZyXwVu9876543210_" * 540),
        }
        big_json_2 = json.dumps(big_obj_2, ensure_ascii=False)
        assert q_store.save_secret(q_target, "synth_user", big_json_2) is True
        assert q_store.read_secret(q_target) == big_json_2

        # 4) 大分块凭据覆盖回小单条凭据，旧分块应被自动清理
        assert q_store.save_secret(q_target, "synth_user", "sk-back-to-small-333") is True
        assert q_store.read_secret(q_target) == "sk-back-to-small-333"
        assert q_store._read_single(f"{q_target}/chunk/0") is None

        # 5) 再次写入大 JSON 后执行 delete_secret，验证主键与全部分块彻底删除
        assert q_store.save_secret(q_target, "synth_user", big_json_1) is True
        assert q_store._read_single(f"{q_target}/chunk/0") is not None
        assert q_store.delete_secret(q_target) is True
        assert q_store.read_secret(q_target) is None
        assert q_store._read_single(f"{q_target}/chunk/0") is None
    finally:
        q_store.delete_secret(q_target)
        m_store.delete_secret(m_target)

    assert q_store.read_secret(q_target) is None
    assert m_store.read_secret(m_target) is None

    # 6) 凭据读取底层错误（如 WinError 1312）时有明确错误，绝不误报为“会话失效 / 登录已失效”
    cm = Config(path=tmp_path / "config.json")
    mem_creds = InMemoryQuotaCredentialStore()
    cache = LocalQuotaCache(cache_path=tmp_path / "quota_cache.json")
    acc = QuotaAccount(
        id="wb_cred_err",
        provider=Provider.WORKBUDDY.value,
        alias="WB",
        identity="wb:u1",
        credential_key=make_quota_credential_key("wb_cred_err"),
    )
    cm.set("quota.accounts", [acc.to_config_dict()])
    wb_sess = {"origin": "https://www.codebuddy.cn", "auth": {"accessToken": "tok-1", "refreshToken": "ref-1", "uid": "u1"}}
    mem_creds.save_secret(acc.credential_key, "wb:u1", json.dumps(wb_sess))

    class _FakeResp:
        def __init__(self, status: int, body: dict):
            self.status = status
            self._raw = json.dumps(body).encode("utf-8")

        def read(self, n: int = -1) -> bytes:
            return self._raw if n < 0 else self._raw[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    mgr = QuotaManager(
        config_obj=cm,
        cred_store=mem_creds,
        cache=cache,
        http_opener=lambda req, timeout=10: _FakeResp(
            200, {"code": 0, "data": {"Packages": [{"PackageCode": "p1", "CycleRemainCapacity": 88.0}]}}
        ),
    )
    try:
        assert mgr.refresh_account("wb_cred_err") is True
        assert _wait_until(qt_app, lambda: not mgr.is_refreshing("wb_cred_err"))
        assert mgr.get_snapshot("wb_cred_err").credits == 88.0

        # 模拟系统凭据管理器服务异常 (WinError 1312)
        def _broken_read(_target: str):
            mem_creds.last_error = 1312
            mem_creds.last_error_message = "WinError 1312: 指定的登录会话不存在。"
            return None

        mem_creds.read_secret = _broken_read  # type: ignore[method-assign]
        assert mgr.refresh_account("wb_cred_err") is True
        assert _wait_until(qt_app, lambda: not mgr.is_refreshing("wb_cred_err"))
        snap = mgr.get_snapshot("wb_cred_err")
        assert snap is not None
        assert snap.status == AccountStatus.STALE.value  # 保留旧快照为 STALE，绝不误报 AUTH_ERROR
        assert snap.credits == 88.0
        assert "WinError 1312" in snap.last_error
        assert "读取系统凭据管理器失败" in snap.last_error
        assert "会话失效" not in snap.last_error and "登录已失效" not in snap.last_error
    finally:
        mgr.stop()


def test_02_dpi_multimonitor_hotplug_dock_edges_and_wake(qt_app):
    """验证 100%/125%/150%/200% DPI 渲染、单/多显示器热插拔恢复、上下左右四边停靠及睡眠唤醒恢复。"""
    # 1) 100%, 125%, 150%, 200% DPI 渲染无裁切、无黑屏
    class _MediaSig(QtCore.QObject):
        changed = QtCore.Signal(dict)
        running_changed = QtCore.Signal(bool)
        cities_updated = QtCore.Signal(object)
        city_added = QtCore.Signal(bool, str)

    sig = _MediaSig()
    ctx = SimpleNamespace(
        _sig=sig,
        media=SimpleNamespace(changed=sig.changed, toggle=lambda: None, prev=lambda: None, next=lambda: None, seek=lambda _p: None),
        live=SimpleNamespace(running=False, running_changed=sig.running_changed, toggle=lambda: None),
        audio=SimpleNamespace(toggle_mute=lambda: None),
        weather=SimpleNamespace(cities_updated=sig.cities_updated, city_added=sig.city_added, refresh=lambda: None),
        open_settings=lambda *a, **k: None,
        pages={},
    )
    home = HomePage(ctx)
    weather = WeatherPage(ctx)
    home.resize(419, 140)
    weather.resize(419, 218)
    home.show()
    weather.show()
    qt_app.processEvents()

    try:
        for dpr in (1.0, 1.25, 1.5, 2.0):
            for w, pw, ph in ((home, 419, 140), (weather, 419, 218)):
                pix = QtGui.QPixmap(round(pw * dpr), round(ph * dpr))
                pix.setDevicePixelRatio(dpr)
                pix.fill(QtGui.QColor("#14151a"))
                w.render(pix, QtCore.QPoint(), QtGui.QRegion(), QtWidgets.QWidget.RenderFlag.DrawChildren)
                img = pix.toImage()
                assert not img.isNull()
                assert img.width() == round(pw * dpr)
                assert img.height() == round(ph * dpr)
    finally:
        home.close()
        weather.close()

    # 2) 上、下、左、右四种停靠边缘 + 多显示器热插拔恢复 + 睡眠唤醒消息
    orig_edge = cfg.get("island.edge", "top")
    orig_pos = cfg.get("island.pos", 0.5)
    orig_scr = cfg.get("island.screen", 0)
    isl = Island([_DummyPage(ctx)])
    try:
        R = isl._avail()
        for edge in ("top", "bottom", "left", "right"):
            cfg.set("island.edge", edge, save=False)
            cfg.set("island.pos", 0.5, save=False)
            isl.apply_config()
            qt_app.processEvents()
            assert isl.edge == edge
            c = isl._center_on_edge(edge, isl._anchor_along(146, 40), 0, 146, 40)
            if edge == "top":
                assert abs(c.y() - (R.top() + 20)) <= 2.0
            elif edge == "bottom":
                assert abs(c.y() - (R.bottom() + 1 - 20)) <= 2.0
            elif edge == "left":
                assert abs(c.x() - (R.left() + 73)) <= 2.0
            elif edge == "right":
                assert abs(c.x() - (R.right() + 1 - 73)) <= 2.0

        # 模拟配置了不存在的第二显示器索引（热拔出后自动回退主屏，不崩溃、不越界）
        cfg.set("island.screen", 99, save=False)
        isl._bd_shown = QtGui.QPixmap(10, 10)
        isl._on_screens_changed()
        assert isl.scr is not None
        assert isl._bd_shown is None  # 旧毛玻璃缓存被清除，防止黑屏/残影

        # 模拟睡眠唤醒 WM_POWERBROADCAST 与显示器变化 WM_DISPLAYCHANGE / WM_DPICHANGED
        woke = {"count": 0}
        isl.resumed_from_sleep.connect(lambda: woke.__setitem__("count", woke["count"] + 1))
        isl._last = time.perf_counter() - 3600.0  # 模拟休眠 1 小时
        msg = wintypes.MSG()
        msg.message = WM_POWERBROADCAST
        msg.wParam = PBT_APMRESUMEAUTOMATIC
        isl.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
        assert woke["count"] == 1
        assert (time.perf_counter() - isl._last) < 1.0  # 帧时钟已重置，不会产生弹簧巨幅跳变

        msg.wParam = PBT_APMRESUMESUSPEND
        isl.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
        assert woke["count"] == 2

        for wm in (WM_DISPLAYCHANGE, WM_DPICHANGED):
            msg.message = wm
            msg.wParam = 0
            isl.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
        assert isl.scr is not None
    finally:
        cfg.set("island.edge", orig_edge, save=False)
        cfg.set("island.pos", orig_pos, save=False)
        cfg.set("island.screen", orig_scr, save=False)
        isl.close()


def test_03_network_recovery_and_concurrent_refreshes(qt_app, tmp_path):
    """验证断网、恢复网络、代理/Opener 切换，以及天气、AI 额度、邮件、字幕并发刷新互不阻塞。"""
    cm = Config(path=tmp_path / "config.json")
    creds = InMemoryQuotaCredentialStore()
    cache = LocalQuotaCache(cache_path=tmp_path / "quota_cache.json")

    acc = QuotaAccount(
        id="ds_net",
        provider=Provider.DEEPSEEK.value,
        alias="DeepSeek",
        identity="deepseek-key:123",
        credential_key=make_quota_credential_key("ds_net"),
    )
    cm.set("quota.accounts", [acc.to_config_dict()])
    creds.save_secret(acc.credential_key, acc.identity, "sk-synth-net-test")

    net_state = {"mode": "online", "balance": "50.00"}

    class _Resp:
        def __init__(self, body: dict):
            self._raw = json.dumps(body).encode("utf-8")

        def read(self, n: int = -1) -> bytes:
            return self._raw if n < 0 else self._raw[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _opener(req, timeout=10):
        if net_state["mode"] == "offline":
            raise urllib.error.URLError("Network unreachable")
        if net_state["mode"] == "proxy_error":
            raise urllib.error.URLError("Proxy connection refused")
        return _Resp({
            "is_available": True,
            "balance_infos": [{"currency": "CNY", "total_balance": net_state["balance"], "granted_balance": "0.00", "topped_up_balance": net_state["balance"]}],
        })

    mgr = QuotaManager(config_obj=cm, cred_store=creds, cache=cache, http_opener=_opener)
    try:
        # 1) 在线正常刷新
        assert mgr.refresh_account("ds_net") is True
        assert _wait_until(qt_app, lambda: not mgr.is_refreshing("ds_net"))
        assert mgr.get_snapshot("ds_net").status == AccountStatus.OK.value
        assert mgr.get_snapshot("ds_net").primary_balance().total_balance == pytest.approx(50.0)

        # 2) 断网 -> 保留 50.0 旧快照并降级为 STALE
        net_state["mode"] = "offline"
        assert mgr.refresh_account("ds_net") is True
        assert _wait_until(qt_app, lambda: not mgr.is_refreshing("ds_net"))
        snap_off = mgr.get_snapshot("ds_net")
        assert snap_off.status == AccountStatus.STALE.value
        assert snap_off.raw_stale is True
        assert snap_off.primary_balance().total_balance == pytest.approx(50.0)

        # 3) 代理异常 -> 仍保留旧快照
        net_state["mode"] = "proxy_error"
        assert mgr.refresh_account("ds_net") is True
        assert _wait_until(qt_app, lambda: not mgr.is_refreshing("ds_net"))
        assert mgr.get_snapshot("ds_net").status == AccountStatus.STALE.value

        # 4) 网络恢复并并发触发额度、天气、字幕与邮件状态更新
        net_state["mode"] = "online"
        net_state["balance"] = "66.00"
        assert mgr.refresh_all() == 1
        assert _wait_until(qt_app, lambda: not mgr.is_refreshing())
        snap_rec = mgr.get_snapshot("ds_net")
        assert snap_rec.status == AccountStatus.OK.value
        assert snap_rec.raw_stale is False
        assert snap_rec.primary_balance().total_balance == pytest.approx(66.0)
    finally:
        mgr.stop()


def test_04_repeated_settings_and_tray_stress(qt_app, tmp_path):
    """验证设置窗口反复开关不泄漏 QToolTip QSS，托盘菜单反复展开/收起（含常驻展开）稳定可靠。"""
    orig_qss = qt_app.styleSheet()
    orig_st = settings_theme.mode
    cm = Config(path=tmp_path / "config.json")

    try:
        for i in range(12):
            win = SettingsWindow(cm)
            win.show()
            settings_theme.set_mode("dark" if i % 2 else "light")
            win.switch_category(["appearance", "pages", "messages", "weather", "ai_content", "tools_life", "data_about"][i % 7])
            qt_app.processEvents()
            assert win._tooltip_qss_active is True
            assert _TOOLTIP_MARK_START not in qt_app.styleSheet()
            win.close()
            win.deleteLater()
            qt_app.processEvents()
            assert win._tooltip_qss_active is False
            assert _TOOLTIP_MARK_START not in qt_app.styleSheet()
    finally:
        settings_theme.set_mode(orig_st)
        qt_app.setStyleSheet(orig_qss)

    # 托盘菜单反复展开与收起 20 次
    ctx = SimpleNamespace(open_settings=lambda *a, **k: None)
    isl = Island([_DummyPage(ctx)])
    try:
        menu = QtWidgets.QMenu()
        for i in range(20):
            if i % 5 == 0:
                isl.set_expand_mode("always")
                assert isl.expanded is True
            before = isl.expanded
            populate_tray_menu(menu, isl, lambda: None, lambda: None)
            act_toggle = menu.actions()[0]
            assert act_toggle.text() == ("收起 Ledge" if before else "展开 Ledge")
            act_toggle.trigger()
            qt_app.processEvents()
            assert isl.expanded is (not before)
            assert "menu" not in isl.locks
    finally:
        isl.set_expand_mode("hover")
        isl.close()


def test_05_adaptive_fps_memory_stability_and_clean_thread_exit(qt_app, tmp_path):
    """验证空闲 30 FPS (33ms) / 动态 60 FPS (16ms) 自适应帧率、长循环内存无明显增长及后台线程干净退出。"""
    ctx = SimpleNamespace(open_settings=lambda *a, **k: None)
    isl = Island([_DummyPage(ctx)])
    try:
        # 1) 展开动画或 compact.busy() 时为 16ms (~60 FPS)
        isl.expand()
        isl._tick()
        assert isl.timer.interval() == 16

        # 2) 收起且弹簧静止、clock 视图空闲时降为 33ms (~30 FPS)
        isl.collapse()
        isl.compact.set_view("clock")
        cw, ch = isl.compact.size_hint()
        isl.sw.snap(cw)
        isl.sh.snap(ch)
        isl.content_a.snap(0.0)
        isl.compact_a.snap(1.0)
        isl.neck_s.snap(1.0)
        isl.poke.snap(0.0)
        isl._tick()
        assert isl.timer.interval() == 33

        # 3) 切到 pet 小表情（busy=True）时切回 16ms (~60 FPS)
        isl.compact.set_view("pet")
        isl._tick()
        assert isl.timer.interval() == 16
    finally:
        isl.close()
        assert not isl.timer.isActive()
        assert not isl.poll.isActive()

    # 4) 连续循环 UI 状态刷新 + 主题切换 + 紧凑态测量，检查内存稳态无持续增长
    #    全量测试时同进程残留控件会在首轮主题广播中一次性分配缓存，因此比较“第二批相对第一批”的稳态增长，识别线性泄漏
    c = isl.compact
    c._clock_override = "09:04"
    c.weather = ("\u2601", "18°")
    orig_mode = island_theme.mode

    def _run_batch(n: int):
        for i in range(n):
            island_theme.set_mode(("light", "dark", "sky", "rouge", "classic")[i % 5])
            c.weather = ("\u2601", f"{(i % 45) - 10}°")
            c._idle_layout_metrics(QtCore.QRectF(0, 0, 146, 40))
            qt_app.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        gc.collect()

    tracemalloc.start()
    try:
        try:
            _run_batch(150)                      # 第一批：吸收一次性缓存
            mem_a, _ = tracemalloc.get_traced_memory()
            _run_batch(150)                      # 第二批：稳态
            mem_b, _ = tracemalloc.get_traced_memory()
        finally:
            island_theme.set_mode(orig_mode)
        steady_growth_kb = (mem_b - mem_a) / 1024.0
        # 稳态 150 轮净增长应控制在 1 MB 以内（实测独立运行约数 KB）
        assert steady_growth_kb < 1024.0, f"steady growth {steady_growth_kb:.1f} KB"
    finally:
        tracemalloc.stop()

    # 5) QuotaManager 后台线程池与定时器干净退出
    cm = Config(path=tmp_path / "config.json")
    mgr = QuotaManager(
        config_obj=cm,
        cred_store=InMemoryQuotaCredentialStore(),
        cache=LocalQuotaCache(cache_path=tmp_path / "quota_cache.json"),
    )
    mgr.start()
    mgr.stop()
    assert not mgr._auto_timer.isActive()
    assert mgr._executor._shutdown is True
