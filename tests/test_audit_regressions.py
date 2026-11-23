# -*- coding: utf-8 -*-
"""针对 Ledge 复核反馈 5 项 P1/P2 问题的专项回归测试。"""
from __future__ import annotations

from types import SimpleNamespace

from PySide6 import QtCore, QtWidgets

from wisland.core.config import Config, cfg
from wisland.core.credentials import InMemoryCredentialStore
from wisland.services.mail.cache import LocalMailBodyCache
from wisland.services.mail.manager import MailAccountManager
from wisland.services.mail.models import MailAccount, MailBody
from wisland.services.notifications import foreground as fg_mod
from wisland.services.notifications.controller import NotificationController
from wisland.services.notifications.foreground import ForegroundClientWatcher
from wisland.services.notifications.identity import WindowCandidate
from wisland.services.notifications.models import NotificationEvent
from wisland.services.quota.cache import LocalQuotaCache
from wisland.services.quota.manager import QuotaManager
from wisland.ui.island import Island
from wisland.ui.pages.base import Page
from wisland.ui.pages.info import WeatherPage
from wisland.ui.settings_window import SettingsWindow


def _app() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_01_foreground_win_hook_delivers_64bit_hwnd_and_clears_unread(monkeypatch):
    """P1 回归：WinEventHook 注册成功时，大于 32 位的 HWND 经队列投递到 _on_foreground_hwnd 并清除微信未读。"""
    app = _app()
    big_hwnd = 0x1_0000_1234  # > 32-bit unsigned int
    captured_cb = {}

    class _FakeUser32:
        def GetForegroundWindow(self):
            return 0

        def SetWinEventHook(self, _e1, _e2, _mod, cb, _pid, _tid, _flags):
            captured_cb["cb"] = cb
            return 0xABCD

        def UnhookWinEvent(self, _h):
            return True

    monkeypatch.setattr(fg_mod, "_user32", _FakeUser32())
    monkeypatch.setattr(fg_mod, "_WIN_EVENT_PROC", lambda fn: fn)

    seen_hwnds: list[int] = []

    def _is_active(h: int) -> bool:
        seen_hwnds.append(h)
        return h == big_hwnd

    def _pid_of(h: int) -> int:
        return 4242 if h == big_hwnd else 0

    def _proc_info(pid: int):
        if pid == 4242:
            return "wechat.exe", 100.0
        return "", None

    def _enum_wins(source_key: str):
        if source_key == "wechat":
            return [WindowCandidate(hwnd=big_hwnd, pid=4242, exe="wechat.exe", title="微信", visible=True, started_at=100.0)]
        return []

    ctrl = NotificationController()
    ev = NotificationEvent(
        event_id="wx:1",
        source_key="wechat",
        app_name="微信",
        app_identity="Tencent.Weixin",
        title="张三",
        body="你好",
        created_at=100.0,
        updated_at=100.0,
    )
    ctrl.ingest_event(ev)
    assert ctrl.counts.get("wechat", 0) == 1

    watcher = ForegroundClientWatcher(
        is_active_fn=_is_active,
        pid_of_hwnd_fn=_pid_of,
        process_info_fn=_proc_info,
        enumerator=_enum_wins,
        use_win_hook=True,
    )
    matched: list[str] = []
    watcher.foreground_matched.connect(lambda src: (matched.append(src), ctrl.clear(src)))

    try:
        watcher.update_unread_counts({"wechat": 1})
        assert "cb" in captured_cb, "WinEventHook 应当注册成功"
        assert not watcher._poll_timer.isActive(), "钩子注册成功后不应回退到轮询"

        # 直接以大于 32 位的合成 HWND 触发钩子回调
        captured_cb["cb"](0xABCD, fg_mod.EVENT_SYSTEM_FOREGROUND, big_hwnd, 0, 0, 0, 0)
        app.processEvents()

        assert big_hwnd in seen_hwnds
        assert matched == ["wechat"]
        assert ctrl.counts.get("wechat", 0) == 0
    finally:
        watcher.stop()


