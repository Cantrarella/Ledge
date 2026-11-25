# -*- coding: utf-8 -*-
"""歌词查询防重试缓存/退避与暂停态歌词微光淡出测试。"""
from __future__ import annotations

from PySide6 import QtCore, QtGui

from wisland.services.lyrics import Lyrics, LyricsService
from wisland.ui.compact import CompactView


def test_lyrics_service_caches_60s_miss_ttl_and_backs_off_on_error(monkeypatch):
    svc = LyricsService()
    bg_calls: list[tuple] = []

    monkeypatch.setattr(
        "wisland.services.lyrics.run_bg",
        lambda fn, *args, on_done=None, on_error=None: bg_calls.append((fn, args, on_done, on_error)),
    )

    now = [100.0]
    monkeypatch.setattr("wisland.services.lyrics.time.monotonic", lambda: now[0])

    # 1) 60s 试听曲目无匹配：短期 TTL 内不得重复发起后台请求
    track_60 = {"title": "试听歌", "artist": "歌手", "duration": 60.0}
    assert svc.get(track_60) is None
    assert len(bg_calls) == 1
    _fn, _args, on_done, _on_err = bg_calls[0]
    on_done(None)

    assert svc.get(track_60) is None
    assert len(bg_calls) == 1  # 未再次排队

    # 超过短期 TTL 后允许重新尝试
    now[0] += 35.0
    assert svc.get(track_60) is None
    assert len(bg_calls) == 2

    # 2) 网络错误：进入退避期，退避期内不重查，退避期满后允许重查
    track_err = {"title": "断网歌", "artist": "歌手", "duration": 200.0}
    assert svc.get(track_err) is None
    assert len(bg_calls) == 3
    _fn2, _args2, _on_done2, on_err2 = bg_calls[2]
    on_err2(RuntimeError("network error"))

    assert svc.get(track_err) is None
    assert len(bg_calls) == 3  # 退避期内不再排队

    now[0] += 6.0
    assert svc.get(track_err) is None
    assert len(bg_calls) == 4


def test_compact_karaoke_glow_disabled_when_paused():
    from PySide6 import QtWidgets

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    c = CompactView()
    lyr = Lyrics(
        lines=[(0.0, "hello world")],
        trans=[],
        words=[[(0.0, 2.0, "hello "), (2.0, 4.0, "world")]],
    )
    c.lyrics = lyr
    c.media = {"has_session": True, "title": "Song", "artist": "Artist", "playing": False}
    c.media_pos = lambda: 1.0

    # 暂停态下微光强度必须归零，不在原地定格亮边
    assert c._karaoke_glow_strength(0, 1.0, 0.5) == 0.0

    # 播放态下在同一词内保持正常微光
    c.media["playing"] = True
    assert c._karaoke_glow_strength(0, 1.0, 0.5) > 0.5


