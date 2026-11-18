# -*- coding: utf-8 -*-
"""多账号邮件基础设施与账号管理单元测试及模拟 IMAP 测试。"""
from __future__ import annotations

import imaplib
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PySide6 import QtWidgets

from wisland.core.config import Config, validate_config
from wisland.services.mail.credentials import InMemoryCredentialStore, WindowsCredentialStore, make_credential_key
from wisland.services.mail.imap import fetch_recent_headers
from wisland.services.mail.imap import test_imap_connection as run_imap_test
from wisland.services.mail.manager import MAX_RUNTIME_HEADERS, MailAccountManager
from wisland.services.mail.models import MailAccount
from wisland.services.mail.providers import get_provider, validate_account_params
from wisland.ui.settings.mail_accounts import MailAccountsSettingsPage


class _FakeIMAP:
    """模拟 IMAP 服务器：记录所有命令以验证只读 EXAMINE、BODY.PEEK、无附件下载且不改服务器状态。"""

    def __init__(self, host, port, security, timeout, mode="ok", delay=0.0, message_count=5):
        self.host = host
        self.port = port
        self.security = security
        self.timeout = timeout
        self.mode = mode
        self.delay = delay
        self.message_count = message_count
        self.commands: list[tuple] = []
        self.logged_out = False

    def login(self, user, secret):
        if self.delay:
            time.sleep(self.delay)
        if self.mode == "timeout":
            raise TimeoutError("timed out")
        if self.mode == "bad_auth" or secret == "wrong_code":
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        self.commands.append(("LOGIN", user))
        return "OK", [b"Logged in"]

    def xatom(self, cmd, *args):
        self.commands.append(("XATOM", cmd, args))
        return "OK", [b""]

    def select(self, mailbox="INBOX", readonly=False):
        self.commands.append(("SELECT", mailbox, readonly))
        return "OK", [b"1"]

    def noop(self):
        self.commands.append(("NOOP",))
        return "OK", [b"NOOP completed"]

    def uid(self, cmd, *args):
        self.commands.append(("UID", cmd, args))
        if cmd == "SEARCH":
            uids = b" ".join(str(100 + i).encode("ascii") for i in range(self.message_count))
            return "OK", [uids]
        if cmd == "FETCH":
            uid_arg, spec = args[0], args[1]
            assert "BODY.PEEK[HEADER.FIELDS" in spec, "必须仅抓取 BODY.PEEK 头字段，不能下载正文/附件"
            out = []
            for idx, u in enumerate(uid_arg.split(",")):
                flags = rb"\Seen" if idx % 2 == 0 else rb""
                meta = f"1 (UID {u} FLAGS ({flags.decode()}))".encode("ascii")
                hdr = (
                    f"From: Tester <sender{u}@example.com>\r\n"
                    f"Subject: =?utf-8?B?5rWL6K+V6YKu5Lu2?=\r\n"
                    f"Date: Wed, 18 Nov 2026 09:{idx % 60:02d}:00 +0900\r\n\r\n"
                ).encode("utf-8")
                out.append((meta, hdr))
            return "OK", out
        return "NO", []

    def logout(self):
        self.logged_out = True
        self.commands.append(("LOGOUT",))
        return "BYE", [b""]


