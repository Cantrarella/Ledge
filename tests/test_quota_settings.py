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
    keys = [pg.key for pg in win.pages]
    titles = [pg.title for pg in win.pages]
    assert "quota" in keys
    assert "AI 额度" in titles
    assert keys.index("quota") == keys.index("mail_accounts") + 1

    quota_pg: QuotaSettingsPage = next(pg for pg in win.pages if isinstance(pg, QuotaSettingsPage))
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
    acc_wb = QuotaAccount(id="wb_1", provider=Provider.WORKBUDDY.value, alias="WB占位", identity="wb")
    cm.set("quota.accounts", [acc_ok.to_config_dict(), acc_err.to_config_dict(), acc_wb.to_config_dict()])
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

    quota_pg: QuotaSettingsPage = next(pg for pg in win.pages if isinstance(pg, QuotaSettingsPage))
    assert len(quota_pg._cards) == 3
    cards_by_id = {c.acc.id: c for c in quota_pg._cards}

    # 1) DeepSeek 正常卡：显示余额，不显示无意义的“重新连接”
    c_ok: _QuotaAccountCard = cards_by_id["ds_ok"]
    assert "66.60" in c_ok.lbl_main_bal.text()
    assert not c_ok.btn_reconnect.isVisible()
    assert c_ok.btn_refresh.isEnabled()

    # 2) 错误卡：显示“重试”和“重新连接”
    c_err: _QuotaAccountCard = cards_by_id["ds_err"]
    assert c_err.btn_refresh.text == "重试"
    assert c_err.btn_reconnect.isVisible()

    # 3) 未接入服务商：显示“连接器待接入”，且添加表单选中时禁用确认按钮
    c_wb: _QuotaAccountCard = cards_by_id["wb_1"]
    assert c_wb.status_pill.label_text == "连接器待接入"
    quota_pg.open_add_form(Provider.GEMINI.value)
    assert not quota_pg.btn_test_save.isEnabled()
    assert quota_pg.btn_test_save.text == "下一阶段接入"
    quota_pg.close_add_form()

    # 4) 颜色菜单不常驻展开（卡片中不存在常驻颜色下拉/色板控件）
    assert not hasattr(c_ok, "color_picker")

    # 5) 浅色/深色主题切换无截断、无横向滚动
    scroll = win.stack.currentWidget()
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
    quota_pg: QuotaSettingsPage = next(pg for pg in win.pages if isinstance(pg, QuotaSettingsPage))

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


def test_04_no_msg_or_quota_island_page_registered(qt_app):
    reg = PageRegistry()
    for k in ("home", "mail", "weather", "trans", "tools", "life"):
        pg = type(f"P_{k}", (Page,), {"key": k, "glyph": "", "title": k})()
        reg.register(pg)
    assert reg.get_entry("msg") is None
    assert reg.get_entry("quota") is None

    root = pathlib.Path(__file__).resolve().parents[1]
    reg_src = (root / "wisland" / "ui" / "page_registry.py").read_text(encoding="utf-8")
    app_src = (root / "wisland" / "app.py").read_text(encoding="utf-8")
    assert '"quota"' not in reg_src
    assert 'P["msg"]' not in app_src