def test_02_settings_cancel_blocked_while_saving_preserves_pending_and_draft(tmp_path):
    """P1 回归：_saving=True 期间调用 cancel_and_close() 不得清空邮件/额度/字幕页待提交内容，也不得重置草稿。"""
    _app()
    local_cfg = Config(tmp_path / "cfg.json")
    creds = InMemoryCredentialStore()
    mail_mgr = MailAccountManager(config_obj=local_cfg, cred_store=creds, body_cache=LocalMailBodyCache(tmp_path / "mc"))
    quota_mgr = QuotaManager(config_obj=local_cfg, cred_store=creds, cache=LocalQuotaCache(tmp_path / "qc.json"))
    ctx = SimpleNamespace(mail=mail_mgr, quota=quota_mgr, live=None, battery=None)

    win = SettingsWindow(ctx=ctx, config_obj=local_cfg)
    try:
        mail_pg = next(pg for pg in win.leaf_pages if pg.key == "mail_accounts")
        quota_pg = next(pg for pg in win.leaf_pages if pg.key == "quota")
        sub_pg = next(pg for pg in win.leaf_pages if pg.key == "subtitles")
        mail_pg.ensure_built()
        quota_pg.ensure_built()
        sub_pg.ensure_built()

        # 注入三页待提交状态与草稿修改
        mail_pg._pending_secrets["acc-1"] = ("u@example.com", "secret-mail")
        quota_pg._pending_secrets["q-1"] = ("u-q", "secret-quota")
        sub_pg._pending_api_key = "sk-live-pending"
        win.draft.set("island.auto_cycle_s", 77)
        orig_draft_obj = win.draft

        # 模拟保存进行中（同时验证 btn_cancel 在 save_settings 期间被禁用）
        win._saving = True
        assert win.cancel_and_close(prompt_if_dirty=False) is False

        assert "acc-1" in mail_pg._pending_secrets
        assert "q-1" in quota_pg._pending_secrets
        assert sub_pg._pending_api_key == "sk-live-pending"
        assert win.draft is orig_draft_obj
        assert win.draft.get("island.auto_cycle_s") == 77

        # 验证真实 save_settings 入口在 processEvents() 时禁用了取消按钮，且结束后恢复
        win._saving = False
        cancel_states: list[bool] = []
        orig_pe = QtWidgets.QApplication.processEvents

        def _spy_pe(*a, **kw):
            cancel_states.append(win.btn_cancel.isEnabled())
            return orig_pe(*a, **kw)

        QtWidgets.QApplication.processEvents = _spy_pe
        try:
            win.save_settings()
        finally:
            QtWidgets.QApplication.processEvents = orig_pe
        assert cancel_states and cancel_states[0] is False
        assert win.btn_cancel.isEnabled() is True
    finally:
        win._saving = False
        win.close()
        mail_mgr.shutdown()
        quota_mgr.stop()


def test_03_mail_cache_no_stale_backfill_on_remove_or_uidvalidity_change(tmp_path):
    """P2 回归：锁外读取磁盘正文期间账号被删或 UIDVALIDITY 变更时，旧正文不得回填进内存缓存。"""
    _app()
    local_cfg = Config(tmp_path / "cfg.json")
    creds = InMemoryCredentialStore()
    disk_cache = LocalMailBodyCache(tmp_path / "mail_cache")
    mgr = MailAccountManager(config_obj=local_cfg, cred_store=creds, body_cache=disk_cache)

    try:
        acc = MailAccount(id="acc1", provider="custom", email="a@example.com", host="imap.example.com", port=993)
        mgr._accounts = [acc]
        mgr._uid_validities["acc1"] = "uv1"

        old_body = MailBody(account_id="acc1", uid="101", uid_validity="uv1", text="验证码 123456")
        disk_cache.put(old_body, None)

        # 场景 A：锁外磁盘读取期间账号被删除
        orig_get = disk_cache.get

        def _get_while_removing(account_id, uid, uid_validity=""):
            res = orig_get(account_id, uid, uid_validity=uid_validity)
            mgr.remove_account(account_id)
            return res

        disk_cache.get = _get_while_removing
        got = mgr.get_cached_body("acc1", "101", "uv1")
        assert got is None
        assert not any(k[0] == "acc1" for k in mgr._bodies)
        assert not any(k[0] == "acc1" for k in mgr._otp_cache)

        # 场景 B：锁外磁盘读取期间 UIDVALIDITY 切换为 uv2
        mgr._accounts = [acc]
        mgr._uid_validities["acc1"] = "uv1"
        disk_cache.put(old_body, None)

        def _get_while_uv_changes(account_id, uid, uid_validity=""):
            res = orig_get(account_id, uid, uid_validity=uid_validity)
            with mgr._lock:
                mgr._uid_validities[account_id] = "uv2"
            return res

        disk_cache.get = _get_while_uv_changes
        got2 = mgr.get_cached_body("acc1", "101", "uv1")
        assert got2 is None
        assert ("acc1", "uv1", "101") not in mgr._bodies
        assert ("acc1", "uv1", "101") not in mgr._otp_cache
    finally:
        mgr.shutdown()


