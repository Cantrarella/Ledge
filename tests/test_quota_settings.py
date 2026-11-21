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


def test_09_settings_tooltip_theme_and_qss_lifecycle(qt_app, tmp_path):
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
        gpos = win.mapToGlobal(win.rect().center())
        tip_txt_light = target_w.toolTip() or "从监控列表移除该账号"
        QtWidgets.QToolTip.showText(gpos, tip_txt_light, win)
        _wait_until(
            qt_app,
            lambda: any(w.metaObject().className() == "QTipLabel" and w.isVisible() for w in qt_app.topLevelWidgets()),
            timeout_s=1.0,
        )
        tip_widget = next(
            (w for w in qt_app.topLevelWidgets() if w.metaObject().className() == "QTipLabel"),
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
            (w for w in qt_app.topLevelWidgets() if w.metaObject().className() == "QTipLabel"),
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
