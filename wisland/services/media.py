# -*- coding: utf-8 -*-
"""媒体会话：Windows GlobalSystemMediaTransportControls（SMTC）。

所有 WinRT 调用都在一个专用 asyncio 线程中执行；信号从该线程发出（Qt 自动排队到主线程）。
"""
from __future__ import annotations

import asyncio
import ctypes
import datetime as _dt
import threading
import time
import traceback

from PySide6 import QtCore, QtGui

from wisland.core.config import cfg
from wisland.services.netease import NeteaseLog

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _Manager,
    )
    from winrt.windows.storage.streams import Buffer as _Buffer, InputStreamOptions as _ISO
except Exception:  # noqa: BLE001
    traceback.print_exc()
    _Manager = None

POLL_S = 0.7
VK_MEDIA_NEXT, VK_MEDIA_PREV, VK_MEDIA_PLAY_PAUSE = 0xB0, 0xB1, 0xB3
_user32 = ctypes.WinDLL("user32")


def media_key(vk: int):
    """模拟键盘媒体键：播放器没有接入系统媒体控件（SMTC）时也能控制大多数播放器。"""
    try:
        _user32.keybd_event(vk, 0, 0x1, 0)          # KEYEVENTF_EXTENDEDKEY
        _user32.keybd_event(vk, 0, 0x1 | 0x2, 0)    # | KEYEVENTF_KEYUP
    except Exception:  # noqa: BLE001
        traceback.print_exc()
_PLAYING = 4  # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING

_FRIENDLY = [
    ("cloudmusic", "网易云音乐"),
    ("bilibili", "哔哩哔哩"),
    ("chrome", "Chrome"),
    ("zen", "Zen"),
    ("msedge", "Edge"),
    ("firefox", "Firefox"),
    ("spotify", "Spotify"),
    ("qqmusic", "QQ音乐"),
]


def friendly_name(app_id: str) -> str:
    low = (app_id or "").lower()
    for key, name in _FRIENDLY:
        if key in low:
            return name
    s = app_id or ""
    for sep in ("!", "\\", "/"):
        s = s.rsplit(sep, 1)[-1]
    if s.lower().endswith(".exe"):
        s = s[:-4]
    return s or "媒体"


def _secs(v) -> float:
    """TimeSpan -> 秒（pywinrt 映射为 datetime.timedelta；兼容 int ticks）。"""
    if v is None:
        return 0.0
    if isinstance(v, _dt.timedelta):
        return v.total_seconds()
    try:
        return float(v) / 1e7
    except Exception:  # noqa: BLE001
        return 0.0


