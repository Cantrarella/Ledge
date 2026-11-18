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


def test_04_dynamic_quota_page_visibility_and_safe_switch(qt_app, tmp_path):
    from wisland.ui.island import Island
    from wisland.ui.pages.base import Page

    acc_gk = QuotaAccount(id="gk_old", provider=Provider.GROK_BOT.value, alias="伪GK", identity="gk-old", last_test_at=0.0)
    cm, creds, _cache, mgr = _make_mgr(tmp_path, accounts=[acc_gk])
    ctx = type("Ctx", (), {"quota": mgr, "open_settings": lambda *a, **kw: None})()

    pg_home = type("P_home", (Page,), {"key": "home", "glyph": "", "title": "首页"})()
    pg_mail = type("P_mail", (Page,), {"key": "mail", "glyph": "", "title": "邮箱"})()
    pg_quota = QuotaPage(ctx=ctx)

    reg = PageRegistry()
    reg.register(pg_home, visible=True)
    reg.register(pg_mail, visible=True)
    reg.register(pg_quota, visible=True)

    # 1) 只有从未连接的伪账号（last_test_at=0、无有效快照）时：不显示 quota 页面
    assert not pg_quota.has_connected_accounts()
    assert not pg_quota.should_show()
    assert [p.key for p in reg.visible_pages()] == ["home", "mail"]

    island = Island(reg.visible_pages(), registry=reg)
    assert island.nav.keys() == ["home", "mail"]

    # 2) 添加一个真实已连接 DeepSeek 账号：无需重启即可动态出现 quota 页
    acc_ds = QuotaAccount(
        id="ds_1",
        provider=Provider.DEEPSEEK.value,
        alias="主力DS",
        identity="ds-1",
        last_test_at=time.time(),
    )
    mgr._accounts.append(acc_ds)
    mgr._snapshots["ds_1"] = QuotaSnapshot(
        account_id="ds_1",
        fetched_at=time.time(),
        status=AccountStatus.OK.value,
        balances=[QuotaBalance("CNY", 25.0, 0.0, 25.0)],
    )
    assert pg_quota.should_show()
    island.rebuild_pages(reg.visible_pages())
    assert island.nav.keys() == ["home", "mail", "quota"]

    # 停留在 quota 页上时，若用户偏好关闭 visible.quota，同样会隐藏
    reg.get_entry("quota").visible = False
    assert [p.key for p in reg.visible_pages()] == ["home", "mail"]
    reg.get_entry("quota").visible = True

    # 3) 停留在 quota 页时移除最后一个真实账号：安全切回可用页面，不越界
    island.show_page("quota")
    assert island.nav.keys()[island.nav.index] == "quota"
    mgr.remove_account_after_commit("ds_1", acc_ds.credential_key)
    assert not pg_quota.should_show()
    island.rebuild_pages(reg.visible_pages())
    assert island.nav.keys() == ["home", "mail"]
    assert 0 <= island.nav.index < len(island.nav.keys())
    assert island.nav.keys()[island.nav.index] != "quota"

    island.close()
    pg_quota.close()
    mgr.stop()


