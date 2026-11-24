# -*- coding: utf-8 -*-
"""针对全仓库静态审查 7 项隐患的回归测试（邮件唤醒补查/旧批次拦截、字幕模型下载校验/416/异常兜底、设置备份失败中止、置物架写盘原子性）。"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from PySide6 import QtWidgets

from wisland.core.config import Config
from wisland.live import download as dl_mod
from wisland.services.mail.cache import LocalMailBodyCache
from wisland.services.mail.manager import MailAccountManager
from wisland.services.mail.models import MailAccount, MailHeader
from wisland.services.mail.monitor import MailMonitorService
from wisland.services.shelf import ShelfService
from wisland.ui.settings import data as data_mod


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _MemCreds:
    def __init__(self):
        self._store: dict[str, str] = {}

    def write_secret(self, key: str, secret: str):
        self._store[key] = secret

    def read_secret(self, key: str) -> str:
        return self._store.get(key, "")

    def delete_secret(self, key: str):
        self._store.pop(key, None)


class _FakeImapClient:
    def __init__(self, state: dict):
        self._state = state
        self.capabilities = (b"IMAP4REV1", b"IDLE")

    def login(self, user, pwd):
        return ("OK", [b"logged in"])

    def select(self, box="INBOX", readonly=True):
        return ("OK", [f"[UIDVALIDITY {self._state['uv']}]".encode("ascii")])

    def response(self, code):
        if code == "UIDVALIDITY":
            return ("UIDVALIDITY", [str(self._state["uv"]).encode("ascii")])
        return (code, [None])

    def uid(self, cmd, *args):
        c = cmd.upper()
        if c == "SEARCH":
            joined = " ".join(str(u) for u in self._state["uids"]).encode("ascii")
            return ("OK", [joined])
        if c == "FETCH":
            want = [u.strip() for u in str(args[0]).split(",") if u.strip()]
            rows = []
            for u in want:
                hdr = f"From: tester@example.com\r\nSubject: Mail {u}\r\nDate: Tue, 24 Nov 2026 10:00:00 +0800\r\n\r\n".encode("utf-8")
                rows.append((f"1 (UID {u} FLAGS ())".encode("ascii"), hdr))
            return ("OK", rows)
        return ("NO", [])

    def idle_wait(self, timeout, stop_ev: threading.Event):
        self._state["entered_idle"].set()
        stop_ev.wait(timeout)

    def logout(self):
        return ("BYE", [])


def test_01_mail_resume_preserves_uid_cursor_and_catches_missed_mail(tmp_path):
    """休眠唤醒重建监听线程时保留 UID/UIDVALIDITY 游标，不漏掉休眠期间到达的新邮件。"""
    app = _app()
    cm = Config(tmp_path / "cfg.json")
    creds = _MemCreds()
    mgr = MailAccountManager(config_obj=cm, cred_store=creds, body_cache=LocalMailBodyCache(tmp_path / "cache.json"))
    acc = MailAccount(
        id="acc-resume-1",
        email="a@example.com",
        username="a@example.com",
        host="imap.example.com",
        port=993,
        provider="custom",
        credential_key="k-resume-1",
        enabled=True,
    )
    creds.write_secret(acc.credential_key, "pw")
    mgr._accounts.append(acc)

    events: list = []
    ctrl = SimpleNamespace(
        counts={},
        ingest_event=lambda ev: events.append(ev),
        set_source_unread=lambda *a, **k: None,
        clear_unread=lambda *a, **k: None,
    )

    state = {"uv": "42", "uids": [100], "entered_idle": threading.Event()}
    mon = MailMonitorService(
        mgr,
        controller=ctrl,
        config_obj=cm,
        client_factory=lambda *a, **k: _FakeImapClient(state),
        idle_renew_s=5.0,
    )
    mon.start()
    try:
        assert state["entered_idle"].wait(3.0)
        app.processEvents()
        assert events == []

        # 模拟休眠期间收到新邮件 UID 101，唤醒后触发 apply_config() 重建监听线程
        state["uids"].append(101)
        state["entered_idle"].clear()
        mon.apply_config()
        assert state["entered_idle"].wait(3.0)
        deadline = time.monotonic() + 2.0
        while not events and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)

        assert len(events) == 1
        assert events[0].meta["uid"] == "101"
        assert mon.unread_count_for_account(acc.id) == 1
    finally:
        mon.stop()


def test_02_mail_disabled_or_stale_worker_batch_dropped(tmp_path):
    """停用/删除邮箱或重建旧线程后，已排队的旧邮件批次不得重建未读集合或触发通知横幅。"""
    _app()
    cm = Config(tmp_path / "cfg.json")
    creds = _MemCreds()
    mgr = MailAccountManager(config_obj=cm, cred_store=creds, body_cache=LocalMailBodyCache(tmp_path / "cache.json"))
    acc = MailAccount(
        id="acc-stale-2",
        email="b@example.com",
        username="b@example.com",
        host="imap.example.com",
        port=993,
        provider="custom",
        credential_key="k-stale-2",
        enabled=True,
    )
    creds.write_secret(acc.credential_key, "pw")
    mgr._accounts.append(acc)

    events: list = []
    ctrl = SimpleNamespace(
        counts={},
        ingest_event=lambda ev: events.append(ev),
        set_source_unread=lambda *a, **k: None,
        clear_unread=lambda *a, **k: None,
    )
    mon = MailMonitorService(mgr, controller=ctrl, config_obj=cm)
    mon._running = True

    old_worker = SimpleNamespace(_baseline_uid=10, _uid_validity="42")
    new_worker = SimpleNamespace(_baseline_uid=20, _uid_validity="42")
    mon._workers[acc.id] = new_worker

    hdr = MailHeader(uid="11", account_id=acc.id, sender="x", subject="stale", received_at=1.0, is_read=False, uid_validity="42")
    # 1) 来自旧 worker 的批次应被丢弃
    mon._handle_new_mail_on_main(acc.id, "42", [hdr], old_worker)
    assert events == []
    assert mon.unread_count_for_account(acc.id) == 0

    # 2) 停用/移除账号后，已排队的批次也应被丢弃
    mon._workers.pop(acc.id, None)
    mon._handle_new_mail_on_main(acc.id, "42", [hdr], new_worker)
    assert events == []
    assert mon.unread_count_for_account(acc.id) == 0


class _FakeStreamResp:
    def __init__(self, status_code: int, chunks: list[bytes] | None = None, headers: dict | None = None, exc: Exception | None = None):
        self.status_code = status_code
        self._chunks = chunks or []
        self.headers = headers or {}
        self._exc = exc

    def __enter__(self):
        if self._exc is not None:
            raise self._exc
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_bytes(self, _chunk_size):
        yield from self._chunks


class _FakeHttpClient:
    def __init__(self, handler):
        self._handler = handler
        self.closed = False

    def stream(self, method, url, headers=None):
        return self._handler(method, url, headers or {})

    def close(self):
        self.closed = True


def test_03_download_corrupt_dest_does_not_false_succeed_or_skip_mirror(tmp_path, monkeypatch):
    """损坏的旧模型存在时，首个源失败不得误报成功并跳过备用镜像。"""
    _app()
    monkeypatch.setattr(dl_mod, "MODELS", tmp_path)
    monkeypatch.setattr(dl_mod, "FILES", [("m.onnx", "repo", "m.onnx", 1000)])
    # 预先放置残缺旧模型（仅 100 字节）
    dest = tmp_path / "m.onnx"
    dest.write_bytes(b"x" * 100)

    seen_urls: list[str] = []
    monkeypatch.setattr(dl_mod, "_urls", lambda repo, name: ["https://bad.example/m.onnx", "https://good.example/m.onnx"])

    def _handler(_m, url, _h):
        seen_urls.append(url)
        if "bad.example" in url:
            return _FakeStreamResp(500, exc=ConnectionError("mirror 1 down"))
        return _FakeStreamResp(200, chunks=[b"a" * 1000], headers={"Content-Length": "1000"})

    client = _FakeHttpClient(_handler)
    monkeypatch.setattr(dl_mod, "_clients", lambda: [client])

    dl = dl_mod.ModelDownloader()
    results: list[tuple[bool, str]] = []
    dl.finished.connect(lambda ok, msg: results.append((ok, msg)))
    dl._run()

    assert seen_urls == ["https://bad.example/m.onnx", "https://good.example/m.onnx"]
    assert results == [(True, "")]
    assert dest.stat().st_size == 1000
    assert client.closed is True


def test_04_download_416_verifies_content_range_and_recovers_oversized_part(tmp_path, monkeypatch):
    """HTTP 416 时核对 Content-Range 总长度：过大的损坏 .part 会被删除并重下，合法完整 .part 可直接落盘。"""
    _app()
    dest = tmp_path / "m.onnx"
    part = tmp_path / "m.onnx.part"
    part.write_bytes(b"z" * 2000)  # 比远端 1000 字节还大的损坏分片

    calls: list[dict] = []

    def _handler(_m, _url, headers):
        calls.append(dict(headers))
        if "Range" in headers:
            return _FakeStreamResp(416, headers={"Content-Range": "bytes */1000"})
        return _FakeStreamResp(200, chunks=[b"b" * 1000], headers={"Content-Length": "1000"})

    dl = dl_mod.ModelDownloader()
    dl._get(_FakeHttpClient(_handler), "https://mirror/m.onnx", dest, lambda _n: None, expected_size=1000)
    assert len(calls) == 2
    assert "Range" in calls[0] and "Range" not in calls[1]
    assert dest.read_bytes() == b"b" * 1000
    assert not part.exists()

    # 合法 416（have == remote_total）应直接替换落盘
    dest2 = tmp_path / "m2.onnx"
    part2 = tmp_path / "m2.onnx.part"
    part2.write_bytes(b"c" * 1000)
    dl._get(
        _FakeHttpClient(lambda _m, _u, _h: _FakeStreamResp(416, headers={"Content-Range": "bytes */1000"})),
        "https://mirror/m2.onnx",
        dest2,
        lambda _n: None,
        expected_size=1000,
    )
    assert dest2.read_bytes() == b"c" * 1000


def test_05_download_outer_exception_always_emits_finished(monkeypatch):
    """创建客户端等外层步骤抛异常时，也必须发出 finished(False, ...) 释放界面下载态。"""
    _app()
    monkeypatch.setattr(dl_mod, "missing", lambda: [("m.onnx", "repo", "m.onnx", 1000)])
    monkeypatch.setattr(dl_mod, "_clients", lambda: (_ for _ in ()).throw(PermissionError("denied")))

    dl = dl_mod.ModelDownloader()
    results: list[tuple[bool, str]] = []
    dl.finished.connect(lambda ok, msg: results.append((ok, msg)))
    dl._run()

    assert len(results) == 1
    assert results[0][0] is False
    assert "PermissionError" in results[0][1]


def test_06_reset_defaults_aborts_when_backup_fails(tmp_path, monkeypatch):
    """恢复默认设置时若备份 config.backup.json 失败，必须保留原草稿并弹出失败提示。"""
    _app()
    monkeypatch.setattr(data_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(data_mod.ConfirmDialog, "ask", staticmethod(lambda *a, **k: True))
    infos: list[tuple] = []
    monkeypatch.setattr(data_mod.ConfirmDialog, "info", staticmethod(lambda *a, **k: infos.append(a)))

    # 模拟备份目录不可写（把 config.backup.json 建为目录导致 write_text 抛错）
    (tmp_path / "config.backup.json").mkdir()

    draft = SimpleNamespace(data={"general": {"theme": "custom_keep"}, "extra_key": 123})
    reloaded = []
    page = data_mod.DataSettingsPage(on_reload_draft=lambda: reloaded.append(True))
    page._draft_ref = draft
    page._reset_defaults()

    assert draft.data["general"]["theme"] == "custom_keep"
    assert reloaded == []
    assert len(infos) == 1
    assert "备份" in infos[0][1]


def test_07_shelf_save_failure_preserves_memory_and_allows_retry(tmp_path, monkeypatch):
    """置物架写盘失败时不得污染内存列表，问题恢复后同一次添加/移除/清空可正常重试。"""
    _app()
    f1 = tmp_path / "demo.txt"
    f1.write_text("hello", encoding="utf-8")
    shelf = ShelfService(path=tmp_path / "shelf.json")
    notices: list[tuple[str, bool]] = []
    shelf.notice.connect(lambda msg, is_err: notices.append((msg, is_err)))

    orig_save = shelf._save_items_locked

    def _boom(_items):
        raise OSError("disk full")

    # 1) 添加时写盘失败 -> 内存保持空，恢复后重试成功
    monkeypatch.setattr(shelf, "_save_items_locked", _boom)
    added, msg = shelf.add_files([f1])
    assert added == 0 and "失败" in msg
    assert shelf.paths() == []
    assert notices[-1][1] is True

    monkeypatch.setattr(shelf, "_save_items_locked", orig_save)
    added2, _ = shelf.add_files([f1])
    assert added2 == 1
    assert len(shelf.paths()) == 1

    # 2) 移除与清空时写盘失败 -> 内存仍保留原记录
    monkeypatch.setattr(shelf, "_save_items_locked", _boom)
    assert shelf.remove_file(str(f1)) is False
    assert len(shelf.paths()) == 1
    shelf.clear()
    assert len(shelf.paths()) == 1

    # 3) 恢复写盘后可正常清空
    monkeypatch.setattr(shelf, "_save_items_locked", orig_save)
    shelf.clear()
    assert shelf.paths() == []