def test_lyrics_network_error_backoff_partial_fallback_and_duration_change(monkeypatch, tmp_path):
    import httpx
    import wisland.services.lyrics as lyr_mod

    monkeypatch.setattr(lyr_mod, "_DISK_CACHE_FILE", tmp_path / "lyrics_cache.json")
    now = [200.0]
    monkeypatch.setattr("wisland.services.lyrics.time.monotonic", lambda: now[0])

    svc = LyricsService()
    sample = Lyrics([(0.0, "ok")], [])

    # 1) 部分供应商抛 ConnectError，其余供应商成功 -> 正常返回歌词
    monkeypatch.setattr(svc, "_fetch_kugou", lambda m: (_ for _ in ()).throw(httpx.ConnectError("down")))
    monkeypatch.setattr(svc, "_fetch_qq", lambda m: sample)
    monkeypatch.setattr(svc, "_fetch_lrclib", lambda m: None)
    monkeypatch.setattr(svc, "_fetch_netease", lambda m: None)
    assert svc._fetch({"title": "A", "artist": "B", "duration": 180.0}) is sample

    # 2) 所有供应商都抛网络错误 -> 必须抛出异常进入退避，不得写入空缓存；恢复后可重查
    monkeypatch.setattr(svc, "_fetch_qq", lambda m: (_ for _ in ()).throw(httpx.ConnectError("down")))
    monkeypatch.setattr(svc, "_fetch_lrclib", lambda m: (_ for _ in ()).throw(httpx.ReadTimeout("timeout")))
    monkeypatch.setattr(svc, "_fetch_netease", lambda m: (_ for _ in ()).throw(httpx.ConnectError("down")))

    bg_calls = []
    monkeypatch.setattr(
        "wisland.services.lyrics.run_bg",
        lambda fn, *args, on_done=None, on_error=None: bg_calls.append((fn, args, on_done, on_error)),
    )

    m_net = {"title": "NetFail", "artist": "X", "duration": 210.0}
    assert svc.get(m_net) is None
    assert len(bg_calls) == 1
    fn, args, on_done, on_err = bg_calls[0]
    try:
        res = fn(*args)
    except Exception as exc:
        on_err(exc)
    else:
        on_done(res)

    # 退避期内不重查
    assert svc.get(m_net) is None
    assert len(bg_calls) == 1

    # 退避期满后可重查，且网络恢复后能拿到歌词
    now[0] += 6.0
    assert svc.get(m_net) is None
    assert len(bg_calls) == 2

    # 3) 同一 song_id 从 60s 试听无匹配更新为 240s 完整时长 -> 立即重查一次
    monkeypatch.setattr(svc, "_fetch_netease", lambda m: None)
    monkeypatch.setattr(svc, "_fetch_kugou", lambda m: None)
    monkeypatch.setattr(svc, "_fetch_qq", lambda m: None)
    monkeypatch.setattr(svc, "_fetch_lrclib", lambda m: None)

    m_id_60 = {"song_id": 12345, "title": "Preview", "artist": "Y", "duration": 60.0}
    assert svc.get(m_id_60) is None
    assert len(bg_calls) == 3
    fn3, args3, on_done3, _ = bg_calls[2]
    on_done3(fn3(*args3))

    # 同一时长下被 30s 缓存拦住
    assert svc.get(m_id_60) is None
    assert len(bg_calls) == 3

    # 时长更新为 240s 后立即允许重查一次
    m_id_240 = {"song_id": 12345, "title": "Preview", "artist": "Y", "duration": 240.0}
    assert svc.get(m_id_240) is None
    assert len(bg_calls) == 4


def test_kugou_download_network_error_triggers_backoff(monkeypatch, tmp_path):
    import httpx
    import wisland.services.lyrics as lyr_mod

    monkeypatch.setattr(lyr_mod, "_DISK_CACHE_FILE", tmp_path / "lyrics_cache.json")
    svc = LyricsService()

    class _FakeResp:
        def __init__(self, data: dict):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    class _FakeClient:
        def get(self, url, params=None):
            if "search/song" in url:
                return _FakeResp({"data": {"info": [{"songname": "KgSong", "singername": "KgArtist", "duration": 200, "hash": "h1"}]}})
            if "krcs.kugou.com/search" in url:
                return _FakeResp({"candidates": [{"id": "1", "accesskey": "k", "duration": 200000}]})
            raise httpx.ConnectError("download down")

    svc._local.client = _FakeClient()
    monkeypatch.setattr(svc, "_fetch_qq", lambda m: None)
    monkeypatch.setattr(svc, "_fetch_lrclib", lambda m: None)
    monkeypatch.setattr(svc, "_fetch_netease", lambda m: None)

    try:
        svc._fetch({"title": "KgSong", "artist": "KgArtist", "duration": 200.0})
        assert False, "expected httpx.ConnectError from kugou download"
    except httpx.ConnectError:
        pass


