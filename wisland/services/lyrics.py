# -*- coding: utf-8 -*-
"""歌词：从网易云音乐公开接口获取 LRC 歌词（含翻译），按播放进度给出当前行。

- 网易云（读本地日志得到歌曲 id）：直接按 id 取歌词
- 其它播放器（QQ 音乐、Spotify、浏览器等）：按「歌名 + 歌手」搜索，取时长最接近的一首

信号 ready(key)：某首歌的歌词取到了（或确认没有），界面收到后重新查询 line()。
"""
from __future__ import annotations

import base64
import bisect
import html
import re
import threading

import httpx
from PySide6 import QtCore

from wisland.core.bg import run_bg, ui
from wisland.core.config import cfg

API = "https://music.163.com/api"
HEADERS = {"Referer": "https://music.163.com/", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_TS = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_OFFSET = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.IGNORECASE)
# 开头的制作信息（作词 / 作曲 / 编曲 …）不当作歌词显示
_CREDIT = re.compile(r"^\s*[^\s:：]{1,12}\s*[:：]")
_INSTRUMENTAL_PLACEHOLDER = re.compile(r"没有填词的纯音乐|纯音乐[，,\s]*请欣赏|no\s+lyrics", re.IGNORECASE)
_VERSION_TAG = re.compile(r"\b(live|remix|dj|instrumental|karaoke)\b|伴奏|纯音乐|现场|翻唱", re.IGNORECASE)


def parse_lrc(text: str) -> list[tuple[float, str]]:
    offset_s = 0.0
    m_off = _OFFSET.search(text or "")
    if m_off:
        try:
            offset_s = int(m_off.group(1)) / 1000.0
        except ValueError:
            offset_s = 0.0
    out = []
    for raw in (text or "").splitlines():
        stamps = list(_TS.finditer(raw))
        if not stamps:
            continue
        body = raw[stamps[-1].end():].strip()
        for m in stamps:
            frac = m.group(3) or "0"
            t = int(m.group(1)) * 60 + int(m.group(2)) + int(frac) / (10 ** len(frac))
            out.append((max(0.0, t - offset_s), body))
    out.sort(key=lambda x: x[0])
    # 去掉开头连续的制作信息行
    i = 0
    while i < len(out) and (not out[i][1] or (out[i][0] < 40 and _CREDIT.match(out[i][1]))):
        i += 1
    cleaned = out[i:]
    # 若整首只剩“纯音乐，请欣赏”之类占位提示，视为无歌词（让收起态胶囊按歌名自适应收缩）
    non_empty = [s for _, s in cleaned if s]
    if non_empty and all(_INSTRUMENTAL_PLACEHOLDER.search(s) for s in non_empty):
        return []
    return cleaned


class Lyrics:
    def __init__(self, lines: list[tuple[float, str]], trans: list[tuple[float, str]]):
        self.times = [t for t, _ in lines]
        self.lines = [s for _, s in lines]
        tmap = {round(t, 1): s for t, s in trans if s}
        self.trans = [tmap.get(round(t, 1), "") for t in self.times]

    def __bool__(self):
        return bool(self.lines)

    def at(self, pos: float) -> tuple[int, str, str]:
        """-> (行号, 歌词, 翻译)；还没到第一行时行号为 -1。"""
        i = bisect.bisect_right(self.times, pos + 0.15) - 1
        if i < 0:
            return -1, "", ""
        return i, self.lines[i], self.trans[i]