def test_04_island_apply_config_passthrough_blocks_always_expand_and_toggle_exits_passthrough():
    """P2 回归：设置保存路径同时开启 always + passthrough 不得留下展开面板；托盘/热键 toggle() 主动展开应退出穿透。"""
    _app()

    class _DummyPage(Page):
        key = "home"
        glyph = "H"
        title = "首页"

    orig_mode = cfg.get("island.expand_mode", "hover")
    orig_pin = cfg.get("island.pinned", False)
    orig_pt = cfg.get("island.passthrough", False)
    orig_edge = cfg.get("island.edge", "top")

    isl = Island([_DummyPage()], peak_fn=lambda: 0.0)
    try:
        cfg.set("island.edge", "top", save=False)
        cfg.set("island.expand_mode", "always", save=False)
        cfg.set("island.pinned", True, save=False)
        cfg.set("island.passthrough", False, save=False)
        isl.apply_config()
        assert isl.expanded is True

        # 通过设置保存路径同时开启 always + passthrough
        cfg.set("island.expand_mode", "always", save=False)
        cfg.set("island.pinned", True, save=False)
        cfg.set("island.passthrough", True, save=False)
        isl.apply_config()
        assert isl.passthrough() is True
        assert isl.expanded is False

        # 穿透状态下直接 expand() 也应被拦截
        isl.expand()
        assert isl.expanded is False

        # 托盘/热键主动 toggle() 展开应自动退出穿透并正常展开
        isl.toggle()
        assert isl.passthrough() is False
        assert isl.expanded is True
    finally:
        cfg.set("island.expand_mode", orig_mode, save=False)
        cfg.set("island.pinned", orig_pin, save=False)
        cfg.set("island.passthrough", orig_pt, save=False)
        cfg.set("island.edge", orig_edge, save=False)
        isl.close()


def test_05_weather_add_city_selects_new_city_when_added_before_cities_updated():
    """P2 回归：两座旧城市 -> 添加第三座，city_added(True) 先于 cities_updated 到达时，最终应选中第三座新城市。"""
    _app()

    class _FakeWeather(QtCore.QObject):
        updated = QtCore.Signal(dict)
        cities_updated = QtCore.Signal(list)
        city_added = QtCore.Signal(bool, str)

        def __init__(self):
            super().__init__()
            self.added_queries: list[str] = []

        def add_city(self, q: str):
            self.added_queries.append(q)

        def refresh(self):
            pass

    w_svc = _FakeWeather()
    ctx = SimpleNamespace(weather=w_svc, open_settings=lambda *a, **k: None)
    pg = WeatherPage(ctx)
    try:
        c1 = {"name": "北京", "temp": 18, "desc": "晴", "icon": "01d"}
        c2 = {"name": "上海", "temp": 22, "desc": "多云", "icon": "02d"}
        c3 = {"name": "深圳", "temp": 27, "desc": "小雨", "icon": "10d"}

        w_svc.cities_updated.emit([c1, c2])
        assert pg.sel == 0

        # 模拟用户输入并添加第三座城市“深圳”
        pg.add_edit.setText("深圳")
        pg._do_add()
        assert w_svc.added_queries == ["深圳"]

        # 真实事件顺序：city_added(True, ...) 先到，cities_updated([c1, c2, c3]) 后到
        w_svc.city_added.emit(True, "已添加 深圳（CN）")
        w_svc.cities_updated.emit([c1, c2, c3])

        assert pg.sel == 2
        assert pg.city_btn.text == "深圳"
    finally:
        pg.close()


