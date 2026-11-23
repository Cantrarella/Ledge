# -*- coding: utf-8 -*-
"""MediaService SMTC 封面去滞留、同专辑判定与 Echo Music 精确封面优先回归测试。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from PySide6 import QtCore, QtGui, QtWidgets

import wisland.services.media as media_mod
from wisland.services.media import MediaService


def _mk_img(color: str) -> QtGui.QImage:
    img = QtGui.QImage(16, 16, QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtGui.QColor(color))
    return img


class _FakeSession:
    def __init__(self, app_id: str, title: str, artist: str, album: str):
        self.source_app_user_model_id = app_id
        self._props = SimpleNamespace(title=title, artist=artist, album_title=album, thumbnail=object())

    def get_playback_info(self):
        return SimpleNamespace(
            playback_status=4,
            controls=SimpleNamespace(is_previous_enabled=True, is_next_enabled=True),
        )

    def get_timeline_properties(self):
        return None

    async def try_get_media_properties_async(self):
        return self._props


class _FakeManager:
    def __init__(self, session: _FakeSession):
        self.session = session

    def get_sessions(self):
        return [self.session]


def _setup_service(monkeypatch, session: _FakeSession, read_cover_fn):
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    svc = MediaService()
    svc._mgr = _FakeManager(session)
    # 屏蔽本机网易云日志，保证走 SMTC 分支
    monkeypatch.setattr(svc._netease, "poll", lambda: None)
    monkeypatch.setattr(svc._netease, "snapshot", lambda: None)
    monkeypatch.setattr(svc, "_read_cover", read_cover_fn)
    return svc


def test_echo_music_stale_smtc_cover_uses_echo_cover_immediately(monkeypatch):
    """Echo Music 切到不同专辑且 SMTC 残留上一首封面时，立即拉取 echo_cover，不显示旧图。"""
    img_a = _mk_img("#ff0000")
    img_b_echo = _mk_img("#00ff00")
    cur_thumb = {"img": img_a, "hash": "hash_a"}

    async def fake_read_cover(_props):
        return cur_thumb["img"], cur_thumb["hash"]

    sess = _FakeSession("EchoMusic.exe", "定玄", "黄霄雲", "定玄")
    svc = _setup_service(monkeypatch, sess, fake_read_cover)

    monkeypatch.setattr(
        media_mod,
        "lookup_echo_track",
        lambda title, artist="": (
            {"kugou_hash": "h2", "cover": "https://img.example/replay.jpg"}
            if "Replay" in title
            else {"kugou_hash": "h1", "cover": "https://img.example/dingxuan.jpg"}
        ),
    )
    monkeypatch.setattr(
        media_mod,
        "_download_cover",
        lambda url: img_b_echo if "replay.jpg" in url else img_a,
    )

    # 第一首：定玄（拿到 hash_a）
    asyncio.run(svc._poll())
    assert svc.cover_image is img_a

    # 切到第二首：Replay (重映)，不同专辑，SMTC 仍残留 hash_a
    sess._props = SimpleNamespace(title="Replay (重映)", artist="鸣潮先约电台", album_title="Replay (重映)", thumbnail=object())
    asyncio.run(svc._poll())
    assert svc.cover_image is img_b_echo


def test_different_album_never_restores_stale_cover_after_4_polls(monkeypatch):
    """非 Echo 播放器切到不同专辑且 SMTC 持续残留旧图时，满 4 次轮询绝不恢复旧图，改走在线兜底。"""
    img_a = _mk_img("#ff0000")
    img_b_fb = _mk_img("#0000ff")

    async def fake_read_cover(_props):
        return img_a, "hash_a"

    sess = _FakeSession("msedge.exe", "Song A", "Artist A", "Album A")
    svc = _setup_service(monkeypatch, sess, fake_read_cover)
    monkeypatch.setattr(media_mod, "_search_cover_url", lambda t, a, d: "https://img.example/b.jpg")
    monkeypatch.setattr(media_mod, "_download_cover", lambda url: img_b_fb)

    asyncio.run(svc._poll())
    assert svc.cover_image is img_a

    # 切到不同专辑，SMTC 始终残留 hash_a
    sess._props = SimpleNamespace(title="Song B", artist="Artist B", album_title="Album B", thumbnail=object())
    for _ in range(5):
        asyncio.run(svc._poll())
        assert svc.cover_image is not img_a

    # 第 4 次轮询后应已通过在线兜底拿到新图
    assert svc.cover_image is img_b_fb


def test_same_album_restores_shared_cover_after_4_polls(monkeypatch):
    """同专辑连续切歌且 SMTC 缩略图哈希不变时，满 4 次轮询后正常恢复同专辑共用封面。"""
    img_album = _mk_img("#abcdef")

    async def fake_read_cover(_props):
        return img_album, "hash_shared"

    sess = _FakeSession("msedge.exe", "Track 1", "Artist", "Shared Album")
    svc = _setup_service(monkeypatch, sess, fake_read_cover)

    asyncio.run(svc._poll())
    assert svc.cover_image is img_album

    sess._props = SimpleNamespace(title="Track 2", artist="Artist", album_title="Shared Album", thumbnail=object())
    asyncio.run(svc._poll())
    assert svc.cover_image is None  # 切歌瞬间先清空防误判
    for _ in range(3):
        asyncio.run(svc._poll())
    assert svc.cover_image is img_album


def test_rapid_skip_preserves_prev_cover_hash(monkeypatch):
    """连续快切（中间曲目尚未拿到新封面）时，_prev_cover_hash 不被置空，第三首仍能拦截旧图。"""
    img_a = _mk_img("#123456")

    async def fake_read_cover(_props):
        return img_a, "hash_a"

    sess = _FakeSession("msedge.exe", "Song A", "Artist", "Album A")
    svc = _setup_service(monkeypatch, sess, fake_read_cover)
    monkeypatch.setattr(media_mod, "_search_cover_url", lambda t, a, d: "")

    asyncio.run(svc._poll())
    assert svc.cover_image is img_a

    # 快切到 Song B（此时 SMTC 仍残留 hash_a，cover_image 被清空）
    sess._props = SimpleNamespace(title="Song B", artist="Artist", album_title="Album B", thumbnail=object())
    asyncio.run(svc._poll())
    assert svc.cover_image is None
    assert svc._prev_cover_hash == "hash_a"

    # 立刻再快切到 Song C（不同专辑，SMTC 仍残留 hash_a）：依然必须拦截，不能误显示 img_a
    sess._props = SimpleNamespace(title="Song C", artist="Artist", album_title="Album C", thumbnail=object())
    asyncio.run(svc._poll())
    assert svc.cover_image is None
    assert svc._prev_cover_hash == "hash_a"
