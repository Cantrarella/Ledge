# -*- coding: utf-8 -*-
"""AI 额度第一阶段设置页测试与回归边界验证。"""
from __future__ import annotations

import json
import pathlib
import sys
import time

import pytest
from PySide6 import QtCore, QtWidgets

from wisland.core.config import Config, ConfigDraft
from wisland.services.quota import (
    AccountStatus,
    InMemoryQuotaCredentialStore,
    LocalQuotaCache,
    Provider,
    QuotaAccount,
    QuotaBalance,
    QuotaManager,
    QuotaSnapshot,
)
from wisland.ui.page_registry import PageRegistry
from wisland.ui.pages.base import Page
from wisland.ui.settings.quota import QuotaSettingsPage, _QuotaAccountCard
from wisland.ui.settings_window import SettingsWindow
from wisland.ui.theme import theme


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    return app


class _FakeResp:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _wait_until(qt_app, pred, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    qt_app.processEvents()
    return bool(pred())


def _make_quota_env(tmp_path):
    cfg_path = tmp_path / "config.json"
    cm = Config(path=cfg_path)
    creds = InMemoryQuotaCredentialStore()
    cache = LocalQuotaCache(cache_path=tmp_path / "quota_cache.json")

    def _opener(req, timeout=10):
        body = {
            "is_available": True,
            "balance_infos": [
                {"currency": "CNY", "total_balance": "66.60", "granted_balance": "6.60", "topped_up_balance": "60.00"}
            ],
        }
        return _FakeResp(200, json.dumps(body).encode("utf-8"))

    mgr = QuotaManager(config_obj=cm, cred_store=creds, cache=cache, http_opener=_opener)
    return cm, creds, cache, mgr, cfg_path


def test_01_settings_nav_order_and_real_actions_only(qt_app, tmp_path):
    cm, creds, _cache, mgr, _cfg_path = _make_quota_env(tmp_path)
    ctx = type("Ctx", (), {"quota": mgr})()
    win = SettingsWindow(ctx=ctx, config_obj=cm)
    assert len(win.pages) == 8
    group_keys = [k for k, _, _, _ in win._group_specs]
    group_titles = [t for _, _, t, _ in win._group_specs]
    assert group_keys == ["appearance", "pages", "messages", "weather", "ai_content", "battery", "tools_life", "data_about"]
    assert group_titles == ["外观与岛体", "页面与导航", "消息与邮件", "天气", "AI 与字幕", "设备电量", "工具与生活", "数据与关于"]

    keys = [pg.key for pg in win.leaf_pages]
    titles = [pg.title for pg in win.leaf_pages]
    assert group_keys.index("ai_content") == group_keys.index("weather") + 1
    assert "quota" in keys
    assert "AI 额度" in titles

    quota_pg: QuotaSettingsPage = next(pg for pg in win.leaf_pages if isinstance(pg, QuotaSettingsPage))
    # 设置页只显示真实可用操作：无假“识别本机账号”按钮，无“收起时显示常驻账号”
    assert not hasattr(quota_pg, "btn_detect_local")
    assert not hasattr(quota_pg, "cmb_compact_account")
    assert quota_pg.empty_card.isVisibleTo(quota_pg) or not quota_pg._draft_accounts
    win.close()
    mgr.stop()

def test_02_quota_cards_status_theme_and_no_horizontal_scroll(qt_app, tmp_path):
    cm, creds, cache, mgr, _cfg_path = _make_quota_env(tmp_path)

    acc_ok = QuotaAccount(id="ds_ok", provider=Provider.DEEPSEEK.value, alias="DS主力", identity="deepseek-key:111")
    acc_err = QuotaAccount(id="ds_err", provider=Provider.DEEPSEEK.value, alias="DS失效", identity="deepseek-key:222", last_error="API Key 无效")
    cm.set("quota.accounts", [acc_ok.to_config_dict(), acc_err.to_config_dict()])
    mgr.load_from_config()
    mgr._snapshots["ds_ok"] = QuotaSnapshot(
        account_id="ds_ok",
        fetched_at=time.time(),
        status=AccountStatus.OK.value,
        balances=[QuotaBalance("CNY", 66.6, 6.6, 60.0)],
    )
    mgr._snapshots["ds_err"] = QuotaSnapshot(
        account_id="ds_err",
        fetched_at=time.time(),
        status=AccountStatus.AUTH_ERROR.value,
        last_error="API Key 无效或已失效 (401/403)",
    )

    ctx = type("Ctx", (), {"quota": mgr})()
    win = SettingsWindow(ctx=ctx, config_obj=cm)
    win.resize(760, 560)
    win.show()
    win.switch_category("quota")
    qt_app.processEvents()

    quota_pg: QuotaSettingsPage = next(pg for pg in win.leaf_pages if isinstance(pg, QuotaSettingsPage))
    assert len(quota_pg._cards) == 2
    cards_by_id = {c.acc.id: c for c in quota_pg._cards}

    # 1) 默认折叠显示摘要/状态胶囊与“详情”按钮；点击“详情”展开后显示“移除”“监控”“常驻岛上”“刷新”“重新连接”
    c_ok: _QuotaAccountCard = cards_by_id["ds_ok"]
    assert not c_ok.expanded
    assert c_ok.btn_toggle_detail.text == "详情"
    assert "66.60" in c_ok.lbl_summary.text()

    quota_pg._toggle_card_expanded("ds_ok")
    quota_pg._toggle_card_expanded("ds_err")
    qt_app.processEvents()
    cards_by_id = {c.acc.id: c for c in quota_pg._cards}
    c_ok = cards_by_id["ds_ok"]
    c_err = cards_by_id["ds_err"]

    assert c_ok.expanded and c_ok.btn_toggle_detail.text == "收起"
    assert "66.60" in c_ok.lbl_main_bal.text()
    assert c_ok.btn_remove.isVisible()
    assert c_ok.btn_reconnect.isVisible()
    assert c_ok.btn_refresh.isEnabled()

    # 2) 错误卡：显示“重试”、“重新连接”与“移除”
    assert c_err.btn_refresh.text == "重试"
    assert c_err.btn_reconnect.isVisible()
    assert c_err.btn_remove.isVisible()

    # 行内改名直接生效并标记脏状态
    c_ok.ed_alias_inline.setText("DS新名字")
    c_ok._on_inline_alias_edited()
    assert c_ok.acc.alias == "DS新名字"
    assert "DS新名字" in c_ok.lbl_alias.text()

    # 3) 五个服务商均已完成接入闭环，添加下拉只显示已实现服务商
    combo_values = [quota_pg.cmb_provider.itemData(i) for i in range(quota_pg.cmb_provider.count())]
    assert combo_values == [
        Provider.DEEPSEEK.value,
        Provider.WORKBUDDY.value,
        Provider.GEMINI.value,
        Provider.GROK_BOT.value,
        Provider.CODEX.value,
    ]
    quota_pg.open_add_form(Provider.DEEPSEEK.value)
    assert quota_pg.btn_test_save.text == "测试并保存"
    quota_pg.close_add_form()

    # 4) 颜色菜单不常驻展开（卡片中不存在常驻颜色下拉/色板控件）
    assert not hasattr(c_ok, "color_picker")

    # 5) 浅色/深色主题切换无截断、无横向滚动
    cur_group = win.stack.currentWidget()
    scroll = cur_group.current_leaf() if hasattr(cur_group, "current_leaf") else cur_group
    assert isinstance(scroll, QtWidgets.QScrollArea)
    for mode in ("light", "dark"):
        theme.set_mode(mode)
        qt_app.processEvents()
        assert scroll.horizontalScrollBar().maximum() == 0
        for card in quota_pg._cards:
            assert card.width() <= scroll.viewport().width()

    win.close()
    mgr.stop()

def test_03_add_cancel_save_fail_and_delete_confirm_semantics(qt_app, tmp_path, monkeypatch):
    cm, creds, cache, mgr, cfg_path = _make_quota_env(tmp_path)
    ctx = type("Ctx", (), {"quota": mgr})()
    win = SettingsWindow(ctx=ctx, config_obj=cm)
    win.switch_category("quota")
    quota_pg: QuotaSettingsPage = next(pg for pg in win.leaf_pages if isinstance(pg, QuotaSettingsPage))

    # 1) 添加 DeepSeek 账号并测试：成功前仅在 _pending_secrets 中，取消后不写入凭据
    quota_pg.open_add_form(Provider.DEEPSEEK.value)
    quota_pg.ed_alias.setText("测试号")
    quota_pg.ed_api_key.setText("sk-temp-cancel-test")
    quota_pg._on_submit_add_form()
    assert _wait_until(qt_app, lambda: len(quota_pg._draft_accounts) == 1)
    temp_acc = quota_pg._draft_accounts[0]
    assert creds.read_secret(temp_acc.credential_key) is None  # 尚未点保存，不能写入凭据

    win.cancel_and_close(prompt_if_dirty=False)
    assert creds.read_secret(temp_acc.credential_key) is None
    assert cm.get("quota.accounts") == []

    # 2) 重新添加并点击保存设置：配置原子写入成功后才写入凭据
    win.show()
    quota_pg.open_add_form(Provider.DEEPSEEK.value)
    quota_pg.ed_alias.setText("正式号")
    quota_pg.ed_api_key.setText("sk-formal-12345")
    quota_pg._on_submit_add_form()
    assert _wait_until(qt_app, lambda: len(quota_pg._draft_accounts) == 1)
    saved_acc = quota_pg._draft_accounts[0]
    assert win.save_settings() is True
    assert creds.read_secret(saved_acc.credential_key) == "sk-formal-12345"
    assert "sk-formal-12345" not in cfg_path.read_text(encoding="utf-8")

    # 3) 删除账号需要确认；若配置保存失败，不得删除旧凭据
    from wisland.ui.widgets import ConfirmDialog
    monkeypatch.setattr(ConfirmDialog, "ask", staticmethod(lambda *a, **kw: True))
    quota_pg._on_delete_account(saved_acc)
    assert len(quota_pg._draft_accounts) == 0
    assert creds.read_secret(saved_acc.credential_key) == "sk-formal-12345"  # 未点保存，旧凭据仍在

    # 模拟配置校验失败
    quota_pg.sp_refresh_minutes.setValue(1)
    monkeypatch.setattr(cm, "commit_draft", lambda *a, **kw: (False, ["模拟配置保存失败"]))
    assert win.save_settings() is False
    assert creds.read_secret(saved_acc.credential_key) == "sk-formal-12345"

    # 恢复正常保存后，凭据与缓存才被清理
    monkeypatch.undo()
    assert win.save_settings() is True
    assert creds.read_secret(saved_acc.credential_key) is None
    assert cache.get(saved_acc.id) is None

    win.close()
    mgr.stop()

def test_04_quota_island_page_registered_and_msg_remains_removed(qt_app):
    reg = PageRegistry()
    for k in ("home", "mail", "quota", "weather", "trans", "tools", "life"):
        pg = type(f"P_{k}", (Page,), {"key": k, "glyph": "", "title": k})()
        reg.register(pg)
    assert reg.get_entry("msg") is None
    assert reg.get_entry("quota") is not None

    root = pathlib.Path(__file__).resolve().parents[1]
    app_src = (root / "wisland" / "app.py").read_text(encoding="utf-8")
    assert 'P["msg"]' not in app_src


def test_05_open_instance_focuses_quota_reconnect_account(qt_app, tmp_path, monkeypatch):
    cm, _creds, _cache, mgr, _cfg_path = _make_quota_env(tmp_path)
    acc = QuotaAccount(
        id="reconnect-me",
        provider=Provider.DEEPSEEK.value,
        alias="待重连",
        identity="deepseek-reconnect",
    )
    cm.set("quota.accounts", [acc.to_config_dict()])
    mgr.load_from_config()
    monkeypatch.setattr(SettingsWindow, "_instance", None)

    ctx = type("Ctx", (), {"quota": mgr})()
    SettingsWindow._instance = SettingsWindow(ctx=ctx, config_obj=cm)
    win = SettingsWindow.open_instance(
        ctx,
        category="quota",
        account_id=acc.id,
    )
    try:
        qt_app.processEvents()
        quota_pg = next(pg for pg in win.leaf_pages if isinstance(pg, QuotaSettingsPage))
        cur_group = win.stack.currentWidget()
        assert (cur_group.current_leaf() if hasattr(cur_group, "current_leaf") else cur_group) is quota_pg
        assert quota_pg._reconnect_target_acc is not None
        assert quota_pg._reconnect_target_acc.id == acc.id
        assert not quota_pg.add_card.isHidden()
        assert quota_pg.ed_alias.text() == "待重连"
    finally:
        win.close()
        SettingsWindow._instance = None
        mgr.stop()



def test_06_account_card_hides_identity_source_and_credential_fingerprints(qt_app, tmp_path):
    cm, _creds, _cache, mgr, _cfg_path = _make_quota_env(tmp_path)
    accounts = [
        QuotaAccount(id="a1", provider=Provider.DEEPSEEK.value, alias="DS主力", identity="deepseek-key:abc123", source="https://api.deepseek.com"),
        QuotaAccount(id="a2", provider=Provider.WORKBUDDY.value, alias="WB主力", identity="wb:slot999", source="https://www.codebuddy.cn"),
        QuotaAccount(id="a3", provider=Provider.GEMINI.value, alias="GM主力", identity="gemini:hash888", source="https://gemini.google.com/usage"),
        QuotaAccount(id="a4", provider=Provider.CODEX.value, alias="CX主力", identity="generic-key:codex1", source="C:/auth.json"),
    ]
    cm.set("quota.accounts", [a.to_config_dict() for a in accounts])
    mgr.load_from_config()

    ctx = type("Ctx", (), {"quota": mgr})()
    win = SettingsWindow(ctx=ctx, config_obj=cm)
    win.switch_category("quota")
    qt_app.processEvents()

    quota_pg = next(pg for pg in win.leaf_pages if isinstance(pg, QuotaSettingsPage))
    for narrow in (False, True):
        quota_pg._narrow_mode = narrow
        quota_pg._rebuild_account_cards()
        qt_app.processEvents()
        assert len(quota_pg._cards) == 4
        for card in quota_pg._cards:
            assert not hasattr(card, "lbl_source")
            texts: list[str] = []
            for w in card.findChildren(QtWidgets.QWidget):
                if hasattr(w, "text") and callable(w.text):
                    t = str(w.text() or "")
                    if t:
                        texts.append(t)
                elif hasattr(w, "_text") and isinstance(w._text, str):
                    texts.append(w._text)
                if w.toolTip():
                    texts.append(w.toolTip())
            joined = "\n".join(texts).lower()
            for forbidden in ("key:", "wb:", "gemini:", "deepseek-key:", "credential"):
                assert forbidden not in joined
            assert card.status_pill.isVisibleTo(card)

    win.close()
    mgr.stop()

def test_07_gemini_workbuddy_save_consistency_6kb_and_credwrite_failure(qt_app, tmp_path):
    cm = Config(path=tmp_path / "config.json")
    creds = InMemoryQuotaCredentialStore()
    cache = LocalQuotaCache(cache_path=tmp_path / "quota_cache.json")

    def _opener(req, timeout=10):
        if "gemini.google.com" in req.full_url:
            body = [
                {
                    "data": {
                        "viewer": {
                            "rpcs": {
                                "getUsageInfo": {
                                    "subscriptionTier": "TIER1",
                                    "pools": [
                                        {"poolType": "GXU_FIVE_HOURLY", "percentageUsed": 0.2},
                                        {"poolType": "GXU_WEEKLY", "percentageUsed": 0.1},
                                    ],
                                }
                            }
                        }
                    }
                }
            ]
            return _FakeResp(200, json.dumps(body).encode("utf-8"))
        return _FakeResp(
            200,
            json.dumps({
                "code": 0,
                "data": {"Packages": [{"PackageCode": "p1", "CycleRemainCapacity": 239.59}]},
            }).encode("utf-8"),
        )

    mgr = QuotaManager(config_obj=cm, cred_store=creds, cache=cache, http_opener=_opener)
    ctx = type("Ctx", (), {"quota": mgr})()
    win = SettingsWindow(ctx=ctx, config_obj=cm)
    win.switch_category("quota")
    quota_pg = next(pg for pg in win.leaf_pages if isinstance(pg, QuotaSettingsPage))

    # 1) Gemini 测试连接成功 -> 保存 -> 立即 read_secret 完全一致
    big_cookie = "SID=sid-secret-value; __Secure-1PSID=" + ("a" * 6200)
    quota_pg.open_add_form(Provider.GEMINI.value)
    quota_pg.ed_email.setText("user@example.com")
    quota_pg.ed_api_key.setText(big_cookie)
    quota_pg._on_submit_add_form()
    assert _wait_until(qt_app, lambda: len(quota_pg._draft_accounts) == 1)

    gm_acc = quota_pg._draft_accounts[0]
    expected_gm_json = quota_pg._pending_secrets[gm_acc.id]
    assert len(expected_gm_json.encode("utf-8")) > 6000
    assert win.save_settings() is True
    assert creds.read_secret(gm_acc.credential_key) == expected_gm_json

    # 2) 模拟 CredWriteW 失败：追加 WorkBuddy 6KB+ 会话，保存必须失败且保留 _pending_secrets
    wb_big_sess = json.dumps({
        "origin": "https://www.codebuddy.cn",
        "auth": {"accessToken": "wb-tok-" + ("b" * 6200), "refreshToken": "wb-ref-1", "uid": "wb-u1"},
        "slot": "slot-wb-1",
    })
    wb_acc = QuotaAccount(
        id="wb_6kb",
        provider=Provider.WORKBUDDY.value,
        alias="WB大凭据",
        identity="wb:slot-wb-1",
        credential_key=f"Wisland/quota/wb_6kb",
    )
    quota_pg._draft_accounts.append(wb_acc)
    quota_pg._pending_secrets[wb_acc.id] = wb_big_sess
    quota_pg._draft_snapshots[wb_acc.id] = QuotaSnapshot(account_id=wb_acc.id, status=AccountStatus.OK.value, credits=239.59)

    orig_save = creds.save_secret

    def _fail_cred(target, username, secret):
        creds.last_error = 5
        creds.last_error_message = "WinError 5: 拒绝访问"
        return False

    creds.save_secret = _fail_cred  # type: ignore[method-assign]
    assert win.save_settings() is False
    assert "保存失败" in win.status_lbl.text()
    assert "会话凭据保存失败，未写入系统凭据管理器" in win.status_lbl.text()
    assert "已保存并生效" not in win.status_lbl.text()
    assert wb_acc.id in quota_pg._pending_secrets
    creds.save_secret = orig_save  # type: ignore[method-assign]

    # 恢复后保存成功
    assert win.save_settings() is True
    assert creds.read_secret(wb_acc.credential_key) == wb_big_sess

    # config.json 与 quota_cache.json 不得包含 Cookie / accessToken / refreshToken
    cfg_raw = (tmp_path / "config.json").read_text(encoding="utf-8")
    cache_raw = (tmp_path / "quota_cache.json").read_text(encoding="utf-8")
    for sensitive in ("sid-secret-value", "wb-tok-", "wb-ref-1", "a" * 100, "b" * 100):
        assert sensitive not in cfg_raw
        assert sensitive not in cache_raw

    win.close()
    mgr.stop()

def test_08_save_settings_no_race_and_gemini_browser_connect(qt_app, tmp_path, monkeypatch):
    from wisland.services.quota.adapters import gemini as gm_mod

    cm = Config(path=tmp_path / "config.json")
    creds = InMemoryQuotaCredentialStore()
    cache = LocalQuotaCache(cache_path=tmp_path / "quota_cache.json")
    mgr = QuotaManager(config_obj=cm, cred_store=creds, cache=cache)

    # 1) 模拟监听 accounts_changed 时立刻刷新：在 save_settings 过程中绝不能在凭据写入前读到 None
    seen_errors: list[str] = []

    def _on_acc_changed():
        for a in mgr.list_accounts():
            sec = creds.read_secret(a.credential_key)
            if sec is None:
                seen_errors.append("missing_secret_during_signal")

    mgr.accounts_changed.connect(_on_acc_changed)

    ctx = type("Ctx", (), {"quota": mgr})()
    win = SettingsWindow(ctx=ctx, config_obj=cm)
    win.switch_category("quota")
    quota_pg = next(pg for pg in win.leaf_pages if isinstance(pg, QuotaSettingsPage))

    # 2) Gemini 浏览器自动登录模式（不填 Cookie 时触发 connect_gemini_browser）
    def _fake_browser_connect(account_id: str, expected_email: str = "", cancel_event=None, http_opener=None, timeout_s=300.0):
        sess = {"email": "auto@gmail.com", "cookie": "SID=auto-browser-sid"}
        snap = QuotaSnapshot(
            account_id=account_id,
            fetched_at=time.time(),
            status=AccountStatus.OK.value,
            plan_name="AI Pro",
        )
        return True, "", sess, snap

    monkeypatch.setattr(gm_mod, "connect_gemini_browser", _fake_browser_connect)
    # 同步替换 manager 中已导入的引用
    import wisland.services.quota.manager as mgr_mod
    monkeypatch.setattr(mgr_mod, "connect_gemini_browser", _fake_browser_connect)

    quota_pg.open_add_form(Provider.GEMINI.value)
    assert quota_pg.btn_test_save.text == "浏览器登录 Gemini"
    assert quota_pg.row_form_secret.isHidden()
    quota_pg._on_submit_add_form()
    assert _wait_until(qt_app, lambda: len(quota_pg._draft_accounts) == 1)

    assert win.save_settings() is True
    assert seen_errors == []
    gm_acc = quota_pg._draft_accounts[0]
    assert json.loads(creds.read_secret(gm_acc.credential_key) or "{}") == {
        "email": "auto@gmail.com",
        "cookie": "SID=auto-browser-sid",
    }

    win.close()
    mgr.stop()


def test_09_settings_tooltip_theme_and_qss_lifecycle(qt_app, tmp_path, monkeypatch):
    """验证设置窗口 QApplication 级 QToolTip 浅色/深色主题、防重复追加与关闭恢复。"""
    import os
    from pathlib import Path
    from wisland.ui.settings_window import _TOOLTIP_MARK_START
    from wisland.ui.theme import island_theme, settings_theme

    shot_dir = Path(os.environ.get("WISLAND_SETTINGS_TIP_SHOT_DIR") or (tmp_path / "tip_shots"))
    shot_dir.mkdir(parents=True, exist_ok=True)

    orig_app_qss = qt_app.styleSheet()
    orig_set_mode = settings_theme.mode
    orig_isl_mode = island_theme.mode
    base_qss = "QLabel { font-size: 12px; }"

    cm, creds, cache, mgr, _ = _make_quota_env(tmp_path)
    acc = QuotaAccount(
        id="acc_tip",
        provider=Provider.DEEPSEEK.value,
        alias="测试账号",
        identity="ds-tip",
        credential_key="Wisland/quota/acc_tip",
    )
    from dataclasses import asdict
    creds.save_secret(acc.credential_key, acc.identity, "sk-test-tip")
    cm.set("quota.accounts", [asdict(acc)])
    mgr.load_from_config()

    win = None
    try:
        qt_app.setStyleSheet(base_qss)
        cm.set("settings_window.theme", "light", save=False)
        ctx = type("Ctx", (), {"quota": mgr})()
        win = SettingsWindow(ctx=ctx, config_obj=cm)
        win.switch_category("quota")
        quota_pg = next(pg for pg in win.leaf_pages if isinstance(pg, QuotaSettingsPage))
        quota_pg.focus_account(acc.id)

        # 1) 打开前未注入，打开后不修改全局 qt_app.styleSheet()（避免全应用控件样式重排卡顿）
        win.show()
        qt_app.processEvents()
        assert qt_app.styleSheet() == base_qss
        assert win._tooltip_qss_active is True
        assert "SettingsWindow QToolTip" not in win.styleSheet()

        # 触发原生 QToolTip 渲染并检查背景色（浅色白底，非系统黑框）
        card = quota_pg._cards[0]
        target_w = card.btn_remove
        from PySide6 import QtGui
        gpos = win.mapToGlobal(win.rect().center())
        monkeypatch.setattr(QtGui.QCursor, "pos", staticmethod(lambda: gpos))
        tip_txt_light = target_w.toolTip() or "从监控列表移除该账号"
        QtWidgets.QToolTip.showText(gpos, tip_txt_light, win)
        _wait_until(
            qt_app,
            lambda: any(w.metaObject().className() == "QTipLabel" and w.isVisible() for w in qt_app.topLevelWidgets()),
            timeout_s=1.0,
        )
        tip_widget = next(
            (w for w in qt_app.topLevelWidgets() if w.metaObject().className() == "QTipLabel" and w.isVisible()),
            None,
        )
        assert tip_widget is not None
        assert _TOOLTIP_MARK_START in tip_widget.styleSheet()
        assert "#FFFFFF" in tip_widget.styleSheet() and "#181A20" in tip_widget.styleSheet()
        tip_img_light = tip_widget.grab().toImage()
        assert tip_img_light.save(str(shot_dir / "settings-tooltip-light.png"))
        bg_px_light = tip_img_light.pixelColor(tip_img_light.width() // 2, 4)
        assert bg_px_light.red() >= 235 and bg_px_light.green() >= 235 and bg_px_light.blue() >= 235
        QtWidgets.QToolTip.hideText()

        # 2) 切换岛体皮肤（classic/rouge）不影响设置窗口 Tooltip
        island_theme.set_mode("classic")
        qt_app.processEvents()
        assert settings_theme.dark is False

        # 3) 切换设置窗口为深色主题，Tooltip 立即变为 #242630 + 白字，且不污染全局 styleSheet
        settings_theme.set_mode("dark")
        qt_app.processEvents()
        assert qt_app.styleSheet() == base_qss

        tip_txt_dark = quota_pg.ed_alias.toolTip() or "修改此账号在岛上与设置中的显示名称"
        QtWidgets.QToolTip.showText(gpos, tip_txt_dark, win)
        _wait_until(
            qt_app,
            lambda: any(w.metaObject().className() == "QTipLabel" and w.isVisible() for w in qt_app.topLevelWidgets()),
            timeout_s=1.0,
        )
        tip_widget_dark = next(
            (w for w in qt_app.topLevelWidgets() if w.metaObject().className() == "QTipLabel" and w.isVisible()),
            None,
        )
        assert tip_widget_dark is not None
        assert "#242630" in tip_widget_dark.styleSheet() and "#F4F6FB" in tip_widget_dark.styleSheet()
        tip_img_dark = tip_widget_dark.grab().toImage()
        assert tip_img_dark.save(str(shot_dir / "settings-tooltip-dark.png"))
        bg_px_dark = tip_img_dark.pixelColor(tip_img_dark.width() // 2, 4)
        assert (bg_px_dark.red(), bg_px_dark.green(), bg_px_dark.blue()) == (36, 38, 48)
        QtWidgets.QToolTip.hideText()

        # 4) 反复开关设置窗口：关闭卸载过滤器并清空 QTipLabel 样式，保持全局 styleSheet 不变
        win.close()
        qt_app.processEvents()
        assert qt_app.styleSheet() == base_qss
        assert win._tooltip_qss_active is False
        assert not any(
            w.metaObject().className() == "QTipLabel" and _TOOLTIP_MARK_START in w.styleSheet()
            for w in qt_app.topLevelWidgets()
        )

        win.show()
        qt_app.processEvents()
        assert win._tooltip_qss_active is True
        win.close()
        qt_app.processEvents()
        assert qt_app.styleSheet() == base_qss
        assert win._tooltip_qss_active is False
    finally:
        QtWidgets.QToolTip.hideText()
        if win is not None:
            win.close()
        qt_app.setStyleSheet(orig_app_qss)
        settings_theme.set_mode(orig_set_mode)
        island_theme.set_mode(orig_isl_mode)
        mgr.stop()


def test_10_settings_save_atomicity_and_disk_write_rollback(qt_app, tmp_path, monkeypatch):
    """配置校验失败时不得先写入凭据；磁盘写失败不得污染内存；finalize_commit 失败应回滚配置。"""
    from wisland.ui.settings.subtitles import SubtitlesSettingsPage

    cm, creds, cache, mgr, _ = _make_quota_env(tmp_path)
    orig_interval = cm.get("weather.interval_min")

    # 1) 模拟磁盘拒写：commit_draft 抛异常后内存数据不得变成新值
    draft_bad_disk = cm.create_draft()
    draft_bad_disk.set("weather.interval_min", 45)
    monkeypatch.setattr(cm, "save", lambda: (_ for _ in ()).throw(PermissionError("disk readonly")))
    try:
        cm.commit_draft(draft_bad_disk, strict=True)
        assert False, "expected PermissionError"
    except PermissionError:
        pass
    assert cm.get("weather.interval_min") == orig_interval
    monkeypatch.undo()

    # 2) 配置校验失败时，不得先写入字幕凭据
    ctx = type("Ctx", (), {"quota": mgr})()
    win = SettingsWindow(ctx=ctx, config_obj=cm)
    try:
        sub_pg = next(pg for pg in win.leaf_pages if isinstance(pg, SubtitlesSettingsPage))
        sub_called = {"n": 0}
        monkeypatch.setattr(sub_pg, "has_pending_credentials", lambda: True)
        monkeypatch.setattr(sub_pg, "commit_credentials", lambda: (sub_called.__setitem__("n", sub_called["n"] + 1) or (True, "")))

        win.draft.set("weather.interval_min", 99999)  # 非法刷新间隔
        # 让 collect_to_draft 保留非法值
        monkeypatch.setattr(win, "collect_to_draft", lambda: win.draft)
        assert win.save_settings() is False
        assert sub_called["n"] == 0

        # 3) commit_credential_mutations 失败时，配置不得被修改
        win.draft.set("weather.interval_min", 30)
        quota_pg = next(pg for pg in win.leaf_pages if isinstance(pg, QuotaSettingsPage))
        monkeypatch.setattr(quota_pg, "commit_credentials", lambda: (True, ""))
        monkeypatch.setattr(quota_pg, "commit_credential_mutations", lambda: (False, "cred write failed"))
        assert win.save_settings() is False
        assert cm.get("weather.interval_min") == orig_interval
    finally:
        win.close()
        mgr.stop()


def test_11_settings_save_full_rollback_matrix(qt_app, tmp_path, monkeypatch):
    """验证跨模块保存失败回滚：校验失败、os.replace 拒写、配额第二笔凭据失败、回滚失败提示、仅广播一次 changed。"""
    import wisland.live.credentials as live_creds
    from wisland.services.mail.credentials import InMemoryCredentialStore, make_credential_key
    from wisland.services.mail.manager import MailAccountManager
    from wisland.services.mail.models import MailAccount
    from wisland.ui.settings.mail_accounts import MailAccountsSettingsPage
    from wisland.ui.settings.subtitles import SubtitlesSettingsPage

    cm, creds, cache, mgr, _ = _make_quota_env(tmp_path)
    sub_store = InMemoryCredentialStore()
    monkeypatch.setattr(live_creds, "_store", lambda: sub_store)
    live_creds.save_translate_api_key("sub-old-secret", config=cm, store=sub_store)
    monkeypatch.setattr(
        "wisland.ui.settings.subtitles.save_translate_api_key",
        lambda val: live_creds.save_translate_api_key(val, config=cm, store=sub_store),
    )
    monkeypatch.setattr(
        "wisland.ui.settings.subtitles.get_translate_api_key",
        lambda: live_creds.get_translate_api_key(config=cm, store=sub_store),
    )

    mail_creds = InMemoryCredentialStore()
    mail_acc = MailAccount(
        id="m1",
        email="old@example.com",
        provider="qq",
        host="imap.qq.com",
        port=993,
        security="ssl",
        username="old@example.com",
        credential_key=make_credential_key("m1"),
    )
    mail_creds.save_secret(mail_acc.credential_key, mail_acc.username, "mail-old-secret")
    cm.set("mail.accounts", [mail_acc.to_config_dict()], save=True)
    mail_mgr = MailAccountManager(config_obj=cm, cred_store=mail_creds)

    changed_events: list[int] = []
    cm.changed.connect(lambda: changed_events.append(cm.get("weather.interval_min")))

    ctx = type("Ctx", (), {"quota": mgr, "mail": mail_mgr})()
    win = SettingsWindow(ctx=ctx, config_obj=cm)
    try:
        sub_pg = next(pg for pg in win.leaf_pages if isinstance(pg, SubtitlesSettingsPage))
        mail_pg = next(pg for pg in win.leaf_pages if isinstance(pg, MailAccountsSettingsPage))
        quota_pg = next(pg for pg in win.leaf_pages if isinstance(pg, QuotaSettingsPage))

        # 路径 1：同次修改字幕密钥、删除邮件账号、改天气间隔，并让配额第二笔凭据写入失败
        sub_pg.ed_api_key.setText("sub-new-secret")
        mail_pg._draft_accounts = []
        mail_pg._deleted_accounts = [mail_acc]

        q1 = QuotaAccount(id="q1", provider=Provider.DEEPSEEK.value, alias="Q1", identity="q1-id", credential_key="Wisland/quota/q1")
        q2 = QuotaAccount(id="q2", provider=Provider.DEEPSEEK.value, alias="Q2", identity="q2-id", credential_key="Wisland/quota/q2")
        quota_pg._draft_accounts = [q1, q2]
        quota_pg._pending_secrets = {"q1": "q1-new-secret", "q2": "q2-new-secret"}

        orig_save_secret = creds.save_secret
        def _fail_second_quota(target, username, secret):
            if target == q2.credential_key:
                creds.last_error = 5
                return False
            return orig_save_secret(target, username, secret)
        monkeypatch.setattr(creds, "save_secret", _fail_second_quota)

        orig_interval = cm.get("weather.interval_min")
        win.draft.set("weather.interval_min", 25)
        monkeypatch.setattr(win, "collect_to_draft", lambda: win.draft)

        assert win.save_settings() is False
        # 配置内存/磁盘未变，运行中服务从未收到未提交配置
        assert cm.get("weather.interval_min") == orig_interval
        assert changed_events == []
        # 字幕旧密钥恢复，邮件旧授权码未被删除，配额第一笔新增凭据被回滚清除
        assert sub_store.read_secret(live_creds.TRANSLATE_CREDENTIAL_KEY) == "sub-old-secret"
        assert mail_creds.read_secret(mail_acc.credential_key) == "mail-old-secret"
        assert creds.read_secret(q1.credential_key) is None
        assert "保存失败" in win.status_lbl.text()

        # 路径 2：os.replace 拒写时，不抛出异常，且回滚已写入的字幕密钥
        monkeypatch.setattr(creds, "save_secret", orig_save_secret)
        quota_pg._draft_accounts = []
        quota_pg._pending_secrets = {}
        monkeypatch.setattr("wisland.core.config.os.replace", lambda *_a, **_kw: (_ for _ in ()).throw(PermissionError("readonly")))
        assert win.save_settings() is False
        assert cm.get("weather.interval_min") == orig_interval
        assert changed_events == []
        assert sub_store.read_secret(live_creds.TRANSLATE_CREDENTIAL_KEY) == "sub-old-secret"

        # 路径 3：回滚本身也失败时，界面明确报告回滚失败且不含密钥明文
        monkeypatch.setattr(sub_pg, "rollback_credentials", lambda: False)
        assert win.save_settings() is False
        status_txt = win.status_lbl.text()
        assert "回滚失败" in status_txt
        assert "sub-new-secret" not in status_txt
        assert "sub-old-secret" not in status_txt
        monkeypatch.undo()

        # 路径 4：全部成功时只广播一次配置变更
        changed_events.clear()
        win.draft.set("weather.interval_min", 25)
        monkeypatch.setattr(win, "collect_to_draft", lambda: win.draft)
        assert win.save_settings() is True
        assert changed_events == [25]
    finally:
        win.close()
        mgr.stop()


def test_12_quota_rollback_failure_and_delete_secret_failure(qt_app, tmp_path, monkeypatch):
    """验证配额凭据回滚失败提示，以及删除邮箱/配额凭据失败时回滚配置且不显示保存成功。"""
    from wisland.services.mail.credentials import InMemoryCredentialStore, make_credential_key
    from wisland.services.mail.manager import MailAccountManager
    from wisland.services.mail.models import MailAccount
    from wisland.ui.settings.mail_accounts import MailAccountsSettingsPage

    cm, creds, cache, mgr, _ = _make_quota_env(tmp_path)
    mail_creds = InMemoryCredentialStore()
    mail_acc = MailAccount(
        id="m_del",
        email="del@example.com",
        provider="qq",
        host="imap.qq.com",
        port=993,
        security="ssl",
        username="del@example.com",
        credential_key=make_credential_key("m_del"),
    )
    mail_creds.save_secret(mail_acc.credential_key, mail_acc.username, "mail-del-secret")

    q_existing = QuotaAccount(id="q_del", provider=Provider.DEEPSEEK.value, alias="QD", identity="qd-id", credential_key="Wisland/quota/q_del")
    creds.save_secret(q_existing.credential_key, q_existing.identity, "qd-secret")

    cm.set("mail.accounts", [mail_acc.to_config_dict()], save=True)
    cm.set("quota.accounts", [q_existing.__dict__], save=True)
    mgr.load_from_config()
    mail_mgr = MailAccountManager(config_obj=cm, cred_store=mail_creds)

    changed_events: list[int] = []
    cm.changed.connect(lambda: changed_events.append(1))

    ctx = type("Ctx", (), {"quota": mgr, "mail": mail_mgr})()
    win = SettingsWindow(ctx=ctx, config_obj=cm)
    try:
        mail_pg = next(pg for pg in win.leaf_pages if isinstance(pg, MailAccountsSettingsPage))
        quota_pg = next(pg for pg in win.leaf_pages if isinstance(pg, QuotaSettingsPage))
        monkeypatch.setattr(win, "collect_to_draft", lambda: win.draft)

        # 1) 配额第二笔写入失败且第一笔回滚删除也失败 -> 界面必须明确提示回滚失败
        q1 = QuotaAccount(id="q1", provider=Provider.DEEPSEEK.value, alias="Q1", identity="q1-id", credential_key="Wisland/quota/q1")
        q2 = QuotaAccount(id="q2", provider=Provider.DEEPSEEK.value, alias="Q2", identity="q2-id", credential_key="Wisland/quota/q2")
        quota_pg._draft_accounts = [q_existing, q1, q2]
        quota_pg._pending_secrets = {"q1": "q1-secret-val", "q2": "q2-secret-val"}

        orig_save = creds.save_secret
        orig_del = creds.delete_secret
        monkeypatch.setattr(creds, "save_secret", lambda t, u, s: False if t == q2.credential_key else orig_save(t, u, s))
        monkeypatch.setattr(creds, "delete_secret", lambda t: False if t == q1.credential_key else orig_del(t))

        assert win.save_settings() is False
        assert "回滚失败" in win.status_lbl.text()
        assert "q1-secret-val" not in win.status_lbl.text()
        assert changed_events == []

        # 2) 删除邮箱凭据失败 -> 配置回滚保留该账号，不广播变更，界面提示保存失败
        monkeypatch.setattr(creds, "save_secret", orig_save)
        monkeypatch.setattr(creds, "delete_secret", orig_del)
        quota_pg._draft_accounts = [q_existing]
        quota_pg._pending_secrets = {}
        mail_pg._draft_accounts = []
        mail_pg._deleted_accounts = [mail_acc]
        win.draft.set("mail.accounts", [])
        monkeypatch.setattr(mail_creds, "delete_secret", lambda _t: False)

        assert win.save_settings() is False
        assert len(cm.get("mail.accounts")) == 1
        assert changed_events == []
        assert "保存失败" in win.status_lbl.text()

        # 3) 删除配额凭据失败 -> 配置回滚保留该账号，不广播变更，界面提示保存失败
        monkeypatch.setattr(mail_creds, "delete_secret", InMemoryCredentialStore.delete_secret.__get__(mail_creds, InMemoryCredentialStore))
        mail_pg._draft_accounts = [mail_acc]
        mail_pg._deleted_accounts = []
        win.draft.set("mail.accounts", [mail_acc.to_config_dict()])
        quota_pg._draft_accounts = []
        quota_pg._deleted_accounts = [q_existing]
        win.draft.set("quota.accounts", [])
        monkeypatch.setattr(creds, "delete_secret", lambda _t: False)

        assert win.save_settings() is False
        assert len(cm.get("quota.accounts")) == 1
        assert changed_events == []
        assert "保存失败" in win.status_lbl.text()
    finally:
        win.close()
        mgr.stop()


def test_13_multi_delete_rollback_and_single_signal_commit(qt_app, tmp_path, monkeypatch):
    """验证多账号删除失败回滚（2邮箱/2配额/跨模块混删）、恢复失败提示及全成功时单次广播。"""
    from dataclasses import asdict
    from wisland.services.mail.credentials import InMemoryCredentialStore, make_credential_key
    from wisland.services.mail.manager import MailAccountManager
    from wisland.services.mail.models import MailAccount
    from wisland.services.quota.models import QuotaSnapshot
    from wisland.ui.settings.mail_accounts import MailAccountsSettingsPage

    cm, creds, cache, mgr, _ = _make_quota_env(tmp_path)
    mail_creds = InMemoryCredentialStore()

    m1 = MailAccount(id="m1", email="m1@example.com", provider="qq", host="imap.qq.com", port=993, security="ssl", username="m1@example.com", credential_key=make_credential_key("m1"))
    m2 = MailAccount(id="m2", email="m2@example.com", provider="qq", host="imap.qq.com", port=993, security="ssl", username="m2@example.com", credential_key=make_credential_key("m2"))
    mail_creds.save_secret(m1.credential_key, m1.username, "mail-sec-1")
    mail_creds.save_secret(m2.credential_key, m2.username, "mail-sec-2")

    q1 = QuotaAccount(id="q1", provider=Provider.DEEPSEEK.value, alias="Q1", identity="q1-id", credential_key="Wisland/quota/q1")
    q2 = QuotaAccount(id="q2", provider=Provider.DEEPSEEK.value, alias="Q2", identity="q2-id", credential_key="Wisland/quota/q2")
    creds.save_secret(q1.credential_key, q1.identity, "quota-sec-1")
    creds.save_secret(q2.credential_key, q2.identity, "quota-sec-2")
    cache.put(QuotaSnapshot(account_id="q1", fetched_at=100.0, status="ok"), flush=True)
    cache.put(QuotaSnapshot(account_id="q2", fetched_at=100.0, status="ok"), flush=True)

    cm.set("mail.accounts", [m1.to_config_dict(), m2.to_config_dict()], save=True)
    cm.set("quota.accounts", [asdict(q1), asdict(q2)], save=True)
    mgr.load_from_config()
    mail_mgr = MailAccountManager(config_obj=cm, cred_store=mail_creds)

    cfg_signals: list[tuple] = []
    mail_signals: list[list[str]] = []
    quota_signals: list[list[str]] = []
    cm.changed.connect(lambda: cfg_signals.append((len(cm.get("mail.accounts")), len(cm.get("quota.accounts")))))
    mail_mgr.accounts_changed.connect(lambda: mail_signals.append([a.id for a in mail_mgr.list_accounts()]))
    mgr.accounts_changed.connect(lambda: quota_signals.append([a.id for a in mgr.list_accounts()]))

    ctx = type("Ctx", (), {"quota": mgr, "mail": mail_mgr})()
    win = SettingsWindow(ctx=ctx, config_obj=cm)
    try:
        mail_pg = next(pg for pg in win.leaf_pages if isinstance(pg, MailAccountsSettingsPage))
        quota_pg = next(pg for pg in win.leaf_pages if isinstance(pg, QuotaSettingsPage))
        monkeypatch.setattr(win, "collect_to_draft", lambda: win.draft)

        orig_mail_del = mail_creds.delete_secret
        orig_mail_save = mail_creds.save_secret
        orig_quota_del = creds.delete_secret
        orig_quota_save = creds.save_secret

        # 1) 删除两个邮箱账号：m1 删成功，m2 删失败 -> 配置、m1/m2 凭据全部恢复，无广播
        mail_pg._draft_accounts = []
        mail_pg._deleted_accounts = [m1, m2]
        win.draft.set("mail.accounts", [])
        monkeypatch.setattr(mail_creds, "delete_secret", lambda t: False if t == m2.credential_key else orig_mail_del(t))

        assert win.save_settings() is False
        assert len(cm.get("mail.accounts")) == 2
        assert mail_creds.read_secret(m1.credential_key) == "mail-sec-1"
        assert mail_creds.read_secret(m2.credential_key) == "mail-sec-2"
        assert [a.id for a in mail_mgr.list_accounts()] == ["m1", "m2"]
        assert cfg_signals == [] and mail_signals == [] and quota_signals == []

        # 2) 删除两个配额账号：q1 删成功，q2 删失败 -> 配置、q1/q2 凭据、缓存与运行时列表全部保留，无广播
        monkeypatch.setattr(mail_creds, "delete_secret", orig_mail_del)
        mail_pg._draft_accounts = [m1, m2]
        mail_pg._deleted_accounts = []
        win.draft.set("mail.accounts", [m1.to_config_dict(), m2.to_config_dict()])

        quota_pg._draft_accounts = []
        quota_pg._deleted_accounts = [q1, q2]
        win.draft.set("quota.accounts", [])
        monkeypatch.setattr(creds, "delete_secret", lambda t: False if t == q2.credential_key else orig_quota_del(t))

        assert win.save_settings() is False
        assert len(cm.get("quota.accounts")) == 2
        assert creds.read_secret(q1.credential_key) == "quota-sec-1"
        assert creds.read_secret(q2.credential_key) == "quota-sec-2"
        assert cache.get("q1") is not None and cache.get("q2") is not None
        assert [a.id for a in mgr.list_accounts()] == ["q1", "q2"]
        assert cfg_signals == [] and mail_signals == [] and quota_signals == []

        # 3) 同一次保存删除 q1 和 m1：q1 凭据删成功，m1 删失败 -> q1 凭据/缓存/运行时恢复，无任何变更广播
        monkeypatch.setattr(creds, "delete_secret", orig_quota_del)
        quota_pg._draft_accounts = [q2]
        quota_pg._deleted_accounts = [q1]
        win.draft.set("quota.accounts", [asdict(q2)])
        mail_pg._draft_accounts = [m2]
        mail_pg._deleted_accounts = [m1]
        win.draft.set("mail.accounts", [m2.to_config_dict()])
        monkeypatch.setattr(mail_creds, "delete_secret", lambda t: False if t == m1.credential_key else orig_mail_del(t))

        assert win.save_settings() is False
        assert len(cm.get("quota.accounts")) == 2 and len(cm.get("mail.accounts")) == 2
        assert creds.read_secret(q1.credential_key) == "quota-sec-1"
        assert mail_creds.read_secret(m1.credential_key) == "mail-sec-1"
        assert cache.get("q1") is not None
        assert [a.id for a in mgr.list_accounts()] == ["q1", "q2"]
        assert [a.id for a in mail_mgr.list_accounts()] == ["m1", "m2"]
        assert cfg_signals == [] and mail_signals == [] and quota_signals == []

        # 4) 恢复已删除凭据本身也失败时，界面明确提示回滚失败且不含凭据明文
        monkeypatch.setattr(creds, "save_secret", lambda t, u, s: False if t == q1.credential_key else orig_quota_save(t, u, s))
        assert win.save_settings() is False
        st_txt = win.status_lbl.text()
        assert "回滚失败" in st_txt
        assert "quota-sec-1" not in st_txt and "mail-sec-1" not in st_txt

        # 5) 同批操作全部成功 -> 账号、凭据与缓存全部清除，配置与管理器各只广播一次最终状态
        monkeypatch.setattr(creds, "save_secret", orig_quota_save)
        monkeypatch.setattr(mail_creds, "delete_secret", orig_mail_del)
        assert win.save_settings() is True
        assert [a.id for a in mgr.list_accounts()] == ["q2"]
        assert [a.id for a in mail_mgr.list_accounts()] == ["m2"]
        assert creds.read_secret(q1.credential_key) is None
        assert mail_creds.read_secret(m1.credential_key) is None
        assert cache.get("q1") is None
        assert cfg_signals == [(1, 1)]
        assert quota_signals == [["q2"]]
        assert mail_signals == [["m2"]]
    finally:
        win.close()
        mgr.stop()


def test_14_chunked_quota_partial_delete_rollback(qt_app, tmp_path, monkeypatch):
    """分块配额凭据部分删除失败时，两笔凭据（含主清单/分块/校验和）必须完整恢复；恢复失败须提示。"""
    from dataclasses import asdict
    from wisland.services.quota.credentials import WindowsQuotaCredentialStore
    from wisland.services.quota.models import QuotaSnapshot

    # 使用真实的 WindowsQuotaCredentialStore 分块逻辑，仅替换底层单条 Win32 读写删
    fake_raw: dict[str, tuple[str, str]] = {}
    creds = WindowsQuotaCredentialStore()
    fail_delete_targets: set[str] = set()
    fail_write_targets: set[str] = set()

    def _fake_write(target: str, username: str, secret: str) -> bool:
        if target in fail_write_targets:
            creds.last_error = 5
            creds.last_error_message = "WinError 5: Access denied"
            return False
        fake_raw[target] = (username, secret)
        return True

    def _fake_read(target: str) -> str | None:
        item = fake_raw.get(target)
        return item[1] if item is not None else None

    def _fake_delete(target: str) -> bool:
        if target in fail_delete_targets:
            creds.last_error = 5
            creds.last_error_message = "WinError 5: Access denied"
            return False
        fake_raw.pop(target, None)
        return True

    monkeypatch.setattr(creds, "_write_single", _fake_write)
    monkeypatch.setattr(creds, "_read_single", _fake_read)
    monkeypatch.setattr(creds, "_delete_single", _fake_delete)

    cm, _, cache, old_mgr, _ = _make_quota_env(tmp_path)
    old_mgr.stop()
    from wisland.services.quota.manager import QuotaManager
    mgr = QuotaManager(config_obj=cm, cred_store=creds, cache=cache)

    q1 = QuotaAccount(id="q1", provider=Provider.DEEPSEEK.value, alias="Q1", identity="q1-id", credential_key="Wisland/quota/q1")
    q2 = QuotaAccount(id="q2", provider=Provider.DEEPSEEK.value, alias="Q2", identity="q2-id", credential_key="Wisland/quota/q2")
    sec1 = "short-secret-1"
    sec2 = "chunked-secret-" + ("A1B2C3D4E5" * 260)  # > 1800 UTF-16 bytes，触发主清单 + 多分块
    assert creds.save_secret(q1.credential_key, q1.identity, sec1) is True
    assert creds.save_secret(q2.credential_key, q2.identity, sec2) is True
    assert f"{q2.credential_key}/chunk/0" in fake_raw
    assert f"{q2.credential_key}/chunk/1" in fake_raw

    cache.put(QuotaSnapshot(account_id="q1", fetched_at=100.0, status="ok"), flush=True)
    cache.put(QuotaSnapshot(account_id="q2", fetched_at=100.0, status="ok"), flush=True)
    cm.set("quota.accounts", [asdict(q1), asdict(q2)], save=True)
    mgr.load_from_config()

    cfg_signals: list[int] = []
    quota_signals: list[int] = []
    cm.changed.connect(lambda: cfg_signals.append(1))
    mgr.accounts_changed.connect(lambda: quota_signals.append(1))

    ctx = type("Ctx", (), {"quota": mgr})()
    win = SettingsWindow(ctx=ctx, config_obj=cm)
    try:
        quota_pg = next(pg for pg in win.leaf_pages if isinstance(pg, QuotaSettingsPage))
        monkeypatch.setattr(win, "collect_to_draft", lambda: win.draft)

        # 1) 第一笔正常删除，第二笔主清单删除成功但 chunk/1 删除失败
        quota_pg._draft_accounts = []
        quota_pg._deleted_accounts = [q1, q2]
        win.draft.set("quota.accounts", [])
        fail_delete_targets.add(f"{q2.credential_key}/chunk/1")

        assert win.save_settings() is False
        assert len(cm.get("quota.accounts")) == 2
        assert creds.read_secret(q1.credential_key) == sec1
        assert creds.read_secret(q2.credential_key) == sec2
        assert cache.get("q1") is not None and cache.get("q2") is not None
        assert [a.id for a in mgr.list_accounts()] == ["q1", "q2"]
        assert cfg_signals == [] and quota_signals == []

        # 2) 模拟部分删除后恢复写入也失败 -> 界面明确提示回滚失败且不含凭据明文
        fail_write_targets.add(f"{q2.credential_key}/chunk/0")
        assert win.save_settings() is False
        st_txt = win.status_lbl.text()
        assert "回滚失败" in st_txt
        assert "chunked-secret-" not in st_txt
        assert sec1 not in st_txt
    finally:
        win.close()
        mgr.stop()


def test_15_both_managers_updated_before_any_success_signal(qt_app, tmp_path, monkeypatch):
    """同一次保存同时删除邮箱与配额账号时，两个管理器和缓存都必须先更新完毕再发送通知。"""
    from dataclasses import asdict
    from wisland.services.mail.credentials import InMemoryCredentialStore, make_credential_key
    from wisland.services.mail.manager import MailAccountManager
    from wisland.services.mail.models import MailAccount
    from wisland.services.quota.models import QuotaSnapshot
    from wisland.ui.settings.mail_accounts import MailAccountsSettingsPage

    cm, creds, cache, mgr, _ = _make_quota_env(tmp_path)
    mail_creds = InMemoryCredentialStore()

    m1 = MailAccount(id="m1", email="m1@example.com", provider="qq", host="imap.qq.com", port=993, security="ssl", username="m1@example.com", credential_key=make_credential_key("m1"))
    m2 = MailAccount(id="m2", email="m2@example.com", provider="qq", host="imap.qq.com", port=993, security="ssl", username="m2@example.com", credential_key=make_credential_key("m2"))
    mail_creds.save_secret(m1.credential_key, m1.username, "mail-sec-1")
    mail_creds.save_secret(m2.credential_key, m2.username, "mail-sec-2")

    q1 = QuotaAccount(id="q1", provider=Provider.DEEPSEEK.value, alias="Q1", identity="q1-id", credential_key="Wisland/quota/q1")
    q2 = QuotaAccount(id="q2", provider=Provider.DEEPSEEK.value, alias="Q2", identity="q2-id", credential_key="Wisland/quota/q2")
    creds.save_secret(q1.credential_key, q1.identity, "quota-sec-1")
    creds.save_secret(q2.credential_key, q2.identity, "quota-sec-2")
    cache.put(QuotaSnapshot(account_id="q1", fetched_at=100.0, status="ok"), flush=True)
    cache.put(QuotaSnapshot(account_id="q2", fetched_at=100.0, status="ok"), flush=True)

    cm.set("mail.accounts", [m1.to_config_dict(), m2.to_config_dict()], save=True)
    cm.set("quota.accounts", [asdict(q1), asdict(q2)], save=True)
    mgr.load_from_config()
    mail_mgr = MailAccountManager(config_obj=cm, cred_store=mail_creds)

    observations: list[tuple[str, list[str], list[str], bool]] = []

    def _observe(tag: str):
        observations.append((
            tag,
            [a.id for a in mgr.list_accounts()],
            [a.id for a in mail_mgr.list_accounts()],
            cache.get("q1") is None,
        ))

    mgr.accounts_changed.connect(lambda: _observe("quota"))
    mail_mgr.accounts_changed.connect(lambda: _observe("mail"))
    cm.changed.connect(lambda: _observe("cfg"))

    ctx = type("Ctx", (), {"quota": mgr, "mail": mail_mgr})()
    win = SettingsWindow(ctx=ctx, config_obj=cm)
    try:
        mail_pg = next(pg for pg in win.leaf_pages if isinstance(pg, MailAccountsSettingsPage))
        quota_pg = next(pg for pg in win.leaf_pages if isinstance(pg, QuotaSettingsPage))
        monkeypatch.setattr(win, "collect_to_draft", lambda: win.draft)

        quota_pg._draft_accounts = [q2]
        quota_pg._deleted_accounts = [q1]
        win.draft.set("quota.accounts", [asdict(q2)])
        mail_pg._draft_accounts = [m2]
        mail_pg._deleted_accounts = [m1]
        win.draft.set("mail.accounts", [m2.to_config_dict()])

        assert win.save_settings() is True
        assert [tag for tag, *_ in observations] == ["quota", "mail", "cfg"]
        for tag, q_ids, m_ids, q1_purged in observations:
            assert q_ids == ["q2"], f"{tag} saw stale quota: {q_ids}"
            assert m_ids == ["m2"], f"{tag} saw stale mail: {m_ids}"
            assert q1_purged is True, f"{tag} saw unpurged q1 cache"
    finally:
        win.close()
        mgr.stop()


def test_16_chunked_quota_backup_read_error_aborts_before_delete(qt_app, tmp_path, monkeypatch):
    """分块备份读取出错（如 WinError 5）时，必须在删除前中止并保留原凭据完整可读。"""
    from dataclasses import asdict
    from wisland.services.quota.credentials import WindowsQuotaCredentialStore
    from wisland.services.quota.models import QuotaSnapshot

    fake_raw: dict[str, tuple[str, str]] = {}
    creds = WindowsQuotaCredentialStore()
    fail_read_targets: set[str] = set()
    fail_delete_targets: set[str] = set()

    def _fake_write(target: str, username: str, secret: str) -> bool:
        fake_raw[target] = (username, secret)
        return True

    def _fake_read(target: str) -> str | None:
        if target in fail_read_targets:
            creds.last_error = 5
            creds.last_error_message = "WinError 5: Access denied"
            return None
        item = fake_raw.get(target)
        return item[1] if item is not None else None

    def _fake_delete(target: str) -> bool:
        if target in fail_delete_targets:
            creds.last_error = 5
            creds.last_error_message = "WinError 5: Access denied"
            return False
        fake_raw.pop(target, None)
        return True

    monkeypatch.setattr(creds, "_write_single", _fake_write)
    monkeypatch.setattr(creds, "_read_single", _fake_read)
    monkeypatch.setattr(creds, "_delete_single", _fake_delete)

    cm, _, cache, old_mgr, _ = _make_quota_env(tmp_path)
    old_mgr.stop()
    from wisland.services.quota.manager import QuotaManager
    mgr = QuotaManager(config_obj=cm, cred_store=creds, cache=cache)

    q1 = QuotaAccount(id="q1", provider=Provider.DEEPSEEK.value, alias="Q1", identity="q1-id", credential_key="Wisland/quota/q1")
    q2 = QuotaAccount(id="q2", provider=Provider.DEEPSEEK.value, alias="Q2", identity="q2-id", credential_key="Wisland/quota/q2")
    sec1 = "short-secret-1"
    sec2 = "chunked-secret-" + ("A1B2C3D4E5" * 260)
    assert creds.save_secret(q1.credential_key, q1.identity, sec1) is True
    assert creds.save_secret(q2.credential_key, q2.identity, sec2) is True

    cache.put(QuotaSnapshot(account_id="q1", fetched_at=100.0, status="ok"), flush=True)
    cache.put(QuotaSnapshot(account_id="q2", fetched_at=100.0, status="ok"), flush=True)
    cm.set("quota.accounts", [asdict(q1), asdict(q2)], save=True)
    mgr.load_from_config()

    ctx = type("Ctx", (), {"quota": mgr})()
    win = SettingsWindow(ctx=ctx, config_obj=cm)
    try:
        quota_pg = next(pg for pg in win.leaf_pages if isinstance(pg, QuotaSettingsPage))
        monkeypatch.setattr(win, "collect_to_draft", lambda: win.draft)

        # 让 q2 首个分块读取返回 WinError 5（但允许删除），同时让下一分块删除失败
        quota_pg._draft_accounts = []
        quota_pg._deleted_accounts = [q1, q2]
        win.draft.set("quota.accounts", [])
        fail_read_targets.add(f"{q2.credential_key}/chunk/0")
        fail_delete_targets.add(f"{q2.credential_key}/chunk/1")

        assert win.save_settings() is False
        assert len(cm.get("quota.accounts")) == 2

        # 恢复正常读取权限后，两笔凭据都必须完整可读（q2 未被部分删除）
        fail_read_targets.clear()
        assert creds.read_secret(q1.credential_key) == sec1
        assert creds.read_secret(q2.credential_key) == sec2
    finally:
        win.close()
        mgr.stop()


def test_17_settings_backup_read_error_aborts_before_delete(qt_app, tmp_path, monkeypatch):
    """设置层首次备份读取遇到短暂错误（如 WinError 5）时，必须中止删除并不得将其误记为无旧值。"""
    from dataclasses import asdict
    from wisland.services.quota.credentials import WindowsQuotaCredentialStore
    from wisland.services.quota.models import QuotaSnapshot

    fake_raw: dict[str, tuple[str, str]] = {}
    creds = WindowsQuotaCredentialStore()
    q1_read_calls = {"n": 0}
    fail_delete_targets: set[str] = set()

    def _fake_write(target: str, username: str, secret: str) -> bool:
        fake_raw[target] = (username, secret)
        return True

    def _fake_read(target: str) -> str | None:
        if target == "Wisland/quota/q1":
            q1_read_calls["n"] += 1
            if q1_read_calls["n"] == 1:
                creds.last_error = 5
                creds.last_error_message = "WinError 5: Access denied"
                return None
        item = fake_raw.get(target)
        return item[1] if item is not None else None

    def _fake_delete(target: str) -> bool:
        if target in fail_delete_targets:
            creds.last_error = 5
            creds.last_error_message = "WinError 5: Access denied"
            return False
        fake_raw.pop(target, None)
        return True

    monkeypatch.setattr(creds, "_write_single", _fake_write)
    monkeypatch.setattr(creds, "_read_single", _fake_read)
    monkeypatch.setattr(creds, "_delete_single", _fake_delete)

    cm, _, cache, old_mgr, _ = _make_quota_env(tmp_path)
    old_mgr.stop()
    from wisland.services.quota.manager import QuotaManager
    mgr = QuotaManager(config_obj=cm, cred_store=creds, cache=cache)

    q1 = QuotaAccount(id="q1", provider=Provider.DEEPSEEK.value, alias="Q1", identity="q1-id", credential_key="Wisland/quota/q1")
    q2 = QuotaAccount(id="q2", provider=Provider.DEEPSEEK.value, alias="Q2", identity="q2-id", credential_key="Wisland/quota/q2")
    sec1 = "short-secret-1"
    sec2 = "short-secret-2"
    assert creds.save_secret(q1.credential_key, q1.identity, sec1) is True
    assert creds.save_secret(q2.credential_key, q2.identity, sec2) is True

    cache.put(QuotaSnapshot(account_id="q1", fetched_at=100.0, status="ok"), flush=True)
    cache.put(QuotaSnapshot(account_id="q2", fetched_at=100.0, status="ok"), flush=True)
    cm.set("quota.accounts", [asdict(q1), asdict(q2)], save=True)
    mgr.load_from_config()

    ctx = type("Ctx", (), {"quota": mgr})()
    win = SettingsWindow(ctx=ctx, config_obj=cm)
    try:
        quota_pg = next(pg for pg in win.leaf_pages if isinstance(pg, QuotaSettingsPage))
        monkeypatch.setattr(win, "collect_to_draft", lambda: win.draft)

        quota_pg._draft_accounts = []
        quota_pg._deleted_accounts = [q1, q2]
        win.draft.set("quota.accounts", [])
        q1_read_calls["n"] = 0
        fail_delete_targets.add(q2.credential_key)

        # q1 首次备份读取返回 WinError 5 -> 必须立即中止，不得继续删除 q1
        assert win.save_settings() is False
        assert len(cm.get("quota.accounts")) == 2
        assert creds.read_secret(q1.credential_key) == sec1
        assert creds.read_secret(q2.credential_key) == sec2
    finally:
        win.close()
        mgr.stop()