def test_06_mail_queued_prefetch_dropped_after_account_removed(tmp_path):
    """复核回归：删除账号后，Qt 队列中已排队的旧 _internal_prefetch_item 不得写入内存或磁盘，也不得发射 body_prefetched。"""
    import threading

    app = _app()
    local_cfg = Config(tmp_path / "cfg.json")
    creds = InMemoryCredentialStore()
    disk_cache = LocalMailBodyCache(tmp_path / "mail_cache")
    mgr = MailAccountManager(config_obj=local_cfg, cred_store=creds, body_cache=disk_cache)
    prefetched_events: list[tuple[str, str]] = []
    mgr.body_prefetched.connect(lambda aid, uid: prefetched_events.append((aid, uid)))

    try:
        acc = MailAccount(id="acc_del", provider="custom", email="del@example.com", host="imap.example.com", port=993)
        mgr._accounts = [acc]
        mgr._uid_validities["acc_del"] = "old"
        stale_body = MailBody(account_id="acc_del", uid="201", uid_validity="old", text="旧正文 999888")

        # 1) 从后台线程投递信号，使其进入主线程 Qt 队列暂不处理
        t = threading.Thread(target=lambda: mgr._internal_prefetch_item.emit("acc_del", stale_body))
        t.start()
        t.join()

        # 2) 在队列事件处理前删除账号
        assert mgr.remove_account("acc_del") is True

        # 3) 处理 Qt 队列中的旧预取事件
        app.processEvents()

        assert not any(k[0] == "acc_del" for k in mgr._bodies)
        assert disk_cache.get("acc_del", "201", uid_validity="old")[0] is None
        assert prefetched_events == []
    finally:
        mgr.shutdown()


def test_07_mail_queued_prefetch_dropped_after_uidvalidity_changed(tmp_path):
    """复核回归：UIDVALIDITY 通过正常同步切换为 new 后，队列中旧预取结果（old）不得写入内存/磁盘，也不得发射 body_prefetched。"""
    import threading

    app = _app()
    local_cfg = Config(tmp_path / "cfg.json")
    creds = InMemoryCredentialStore()
    disk_cache = LocalMailBodyCache(tmp_path / "mail_cache")
    mgr = MailAccountManager(config_obj=local_cfg, cred_store=creds, body_cache=disk_cache)
    prefetched_events: list[tuple[str, str]] = []
    mgr.body_prefetched.connect(lambda aid, uid: prefetched_events.append((aid, uid)))

    try:
        acc = MailAccount(id="acc_uv", provider="custom", email="uv@example.com", host="imap.example.com", port=993)
        mgr._accounts = [acc]
        mgr._uid_validities["acc_uv"] = "old"
        stale_body = MailBody(account_id="acc_uv", uid="301", uid_validity="old", text="旧纪元正文 111222")

        # 1) 后台线程发射旧预取信号留在队列
        t = threading.Thread(target=lambda: mgr._internal_prefetch_item.emit("acc_uv", stale_body))
        t.start()
        t.join()

        # 2) 通过正常 sync 回调将 UIDVALIDITY 切换为 new
        mgr._on_task_done("req-uv-1", "sync", True, "", {"account_id": "acc_uv", "uid_validity": "new", "headers": [], "bodies": []})
        assert mgr._uid_validities["acc_uv"] == "new"

        # 3) 处理队列中遗留的旧预取信号
        app.processEvents()

        assert ("acc_uv", "old", "301") not in mgr._bodies
        assert disk_cache.get("acc_uv", "301", uid_validity="old")[0] is None
        assert prefetched_events == []
    finally:
        mgr.shutdown()


def test_08_mail_prefetch_and_fetch_body_valid_vs_stale(tmp_path):
    """复核回归：账号和 UIDVALIDITY 匹配时预取与手动拉正文正常落盘并通知；手动拉正文遇过期 UIDVALIDITY 不入缓存。"""
    import threading

    app = _app()
    local_cfg = Config(tmp_path / "cfg.json")
    creds = InMemoryCredentialStore()
    disk_cache = LocalMailBodyCache(tmp_path / "mail_cache")
    mgr = MailAccountManager(config_obj=local_cfg, cred_store=creds, body_cache=disk_cache)
    prefetched_events: list[tuple[str, str]] = []
    loaded_events: list[tuple[str, str, bool]] = []
    mgr.body_prefetched.connect(lambda aid, uid: prefetched_events.append((aid, uid)))
    mgr.body_loaded.connect(lambda aid, uid, ok, _b, _e: loaded_events.append((aid, uid, ok)))

    try:
        acc = MailAccount(id="acc_ok", provider="custom", email="ok@example.com", host="imap.example.com", port=993)
        mgr._accounts = [acc]
        mgr._uid_validities["acc_ok"] = "uv_cur"

        good_body = MailBody(account_id="acc_ok", uid="401", uid_validity="uv_cur", text="有效正文")
        t = threading.Thread(target=lambda: mgr._internal_prefetch_item.emit("acc_ok", good_body))
        t.start()
        t.join()
        app.processEvents()

        assert ("acc_ok", "uv_cur", "401") in mgr._bodies
        assert disk_cache.get("acc_ok", "401", uid_validity="uv_cur")[0] is not None
        assert prefetched_events == [("acc_ok", "401")]

        # 手动 fetch_body 正常匹配 -> 写入缓存且 ok=True
        manual_ok = MailBody(account_id="acc_ok", uid="402", uid_validity="uv_cur", text="手动拉取有效正文")
        mgr._on_task_done("req-b1", "fetch_body", True, "", {"account_id": "acc_ok", "uid": "402", "body": manual_ok})
        assert ("acc_ok", "uv_cur", "402") in mgr._bodies
        assert disk_cache.get("acc_ok", "402", uid_validity="uv_cur")[0] is not None
        assert loaded_events[-1] == ("acc_ok", "402", True)

        # 手动 fetch_body 携带过期 UIDVALIDITY -> 不入内存/磁盘且 ok=False
        manual_stale = MailBody(account_id="acc_ok", uid="403", uid_validity="uv_old", text="手动拉取过期正文")
        mgr._on_task_done("req-b2", "fetch_body", True, "", {"account_id": "acc_ok", "uid": "403", "body": manual_stale})
        assert ("acc_ok", "uv_old", "403") not in mgr._bodies
        assert disk_cache.get("acc_ok", "403", uid_validity="uv_old")[0] is None
        assert loaded_events[-1] == ("acc_ok", "403", False)
    finally:
        mgr.shutdown()


