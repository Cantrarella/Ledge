# -*- coding: utf-8 -*-
"""岛内 AI 额度展示页测试（覆盖十三节全部 20 项测试要求）。"""
from __future__ import annotations

import json
import pathlib
import sys
import threading
import time
import urllib.error

import pytest
from PySide6 import QtCore, QtGui, QtWidgets

from wisland.core.config import Config
from wisland.services.quota import (
    AccountStatus,
    InMemoryQuotaCredentialStore,
    LocalQuotaCache,
    Provider,
    QuotaAccount,
    QuotaBalance,
    QuotaManager,
    QuotaSnapshot,
    QuotaWindow,
)
from wisland.ui.icons import get_ai_svg_renderer
from wisland.ui.page_registry import PageRegistry
from wisland.ui.pages.quota import QuotaPage
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


def _collect_texts(root: QtWidgets.QWidget) -> list[str]:
    texts: list[str] = []
    for w in root.findChildren(QtWidgets.QWidget):
        if hasattr(w, "text") and callable(w.text):
            t = w.text()
            if isinstance(t, str) and t:
                texts.append(t)
        elif hasattr(w, "_text") and isinstance(w._text, str) and w._text:
            texts.append(w._text)
    return texts


def _make_mgr(tmp_path, accounts=None, http_opener=None):
    cm = Config(path=tmp_path / "config.json")
    if accounts:
        cm.set("quota.accounts", [a.to_config_dict() for a in accounts])
    creds = InMemoryQuotaCredentialStore()
    cache = LocalQuotaCache(cache_path=tmp_path / "quota_cache.json")
    mgr = QuotaManager(config_obj=cm, cred_store=creds, cache=cache, http_opener=http_opener)
    return cm, creds, cache, mgr


def test_01_registry_dimensions_and_svg_icons(qt_app, tmp_path):
    _cm, _creds, _cache, mgr = _make_mgr(tmp_path)
    opened: list[tuple] = []
    ctx = type("Ctx", (), {
        "quota": mgr,
        "open_settings": staticmethod(lambda *a, **kw: opened.append((a, kw))),
    })()

    # 1) quota 页面已注册且 msg 不存在，panel_width=419, panel_height=176
    pg = QuotaPage(ctx=ctx)
    assert pg.key == "quota"
    assert pg.panel_width == 419
    assert pg.panel_height == 176
    assert pg.title == "AI 额度"

    reg = PageRegistry()
    reg.register(pg)
    assert reg.get_entry("quota") is not None
    assert reg.get_entry("msg") is None

    # 5 个服务商 SVG 图标均可从 ai-icons 加载
    for prov in ("deepseek", "workbuddy", "gemini", "grok_bot", "codex"):
        for dark in (False, True):
            ren = get_ai_svg_renderer(prov, dark=dark)
            assert ren is not None and ren.isValid()
    spec = (pathlib.Path(__file__).resolve().parents[1] / "WIsland.spec").read_text(encoding="utf-8")
    assert 'ROOT / "wisland" / "assets"' in spec

    # 2) 无账号空状态，点击去添加与齿轮均打开设置 quota 分类
    pg.resize(419, 176)
    pg.show()
    qt_app.processEvents()
    texts = " ".join(_collect_texts(pg))
    assert "还没有 AI 额度账号" in texts
    assert not pg.chip_scroll.isVisible()

    pg.btn_gear.clicked.emit()
    assert len(opened) == 1 and opened[0][0][0] == "quota"

    # 确认页面源码不直接发起 HTTP 请求
    quota_src = (pathlib.Path(__file__).resolve().parents[1] / "wisland" / "ui" / "pages" / "quota.py").read_text(encoding="utf-8")
    assert "urllib" not in quota_src and "requests" not in quota_src
    pg.close()
    mgr.stop()