class TestMailInfra(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cfg_path = Path(self.tmpdir.name) / "config.json"
        self.cfg = Config(path=self.cfg_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_01_providers_custom_validation_and_outlook_rejection(self):
        """测试七大服务商默认配置、自定义服务器校验与 Microsoft OAuth 提前拒绝。"""
        expected = {
            "qq": ("imap.qq.com", 993, "ssl"),
            "163": ("imap.163.com", 993, "ssl"),
            "126": ("imap.126.com", 993, "ssl"),
            "exmail": ("imap.exmail.qq.com", 993, "ssl"),
            "icloud": ("imap.mail.me.com", 993, "ssl"),
            "gmail": ("imap.gmail.com", 993, "ssl"),
        }
        for key, (h, p, sec) in expected.items():
            spec = get_provider(key)
            self.assertEqual((spec.host, spec.port, spec.security), (h, p, sec))

        # 自定义服务器校验
        ok_bad_host, msg_h, _ = validate_account_params("custom", "a@b.com", "sec", host="bad host!", port=993)
        self.assertFalse(ok_bad_host)
        self.assertIn("服务器", msg_h)

        ok_bad_port, msg_p, _ = validate_account_params("custom", "a@b.com", "sec", host="imap.my.com", port=70000)
        self.assertFalse(ok_bad_port)
        self.assertIn("端口", msg_p)

        ok_custom, _, clean = validate_account_params("custom", "a@b.com", "sec", host="imap.my.com", port=143, security="starttls")
        self.assertTrue(ok_custom)
        self.assertEqual((clean["host"], clean["port"], clean["security"]), ("imap.my.com", 143, "starttls"))

        # 不支持 Microsoft OAuth 提前拒绝
        for ms_email in ("user@outlook.com", "user@hotmail.com", "user@live.com"):
            ok_ms, msg_ms, _ = validate_account_params("custom", ms_email, "sec", host="outlook.office365.com")
            self.assertFalse(ok_ms)
            self.assertIn("OAuth", msg_ms)

    def test_02_credentials_win32_store_and_no_secret_in_config(self):
        """测试 Windows Credential Manager 保存/读取/删除与配置文件绝不落盘密码。"""
        store = WindowsCredentialStore()
        target = make_credential_key("wisland_unit_test_acc")
        try:
            self.assertTrue(store.save_secret(target, "test@qq.com", "my_auth_code_123"))
            self.assertEqual(store.read_secret(target), "my_auth_code_123")
            self.assertTrue(store.delete_secret(target))
            self.assertIsNone(store.read_secret(target))
        finally:
            store.delete_secret(target)

        # 配置校验必须剥离任何敏感键且恢复损坏的 mail.accounts
        ok_v, errs, cleaned = validate_config({
            "mail": {
                "accounts": [{
                    "id": "a1",
                    "provider": "qq",
                    "email": "u@qq.com",
                    "host": "imap.qq.com",
                    "password": "LEAKED_PASSWORD",
                    "auth_code": "LEAKED_CODE",
                    "credential_key": "Wisland/mail/a1",
                }]
            }
        }, strict=False)
        self.assertTrue(ok_v)
        self.assertEqual(errs, [])
        acc_dict = cleaned["mail"]["accounts"][0]
        self.assertNotIn("password", acc_dict)
        self.assertNotIn("auth_code", acc_dict)

        ok_bad, bad_errs, bad_cleaned = validate_config({"mail": {"accounts": "corrupted"}}, strict=False)
        self.assertFalse(ok_bad)
        self.assertTrue(bad_errs)
        self.assertEqual(bad_cleaned["mail"]["accounts"], [])

    def test_03_imap_timeout_bad_auth_cancel_and_readonly_headers(self):
        """测试 IMAP 超时、错误授权码、取消连接以及只读读取最近邮件头（不下载附件、不改状态）。"""
        acc = MailAccount(id="acc1", provider="qq", email="u@qq.com", host="imap.qq.com", port=993, security="ssl")

        # 1) 超时
        ok_t, err_t = run_imap_test(acc, "code", client_factory=lambda *a: _FakeIMAP(*a, mode="timeout"))
        self.assertFalse(ok_t)
        self.assertIn("超时", err_t)

        # 2) 错误授权码（绝不泄露密码明文）
        ok_b, err_b = run_imap_test(acc, "wrong_code", client_factory=lambda *a: _FakeIMAP(*a, mode="bad_auth"))
        self.assertFalse(ok_b)
        self.assertNotIn("wrong_code", err_b)
        self.assertIn("授权码", err_b)

        # 3) 取消连接
        ev = threading.Event()
        ev.set()
        ok_c, err_c = run_imap_test(acc, "code", cancel_event=ev, client_factory=lambda *a: _FakeIMAP(*a))
        self.assertFalse(ok_c)
        self.assertIn("取消", err_c)

        # 4) 模拟服务器只读抓取最近邮件头
        created_clients: list[_FakeIMAP] = []

        def _factory(*a):
            c = _FakeIMAP(*a, message_count=25)
            created_clients.append(c)
            return c

        ok_f, hdrs, err_f = fetch_recent_headers(acc, "code", limit=20, client_factory=_factory)
        self.assertTrue(ok_f)
        self.assertEqual(err_f, "")
        self.assertEqual(len(hdrs), 20)
        self.assertEqual(hdrs[0].subject, "测试邮件")
        # 确认只读 EXAMINE 且无任何修改服务器标志的 STORE/EXPUNGE/DELETE
        cmds = created_clients[0].commands
        self.assertIn(("SELECT", "INBOX", True), cmds)
        self.assertFalse(any(c[0] in ("STORE", "EXPUNGE", "DELETE") for c in cmds))

    def test_04_manager_multi_account_lifecycle_and_reconnect(self):
        """测试多账号独立保存、独立错误状态、失败不落盘、重连失败保旧凭据与删除清理凭据。"""
        creds = InMemoryCredentialStore()
        state = {"mode": "ok"}
        mgr = MailAccountManager(
            config_obj=self.cfg,
            cred_store=creds,
            client_factory=lambda *a: _FakeIMAP(*a, mode=state["mode"], message_count=70),
        )
        done_events: list[tuple[str, bool, str]] = []
        mgr.task_finished.connect(lambda rid, ok, err: done_events.append((rid, ok, err)))

        # 1) 添加失败不留下半完成账号或凭据
        state["mode"] = "bad_auth"
        started, _, rid_fail = mgr.start_add_account("qq", "fail@qq.com", "bad")
        self.assertTrue(started)
        deadline = time.monotonic() + 2.0
        while not any(x[0] == rid_fail for x in done_events) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(mgr.list_accounts(), [])
        self.assertEqual(creds._data, {})

        # 2) 成功添加两个不同账号
        state["mode"] = "ok"
        _, _, rid1 = mgr.start_add_account("qq", "one@qq.com", "sec_one", display_name="主号")
        _, _, rid2 = mgr.start_add_account("163", "two@163.com", "sec_two", display_name="工作")
        deadline = time.monotonic() + 2.0
        while sum(1 for x in done_events if x[0] in (rid1, rid2)) < 2 and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

        accs = mgr.list_accounts()
        self.assertEqual(len(accs), 2)
        disk_text = self.cfg_path.read_text(encoding="utf-8")
        self.assertNotIn("sec_one", disk_text)
        self.assertNotIn("sec_two", disk_text)

        # 3) 重连验证失败不覆盖旧凭据，验证成功才替换
        acc1 = accs[0]
        state["mode"] = "bad_auth"
        _, _, rid_r1 = mgr.start_reconnect_account(acc1.id, "bad_new")
        deadline = time.monotonic() + 2.0
        while not any(x[0] == rid_r1 for x in done_events) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(creds.read_secret(acc1.credential_key), "sec_one")
        self.assertTrue(mgr.get_account(acc1.id).last_error)
        self.assertEqual(mgr.get_account(accs[1].id).last_error, "")  # 另一个账号独立不受影响

        state["mode"] = "ok"
        _, _, rid_r2 = mgr.start_reconnect_account(acc1.id, "good_new")
        deadline = time.monotonic() + 2.0
        while not any(x[0] == rid_r2 for x in done_events) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(creds.read_secret(acc1.credential_key), "good_new")
        self.assertEqual(mgr.get_account(acc1.id).last_error, "")

        # 4) 同步最近邮件上限 <= 60
        _, _, rid_s = mgr.start_sync_headers(acc1.id, limit=70)
        deadline = time.monotonic() + 2.0
        while not any(x[0] == rid_s for x in done_events) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertLessEqual(len(mgr.headers_for(acc1.id)), MAX_RUNTIME_HEADERS)

        # 5) 停用/改别名 & 重启恢复 & 删除清理凭据
        mgr.set_enabled(accs[1].id, False)
        mgr.set_display_name(acc1.id, "个人主号")
        mgr2 = MailAccountManager(config_obj=Config(path=self.cfg_path), cred_store=creds)
        self.assertEqual(len(mgr2.list_accounts()), 2)
        self.assertEqual(mgr2.get_account(acc1.id).display_name, "个人主号")
        self.assertFalse(mgr2.get_account(accs[1].id).enabled)

        unrelated = Path(self.tmpdir.name) / "keep_me.txt"
        unrelated.write_text("ok", encoding="utf-8")
        self.assertTrue(mgr2.remove_account(acc1.id))
        self.assertIsNone(creds.read_secret(acc1.credential_key))
        self.assertTrue(unrelated.exists())
        mgr.shutdown()
        mgr2.shutdown()

    def test_05_mail_accounts_settings_page_ui(self):
        """测试设置窗口「邮件账号」页配置说明换行、密码显隐、响应式布局、草稿保存与取消语义。"""
        from wisland.ui.settings_window import SettingsWindow
        from wisland.ui.settings.mail_accounts import _classify_account_status

        creds = InMemoryCredentialStore()
        mgr = MailAccountManager(config_obj=self.cfg, cred_store=creds, client_factory=lambda *a: _FakeIMAP(*a))
        win = SettingsWindow(ctx=type("Ctx", (), {"mail": mgr})(), config_obj=self.cfg)
        win.switch_category("mail_accounts")
        page: MailAccountsSettingsPage = next(pg for pg in win.pages if isinstance(pg, MailAccountsSettingsPage))
        win.resize(840, 620)
        win.show()
        self.app.processEvents()

        # 1) 配置说明自动换行，不截断
        self.assertTrue(page.lbl_guide_body.wordWrap())
        self.assertNotIn("…", page.lbl_guide_body.text())

        # 2) 默认 QQ 不显示自定义服务器字段；切到自定义 IMAP 才显示
        self.assertTrue(page.row_host.isHidden())
        idx_custom = page.cmb_provider.findData("custom")
        page.cmb_provider.setCurrentIndex(idx_custom)
        self.app.processEvents()
        self.assertFalse(page.row_host.isHidden())

        # 3) 授权码显示/隐藏切换不改变输入框宽度
        w_before = page.ed_secret.width()
        self.assertEqual(page.ed_secret.edit.echoMode(), QtWidgets.QLineEdit.EchoMode.Password)
        page.ed_secret.toggle_visible()
        self.assertEqual(page.ed_secret.edit.echoMode(), QtWidgets.QLineEdit.EchoMode.Normal)
        self.assertEqual(page.ed_secret.width(), w_before)
        page.ed_secret.toggle_visible()
        self.assertEqual(page.ed_secret.edit.echoMode(), QtWidgets.QLineEdit.EchoMode.Password)

        # 4) 窄屏 (<560px 内容区) 切换为上下排列
        page.resize(500, 600)
        self.app.processEvents()
        self.assertTrue(page._narrow_mode)
        self.assertTrue(page.row_email._compact)

        # 5) 输入 Outlook 邮箱立即拒绝
        page.ed_email.setText("someone@outlook.com")
        page.ed_secret.edit.setText("abc")
        page.ed_host.setText("outlook.office365.com")
        page._on_stage_add_clicked()
        self.assertTrue(page.lbl_status.isVisible())
        self.assertIn("OAuth", page.lbl_status.text())

        # 6) 验证并加入设置只进草稿，取消不写 config/凭据，保存才提交
        page.cmb_provider.setCurrentIndex(page.cmb_provider.findData("qq"))
        page.ed_email.setText("draft_user@qq.com")
        page.ed_secret.edit.setText("secret_abc")
        page._on_stage_add_clicked()
        deadline = time.monotonic() + 2.0
        while page._active_req_id and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(len(page._draft_accounts), 1)
        self.assertEqual(self.cfg.get("mail.accounts"), [])
        self.assertEqual(creds._data, {})

        # 取消：清空暂存，不落盘
        win.cancel_and_close(prompt_if_dirty=False)
        self.assertEqual(page._draft_accounts, [])
        self.assertEqual(page._pending_secrets, {})
        self.assertEqual(self.cfg.get("mail.accounts"), [])
        self.assertEqual(creds._data, {})

        # 再次加入并点击底部保存设置 -> 同时写配置与凭据
        win.show()
        page.ed_email.setText("saved_user@qq.com")
        page.ed_secret.edit.setText("secret_xyz")
        page._on_stage_add_clicked()
        deadline = time.monotonic() + 2.0
        while page._active_req_id and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(win.save_settings())
        saved_accs = self.cfg.get("mail.accounts")
        self.assertEqual(len(saved_accs), 1)
        self.assertEqual(creds.read_secret(saved_accs[0]["credential_key"]), "secret_xyz")

        # 7) 状态标签分类覆盖
        a = MailAccount(id="x", provider="qq", email="x@qq.com", host="imap.qq.com")
        self.assertEqual(_classify_account_status(a, True, False)[0], "待保存")
        self.assertEqual(_classify_account_status(a, False, True)[0], "正在连接")
        self.assertEqual(_classify_account_status(a, False, False)[0], "未测试")
        a.last_test_at = 1.0
        self.assertEqual(_classify_account_status(a, False, False)[0], "连接正常")
        a.last_error = "授权码错误"
        self.assertEqual(_classify_account_status(a, False, False)[0], "登录失效")
        a.last_error = "连接超时"
        self.assertEqual(_classify_account_status(a, False, False)[0], "网络错误")
        a.enabled = False
        self.assertEqual(_classify_account_status(a, False, False)[0], "已停用")

        win.close()
        mgr.shutdown()

    def test_06_settings_window_theme_stress_and_alignment(self):
        """测试邮件账号页两列统一对齐，以及设置窗口反复开关与深浅主题切换不崩溃。"""
        import gc
        from wisland.ui.settings_window import SettingsWindow
        from wisland.ui.theme import theme

        creds = InMemoryCredentialStore()
        mgr = MailAccountManager(config_obj=self.cfg, cred_store=creds, client_factory=lambda *a: _FakeIMAP(*a))
        orig_mode = theme.mode
        try:
            for _ in range(3):
                win = SettingsWindow(ctx=type("Ctx", (), {"mail": mgr})(), config_obj=self.cfg)
                win.switch_category("mail_accounts")
                win.resize(860, 740)
                win.show()
                self.app.processEvents()
                page = next(pg for pg in win.pages if isinstance(pg, MailAccountsSettingsPage))
                # 验证所有控件列左边缘与宽度一致 (330px)
                xs = {
                    page.cmb_provider.mapTo(page.form_card, page.cmb_provider.rect().topLeft()).x(),
                    page.ed_email.mapTo(page.form_card, page.ed_email.rect().topLeft()).x(),
                    page.ed_alias.mapTo(page.form_card, page.ed_alias.rect().topLeft()).x(),
                    page.ed_secret.mapTo(page.form_card, page.ed_secret.rect().topLeft()).x(),
                    page.action_bar.mapTo(page.form_card, page.action_bar.rect().topLeft()).x(),
                }
                self.assertEqual(len(xs), 1, f"控件列左边缘不一致: {xs}")
                for w in (page.cmb_provider, page.ed_email, page.ed_alias, page.ed_secret, page.action_bar):
                    self.assertEqual(w.width(), 330)

                theme.set_mode("light")
                self.app.processEvents()
                theme.set_mode("dark")
                self.app.processEvents()
                win.close()
                win.deleteLater()
                self.app.processEvents()
                gc.collect()
        finally:
            theme.set_mode(orig_mode)
            mgr.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