def test_09_weather_stale_refresh_keeps_pending_until_new_city_arrives():
    """复核回归：添加成功 -> 旧刷新列表（不含新城市）-> 新列表（含新城市），旧列表到达时不跳转，新列表到达后选中深圳。"""
    _app()

    class _FakeWeather(QtCore.QObject):
        updated = QtCore.Signal(dict)
        cities_updated = QtCore.Signal(list)
        city_added = QtCore.Signal(bool, str)

        def add_city(self, _q: str):
            pass

        def refresh(self):
            pass

    w_svc = _FakeWeather()
    ctx = SimpleNamespace(weather=w_svc, open_settings=lambda *a, **k: None)
    pg = WeatherPage(ctx)
    try:
        c1 = {"name": "北京", "temp": 18, "desc": "晴", "icon": "01d"}
        c2 = {"name": "上海", "temp": 22, "desc": "多云", "icon": "02d"}
        c3 = {"name": "深圳", "temp": 27, "desc": "小雨", "icon": "10d"}

        w_svc.cities_updated.emit([c1, c2])
        assert pg.sel == 0
        assert pg.city_btn.text == "北京"

        # 发起添加“深圳”且 city_added(True) 先到
        pg.add_edit.setText("深圳")
        pg._do_add()
        w_svc.city_added.emit(True, "已添加 深圳（CN）")

        # 先收到添加前发起的旧刷新结果 [北京, 上海]：不得跳到上海，且保留待选状态
        w_svc.cities_updated.emit([c1, c2])
        assert pg.sel == 0
        assert pg.city_btn.text == "北京"
        assert pg._pending_select_new is True

        # 再收到包含新城市的最新结果 [北京, 上海, 深圳]：切换到深圳并清除待选状态
        w_svc.cities_updated.emit([c1, c2, c3])
        assert pg.sel == 2
        assert pg.city_btn.text == "深圳"
        assert pg._pending_select_new is False
    finally:
        pg.close()


def test_10_weather_add_city_failed_does_not_switch_on_later_refresh():
    """复核回归：添加城市失败后，后续普通刷新不会误触发切换。"""
    _app()

    class _FakeWeather(QtCore.QObject):
        updated = QtCore.Signal(dict)
        cities_updated = QtCore.Signal(list)
        city_added = QtCore.Signal(bool, str)

        def add_city(self, _q: str):
            pass

        def refresh(self):
            pass

    w_svc = _FakeWeather()
    ctx = SimpleNamespace(weather=w_svc, open_settings=lambda *a, **k: None)
    pg = WeatherPage(ctx)
    try:
        c1 = {"name": "北京", "temp": 18, "desc": "晴", "icon": "01d"}
        c2 = {"name": "上海", "temp": 22, "desc": "多云", "icon": "02d"}

        w_svc.cities_updated.emit([c1, c2])
        assert pg.sel == 0

        pg.add_edit.setText("不存在的城市")
        pg._do_add()
        w_svc.city_added.emit(False, "找不到城市：不存在的城市")

        # 后续普通刷新到达，应保持选中北京，不误切换
        w_svc.cities_updated.emit([c1, c2])
        assert pg.sel == 0
        assert pg.city_btn.text == "北京"
        assert pg._pending_select_new is False
    finally:
        pg.close()