class LyricsService(QtCore.QObject):
    ready = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        ui(lambda: None)
        self._local = threading.local()
        self._cache: dict[str, Lyrics | None] = {}
        self._pending: set[str] = set()
        self._lock = threading.Lock()

    @property
    def _client(self) -> httpx.Client:
        cli = getattr(self._local, "client", None)
        if cli is None:
            cli = httpx.Client(timeout=httpx.Timeout(8.0, connect=4.0), headers=HEADERS)
            self._local.client = cli
        return cli

    @staticmethod
    def key(m: dict) -> str:
        if m.get("song_id"):
            return "id:" + str(m["song_id"])
        if m.get("kugou_hash"):
            return "kg:" + str(m["kugou_hash"])
        return "q:" + (m.get("title") or "") + "|" + (m.get("artist") or "")

    def get(self, m: dict) -> Lyrics | None:
        """返回已缓存的歌词；没有时在后台获取，取到后发 ready(key)。"""
        if not cfg.get("media.lyrics", True) or not m.get("title"):
            return None
        if not m.get("song_id"):
            dur = float(m.get("duration") or 0)
            if not (30 <= dur <= 15 * 60):
                # SMTC 切歌初期 duration 常暂时为 0，等待下次轮询拿到有效时长后再查，避免把 None 写死进缓存
                return None
        k = self.key(m)
        with self._lock:
            if k in self._cache:
                return self._cache[k]
            if k in self._pending:
                return None
            self._pending.add(k)
        run_bg(self._fetch, dict(m), on_done=lambda res, k=k: self._done(k, res, True),
               on_error=lambda e, k=k: self._done(k, None, False))
        return None

    def _done(self, k: str, res, cache_none: bool = True):
        with self._lock:
            self._pending.discard(k)
            if res is not None or cache_none:
                self._cache[k] = res
                if len(self._cache) > 200:
                    self._cache.pop(next(iter(self._cache)))
        self.ready.emit(k)

    # ---------------- 网络 ----------------
    def _fetch(self, m: dict) -> Lyrics | None:
        if m.get("kugou_hash"):
            lyr = self._fetch_kugou_by_hash(
                str(m["kugou_hash"]),
                float(m.get("duration") or 0),
                f"{self._clean_title(m.get('title') or '')} {self._clean_artist(m.get('artist') or '')}".strip(),
            )
            if lyr:
                return lyr
        if m.get("song_id"):
            order = (self._fetch_netease, self._fetch_qq, self._fetch_kugou, self._fetch_lrclib)
        else:
            order = (self._fetch_qq, self._fetch_kugou, self._fetch_lrclib, self._fetch_netease)
        for fn in order:
            lyr = fn(m)
            if lyr:
                return lyr
        return None

    def _fetch_kugou_by_hash(self, kg_hash: str, dur: float, keyword: str = "") -> Lyrics | None:
        """凭 Echo Music 提供的精确酷狗音频 hash 直接拉取对应歌词。"""
        if not kg_hash:
            return None
        try:
            params = {
                "ver": 1,
                "man": "yes",
                "client": "mobi",
                "hash": kg_hash,
            }
            if dur > 0:
                params["duration"] = int(dur * 1000)
            if keyword:
                params["keyword"] = keyword
            rk = self._client.get("http://krcs.kugou.com/search", params=params)
            rk.raise_for_status()
            cands = rk.json().get("candidates") or []
            if not cands:
                return None
            cand = cands[0]
            rd = self._client.get(
                "http://lyrics.kugou.com/download",
                params={
                    "ver": 1,
                    "client": "pc",
                    "id": cand["id"],
                    "accesskey": cand["accesskey"],
                    "fmt": "lrc",
                    "charset": "utf8",
                },
            )
            rd.raise_for_status()
            b64 = rd.json().get("content") or ""
            if not b64:
                return None
            text = base64.b64decode(b64).decode("utf-8", "replace")
            lines = parse_lrc(text)
            lyr = Lyrics(lines, [])
            return lyr if lyr else None
        except Exception:  # noqa: BLE001
            return None

    def _clean_title(self, raw: str) -> str:
        return re.sub(r"\s*[\(（\[【].*?[\)）\]】]\s*", " ", raw or "").strip() or (raw or "")

    def _clean_artist(self, raw: str) -> str:
        return re.sub(r"\s*[/、,;，；]+\s*", " ", raw or "").strip()

    def _norm_text(self, s: str) -> str:
        return re.sub(r"[\s\-_·.,，。!！?？'\"`~()（）\[\]【】]+", "", self._clean_title(s)).lower()

    def _is_valid_match(
        self,
        cand_title: str,
        cand_artist: str,
        cand_dur: float,
        q_title: str,
        q_artist: str,
        q_dur: float,
    ) -> bool:
        ct_raw = self._clean_title(cand_title).lower()
        qt_raw = self._clean_title(q_title).lower()
        ct = self._norm_text(cand_title)
        qt = self._norm_text(q_title)
        if not ct or not qt:
            return False
        exact_title = (ct == qt) or (ct_raw == qt_raw)
        sub_title = (qt in ct) or (ct in qt)
        if not exact_title and not sub_title:
            return False

        ca = cand_artist.lower()
        qa_tokens = [t for t in self._clean_artist(q_artist).lower().split() if len(t) >= 2]
        artist_ok = (not qa_tokens) or any(t in ca for t in qa_tokens)
        dur_diff = abs(cand_dur - q_dur) if (q_dur and cand_dur) else 0.0
        if not exact_title and not artist_ok:
            # 歌名为包含关系（如带副标题/OST后缀）且艺人中英文不同时，仅在时长高度吻合（<=2.5s）时放行
            if not (min(len(ct), len(qt)) >= 2 and q_dur and cand_dur and dur_diff <= 2.5):
                return False

        if q_dur and cand_dur:
            max_dd = 8.0 if exact_title else 5.0
            if dur_diff > max_dd:
                return False
        return True

    def _score_match(
        self,
        cand_title: str,
        cand_artist: str,
        cand_dur: float,
        q_title: str,
        q_artist: str,
        q_dur: float,
    ) -> tuple:
        ct = self._clean_title(cand_title).lower()
        qt = q_title.lower()
        title_rank = 0 if ct == qt else (1 if qt in ct or ct in qt else 2)

        ca = cand_artist.lower()
        qa_tokens = [t for t in self._clean_artist(q_artist).lower().split() if t]
        artist_rank = 0 if (not qa_tokens or any(t in ca for t in qa_tokens)) else 1

        # 原曲未带 Live/伴奏/Remix 等标记时，降权这类变体版本（避免时间轴错位）
        q_has_ver = bool(_VERSION_TAG.search(q_title))
        c_has_ver = bool(_VERSION_TAG.search(cand_title))
        ver_rank = 1 if (c_has_ver and not q_has_ver) else 0

        dd = abs(cand_dur - q_dur) if (q_dur and cand_dur) else 0.0
        return (ver_rank, title_rank, artist_rank, round(dd, 1))

    def _fetch_netease(self, m: dict) -> Lyrics | None:
        try:
            sid = str(m.get("song_id") or "") or self._search_netease(m)
            if not sid:
                return None
            r = self._client.get(API + "/song/lyric", params={"id": sid, "lv": 1, "tv": -1})
            r.raise_for_status()
            j = r.json()
            if not isinstance(j, dict) or j.get("nolyric") or j.get("uncollected"):
                return None
            lines = parse_lrc((j.get("lrc") or {}).get("lyric", ""))
            trans = parse_lrc((j.get("tlyric") or {}).get("lyric", ""))
            lyr = Lyrics(lines, trans)
            return lyr if lyr else None
        except Exception:  # noqa: BLE001
            return None

    def _search_netease(self, m: dict) -> str:
        title = self._clean_title(m.get("title") or "")
        artist = self._clean_artist(m.get("artist") or "")
        q = f"{title} {artist}".strip()
        if not q:
            return ""
        dur = float(m.get("duration") or 0)
        if not (30 <= dur <= 15 * 60):
            return ""            # 不知道时长 / 时长不像一首歌（视频、番剧等）：不按歌名猜歌词
        r = self._client.post(
            API + "/search/get",
            data={"s": q, "type": 1, "limit": 8, "offset": 0},
        )
        r.raise_for_status()
        j = r.json()
        res = j.get("result") if isinstance(j, dict) else None
        if not isinstance(res, dict):
            return ""
        songs = res.get("songs") or []
        if not songs:
            return ""

        def _artists_str(s):
            return " ".join(str(a.get("name") or "") for a in (s.get("artists") or []) if isinstance(a, dict))

        best = min(
            songs,
            key=lambda s: self._score_match(
                s.get("name") or "", _artists_str(s), (s.get("duration") or 0) / 1000.0,
                title, artist, dur,
            ),
        )
        if not self._is_valid_match(
            best.get("name") or "", _artists_str(best), (best.get("duration") or 0) / 1000.0,
            title, artist, dur,
        ):
            return ""
        return str(best.get("id") or "")

    def _fetch_qq(self, m: dict) -> Lyrics | None:
        """QQ 音乐公开接口兜底（华语与腾讯系版权覆盖最全）。"""
        dur = float(m.get("duration") or 0)
        if not (30 <= dur <= 15 * 60):
            return None
        title = self._clean_title(m.get("title") or "")
        artist = self._clean_artist(m.get("artist") or "")
        q = f"{title} {artist}".strip()
        if not q:
            return None
        try:
            r = self._client.get(
                "https://c.y.qq.com/soso/fcgi-bin/client_search_cp",
                params={"w": q, "format": "json", "n": 8, "p": 1},
                headers={"Referer": "https://y.qq.com/"},
            )
            r.raise_for_status()
            songs = (((r.json().get("data") or {}).get("song") or {}).get("list")) or []
            if not songs:
                return None

            def _artists_str(s):
                return " ".join(str(a.get("name") or "") for a in (s.get("singer") or []) if isinstance(a, dict))

            best = min(
                songs,
                key=lambda s: self._score_match(
                    s.get("songname") or "", _artists_str(s), float(s.get("interval") or 0),
                    title, artist, dur,
                ),
            )
            if not self._is_valid_match(
                best.get("songname") or "", _artists_str(best), float(best.get("interval") or 0),
                title, artist, dur,
            ):
                return None
            mid = best.get("songmid") or ""
            if not mid:
                return None
            rl = self._client.get(
                "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg",
                params={"songmid": mid, "format": "json", "nobase64": 1},
                headers={"Referer": "https://y.qq.com/"},
            )
            rl.raise_for_status()
            lrc_txt = html.unescape(rl.json().get("lyric") or "")
            if not lrc_txt:
                return None
            lines = parse_lrc(lrc_txt)
            lyr = Lyrics(lines, [])
            return lyr if lyr else None
        except Exception:  # noqa: BLE001
            return None

    def _fetch_kugou(self, m: dict) -> Lyrics | None:
        """酷狗音乐公开接口兜底（覆盖网易云/QQ无版权曲库）。"""
        dur = float(m.get("duration") or 0)
        if not (30 <= dur <= 15 * 60):
            return None
        title = self._clean_title(m.get("title") or "")
        artist = self._clean_artist(m.get("artist") or "")
        q = f"{title} {artist}".strip()
        if not q:
            return None
        try:
            r = self._client.get(
                "http://mobilecdn.kugou.com/api/v3/search/song",
                params={"format": "json", "keyword": q, "page": 1, "pagesize": 8},
            )
            r.raise_for_status()
            info = ((r.json().get("data") or {}).get("info")) or []
            if not info:
                return None

            best = min(
                info,
                key=lambda s: self._score_match(
                    s.get("songname") or "", s.get("singername") or "", float(s.get("duration") or 0),
                    title, artist, dur,
                ),
            )
            if not self._is_valid_match(
                best.get("songname") or "", best.get("singername") or "", float(best.get("duration") or 0),
                title, artist, dur,
            ):
                return None
            rk = self._client.get(
                "http://krcs.kugou.com/search",
                params={
                    "ver": 1,
                    "man": "yes",
                    "client": "mobi",
                    "keyword": q,
                    "duration": int(float(best.get("duration") or dur) * 1000),
                    "hash": best.get("hash") or "",
                },
            )
            rk.raise_for_status()
            cands = rk.json().get("candidates") or []
            if not cands:
                return None
            cand = cands[0]
            rd = self._client.get(
                "http://lyrics.kugou.com/download",
                params={
                    "ver": 1,
                    "client": "pc",
                    "id": cand["id"],
                    "accesskey": cand["accesskey"],
                    "fmt": "lrc",
                    "charset": "utf8",
                },
            )
            rd.raise_for_status()
            b64 = rd.json().get("content") or ""
            if not b64:
                return None
            text = base64.b64decode(b64).decode("utf-8", "replace")
            lines = parse_lrc(text)
            lyr = Lyrics(lines, [])
            return lyr if lyr else None
        except Exception:  # noqa: BLE001
            return None

    def _fetch_lrclib(self, m: dict) -> Lyrics | None:
        """LRCLib 公开接口兜底（覆盖外文及长尾曲库）。"""
        dur = float(m.get("duration") or 0)
        if not (30 <= dur <= 15 * 60):
            return None
        title = self._clean_title(m.get("title") or "")
        artist = self._clean_artist(m.get("artist") or "")
        if not title:
            return None
        try:
            params = {"track_name": title}
            if artist:
                params["artist_name"] = artist
            r = self._client.get("https://lrclib.net/api/search", params=params)
            r.raise_for_status()
            items = r.json()
            if not isinstance(items, list):
                return None
            synced = [it for it in items if isinstance(it, dict) and it.get("syncedLyrics")]
            if not synced:
                return None
            best = min(
                synced,
                key=lambda it: self._score_match(
                    it.get("trackName") or it.get("name") or "",
                    it.get("artistName") or "",
                    float(it.get("duration") or 0),
                    title, artist, dur,
                ),
            )
            if not self._is_valid_match(
                best.get("trackName") or best.get("name") or "",
                best.get("artistName") or "",
                float(best.get("duration") or 0),
                title, artist, dur,
            ):
                return None
            lines = parse_lrc(best["syncedLyrics"])
            lyr = Lyrics(lines, [])
            return lyr if lyr else None
        except Exception:  # noqa: BLE001
            return None