_COVER_HEADERS = {
    "Referer": "https://music.163.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}


def _download_cover(url: str):
    """下载专辑封面（小图）-> QImage | None。"""
    if not url:
        return None
    try:
        import httpx
        candidates = [url.replace("http://", "https://", 1)]
        if url.startswith("http://"):
            candidates.append(url)
        for base in candidates:
            sep = "&" if "?" in base else "?"
            try:
                r = httpx.get(
                    f"{base}{sep}param=240y240",
                    headers=_COVER_HEADERS,
                    timeout=8,
                    follow_redirects=True,
                )
                r.raise_for_status()
                img = QtGui.QImage.fromData(r.content)
                if not img.isNull():
                    return img
            except Exception:  # noqa: BLE001
                continue
        return None
    except Exception:  # noqa: BLE001
        return None


def _search_cover_url(title: str, artist: str, duration: float) -> str:
    """SMTC 无缩略图时，按歌名 + 歌手 + 时长向网易云公开接口兜底查询封面 URL。"""
    import re
    if not title or not (30 <= float(duration or 0) <= 15 * 60):
        return ""
    try:
        import httpx
        clean_title = re.sub(r"\s*[\(（\[【].*?[\)）\]】]\s*", " ", title).strip() or title
        q = f"{clean_title} {artist}".strip()
        r = httpx.post(
            "https://music.163.com/api/search/get",
            data={"s": q, "type": 1, "limit": 6, "offset": 0},
            headers=_COVER_HEADERS,
            timeout=6,
        )
        r.raise_for_status()
        j = r.json()
        res = j.get("result") if isinstance(j, dict) else None
        songs = res.get("songs") if isinstance(res, dict) else []
        if not songs:
            return ""
        name = clean_title.lower()

        def score(s):
            same = (s.get("name") or "").lower().strip() == name
            dd = abs((s.get("duration") or 0) / 1000.0 - duration) if duration else 0
            return (0 if same else 1, dd)

        best = min(songs, key=score)
        if duration and abs((best.get("duration") or 0) / 1000.0 - duration) > 8:
            return ""
        album = best.get("album") or {}
        pic = album.get("picUrl") or ""
        if not pic:
            # 某些搜索结果仅在 song.album.id 或 artists[0].img1v1Url 有图，优先取 album.picUrl
            artists = best.get("artists") or []
            if artists and isinstance(artists[0], dict):
                pic = artists[0].get("picUrl") or ""
        return str(pic)
    except Exception:  # noqa: BLE001
        return ""


def _empty_state() -> dict:
    return {
        "has_session": False, "app_id": "", "app_name": "", "title": "", "artist": "",
        "album": "", "playing": False, "position": 0.0, "duration": 0.0,
        "stamp": time.monotonic(), "can_prev": False, "can_next": False, "song_id": "", "source": "",
    }


class MediaService(QtCore.QObject):
    changed = QtCore.Signal(dict)
    cover_changed = QtCore.Signal(object)   # QtGui.QImage | None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state: dict = _empty_state()
        self.cover_image: QtGui.QImage | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._mgr = None
        self._session = None
        self._track_key = None
        self._cover_tries = 0
        self._cover_hash = ""
        self._prev_cover_hash = ""
        self._stale_cover_img = None
        self._netease_cover_url = ""
        self._smtc_fallback_tried = False
        self._last_emit = None
        self._netease = NeteaseLog()

    def _emit_cover(self, img: QtGui.QImage | None):
        self.cover_image = img
        self.cover_changed.emit(img)

    # ---------------- 生命周期 ----------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        if _Manager is None:
            self.changed.emit(dict(self.state))
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="wisland-media", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(lambda: None)
            except RuntimeError:
                pass
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None

    def _run(self):
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        finally:
            self._loop = None
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass

    async def _main(self):
        while not self._stop.is_set():
            try:
                if self._mgr is None:
                    self._mgr = await _Manager.request_async()
                await self._poll()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                self._mgr = None
                self._session = None
            # 分段睡眠以便快速退出
            end = time.monotonic() + POLL_S
            while not self._stop.is_set() and time.monotonic() < end:
                await asyncio.sleep(0.1)

    # ---------------- 轮询 ----------------
    def _choose(self, sessions):
        prio = [str(p).lower() for p in (cfg.get("media.priority") or [])]

        def rank(s):
            aid = (s.source_app_user_model_id or "").lower()
            for i, p in enumerate(prio):
                if p in aid:
                    return i
            return len(prio)

        cands = []
        for s in sessions:
            try:
                info = s.get_playback_info()
                playing = info is not None and int(info.playback_status) == _PLAYING
            except Exception:  # noqa: BLE001
                playing = False
            cands.append((0 if playing else 1, rank(s), s))
        if not cands:
            return None
        cands.sort(key=lambda t: (t[0], t[1]))
        return cands[0][2]

    async def _poll(self):
        sessions = list(self._mgr.get_sessions() or [])
        s = self._choose(sessions)
        # 网易云默认不接入系统媒体控件（即使接入也没有进度），优先用它的本地日志
        ne = None
        if cfg.get("media.netease_log", True):
            try:
                self._netease.poll()
                ne = self._netease.snapshot()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        if ne is not None:
            aid = (s.source_app_user_model_id or "").lower() if s is not None else ""
            smtc_playing = False
            if s is not None:
                try:
                    info = s.get_playback_info()
                    smtc_playing = info is not None and int(info.playback_status) == _PLAYING
                except Exception:  # noqa: BLE001
                    pass
            if s is None or "cloudmusic" in aid or (ne["playing"] and not smtc_playing):
                self._session = s if s is not None and "cloudmusic" in aid else None
                await self._publish_netease(ne)
                return
        self._session = s
        if s is None:
            st = _empty_state()
            if self.state.get("has_session") or self._last_emit is None:
                self._track_key = None
                self._cover_tries = 0
                self._cover_hash = ""
                self._prev_cover_hash = ""
                self._stale_cover_img = None
                self._netease_cover_url = ""
                self._smtc_fallback_tried = False
                self._emit_cover(None)
                self._publish(st, force=True)
            return

        app_id = s.source_app_user_model_id or ""
        try:
            props = await s.try_get_media_properties_async()
        except Exception:  # noqa: BLE001
            props = None
        title = (props.title if props else "") or ""
        artist = (props.artist if props else "") or ""
        album = (props.album_title if props else "") or ""

        info = s.get_playback_info()
        playing = bool(info and int(info.playback_status) == _PLAYING)
        can_prev = can_next = False
        if info and info.controls:
            can_prev = bool(info.controls.is_previous_enabled)
            can_next = bool(info.controls.is_next_enabled)

        pos = dur = 0.0
        try:
            tl = s.get_timeline_properties()
            if tl:
                start = _secs(tl.start_time)
                dur = max(0.0, _secs(tl.end_time) - start)
                pos = max(0.0, _secs(tl.position) - start)
                # 按 last_updated_time 外推到“现在”
                if playing:
                    lut = tl.last_updated_time
                    if isinstance(lut, _dt.datetime) and lut.year > 1601:
                        now = _dt.datetime.now(_dt.timezone.utc)
                        if lut.tzinfo is None:
                            lut = lut.replace(tzinfo=_dt.timezone.utc)
                        age = (now - lut).total_seconds()
                        if 0 < age < 6 * 3600:
                            pos += age
                if dur > 0:
                    pos = min(pos, dur)
        except Exception:  # noqa: BLE001
            pass

        st = {
            "has_session": True, "app_id": app_id, "app_name": friendly_name(app_id),
            "title": title, "artist": artist, "album": album, "playing": playing,
            "position": pos, "duration": dur, "stamp": time.monotonic(),
            "can_prev": can_prev, "can_next": can_next, "song_id": "", "source": "smtc",
        }

        key = (app_id, title, artist, album)
        if key != self._track_key:
            self._track_key = key
            self._prev_cover_hash = self._cover_hash
            self._cover_hash = ""
            self._stale_cover_img = None
            self._cover_tries = 1
            self._smtc_fallback_tried = False
            img, h = await self._read_cover(props)
            if img is not None and h and h == self._prev_cover_hash:
                # 切歌瞬间 SMTC 常残留上一首的旧封面：先清空避免错配，若 3 次轮询后仍未变（同专辑）再恢复
                self._stale_cover_img = img
                self._emit_cover(None)
            else:
                self._cover_hash = h
                self._emit_cover(img)
        elif 0 < self._cover_tries < 15:
            # 切歌后持续观察 SMTC 缩略图是否更新（兼容 Echo Music / 浏览器延迟写入新封面）
            self._cover_tries += 1
            img, h = await self._read_cover(props)
            if img is not None and h and h != self._cover_hash and h != self._prev_cover_hash:
                self._cover_hash = h
                self._stale_cover_img = None
                self._emit_cover(img)
            elif self.cover_image is None:
                if self._stale_cover_img is not None and self._cover_tries >= 4:
                    # 连续多次哈希未变，大概率是同专辑共用封面，恢复显示并继续观察
                    self._cover_hash = self._prev_cover_hash
                    self._emit_cover(self._stale_cover_img)
                    self._stale_cover_img = None
                elif self._cover_tries == 4 and not self._smtc_fallback_tried and title:
                    # 若 SMTC 连续多次仍无缩略图，尝试通过在线曲库兜底获取封面
                    self._smtc_fallback_tried = True
                    loop = asyncio.get_running_loop()
                    pic_url = await loop.run_in_executor(None, _search_cover_url, title, artist, dur)
                    if pic_url:
                        fb_img = await loop.run_in_executor(None, _download_cover, pic_url)
                        if fb_img is not None:
                            self._emit_cover(fb_img)
        self._publish(st)

    async def _publish_netease(self, ne: dict):
        st = {
            "has_session": True, "app_id": "cloudmusic.exe", "app_name": "网易云音乐",
            "title": ne["title"], "artist": ne["artist"], "album": ne["album"], "playing": ne["playing"],
            "position": ne["position"], "duration": ne["duration"], "stamp": ne["stamp"],
            "can_prev": True, "can_next": True, "song_id": ne["song_id"], "source": "netease",
        }
        key = ("netease", ne["song_id"])
        cover_url = str(ne.get("cover") or "")
        is_new_track = key != self._track_key
        if is_new_track:
            self._track_key = key
            self._cover_tries = 0
            self._netease_cover_url = ""
            self._emit_cover(None)

        need_fetch = bool(cover_url) and (
            is_new_track
            or cover_url != self._netease_cover_url
            or (self.cover_image is None and 0 < self._cover_tries < 6)
        )
        if need_fetch:
            self._netease_cover_url = cover_url
            img = await asyncio.get_running_loop().run_in_executor(None, _download_cover, cover_url)
            if img is not None:
                self._cover_tries = 0
                self._emit_cover(img)
            else:
                self._cover_tries = max(1, self._cover_tries + 1)
        self._publish(st)

    def _publish(self, st: dict, force: bool = False):
        cmp = {k: v for k, v in st.items() if k not in ("stamp", "position")}
        prev = self.state
        drift = False
        if prev.get("has_session") and st.get("has_session"):
            # 位置与外推值差太多（拖动进度、暂停时）才视为变化
            expect = prev.get("position", 0.0)
            if prev.get("playing"):
                expect += st["stamp"] - prev.get("stamp", st["stamp"])
            drift = abs(expect - st["position"]) > 0.6
        self.state = st
        if force or drift or cmp != self._last_emit:
            self._last_emit = cmp
            self.changed.emit(dict(st))

    async def _read_cover(self, props) -> tuple[QtGui.QImage | None, str]:
        import hashlib
        stream = None
        try:
            thumb = props.thumbnail if props else None
            if thumb is None:
                return None, ""
            stream = await thumb.open_read_async()
            size = int(stream.size)
            if size < 0 or size > 20 * 1024 * 1024:
                return None, ""
            # IRandomAccessStream.read_async 不保证单次读满，循环读取至结束或达到 size
            chunks = []
            total = 0
            chunk_cap = 64 * 1024 if size == 0 else min(size, 512 * 1024)
            limit = size if size > 0 else 20 * 1024 * 1024
            while total < limit:
                want = chunk_cap if size == 0 else min(chunk_cap, size - total)
                buf = _Buffer(want)
                out = await stream.read_async(buf, want, _ISO.READ_AHEAD)
                got = int(out.length)
                if got <= 0:
                    break
                chunks.append(bytes(memoryview(out))[:got])
                total += got
            if not chunks:
                return None, ""
            raw = b"".join(chunks)
            img = QtGui.QImage.fromData(raw)
            if img.isNull():
                return None, ""
            return img, hashlib.md5(raw).hexdigest()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return None, ""
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass

    # ---------------- 控制 ----------------
    def _submit(self, make_coro, fallback_vk: int | None = None):
        """在媒体线程里对当前会话执行操作；没有会话 / 会话拒绝时改发键盘媒体键。"""
        loop = self._loop
        if loop is None or self._session is None:
            if fallback_vk is not None:
                media_key(fallback_vk)
            return

        async def wrapper():
            ok = False
            try:
                s = self._session
                if s is not None:
                    ok = bool(await make_coro(s))
                    await asyncio.sleep(0.15)
                    await self._poll()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            if not ok and fallback_vk is not None:
                media_key(fallback_vk)

        try:
            asyncio.run_coroutine_threadsafe(wrapper(), loop)
        except RuntimeError:
            if fallback_vk is not None:
                media_key(fallback_vk)

    def play_pause(self):
        self._submit(lambda s: s.try_toggle_play_pause_async(), VK_MEDIA_PLAY_PAUSE)

    def next(self):
        self._submit(lambda s: s.try_skip_next_async(), VK_MEDIA_NEXT)

    def prev(self):
        self._submit(lambda s: s.try_skip_previous_async(), VK_MEDIA_PREV)

    def seek(self, seconds: float):
        def go(s):
            start = 0.0
            try:
                start = _secs(s.get_timeline_properties().start_time)
            except Exception:  # noqa: BLE001
                pass
            return s.try_change_playback_position_async(int((max(0.0, seconds) + start) * 1e7))
        self._submit(go)