def test_lyrics_backoff_auto_retries_current_track_and_cancels_on_switch(monkeypatch, tmp_path):
    import httpx
    import wisland.services.lyrics as lyr_mod
    from PySide6 import QtWidgets

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(lyr_mod, "_DISK_CACHE_FILE", tmp_path / "lyrics_cache.json")
    now = [100.0]
    monkeypatch.setattr("wisland.services.lyrics.time.monotonic", lambda: now[0])

    svc = LyricsService()
    bg_calls = []
    monkeypatch.setattr(
        "wisland.services.lyrics.run_bg",
        lambda fn, *args, on_done=None, on_error=None: bg_calls.append((fn, args, on_done, on_error)),
    )

    scheduled = []
    monkeypatch.setattr(svc._retry_timer, "start", lambda ms: scheduled.append(ms))

    track_a = {"title": "SongA", "artist": "ArtA", "duration": 200.0}
    track_b = {"title": "SongB", "artist": "ArtB", "duration": 210.0}

    # 1) 首次查询失败并调度单次退避补查
    assert svc.get(track_a) is None
    assert len(bg_calls) == 1
    _fn, _args, _on_done, on_err = bg_calls[0]
    on_err(httpx.ConnectError("offline"))
    assert len(scheduled) == 1 and scheduled[0] >= 5000

    # 2) 退避到期后自动补查同一首歌（无需拖动进度）
    now[0] += scheduled[0] / 1000.0 + 0.1
    svc._on_retry_timeout()
    assert len(bg_calls) == 2

    # 3) 切歌到 track_b 时取消旧定时器；切回仍在退避期的 track_a 时按剩余时间重新安排补查
    assert svc.get(track_b) is None
    assert len(bg_calls) == 3
    assert svc._active_key == svc.key(track_b)

    # 模拟 track_a 仍在退避期，切回 track_a
    _fn3, _args3, _on_done3, on_err3 = bg_calls[1]
    on_err3(httpx.ConnectError("still offline"))  # 触发第 2 次退避
    scheduled.clear()
    assert svc.get(track_b) is None  # 切走
    now[0] += 1.0
    assert svc.get(track_a) is None  # 仍在退避期，切回 track_a
    assert len(scheduled) == 1 and scheduled[0] > 0

    # 4) 切到刚切入且无 ID、时长为 0 的新曲目时，必须取消旧曲目的退避定时器
    cancelled = []
    monkeypatch.setattr(svc._retry_timer, "stop", lambda: cancelled.append(True))
    track_b_init = {"title": "SongB", "artist": "ArtB", "duration": 0.0}
    assert svc.get(track_b_init) is None
    assert len(cancelled) >= 1
    assert svc._active_key is None


def test_lyrics_sqlite_per_track_disk_cache_and_legacy_json_migration(monkeypatch, tmp_path):
    """歌词磁盘缓存改为 SQLite 按曲读取：不在内存常驻 200 首 _disk_cache，首次启动自动迁移旧 lyrics_cache.json 并支持上限淘汰。"""
    import json
    import time
    import wisland.services.lyrics as lyr_mod

    legacy_json = tmp_path / "lyrics_cache.json"
    monkeypatch.setattr(lyr_mod, "_DISK_CACHE_FILE", legacy_json)
    monkeypatch.setattr(lyr_mod, "_DISK_CACHE_MAX", 3)

    now_ts = int(time.time())
    legacy_payload = {
        "id:old_ok": {
            "times": [0.0, 2.5],
            "lines": ["第一句", "第二句"],
            "trans": ["line 1", "line 2"],
            "words": [[(0.0, 1.0, "第一"), (1.0, 2.0, "句")], []],
            "ts": now_ts - 10,
        },
        "id:expired": {
            "times": [0.0],
            "lines": ["过期"],
            "trans": [""],
            "words": [],
            "ts": now_ts - lyr_mod._DISK_CACHE_TTL - 100,
        },
    }
    legacy_json.write_text(json.dumps(legacy_payload, ensure_ascii=False), encoding="utf-8")

    svc = LyricsService()
    assert not hasattr(svc, "_disk_cache")

    # 1) 首次读取触发旧 JSON 迁移至 SQLite 并清理旧 JSON 文件，过期条目被过滤
    migrated = svc._disk_get("id:old_ok")
    assert migrated is not None
    assert migrated.lines == ["第一句", "第二句"]
    assert migrated.words[0][0][2] == "第一"
    assert svc._disk_get("id:expired") is None
    assert not legacy_json.exists()
    assert legacy_json.with_suffix(".sqlite3").is_file()

    # 2) 按曲写入与容量上限淘汰（MAX=3）
    for idx in range(1, 5):
        lyr = Lyrics([(0.0, f"song-{idx}")], [])
        svc._disk_put(f"id:s{idx}", lyr)
    assert svc._disk_get("id:s1") is None
    for idx in (2, 3, 4):
        got = svc._disk_get(f"id:s{idx}")
        assert got is not None and got.lines == [f"song-{idx}"]