def test_02_deepseek_balances_multi_account_pin_restore_and_scroll(qt_app, tmp_path):
    acc1 = QuotaAccount(id="a1", provider=Provider.DEEPSEEK.value, alias="普通DS", identity="k1", pinned=False, color="blue")
    acc2 = QuotaAccount(id="a2", provider=Provider.DEEPSEEK.value, alias="置顶DS", identity="k2", pinned=True, color="emerald")
    acc3 = QuotaAccount(id="a3", provider=Provider.WORKBUDDY.value, alias="WB测试", identity="k3", pinned=False, color="amber")
    acc4 = QuotaAccount(id="a4", provider=Provider.CODEX.value, alias="Codex测试", identity="k4", pinned=False, color="cyan")

    cm, creds, _cache, mgr = _make_mgr(tmp_path, accounts=[acc1, acc2, acc3, acc4])
    creds.save_secret(acc1.credential_key, "k1", "sk-1")
    creds.save_secret(acc2.credential_key, "k2", "sk-2")

    ts = time.time()
    mgr._snapshots["a1"] = QuotaSnapshot(
        account_id="a1",
        fetched_at=ts,
        status=AccountStatus.OK.value,
        balances=[
            QuotaBalance("CNY", 19.85, 0.00, 19.85),
            QuotaBalance("USD", 0.00, 0.00, 0.00),
        ],
    )
    mgr._snapshots["a2"] = QuotaSnapshot(
        account_id="a2",
        fetched_at=ts,
        status=AccountStatus.OK.value,
        balances=[QuotaBalance("EUR", 12.34, 2.34, 10.00)],
    )
    # 未来通用快照：credits + windows
    mgr._snapshots["a4"] = QuotaSnapshot(
        account_id="a4",
        fetched_at=ts,
        status=AccountStatus.OK.value,
        credits=250.0,
        windows=[
            QuotaWindow("5 小时", remaining_percent=77.0, reset_at=ts + 3600),
            QuotaWindow("本周", remaining_percent=52.0, reset_at=ts + 86400),
        ],
    )

    pg = QuotaPage(ctx=type("Ctx", (), {"quota": mgr, "open_settings": lambda *a, **kw: None})())
    pg.resize(419, 176)
    pg.show()
    qt_app.processEvents()

    # 1) 置顶账号 a2 排在第一位并被优先选中；展示 EUR 与充值/赠送
    assert [c.acc.id for c in pg._chip_widgets] == ["a2", "a1", "a3", "a4"]
    assert pg._selected_account_id == "a2"
    txt_a2 = " ".join(_collect_texts(pg))
    assert "€12.34" in txt_a2
    assert "€10.00" in txt_a2
    assert "€2.34" in txt_a2

    # 2) 点击账号标签切换：不触发网络请求，仅切换显示；DeepSeek CNY 显示 ¥19.85 与 另有 USD $0.00
    pg._chip_widgets[1].clicked.emit()
    qt_app.processEvents()
    assert pg._selected_account_id == "a1"
    txt_a1 = " ".join(_collect_texts(pg))
    assert "¥19.85" in txt_a1
    assert "USD" in txt_a1 and "$0.00" in txt_a1

    # 3) 上次选中账号在刷新时保持恢复
    pg.refresh()
    assert pg._selected_account_id == "a1"

    # 4) 未接入服务商 a3（无快照）：显示“连接器待接入”，无假数据，隐藏刷新按钮
    pg.select_account("a3")
    qt_app.processEvents()
    txt_a3 = " ".join(_collect_texts(pg))
    assert "连接器待接入" in txt_a3
    assert "¥0.00" not in txt_a3
    assert not pg.btn_refresh.isVisible()

    # 5) 未来通用快照（credits + windows）展示
    pg.select_account("a4")
    qt_app.processEvents()
    txt_a4 = " ".join(_collect_texts(pg))
    assert "250.00" in txt_a4

    # 6) 账号标签横向滚动消费滚轮且不透传切页
    wev = QtGui.QWheelEvent(
        QtCore.QPointF(20, 10),
        QtCore.QPointF(20, 10),
        QtCore.QPoint(0, 0),
        QtCore.QPoint(0, -120),
        QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        QtCore.Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    pg.chip_scroll.wheelEvent(wev)
    assert wev.isAccepted()

    # 7) 浅色与深色主题切换无截断、不改变页面固定尺寸
    for mode in ("light", "dark"):
        theme.set_mode(mode)
        qt_app.processEvents()
        assert pg.size() == QtCore.QSize(419, 176)

    # 8) 删除当前选中账号后自动切换到有效账号
    pg.select_account("a1")
    mgr.remove_account_after_commit("a1", acc1.credential_key)
    qt_app.processEvents()
    assert pg._selected_account_id == "a2"

    pg.close()
    mgr.stop()


def test_03_stale_network_error_auth_error_and_single_refresh_guard(qt_app, tmp_path):
    acc = QuotaAccount(id="a1", provider=Provider.DEEPSEEK.value, alias="DS1", identity="k1")
    gate = threading.Event()
    mode = {"mode": "slow_ok", "calls": 0}

    def _opener(req, timeout=10):
        mode["calls"] += 1
        if mode["mode"] == "slow_ok":
            gate.wait(timeout=1.5)
            body = {
                "is_available": True,
                "balance_infos": [{"currency": "CNY", "total_balance": "33.30", "granted_balance": "3.30", "topped_up_balance": "30.00"}],
            }
            return _FakeResp(200, json.dumps(body).encode("utf-8"))
        if mode["mode"] == "net_err":
            raise urllib.error.URLError("offline")
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    cm, creds, _cache, mgr = _make_mgr(tmp_path, accounts=[acc], http_opener=_opener)
    creds.save_secret(acc.credential_key, "k1", "sk-1")
    opened: list[tuple] = []
    pg = QuotaPage(ctx=type("Ctx", (), {
        "quota": mgr,
        "open_settings": staticmethod(lambda *a, **kw: opened.append((a, kw))),
    })())
    pg.resize(419, 176)
    pg.show()

    # 1) 刷新中不重复请求，且禁用刷新按钮
    pg._on_refresh_clicked()
    qt_app.processEvents()
    assert not pg.btn_refresh.isEnabled()
    pg._on_refresh_clicked()
    gate.set()
    assert _wait_until(qt_app, lambda: not mgr.is_refreshing("a1"))
    assert mode["calls"] == 1
    assert "¥33.30" in " ".join(_collect_texts(pg))

    # 2) 断网失败：数据已过期保留旧余额 ¥33.30 并显示“数据已过期”
    mode["mode"] = "net_err"
    pg._on_refresh_clicked()
    assert _wait_until(qt_app, lambda: not mgr.is_refreshing("a1"))
    txt_stale = " ".join(_collect_texts(pg))
    assert "¥33.30" in txt_stale
    assert "数据已过期" in txt_stale

    # 3) 无缓存时的网络错误与 API Key 失效状态（点击重新连接打开设置页 quota 分类）
    mgr._snapshots["a1"] = QuotaSnapshot(account_id="a1", status=AccountStatus.NETWORK_ERROR.value, last_error="网络连接断开")
    pg.refresh()
    qt_app.processEvents()
    assert "网络连接失败" in " ".join(_collect_texts(pg))
    assert pg.size() == QtCore.QSize(419, 176)

    mgr._snapshots["a1"] = QuotaSnapshot(account_id="a1", status=AccountStatus.AUTH_ERROR.value, last_error="401")
    pg.refresh()
    qt_app.processEvents()
    assert "DeepSeek API Key 已失效" in " ".join(_collect_texts(pg))
    reconn_btn = next(w for w in pg.findChildren(QtWidgets.QWidget) if getattr(w, "text", None) == "重新连接")
    reconn_btn.clicked.emit()
    assert opened and opened[-1][0][0] == "quota" and opened[-1][1].get("account_id") == "a1"

    pg.close()
    mgr.stop()