def test_02_deepseek_balances_multi_account_pin_restore_and_scroll(qt_app, tmp_path):
    acc1 = QuotaAccount(id="a1", provider=Provider.DEEPSEEK.value, alias="普通DS", identity="k1", pinned=False, color="blue")
    acc2 = QuotaAccount(id="a2", provider=Provider.DEEPSEEK.value, alias="置顶DS", identity="k2", pinned=True, color="emerald")
    acc3 = QuotaAccount(id="a3", provider=Provider.GROK_BOT.value, alias="GK测试", identity="k3", pinned=False, color="amber")
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

    # 1) 置顶账号 a2 排在第一位并被优先选中；未实现服务商 a3 (Gemini) 不得出现
    assert [c.acc.id for c in pg._chip_widgets] == ["a2", "a1", "a4"]
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

    # 4) 确认通用积分 + 周期额度快照渲染路径（使用已实现账号验证展示能力，无占位）
    mgr._snapshots["a1"] = mgr._snapshots["a4"]
    pg.refresh()
    qt_app.processEvents()
    txt_cw = " ".join(_collect_texts(pg))
    assert "250.00" in txt_cw
    assert "连接器待接入" not in txt_cw

    # 6) 账号标签横向滚动：无余量时 ignore() 交由岛体翻页，有横向余量时消费滚轮 (accept)
    sb_h = pg.chip_scroll.horizontalScrollBar()
    wev_no = QtGui.QWheelEvent(
        QtCore.QPointF(20, 10),
        QtCore.QPointF(20, 10),
        QtCore.QPoint(0, 0),
        QtCore.QPoint(0, -120),
        QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        QtCore.Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    pg.chip_scroll.wheelEvent(wev_no)
    if sb_h.maximum() > sb_h.minimum():
        assert wev_no.isAccepted()
    else:
        assert not wev_no.isAccepted()
        # 缩小宽度制造横向溢出，验证有余量时消费滚轮
        pg.chip_scroll.setFixedWidth(90)
        qt_app.processEvents()
        assert sb_h.maximum() > sb_h.minimum()
        wev_ov = QtGui.QWheelEvent(
            QtCore.QPointF(20, 10),
            QtCore.QPointF(20, 10),
            QtCore.QPoint(0, 0),
            QtCore.QPoint(0, -120),
            QtCore.Qt.MouseButton.NoButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
            QtCore.Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        pg.chip_scroll.wheelEvent(wev_ov)
        assert wev_ov.isAccepted()
        pg.chip_scroll.setMaximumWidth(16777215)
        pg.chip_scroll.setMinimumWidth(0)

    # 验证无灰色大卡片、无纵向滚动条
    from wisland.ui.widgets import Card, WheelScrollArea
    assert not pg.findChildren(Card)
    assert all(sa.orientation == "horizontal" for sa in pg.findChildren(WheelScrollArea))

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


def test_04_dynamic_quota_page_visibility_and_safe_switch(qt_app, tmp_path):
    from wisland.ui.island import Island
    from wisland.ui.pages.base import Page

    acc_gk = QuotaAccount(id="gk_old", provider=Provider.GROK_BOT.value, alias="伪GK", identity="gk-old", last_test_at=0.0)
    cm, creds, _cache, mgr = _make_mgr(tmp_path, accounts=[acc_gk])
    ctx = type("Ctx", (), {"quota": mgr, "open_settings": lambda *a, **kw: None})()

    pg_home = type("P_home", (Page,), {"key": "home", "glyph": "", "title": "首页"})()
    pg_mail = type("P_mail", (Page,), {"key": "mail", "glyph": "", "title": "邮箱"})()
    pg_quota = QuotaPage(ctx=ctx)

    reg = PageRegistry()
    reg.register(pg_home, visible=True)
    reg.register(pg_mail, visible=True)
    reg.register(pg_quota, visible=True)

    # 1) 只有从未连接的伪账号（last_test_at=0、无有效快照）时：不显示 quota 页面
    assert not pg_quota.has_connected_accounts()
    assert not pg_quota.should_show()
    assert [p.key for p in reg.visible_pages()] == ["home", "mail"]

    island = Island(reg.visible_pages(), registry=reg)
    assert island.nav.keys() == ["home", "mail"]

    # 2) 添加一个真实已连接 DeepSeek 账号：无需重启即可动态出现 quota 页
    acc_ds = QuotaAccount(
        id="ds_1",
        provider=Provider.DEEPSEEK.value,
        alias="主力DS",
        identity="ds-1",
        last_test_at=time.time(),
    )
    mgr._accounts.append(acc_ds)
    mgr._snapshots["ds_1"] = QuotaSnapshot(
        account_id="ds_1",
        fetched_at=time.time(),
        status=AccountStatus.OK.value,
        balances=[QuotaBalance("CNY", 25.0, 0.0, 25.0)],
    )
    assert pg_quota.should_show()
    island.rebuild_pages(reg.visible_pages())
    assert island.nav.keys() == ["home", "mail", "quota"]

    # 停留在 quota 页上时，若用户偏好关闭 visible.quota，同样会隐藏
    reg.get_entry("quota").visible = False
    assert [p.key for p in reg.visible_pages()] == ["home", "mail"]
    reg.get_entry("quota").visible = True

    # 3) 停留在 quota 页时移除最后一个真实账号：安全切回可用页面，不越界
    island.show_page("quota")
    assert island.nav.keys()[island.nav.index] == "quota"
    mgr.remove_account_after_commit("ds_1", acc_ds.credential_key)
    assert not pg_quota.should_show()
    island.rebuild_pages(reg.visible_pages())
    assert island.nav.keys() == ["home", "mail"]
    assert 0 <= island.nav.index < len(island.nav.keys())
    assert island.nav.keys()[island.nav.index] != "quota"

    island.close()
    pg_quota.close()
    mgr.stop()


def test_03_stale_network_error_auth_error_and_single_refresh_guard(qt_app, tmp_path):
    acc = QuotaAccount(
        id="a1",
        provider=Provider.DEEPSEEK.value,
        alias="DS1",
        identity="k1",
        last_test_at=time.time(),
    )
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


def test_04_dynamic_quota_page_visibility_and_safe_switch(qt_app, tmp_path):
    from wisland.ui.island import Island
    from wisland.ui.pages.base import Page

    acc_gk = QuotaAccount(id="gk_old", provider=Provider.GROK_BOT.value, alias="伪GK", identity="gk-old", last_test_at=0.0)
    cm, creds, _cache, mgr = _make_mgr(tmp_path, accounts=[acc_gk])
    ctx = type("Ctx", (), {"quota": mgr, "open_settings": lambda *a, **kw: None})()

    pg_home = type("P_home", (Page,), {"key": "home", "glyph": "", "title": "首页"})()
    pg_mail = type("P_mail", (Page,), {"key": "mail", "glyph": "", "title": "邮箱"})()
    pg_quota = QuotaPage(ctx=ctx)

    reg = PageRegistry()
    reg.register(pg_home, visible=True)
    reg.register(pg_mail, visible=True)
    reg.register(pg_quota, visible=True)

    # 1) 只有从未连接的伪账号（last_test_at=0、无有效快照）时：不显示 quota 页面
    assert not pg_quota.has_connected_accounts()
    assert not pg_quota.should_show()
    assert [p.key for p in reg.visible_pages()] == ["home", "mail"]

    island = Island(reg.visible_pages(), registry=reg)
    assert island.nav.keys() == ["home", "mail"]

    # 2) 添加一个真实已连接 DeepSeek 账号：无需重启即可动态出现 quota 页
    acc_ds = QuotaAccount(
        id="ds_1",
        provider=Provider.DEEPSEEK.value,
        alias="主力DS",
        identity="ds-1",
        last_test_at=time.time(),
    )
    mgr._accounts.append(acc_ds)
    mgr._snapshots["ds_1"] = QuotaSnapshot(
        account_id="ds_1",
        fetched_at=time.time(),
        status=AccountStatus.OK.value,
        balances=[QuotaBalance("CNY", 25.0, 0.0, 25.0)],
    )
    assert pg_quota.should_show()
    island.rebuild_pages(reg.visible_pages())
    assert island.nav.keys() == ["home", "mail", "quota"]

    # 停留在 quota 页上时，若用户偏好关闭 visible.quota，同样会隐藏
    reg.get_entry("quota").visible = False
    assert [p.key for p in reg.visible_pages()] == ["home", "mail"]
    reg.get_entry("quota").visible = True

    # 3) 停留在 quota 页时移除最后一个真实账号：安全切回可用页面，不越界
    island.show_page("quota")
    assert island.nav.keys()[island.nav.index] == "quota"
    mgr.remove_account_after_commit("ds_1", acc_ds.credential_key)
    assert not pg_quota.should_show()
    island.rebuild_pages(reg.visible_pages())
    assert island.nav.keys() == ["home", "mail"]
    assert 0 <= island.nav.index < len(island.nav.keys())
    assert island.nav.keys()[island.nav.index] != "quota"

    island.close()
    pg_quota.close()
    mgr.stop()



def test_workbuddy_svg_no_blur_transparent_corners_and_sizes(qt_app, tmp_path):
    from wisland.ui.icons import _SVG_CACHE, draw_ai_svg_icon

    svg_path = pathlib.Path(__file__).resolve().parents[1] / "wisland" / "assets" / "ai-icons" / "workbuddy.svg"
    svg_txt = svg_path.read_text(encoding="utf-8")
    assert "feGaussianBlur" not in svg_txt
    assert 'filter="url(#filter_0)"' not in svg_txt
    assert 'overflow="hidden"' in svg_txt

    _SVG_CACHE.clear()

    def _render_icon(size: int, dpr: float = 1.0) -> QtGui.QImage:
        px = int(round(size * dpr))
        img = QtGui.QImage(px, px, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(img)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        ok = draw_ai_svg_icon(p, QtCore.QRectF(0.0, 0.0, float(size), float(size)), "workbuddy", dark=False)
        p.end()
        assert ok is True
        return img

    # 1) 48x48（1x 和 2x DPI）：四角透明，圆形范围外透明，右下角无黄色像素
    for dpr in (1.0, 2.0):
        img = _render_icon(48, dpr=dpr)
        w, h = img.width(), img.height()
        for cx, cy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            assert QtGui.QColor.fromRgba(img.pixel(cx, cy)).alpha() == 0

        radius = w / 2.0
        center = (w - 1) / 2.0
        for y in range(h):
            for x in range(w):
                dist = ((x - center) ** 2 + (y - center) ** 2) ** 0.5
                col = QtGui.QColor.fromRgba(img.pixel(x, y))
                if dist > radius + 1.0:
                    assert col.alpha() == 0
                if x >= w // 2 and y >= h // 2 and col.alpha() > 0:
                    # 不得出现偏黄装饰像素 (高红高绿低蓝)
                    assert not (col.red() > 200 and col.green() > 180 and col.blue() < 120)

    # 2) 22x22 与 26x26：主体居中、四角透明、无方形背景
    for sz in (22, 26):
        img = _render_icon(sz, dpr=1.0)
        for cx, cy in ((0, 0), (sz - 1, 0), (0, sz - 1), (sz - 1, sz - 1)):
            assert QtGui.QColor.fromRgba(img.pixel(cx, cy)).alpha() == 0
        # 中心区域有非透明像素（主体存在）
        center_col = QtGui.QColor.fromRgba(img.pixel(sz // 2, sz // 2))
        assert center_col.alpha() > 0

    # 3) 浅色、深色、晴空、胭脂皮肤下渲染 QuotaPage 并导出截图检查
    _cm, _creds, _cache, mgr = _make_mgr(
        tmp_path,
        accounts=[QuotaAccount(id="wb1", provider=Provider.WORKBUDDY.value, alias="WorkBuddy", identity="wb:1")],
    )
    mgr._snapshots["wb1"] = QuotaSnapshot(
        account_id="wb1",
        fetched_at=time.time(),
        status=AccountStatus.OK.value,
        credits=239.59,
        windows=[QuotaWindow("赠送", amount=239.59, currency="CREDITS")],
    )
    ctx = type("Ctx", (), {"quota": mgr})()
    pg = QuotaPage(ctx=ctx)
    pg.resize(419, 176)

    out_dir = pathlib.Path(r"C:\Users\User\.gemini\antigravity\brain\8d8a99c7-d155-4d86-87b9-c28576636299\scratch")
    out_dir.mkdir(parents=True, exist_ok=True)
    for mode in ("light", "dark", "sky", "rouge"):
        theme.set_mode(mode)
        qt_app.processEvents()
        pix = QtGui.QPixmap(419, 176)
        pix.fill(QtGui.QColor("#ffffff") if mode == "light" else QtGui.QColor("#111318"))
        pg.render(pix)
        shot_path = out_dir / f"workbuddy_icon_{mode}.png"
        assert pix.save(str(shot_path))
        assert shot_path.is_file()

    theme.set_mode("light")
    pg.close()
    mgr.stop()



def test_window_progress_row_no_native_tooltip_across_all_themes(qt_app, tmp_path):
    from PySide6 import QtTest
    from wisland.ui.pages.quota import _WindowProgressRow

    reset_ts = 1795000200.0  # 固定时间戳用于验证底部重置时间文本
    expected_reset_str = time.strftime("%m月%d日 %H:%M", time.localtime(reset_ts))

    # 1) 直接实例化 _WindowProgressRow，确认无原生 Tooltip
    w_single = QuotaWindow(label="5 小时", remaining_percent=77.0, reset_at=reset_ts, amount=18000.0)
    row_single = _WindowProgressRow(w_single, "purple")
    assert row_single.toolTip() == ""
    assert row_single.win.reset_at == reset_ts
    row_single.close()

    # 2) 在 QuotaPage 中构造 Gemini 与 Codex（含 5 小时 & 本周）两个周期额度账号
    acc_gm = QuotaAccount(id="gm_tip", provider=Provider.GEMINI.value, alias="Gemini")
    acc_cx = QuotaAccount(id="cx_tip", provider=Provider.CODEX.value, alias="Codex")
    _cm, _creds, _cache, mgr = _make_mgr(tmp_path, accounts=[acc_gm, acc_cx])
    mgr._snapshots["gm_tip"] = QuotaSnapshot(
        account_id="gm_tip",
        fetched_at=time.time(),
        status=AccountStatus.OK.value,
        plan_name="AI Pro",
        windows=[
            QuotaWindow("5 小时", remaining_percent=76.0, reset_at=reset_ts, amount=18000.0),
            QuotaWindow("本周", remaining_percent=90.0, reset_at=reset_ts, amount=604800.0),
        ],
    )
    mgr._snapshots["cx_tip"] = QuotaSnapshot(
        account_id="cx_tip",
        fetched_at=time.time(),
        status=AccountStatus.OK.value,
        plan_name="plus",
        windows=[
            QuotaWindow("5 小时", remaining_percent=77.0, reset_at=reset_ts, amount=18000.0),
            QuotaWindow("本周", remaining_percent=3.0, reset_at=reset_ts, amount=604800.0),
        ],
    )

    ctx = type("Ctx", (), {"quota": mgr})()
    pg = QuotaPage(ctx=ctx)
    pg.resize(419, 176)
    pg.show()
    qt_app.processEvents()

    out_dir = pathlib.Path(r"C:\Users\User\.gemini\antigravity\brain\8d8a99c7-d155-4d86-87b9-c28576636299\scratch")
    out_dir.mkdir(parents=True, exist_ok=True)

    def _verify_and_capture(tag: str):
        assert pg.size().width() == 419 and pg.size().height() == 176
        # 遍历 Gemini 与 Codex 两个账号的进度条悬停
        for acc_id in ("gm_tip", "cx_tip"):
            pg.select_account(acc_id)
            qt_app.processEvents()
            rows = pg.findChildren(_WindowProgressRow)
            assert len(rows) == 2
            for row in rows:
                assert row.toolTip() == ""
                QtTest.QTest.mouseMove(row, row.rect().center())
                help_ev = QtGui.QHelpEvent(
                    QtCore.QEvent.Type.ToolTip,
                    row.rect().center(),
                    row.mapToGlobal(row.rect().center()),
                )
                QtWidgets.QApplication.sendEvent(row, help_ev)
                qt_app.processEvents()
                assert not QtWidgets.QToolTip.isVisible()

            # 重置时间仍显示在页面底部安全区域
            texts = _collect_texts(pg)
            assert any(f"{expected_reset_str} 重置" in t for t in texts)

        # 保留页面在 Codex（含 5 小时与本周进度条）并渲染实际主题背景截图
        pix = QtGui.QPixmap(419, 176)
        pix.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pix)
        p.fillRect(pix.rect(), theme.tint_brush(QtCore.QRectF(0, 0, 419, 176)))
        p.end()
        pg.render(pix, QtCore.QPoint(), QtGui.QRegion(), QtWidgets.QWidget.RenderFlag.DrawChildren)
        shot = out_dir / f"quota-tooltip-{tag}.png"
        assert pix.save(str(shot))
        assert shot.is_file()

    try:
        for mode in ("light", "dark", "rouge", "sky"):
            theme.set_mode(mode)
            qt_app.processEvents()
            _verify_and_capture(mode)

        # auto 自动模式：分别模拟深色背景和浅色背景
        theme.set_mode("auto")
        theme._apply("dark")
        qt_app.processEvents()
        assert theme.dark is True
        _verify_and_capture("auto-dark")

        theme.set_mode("auto")
        theme._apply("light")
        qt_app.processEvents()
        assert theme.dark is False
        _verify_and_capture("auto-light")
    finally:
        QtWidgets.QToolTip.hideText()
        theme.set_mode("light")
        pg.close()
        mgr.stop()
