# -*- coding: utf-8 -*-
"""Daozai 风格邮箱列表页（MailPage）单元测试与 UI 验收测试。"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6 import QtCore, QtGui, QtWidgets

from wisland.core.config import Config
from wisland.services.mail.manager import MailAccountManager
from wisland.services.mail.models import MailAccount, MailHeader
from wisland.ui.page_registry import PageRegistry
from wisland.ui.pages.mail import MailPage
from wisland.ui.theme import G


class _MemCreds:
    def __init__(self):
        self.store: dict[str, str] = {}

    def save_secret(self, key: str, username: str, secret: str) -> bool:
        self.store[key] = secret
        return True

    def read_secret(self, key: str) -> str | None:
        return self.store.get(key)

    def delete_secret(self, key: str) -> bool:
        return self.store.pop(key, None) is not None


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def _make_cfg(tmp_path: Path, accounts: list[MailAccount] | None = None) -> Config:
    cm = Config(path=tmp_path / "config.json")
    if accounts:
        cm.set("mail.accounts", [a.to_config_dict() for a in accounts])
    return cm


def _wait_until(qt_app, pred, timeout_s: float = 3.0):
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        qt_app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def _make_dummy_ctx(mgr, opened):
    class _CallableSig:
        def connect(self, *_a, **_kw):
            return None

        def __call__(self, *_a, **_kw):
            return None

    class _SigStub(QtCore.QObject):
        running_changed = QtCore.Signal(bool)
        changed = QtCore.Signal()
        notice = QtCore.Signal(str, bool)
        state_changed = QtCore.Signal()
        partial_ready = QtCore.Signal(str, str)
        final_ready = QtCore.Signal(str, str, str)
        status_changed = QtCore.Signal(str)
        data_changed = QtCore.Signal(object)

        def available(self):
            return (False, "stub")

        def items(self):
            return []

        def history(self):
            return []

        def list_items(self):
            return []

        def state(self):
            return {"count_ml": 0, "goal_ml": 2000, "pct": 0.0}

        def __getattr__(self, _name):
            return _CallableSig()

    stub = _SigStub()
    stub.running = False
    return SimpleNamespace(
        mail=mgr,
        open_settings=lambda cat=None: opened.append(cat),
        live=stub,
        audio=stub,
        media=stub,
        weather=stub,
        shelf=None,
        clipboard=None,
        todo=None,
        water=None,
        health=None,
    )


def test_01_registry_dimensions_and_empty_states(qt_app, tmp_path):
    cm = _make_cfg(tmp_path)
    creds = _MemCreds()
    mgr = MailAccountManager(config_obj=cm, cred_store=creds)
    opened: list[str | None] = []
    ctx = _make_dummy_ctx(mgr, opened)

    reg = PageRegistry.create_default(ctx)
    assert reg.get_entry("msg") is None
    assert list(reg.pages_dict().keys()) == ["home", "mail", "weather", "trans", "tools", "life"]
    entry = reg.get_entry("mail")
    assert entry is not None

    pg = entry.page
    assert isinstance(pg, MailPage)
    assert pg.key == "mail"
    assert pg.title == "邮箱"
    assert pg.glyph == G.MAIL
    assert pg.panel_width == 419
    assert pg.panel_height == 140

    pg.resize(395, 98)
    pg.show()
    qt_app.processEvents()

    # Header 日期时间居中检查
    hc = pg.header.rect().center().x()
    cc = pg.header.center_lbl.geometry().center().x()
    assert abs(hc - cc) <= 2

    # 空状态 1：没有邮箱账号
    assert pg.stack.currentIndex() == 0
    assert pg.empty_no_account.lbl_title.text() == "还没有邮箱"
    assert "系统凭据管理器" in pg.empty_no_account.lbl_desc.text()
    assert pg.empty_no_account.btn_action.text == "去添加"
    pg.empty_no_account.btn_action.clicked.emit()
    assert opened == ["mail_accounts"]

    # 空状态 2：有账号但没有邮件
    acc = MailAccount(id="a1", provider="qq", email="u1@qq.com", host="imap.qq.com", credential_key="Wisland/mail/a1")
    cm.set("mail.accounts", [acc.to_config_dict()])
    mgr.load_from_config()
    mgr.accounts_changed.emit()
    qt_app.processEvents()

    assert pg.stack.currentIndex() == 1
    assert pg.empty_no_mail.lbl_title.text() == "暂无邮件"
    assert pg.empty_no_mail.lbl_desc.text() == "点击刷新重新同步"
    assert pg.empty_no_mail.btn_action.text == "刷新"
    pg.close()


def test_02_multi_account_uid_isolation_filtering_and_scroll(qt_app, tmp_path):
    acc1 = MailAccount(id="acc1", provider="qq", email="a1@qq.com", display_name="工作QQ", host="imap.qq.com", credential_key="k1")
    acc2 = MailAccount(id="acc2", provider="163", email="a2@163.com", display_name="个人163", host="imap.163.com", credential_key="k2")
    cm = _make_cfg(tmp_path, [acc1, acc2])
    creds = _MemCreds()
    mgr = MailAccountManager(config_obj=cm, cred_store=creds)
    ctx = SimpleNamespace(mail=mgr, open_settings=lambda _=None: None)

    now = time.time()
    # 故意在两个账号中使用相同的 UID "1001" 与 "1002"
    mgr._headers["acc1"] = [
        MailHeader(uid="1001", account_id="acc1", sender="Alice", subject="Acc1 Mail 1", received_at=now - 10, is_read=False),
        MailHeader(uid="1002", account_id="acc1", sender="Bob", subject="Acc1 Mail 2", received_at=now - 30, is_read=True),
        MailHeader(uid="1003", account_id="acc1", sender="Carol", subject="Acc1 Mail 3", received_at=now - 50, is_read=False),
    ]
    mgr._headers["acc2"] = [
        MailHeader(uid="1001", account_id="acc2", sender="Dave", subject="Acc2 Mail 1", received_at=now - 20, is_read=False),
        MailHeader(uid="1002", account_id="acc2", sender="Eve", subject="Acc2 Mail 2", received_at=now - 40, is_read=False),
    ]

    pg = MailPage(ctx)
    pg.resize(395, 98)
    pg.show()
    qt_app.processEvents()

    # 全部账号：5 封，其中 4 封未读，严格按 received_at 倒序，不同账号同 UID 不合并
    assert pg.stack.currentIndex() == 2
    assert len(pg._rows) == 5
    assert pg.btn_filter_all._text == "全部 5"
    assert pg.btn_filter_unread._text == "未读 4"
    assert pg.lbl_local_only.text() == "仅本机"
    assert [r.hdr.subject for r in pg._rows] == [
        "Acc1 Mail 1",
        "Acc2 Mail 1",
        "Acc1 Mail 2",
        "Acc2 Mail 2",
        "Acc1 Mail 3",
    ]

    # 筛选未读
    pg.set_filter_mode("unread")
    qt_app.processEvents()
    assert len(pg._rows) == 4
    assert all(not r.hdr.is_read for r in pg._rows)

    # 切换单账号 acc1：不影响 acc2 未读，也不触发网络
    pg.set_filter_mode("all")
    pg.set_account_filter("acc1")
    qt_app.processEvents()
    assert len(pg._rows) == 3
    assert pg.btn_filter_all._text == "全部 3"
    assert pg.btn_filter_unread._text == "未读 2"
    assert sum(1 for h in mgr.headers_for("acc2") if not h.is_read) == 2

    # 全部已读（仅本机修改 acc1，不清除 acc2 未读）
    pg.mark_current_all_read()
    qt_app.processEvents()
    assert pg.btn_filter_unread._text == "未读 0"
    assert sum(1 for h in mgr.headers_for("acc2") if not h.is_read) == 2

    # 切回全部账号：acc2 仍有 2 封未读；总列表 5 封超过 52px 视口高度，需要内部滚动且滚轮被消费
    pg.set_account_filter(None)
    qt_app.processEvents()
    qt_app.sendPostedEvents()
    qt_app.processEvents()
    assert pg.btn_filter_unread._text == "未读 2"
    sb = pg.scroll.verticalScrollBar()
    assert sb.maximum() > 0

    ev = QtGui.QWheelEvent(
        QtCore.QPointF(10, 10),
        QtCore.QPointF(10, 10),
        QtCore.QPoint(0, 0),
        QtCore.QPoint(0, -120),
        QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        QtCore.Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    pg.scroll.wheelEvent(ev)
    assert ev.isAccepted()
    assert sb.value() > 0

    # 点击行进入详情 (419×236)，返回列表恢复 (419×140) 并保持选中态
    pg._rows[0].clicked.emit()
    qt_app.processEvents()
    assert pg._selected_key == ("acc1", "1001")
    assert pg.mode_stack.currentIndex() == 1
    assert (pg.panel_width, pg.panel_height) == (419, 236)
    pg.close_detail()
    qt_app.processEvents()
    assert pg.mode_stack.currentIndex() == 0
    assert (pg.panel_width, pg.panel_height) == (419, 140)
    assert pg._selected_key == ("acc1", "1001")
    assert pg._rows[0].selected is True
    pg.close()


def test_03_single_account_failure_and_concurrent_sync_guard(qt_app, tmp_path):
    acc1 = MailAccount(id="acc1", provider="qq", email="a1@qq.com", host="imap.qq.com", credential_key="k1")
    acc2 = MailAccount(id="acc2", provider="163", email="a2@163.com", host="imap.163.com", credential_key="k2")
    cm = _make_cfg(tmp_path, [acc1, acc2])
    creds = _MemCreds()
    creds.save_secret("k1", "a1@qq.com", "sec1")
    creds.save_secret("k2", "a2@163.com", "sec2")

    gate = threading.Event()
    calls = {"acc1": 0, "acc2": 0}

    class _FakeClient:
        def __init__(self, host, port, timeout=None):
            self.host = host

        def login(self, user, secret):
            if "163" in self.host:
                calls["acc2"] += 1
                raise RuntimeError("mock network failure")
            calls["acc1"] += 1
            gate.wait(timeout=1.0)

        def select(self, mailbox="INBOX", readonly=True):
            return ("OK", [b"1"])

        def uid(self, cmd, *args):
            if cmd == "SEARCH":
                return ("OK", [b"101"])
            if cmd == "FETCH":
                raw = (
                    b"From: Sender <s@qq.com>\r\n"
                    b"Subject: Hello\r\n"
                    b"Date: Wed, 18 Nov 2026 09:00:00 +0800\r\n\r\n"
                )
                return ("OK", [(b"1 (UID 101 FLAGS ())", raw)])
            return ("OK", [])

        def logout(self):
            return ("BYE", [])

    mgr = MailAccountManager(
        config_obj=cm,
        cred_store=creds,
        client_factory=lambda host, port, security, timeout: _FakeClient(host, port, timeout=timeout),
    )

    # acc1 并发发起两次：第二次必须被拦截
    ok1, _, _ = mgr.start_sync_headers("acc1")
    ok_dup, err_dup, _ = mgr.start_sync_headers("acc1")
    assert ok1 is True
    assert ok_dup is False
    assert "正在同步" in err_dup

    # acc2 失败不影响 acc1
    ok2, _, _ = mgr.start_sync_headers("acc2")
    assert ok2 is True

    gate.set()
    assert _wait_until(qt_app, lambda: not mgr.is_syncing())
    assert calls["acc1"] == 1
    assert len(mgr.headers_for("acc1")) == 1
    assert mgr.get_account("acc2").last_error != ""
    assert len(mgr.headers_for("acc1")) == 1
    mgr.shutdown()


def test_04_badge_visibility_and_provider_symbols(qt_app, tmp_path):
    now = time.time()
    acc_qq = MailAccount(id="q1", provider="qq", email="2891@qq.com", display_name="2号邮箱", host="imap.qq.com", credential_key="k1")
    acc_gm = MailAccount(id="g1", provider="gmail", email="u@gmail.com", display_name="工作", host="imap.gmail.com", credential_key="k2")
    acc_163 = MailAccount(id="n1", provider="163", email="u@163.com", display_name="网易", host="imap.163.com", credential_key="k3")
    acc_cus = MailAccount(id="c1", provider="custom", email="u@corp.io", display_name="自建", host="mail.corp.io", credential_key="k4")

    # 1) 仅 1 个启用账号：隐藏徽标（不出现 "2"）
    cm1 = _make_cfg(tmp_path / "one", [acc_qq])
    (tmp_path / "one").mkdir(exist_ok=True)
    mgr1 = MailAccountManager(config_obj=cm1, cred_store=_MemCreds())
    mgr1._headers["q1"] = [MailHeader(uid="1", account_id="q1", sender="GitHub Notification", subject="PR #1", received_at=now, is_read=False)]
    pg1 = MailPage(SimpleNamespace(mail=mgr1, open_settings=lambda *_: None))
    pg1.resize(395, 98)
    pg1.show()
    qt_app.processEvents()
    assert len(pg1._rows) == 1
    assert pg1._rows[0].show_badge is False
    assert pg1._rows[0].height() == 17
    assert "GitHub Notification" in pg1._rows[0].toolTip()
    pg1.close()

    # 2) 多启用账号 + 全部账号：显示服务商徽标 Q / G / 163 / @
    (tmp_path / "multi").mkdir(exist_ok=True)
    cm2 = _make_cfg(tmp_path / "multi", [acc_qq, acc_gm, acc_163, acc_cus])
    mgr2 = MailAccountManager(config_obj=cm2, cred_store=_MemCreds())
    mgr2._headers["q1"] = [MailHeader(uid="1", account_id="q1", sender="S1", subject="Sub1", received_at=now - 1, is_read=False)]
    mgr2._headers["g1"] = [MailHeader(uid="1", account_id="g1", sender="S2", subject="Sub2", received_at=now - 2, is_read=True)]
    mgr2._headers["n1"] = [MailHeader(uid="1", account_id="n1", sender="S3", subject="Sub3", received_at=now - 3, is_read=False)]
    mgr2._headers["c1"] = [MailHeader(uid="1", account_id="c1", sender="S4", subject="Sub4", received_at=now - 4, is_read=False)]
    pg2 = MailPage(SimpleNamespace(mail=mgr2, open_settings=lambda *_: None))
    pg2.resize(395, 98)
    pg2.show()
    qt_app.processEvents()
    assert [r.show_badge for r in pg2._rows] == [True, True, True, True]
    assert [r.badge_text for r in pg2._rows] == ["Q", "G", "163", "@"]

    # 3) 筛选到单个账号：隐藏徽标
    pg2.set_account_filter("q1")
    qt_app.processEvents()
    assert len(pg2._rows) == 1
    assert pg2._rows[0].show_badge is False
    pg2.close()


def test_05_body_fetch_html_safety_and_retry(qt_app, tmp_path):
    from email.message import EmailMessage
    from wisland.services.mail.imap import MAX_BODY_BYTES, extract_safe_body_from_bytes

    # 1) HTML 安全提取：去除 script/style/img，保留文本；跳过附件
    msg_html = EmailMessage()
    msg_html["From"] = "Sec <sec@example.com>"
    msg_html["Subject"] = "HTML Safety"
    msg_html.set_content(
        "<html><head><style>body{color:red}</style></head>"
        "<body><script>alert('xss')</script><img src='https://evil.example/a.png'/>"
        "<p>Hello <b>World</b></p><p>Line Two</p></body></html>",
        subtype="html",
    )
    msg_html.add_attachment(b"secret-bin", maintype="application", subtype="octet-stream", filename="a.bin")
    txt, src, trunc = extract_safe_body_from_bytes(msg_html.as_bytes())
    assert src == "text/html"
    assert trunc is False
    assert "alert" not in txt
    assert "evil.example" not in txt
    assert "secret-bin" not in txt
    assert "Hello World" in txt and "Line Two" in txt

    # 2) text/plain 优先
    msg_mix = EmailMessage()
    msg_mix.set_content("Plain Priority Text")
    msg_mix.add_alternative("<p>HTML Fallback</p>", subtype="html")
    txt2, src2, _ = extract_safe_body_from_bytes(msg_mix.as_bytes())
    assert src2 == "text/plain"
    assert "Plain Priority Text" in txt2

    # 3) 超过 2MB 上限截断
    big = b"Content-Type: text/plain; charset=utf-8\r\n\r\n" + (b"A" * (MAX_BODY_BYTES + 1024))
    txt_big, _, trunc_big = extract_safe_body_from_bytes(big, max_bytes=MAX_BODY_BYTES)
    assert trunc_big is True
    assert "2 MB" in txt_big

    # 4) 失败显示原因与重试，成功后只读长正文可滚动且返回重开回到顶部
    acc = MailAccount(id="a1", provider="qq", email="u@qq.com", host="imap.qq.com", credential_key="k1")
    cm = _make_cfg(tmp_path / "body", [acc])
    (tmp_path / "body").mkdir(exist_ok=True)
    creds = _MemCreds()
    creds.save_secret("k1", "u@qq.com", "sec")
    state = {"fail": True}
    long_lines = "\n".join(f"Line {i}: Wisland secure mail body" for i in range(60))
    raw_mail = f"From: Alice <a@qq.com>\r\nSubject: Long\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{long_lines}".encode("utf-8")

    class _BodyClient:
        def login(self, u, s):
            return ("OK", [])

        def select(self, m="INBOX", readonly=True):
            return ("OK", [b"1"])

        def uid(self, cmd, *args):
            if cmd == "FETCH":
                if state["fail"]:
                    raise TimeoutError("mock timeout")
                return ("OK", [(b"1 (UID 77)", raw_mail)])
            return ("OK", [])

        def logout(self):
            return ("BYE", [])

    from wisland.services.mail.cache import LocalMailBodyCache
    mgr = MailAccountManager(
        config_obj=cm,
        cred_store=creds,
        body_cache=LocalMailBodyCache(cache_dir=tmp_path / "body" / "cache"),
        client_factory=lambda *_: _BodyClient(),
    )
    hdr = MailHeader(uid="77", account_id="a1", sender="Alice", subject="Long", received_at=time.time(), is_read=False)
    mgr._headers["a1"] = [hdr]

    pg = MailPage(SimpleNamespace(mail=mgr, open_settings=lambda *_: None))
    pg.resize(395, 236)
    pg.show()
    pg.open_detail(hdr)
    assert _wait_until(qt_app, lambda: pg.body_stack.currentIndex() == 1)
    assert "超时" in pg.lbl_body_err.text()
    assert pg.lbl_detail_sender.text() == "Alice"

    state["fail"] = False
    pg.btn_retry_body.clicked.emit()
    assert _wait_until(qt_app, lambda: pg.body_stack.currentIndex() == 2)
    assert pg.body_edit.isReadOnly() is True
    assert "Line 59" in pg.body_edit.toPlainText()

    sb = pg.body_edit.verticalScrollBar()
    assert sb.maximum() > 0
    sb.setValue(sb.maximum())
    assert sb.value() > 0

    # 返回列表后再重新打开，滚动条回到顶部 0
    pg.close_detail()
    qt_app.processEvents()
    pg.open_detail(hdr)
    qt_app.processEvents()
    assert pg.body_edit.verticalScrollBar().value() == 0
    pg.close()
    mgr.shutdown()


def test_06_read_sync_policy_and_otp_lifecycle(qt_app, tmp_path):
    from wisland.core.config import cfg as global_cfg
    from wisland.services.mail.otp import extract_verification_code

    # 1) 验证码识别正例与误识别排除
    v1 = extract_verification_code("登录验证码", "您的验证码为 482910，5分钟内有效。")
    assert v1 is not None and v1.code == "482910" and v1.ttl_seconds == 300
    v2 = extract_verification_code("Security code", "Your one-time passcode is A9X4K2, valid for 90 seconds.")
    assert v2 is not None and v2.code == "A9X4K2" and v2.ttl_seconds == 90
    assert extract_verification_code("账单通知", "本次消费金额 ￥1999，订单号 20261118，有效期 30天") is None
    assert extract_verification_code("登录提醒", "Welcome to login your account") is None

    # 2) 默认不向服务器回写已读；开启后回写，且失败时保留本机已读
    acc = MailAccount(id="a1", provider="qq", email="u@qq.com", host="imap.qq.com", credential_key="k1")
    cm = _make_cfg(tmp_path / "sync", [acc])
    (tmp_path / "sync").mkdir(exist_ok=True)
    cm.set("mail.sync_read_to_server", False)
    creds = _MemCreds()
    creds.save_secret("k1", "u@qq.com", "sec")
    store_calls: list[tuple] = []
    remote_state = {"fail": False}

    class _SeenClient:
        def login(self, u, s):
            return ("OK", [])

        def select(self, m="INBOX", readonly=True):
            return ("OK", [b"1"])

        def uid(self, cmd, *args):
            if cmd == "FETCH":
                raw = b"From: Auth <a@qq.com>\r\nSubject: OTP\r\n\r\nYour verification code: 739104"
                return ("OK", [(b"1 (UID 12)", raw)])
            if cmd == "STORE":
                store_calls.append(args)
                if remote_state["fail"]:
                    return ("NO", [b"denied"])
                return ("OK", [])
            return ("OK", [])

        def logout(self):
            return ("BYE", [])

    from wisland.services.mail.cache import LocalMailBodyCache
    mgr = MailAccountManager(
        config_obj=cm,
        cred_store=creds,
        body_cache=LocalMailBodyCache(cache_dir=tmp_path / "sync" / "cache"),
        client_factory=lambda *_: _SeenClient(),
    )
    hdr1 = MailHeader(uid="12", account_id="a1", sender="Auth", subject="Your verification code: 739104", received_at=time.time(), is_read=False)
    mgr._headers["a1"] = [hdr1]

    orig_sync = global_cfg.get("mail.sync_read_to_server", False)
    orig_otp = global_cfg.get("mail.otp_enabled", True)
    try:
        global_cfg.set("mail.sync_read_to_server", False)
        global_cfg.set("mail.otp_enabled", True)

        pg = MailPage(SimpleNamespace(mail=mgr, open_settings=lambda *_: None))
        pg.resize(395, 236)
        pg.show()
        qt_app.processEvents()
        assert pg.otp_preview_card.isVisibleTo(pg)  # 列表中出现大验证码预览卡，直接明文显示
        assert pg.otp_preview_card._info.code == "739104"
        assert 1 <= pg.otp_preview_card._remaining_s <= 180

        pg.open_detail(hdr1)
        assert _wait_until(qt_app, lambda: pg.body_stack.currentIndex() == 2)
        assert hdr1.is_read is True
        assert store_calls == []  # 未开启同步，绝不修改服务器

        # 验证码：详情页一律直接明文显示，点击即可复制，且返回列表后不隐藏、不重置
        assert pg.otp_card.isVisibleTo(pg)
        assert pg.btn_otp_toggle._text == "739104"
        assert pg.lbl_otp_hint.text() == ""
        pg.btn_otp_toggle.clicked.emit()
        assert QtWidgets.QApplication.clipboard().text() == "739104"
        assert pg.lbl_otp_hint.text() == "已复制"

        pg.close_detail()
        qt_app.processEvents()
        assert pg.otp_preview_card.isVisibleTo(pg)
        assert pg.otp_preview_card._info.code == "739104"

        # 开启服务器同步并模拟远程失败：保留本机已读 + 提示失败
        cm.set("mail.sync_read_to_server", True)
        global_cfg.set("mail.sync_read_to_server", True)
        remote_state["fail"] = True
        hdr2 = MailHeader(uid="13", account_id="a1", sender="Auth", subject="OTP 2", received_at=time.time(), is_read=False)
        mgr._headers["a1"].append(hdr2)
        pg.open_detail(hdr2)
        assert _wait_until(qt_app, lambda: len(store_calls) >= 1 and pg.lbl_sync_warn.isVisibleTo(pg))
        qt_app.processEvents()
        assert hdr2.is_read is True
        pg.close()
    finally:
        global_cfg.set("mail.sync_read_to_server", orig_sync)
        global_cfg.set("mail.otp_enabled", orig_otp)
        mgr.shutdown()


def test_07_settings_otp_and_sync_preferences(qt_app, tmp_path):
    from wisland.core.config import ConfigDraft
    from wisland.ui.settings.mail_accounts import MailAccountsSettingsPage

    cm = _make_cfg(tmp_path / "set")
    (tmp_path / "set").mkdir(exist_ok=True)
    mgr = MailAccountManager(config_obj=cm, cred_store=_MemCreds())
    pg = MailAccountsSettingsPage(ctx=SimpleNamespace(mail=mgr))
    draft = ConfigDraft(cm.data)
    pg.load_from_draft(draft)

    assert pg.sw_otp_enabled.isChecked() is True
    assert pg.cmb_otp_hide.currentData() == 180
    assert pg.sw_sync_seen.isChecked() is False
    assert "仅在本机处理" in pg.lbl_privacy_body.text()

    pg.sw_otp_enabled.setChecked(False)
    idx_300 = pg.cmb_otp_hide.findData(300)
    assert idx_300 >= 0
    pg.cmb_otp_hide.setCurrentIndex(idx_300)
    pg.sw_sync_seen.setChecked(True)
    pg.save_to_draft(draft)

    assert draft.get("mail.otp_enabled") is False
    assert draft.get("mail.otp_hide_seconds") == 300
    assert draft.get("mail.sync_read_to_server") is True
    pg.close()
    mgr.shutdown()


def test_08_prefetch_5_plus_15_disk_cache_and_uidvalidity(qt_app, tmp_path):
    from wisland.services.mail.cache import LocalMailBodyCache

    acc = MailAccount(id="a1", provider="qq", email="u@qq.com", host="imap.qq.com", credential_key="k1")
    (tmp_path / "pf").mkdir(exist_ok=True)
    cm = _make_cfg(tmp_path / "pf", [acc])
    creds = _MemCreds()
    creds.save_secret("k1", "u@qq.com", "sec")
    cache = LocalMailBodyCache(cache_dir=tmp_path / "pf" / "cache", max_per_account=25)

    state = {"uv": "111", "logins": 0, "body_fetches": []}

    class _PrefetchClient:
        def __init__(self):
            self._uv = state["uv"]

        def login(self, u, s):
            state["logins"] += 1
            return ("OK", [])

        def select(self, m="INBOX", readonly=True):
            return ("OK", [f"[UIDVALIDITY {self._uv}]".encode("ascii")])

        def response(self, name):
            if name == "UIDVALIDITY":
                return ("UIDVALIDITY", [self._uv.encode("ascii")])
            return (name, [None])

        def uid(self, cmd, *args):
            if cmd == "SEARCH":
                return ("OK", [" ".join(str(i) for i in range(1, 21)).encode("ascii")])
            if cmd == "FETCH":
                spec = str(args[1]) if len(args) > 1 else ""
                uid_arg = str(args[0])
                if "HEADER.FIELDS" in spec:
                    rows = []
                    base_t = 1794960000
                    for u_str in uid_arg.split(","):
                        u_int = int(u_str)
                        dt_s = time.strftime("%a, %d Nov 2026 %H:%M:%S +0800", time.gmtime(base_t + u_int * 60))
                        raw = f"From: Sender{u_int} <s{u_int}@qq.com>\r\nSubject: Mail {u_int}\r\nDate: {dt_s}\r\n\r\n".encode("utf-8")
                        rows.append((f"{u_int} (UID {u_int} FLAGS ())".encode("ascii"), raw))
                    return ("OK", rows)
                state["body_fetches"].append((self._uv, uid_arg))
                code_txt = "您的验证码为 654321，5分钟内有效。" if uid_arg == "20" else f"Body text for UID {uid_arg}"
                raw_b = f"From: S <s@qq.com>\r\nSubject: Mail {uid_arg}\r\n\r\n{code_txt}".encode("utf-8")
                return ("OK", [(f"1 (UID {uid_arg})".encode("ascii"), raw_b)])
            return ("OK", [])

        def logout(self):
            return ("BYE", [])

    mgr = MailAccountManager(
        config_obj=cm,
        cred_store=creds,
        body_cache=cache,
        client_factory=lambda *_: _PrefetchClient(),
    )
    ok, _, _ = mgr.start_sync_headers("a1", limit=20)
    assert ok is True
    # 等待 Header + 前 5 封立即预取 + 其余 15 封低优先级后台预取全部完成
    assert _wait_until(qt_app, lambda: len(state["body_fetches"]) == 20, timeout_s=5.0)
    # 只登录过两次：第 1 次同会话读 20 封 Header + 最近 5 封；第 2 次低优先级单会话预取其余 15 封
    assert state["logins"] == 2
    assert len(mgr.headers_for("a1")) == 20

    # 最近第 20 封直接命中缓存（带验证码），进入详情不显示加载条
    pg = MailPage(SimpleNamespace(mail=mgr, open_settings=lambda *_: None))
    pg.resize(395, 236)
    pg.show()
    hdr20 = mgr.headers_for("a1")[0]
    assert hdr20.uid == "20"
    pg.open_detail(hdr20)
    qt_app.processEvents()
    assert pg.body_stack.currentIndex() == 2  # 直接显示正文，不经过加载页 0
    assert "654321" in pg.body_edit.toPlainText()
    assert pg.otp_card.isVisibleTo(pg)
    assert pg.btn_otp_toggle._text == "654321"
    pg.close()

    # 再次刷新（UIDVALIDITY 未变）：不重新下载已有正文
    fetches_before = len(state["body_fetches"])
    ok_again, _, _ = mgr.start_sync_headers("a1", limit=20)
    assert ok_again is True
    assert _wait_until(qt_app, lambda: not mgr.is_syncing())
    qt_app.processEvents()
    assert len(state["body_fetches"]) == fetches_before

    # 模拟重启（新建 manager 但复用同一磁盘缓存）：无网络请求直接命中本机磁盘缓存与验证码
    mgr2 = MailAccountManager(
        config_obj=cm,
        cred_store=creds,
        body_cache=cache,
        client_factory=lambda *_: (_ for _ in ()).throw(AssertionError("should not hit network")),
    )
    b_disk = mgr2.get_cached_body("a1", "20", "111")
    assert b_disk is not None and "654321" in b_disk.text
    otp_disk = mgr2.get_verification_code("a1", "20", "Mail 20", "111")
    assert otp_disk is not None and otp_disk.code == "654321"

    # UIDVALIDITY 变化后失效旧缓存并重新拉取
    state["uv"] = "222"
    mgr.start_sync_headers("a1", limit=20)
    assert _wait_until(qt_app, lambda: len(state["body_fetches"]) == fetches_before + 20, timeout_s=5.0)
    mgr.shutdown()
    mgr2.shutdown()


def test_09_mail_monitor_idle_baseline_notify_and_per_account_unread(qt_app, tmp_path):
    from wisland.services.mail.cache import LocalMailBodyCache
    from wisland.services.mail.monitor import MailMonitorService
    from wisland.services.notifications.controller import NotificationController
    from wisland.services.notifications.sources.windows import WindowsNotificationSource

    class _FakeWinBackend:
        def permission_status(self):
            from wisland.services.notifications.models import PermissionStatus
            return PermissionStatus.ALLOWED

        def request_permission(self):
            return self.permission_status()

        def fetch_toasts(self):
            return []

    acc1 = MailAccount(id="a1", provider="qq", email="a1@qq.com", host="imap.qq.com", credential_key="k1")
    acc2 = MailAccount(id="a2", provider="163", email="a2@163.com", host="imap.163.com", credential_key="k2")
    (tmp_path / "mon").mkdir(exist_ok=True)
    cm = _make_cfg(tmp_path / "mon", [acc1, acc2])
    creds = _MemCreds()
    creds.save_secret("k1", "a1@qq.com", "s1")
    creds.save_secret("k2", "a2@163.com", "s2")
    cache = LocalMailBodyCache(cache_dir=tmp_path / "mon" / "cache")

    ev_new_a1 = threading.Event()
    ev_new_a2 = threading.Event()
    idle_done_calls = {"count": 0}
    logout_calls = {"count": 0}

    class _IdleClient:
        capabilities = (b"IMAP4rev1", b"IDLE")

        def __init__(self, host):
            self.host = host
            self.pushed = False

        def login(self, u, s):
            return ("OK", [])

        def select(self, m="INBOX", readonly=True):
            return ("OK", [b"[UIDVALIDITY 501]"])

        def response(self, name):
            if name == "UIDVALIDITY":
                return ("UIDVALIDITY", [b"501"])
            return (name, [None])

        def uid(self, cmd, *args):
            if cmd == "SEARCH":
                if "qq" in self.host:
                    return ("OK", [b"1 2 3"] if not self.pushed else [b"1 2 3 4"])
                return ("OK", [b"10"] if not self.pushed else [b"10 11"])
            if cmd == "FETCH":
                uid_arg = str(args[0])
                if "qq" in self.host:
                    raw = b"From: Alice <a@qq.com>\r\nSubject: New QQ Mail\r\nDate: Wed, 18 Nov 2026 11:00:00 +0800\r\n\r\n"
                else:
                    raw = b"From: Bob <b@163.com>\r\nSubject: New 163 Mail\r\nDate: Wed, 18 Nov 2026 11:01:00 +0800\r\n\r\n"
                return ("OK", [(f"{uid_arg} (UID {uid_arg} FLAGS ())".encode("ascii"), raw)])
            return ("OK", [])

        def idle_wait(self, timeout_s, stop_ev):
            ev = ev_new_a1 if "qq" in self.host else ev_new_a2
            while not stop_ev.is_set():
                if ev.wait(0.02):
                    ev.clear()
                    self.pushed = True
                    return
            return

        def idle_done(self):
            idle_done_calls["count"] += 1

        def logout(self):
            logout_calls["count"] += 1
            return ("BYE", [])

    mgr = MailAccountManager(
        config_obj=cm,
        cred_store=creds,
        body_cache=cache,
        client_factory=lambda host, *_args: _IdleClient(host),
    )
    ctrl = NotificationController(win_source=WindowsNotificationSource(backend=_FakeWinBackend()))
    opened_events: list[tuple[str, str]] = []
    ctrl.mail_opener = lambda ev: (opened_events.append((ev.account_key, str((ev.meta or {}).get("uid")))) or True)

    mon = MailMonitorService(
        mgr,
        controller=ctrl,
        config_obj=cm,
        client_factory=lambda host, *_args: _IdleClient(host),
    )
    mon.start()

    # 等待两个账号完成初始 baseline（旧邮件 1,2,3 和 10 不得触发通知）
    assert _wait_until(qt_app, lambda: all(w._baseline_uid is not None for w in mon._workers.values()))
    qt_app.processEvents()
    assert ctrl.counts.get("mail", 0) == 0
    assert len(ctrl.history) == 0

    # 触发两个账号各收到 1 封新邮件
    ev_new_a1.set()
    ev_new_a2.set()
    assert _wait_until(qt_app, lambda: ctrl.counts.get("mail", 0) == 2)
    assert mon.unread_count_for_account("a1") == 1
    assert mon.unread_count_for_account("a2") == 1

    # 默认横幅只显示发件人/标题，不显示正文
    assert ctrl.current_banner is not None
    sub = ctrl.format_banner_sub(ctrl.current_banner)
    assert "Alice" in sub or "Bob" in sub

    # 查看 a1 的新邮件后，只清除 a1 未读，保留 a2 未读色点
    mon.mark_account_seen("a1", "4", "501")
    assert mon.unread_count_for_account("a1") == 0
    assert mon.unread_count_for_account("a2") == 1
    assert ctrl.counts.get("mail", 0) == 1

    # 查看 a2 的新邮件后，mail 总未读清零
    mon.mark_account_seen("a2", "11", "501")
    assert ctrl.counts.get("mail", 0) == 0

    # 停止监听：先退出 IDLE、注销 IMAP 再结束线程
    mon.stop()
    assert idle_done_calls["count"] >= 1
    assert logout_calls["count"] >= 2
    assert all(not w.thread.is_alive() for w in mon._workers.values())
    ctrl.stop()
    mgr.shutdown()


def test_10_otp_preview_card_layout_interaction_and_filtering(qt_app, tmp_path):
    from wisland.services.mail.cache import LocalMailBodyCache
    from wisland.ui.pages.mail import _MailRow, _sender_display_name

    assert _sender_display_name('"bixis3977" <bixis3977@agent.qq.com>') == "bixis3977"

    acc1 = MailAccount(id="a1", provider="qq", email="a1@qq.com", display_name="工作QQ", host="imap.qq.com", credential_key="k1")
    acc2 = MailAccount(id="a2", provider="gmail", email="a2@gmail.com", display_name="Gmail", host="imap.gmail.com", credential_key="k2")
    (tmp_path / "card").mkdir(exist_ok=True)
    cm = _make_cfg(tmp_path / "card", [acc1, acc2])
    cache = LocalMailBodyCache(cache_dir=tmp_path / "card" / "cache")
    mgr = MailAccountManager(config_obj=cm, cred_store=_MemCreds(), body_cache=cache)

    now = time.time()
    # a1 无验证码邮件；a2 有两封不同时间的验证码邮件（最新 998877，较旧 112233）
    mgr._headers["a1"] = [
        MailHeader(uid="1", account_id="a1", sender="Weekly Digest", subject="Regular newsletter", received_at=now - 5, is_read=False),
        MailHeader(uid="2", account_id="a1", sender="Cloud Alert", subject="Monthly report ready", received_at=now - 200, is_read=True),
    ]
    mgr._headers["a2"] = [
        MailHeader(uid="10", account_id="a2", sender="GitHub", subject="Verification code 998877", received_at=now - 10, is_read=True),
        MailHeader(uid="11", account_id="a2", sender="Steam", subject="Your verification code: 112233", received_at=now - 50, is_read=False),
    ]

    pg = MailPage(SimpleNamespace(mail=mgr, open_settings=lambda *_: None))
    pg.resize(395, 98)
    pg.show()
    qt_app.processEvents()

    # 1) 全部账号下存在验证码：左侧紧凑（约 148px）、右侧更宽（约 241px）、间距 6px、顶部对齐
    assert pg.otp_preview_card.isVisibleTo(pg)
    assert 142 <= pg.otp_preview_card.width() <= 154
    assert 235 <= pg.right_pane.width() <= 247
    assert pg.right_pane.width() > pg.otp_preview_card.width()
    assert pg.right_pane.x() - (pg.otp_preview_card.x() + pg.otp_preview_card.width()) == 6
    assert pg.otp_preview_card.y() == pg.right_pane.y()
    assert pg.otp_preview_card.height() == pg.right_pane.height()
    assert pg.otp_preview_card.height() >= 66

    # 2) 左侧区域透明无背景/无描边：渲染到透明底 QImage 检查角落与边缘像素 alpha == 0
    img = QtGui.QImage(pg.otp_preview_card.size(), QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtCore.Qt.GlobalColor.transparent)
    pg.otp_preview_card.render(
        img, QtCore.QPoint(), QtGui.QRegion(), QtWidgets.QWidget.RenderFlag.DrawChildren
    )
    assert img.pixelColor(0, 0).alpha() == 0
    assert img.pixelColor(img.width() - 1, img.height() - 1).alpha() == 0

    # 3) 左侧四层文字矩形互不重叠
    header_r, code_r, subject_r, footer_r = pg.otp_preview_card._layout_rects()
    assert code_r.top() >= header_r.bottom()
    assert subject_r.top() >= code_r.bottom()
    assert footer_r.top() >= subject_r.bottom()
    assert pg.otp_preview_card.current_key() == ("a2", "10")
    assert pg.otp_preview_card._info.code == "998877"

    # 4) 右侧统一使用单行 _MailRow，不包含 "已隐藏" 标签，且内部滚轮消费
    assert all(isinstance(r, _MailRow) for r in pg._rows)
    assert all(r.show_otp_badge is False for r in pg._rows)
    assert pg._rows[0].height() == 17

    sb = pg.scroll.verticalScrollBar()
    assert sb.maximum() > 0
    ev = QtGui.QWheelEvent(
        QtCore.QPointF(10, 10), QtCore.QPointF(10, 10),
        QtCore.QPoint(0, 0), QtCore.QPoint(0, -120),
        QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier,
        QtCore.Qt.ScrollPhase.NoScrollPhase, False,
    )
    pg.scroll.wheelEvent(ev)
    assert ev.isAccepted()
    assert sb.value() > 0

    # 5) 一律直接明文显示，点击即复制，且绝不打开邮件详情；倒计时 180s 到期后自动关闭左侧区并恢复整宽
    assert 1 <= pg.otp_preview_card._remaining_s <= 180
    pg.otp_preview_card.clicked.emit()
    assert QtWidgets.QApplication.clipboard().text() == "998877"
    assert pg.otp_preview_card._copied is True
    assert pg.mode_stack.currentIndex() == 0

    # 模拟灵动岛收起再展开后倒计时继续减少，不重置为 180s
    tok_10 = ("a2", "10", "998877")
    pg._otp_deadlines[tok_10] = time.monotonic() + 25.0
    pg.otp_preview_card._reveal_deadline = pg._otp_deadlines[tok_10]
    pg.hide()
    qt_app.processEvents()
    assert not pg.otp_preview_card._hide_timer.isActive()
    pg.show()
    qt_app.processEvents()
    assert pg.otp_preview_card._remaining_s <= 25
    assert pg.otp_preview_card._hide_timer.isActive()

    # 6) 切换未读筛选：uid 10 已读被过滤掉，预览卡自动切换到未读的 112233（同样直接显示并分配 180s）
    pg.set_filter_mode("unread")
    qt_app.processEvents()
    assert pg.otp_preview_card.isVisibleTo(pg)
    assert pg.otp_preview_card.current_key() == ("a2", "11")
    assert pg.otp_preview_card._info.code == "112233"
    assert 1 <= pg.otp_preview_card._remaining_s <= 180

    # 模拟 180s 到期：自动关闭左侧预览卡，右侧恢复整宽，且刷新/切换后不再重新弹出同一封已过期验证码
    tok_11 = ("a2", "11", "112233")
    pg._otp_deadlines[tok_11] = time.monotonic() - 0.1
    pg.otp_preview_card._reveal_deadline = pg._otp_deadlines[tok_11]
    pg.otp_preview_card._on_tick()
    qt_app.processEvents()
    assert not pg.otp_preview_card.isVisibleTo(pg)
    assert pg.right_pane.width() == 395
    assert all(r.isVisibleTo(pg) for r in pg._rows)

    pg.refresh()
    qt_app.processEvents()
    assert not pg.otp_preview_card.isVisibleTo(pg)

    # 7) 收到新的验证码邮件后：重新出现左侧预览卡，且右侧列表行全部可见
    mgr._headers["a2"].insert(
        0,
        MailHeader(uid="12", account_id="a2", sender="WorkBuddy", subject="Your verification code: 429954", received_at=now + 5, is_read=False),
    )
    mgr.headers_updated.emit("a2", mgr._headers["a2"])
    qt_app.processEvents()
    assert pg.otp_preview_card.isVisibleTo(pg)
    assert pg.otp_preview_card._info.code == "429954"
    assert len(pg._rows) >= 2
    assert all(r.isVisibleTo(pg) and r.height() == 17 for r in pg._rows)

    # 8) 切换到无验证码的账号 a1：左侧预览卡完全隐藏，计时器停止，右侧列表恢复整宽
    pg.set_filter_mode("all")
    pg.set_account_filter("a1")
    qt_app.processEvents()
    assert not pg.otp_preview_card.isVisibleTo(pg)
    assert not pg.otp_preview_card._hide_timer.isActive()
    assert pg.right_pane.width() == 395
    assert all(isinstance(r, _MailRow) and r.isVisibleTo(pg) for r in pg._rows)
    pg.close()
    mgr.shutdown()

