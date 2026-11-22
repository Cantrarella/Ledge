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
import json
import os
import re
import threading
import time
import zlib

import httpx
from PySide6 import QtCore

from wisland.core.bg import run_bg, ui
from wisland.core.config import DATA_DIR, cfg

API = "https://music.163.com/api"
HEADERS = {"Referer": "https://music.163.com/", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_TS = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_KRC_LINE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
_KRC_WORD = re.compile(r"<(\d+),(\d+),\d+>([^<]*)")
_KRC_KEY = bytes([64, 71, 97, 119, 94, 50, 116, 71, 81, 54, 49, 45, 206, 210, 110, 105])
_OFFSET = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.IGNORECASE)
# 开头的制作信息（含带空格的中英文混排，如 "和声编写 Harmonic Arrangement:"）不当作歌词显示
_CREDIT = re.compile(r"^\s*[^:：，。！？；]{1,36}\s*[:：]")
_INSTRUMENTAL_PLACEHOLDER = re.compile(r"没有填词的纯音乐|纯音乐[，,\s]*请欣赏|no\s+lyrics", re.IGNORECASE)
_VERSION_TAG = re.compile(r"\b(live|remix|dj|instrumental|karaoke)\b|伴奏|纯音乐|现场|翻唱", re.IGNORECASE)
_DISK_CACHE_FILE = DATA_DIR / "lyrics_cache.json"
_DISK_CACHE_TTL = 7 * 86400
_DISK_CACHE_MAX = 200


def decrypt_krc(b64_content: str) -> str:
    """解密酷狗 KRC base64 密文（krc1 头 + 16 字节循环异或 + zlib 解压）。"""
    raw = base64.b64decode(b64_content or "")
    if len(raw) <= 4 or raw[:4] != b"krc1":
        return ""
    dec = bytes(b ^ _KRC_KEY[i % 16] for i, b in enumerate(raw[4:]))
    return zlib.decompress(dec).decode("utf-8", "replace")


def _clean_leading_rows(rows: list[tuple]) -> list[tuple]:
    """排序后去掉开头连续的制作信息行，并在仅剩纯音乐占位文本时返回空列表。"""
    rows.sort(key=lambda x: x[0])
    i = 0
    while i < len(rows) and (not rows[i][1] or (rows[i][0] < 40 and _CREDIT.match(rows[i][1]))):
        i += 1
    cleaned = rows[i:]
    non_empty = [r[1] for r in cleaned if r[1]]
    if non_empty and all(_INSTRUMENTAL_PLACEHOLDER.search(s) for s in non_empty):
        return []
    return cleaned


def parse_krc(text: str) -> tuple[list[tuple[float, str]], list[list[tuple[float, float, str]]]]:
    """解析酷狗 KRC 逐字歌词 -> ([(start_s, line_text)], [[(w_start_s, w_end_s, token), ...]])."""
    offset_s = 0.0
    m_off = _OFFSET.search(text or "")
    if m_off:
        try:
            offset_s = int(m_off.group(1)) / 1000.0
        except ValueError:
            offset_s = 0.0
    rows: list[tuple[float, str, list[tuple[float, float, str]]]] = []
    for raw in (text or "").splitlines():
        ml = _KRC_LINE.match(raw.strip())
        if not ml:
            continue
        line_start = max(0.0, int(ml.group(1)) / 1000.0 - offset_s)
        body = ml.group(3)
        words: list[tuple[float, float, str]] = []
        parts: list[str] = []
        for mw in _KRC_WORD.finditer(body):
            w_off = int(mw.group(1)) / 1000.0
            w_dur = max(0.02, int(mw.group(2)) / 1000.0)
            tok = mw.group(3)
            ws = max(0.0, line_start + w_off)
            we = ws + w_dur
            words.append((ws, we, tok))
            parts.append(tok)
        line_txt = "".join(parts).strip()
        if not line_txt and not words:
            continue
        rows.append((line_start, line_txt, words))

    cleaned = _clean_leading_rows(rows)
    if not cleaned:
        return [], []
    return [(t, s) for t, s, _ in cleaned], [w for _, _, w in cleaned]


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
    return _clean_leading_rows(out)


class Lyrics:
    def __init__(
        self,
        lines: list[tuple[float, str]],
        trans: list[tuple[float, str]],
        words: list[list[tuple[float, float, str]]] | None = None,
    ):
        self.times = [t for t, _ in lines]
        self.lines = [s for _, s in lines]
        tmap = {round(t, 1): s for t, s in trans if s}
        self.trans = [tmap.get(round(t, 1), "") for t in self.times]
        self.words = words or []

    def __bool__(self):
        return bool(self.lines)

    def to_dict(self) -> dict:
        return {
            "times": self.times,
            "lines": self.lines,
            "trans": self.trans,
            "words": self.words,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Lyrics | None:
        times = data.get("times") or []
        lines = data.get("lines") or []
        if not times or len(times) != len(lines):
            return None
        obj = cls([], [])
        obj.times = [float(t) for t in times]
        obj.lines = [str(s) for s in lines]
        trans = data.get("trans") or []
        obj.trans = [str(trans[i]) if i < len(trans) else "" for i in range(len(obj.lines))]
        raw_words = data.get("words") or []
        obj.words = [
            [(float(ws), float(we), str(tok)) for ws, we, tok in row]
            for row in raw_words
        ]
        return obj

    def at(self, pos: float) -> tuple[int, str, str]:
        """-> (行号, 歌词, 翻译)；还没到第一行时行号为 -1。"""
        i = bisect.bisect_right(self.times, pos + 0.15) - 1
        if i < 0:
            return -1, "", ""
        return i, self.lines[i], self.trans[i]

    def sweep_at(self, idx: int, pos: float) -> tuple[str, str, float]:
        """-> (已唱前缀文本, 当前正在唱的字/词, 当前字/词内进度 0..1)。
        有 KRC 逐字时间戳时精确到字；否则按整句时间比例平滑扫色。
        """
        if idx < 0 or idx >= len(self.lines):
            return "", "", 0.0
        line = self.lines[idx]
        if not line:
            return "", "", 0.0
        if idx < len(self.words) and self.words[idx]:
            done_parts: list[str] = []
            for ws, we, tok in self.words[idx]:
                if pos >= we:
                    done_parts.append(tok)
                elif pos <= ws:
                    return "".join(done_parts), "", 0.0
                else:
                    frac = max(0.0, min(1.0, (pos - ws) / max(0.02, we - ws)))
                    return "".join(done_parts), tok, frac
            return line, "", 0.0
        t0 = self.times[idx]
        t1 = self.times[idx + 1] if idx + 1 < len(self.times) else (t0 + 5.0)
        span = max(0.6, min(t1 - t0, 12.0))
        frac = max(0.0, min(1.0, (pos - t0) / span))
        return "", line, frac


class LyricsService(QtCore.QObject):
    ready = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        ui(lambda: None)
        self._local = threading.local()
        self._cache: dict[str, Lyrics | None] = {}
        self._miss_until: dict[str, float] = {}
        self._err_backoff: dict[str, tuple[int, float]] = {}
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._disk_lock = threading.Lock()
        self._disk_loaded = False
        self._disk_cache: dict[str, dict] = {}
        self._active_key: str | None = None
        self._active_track: dict | None = None
        self._retry_timer = QtCore.QTimer(self, singleShot=True)
        self._retry_timer.timeout.connect(self._on_retry_timeout)

    def cancel_retry(self):
        """切歌或停止播放时取消待执行的退避补查定时器。"""
        self._retry_timer.stop()
        self._active_key = None
        self._active_track = None

    def _on_retry_timeout(self):
        if self._active_key and self._active_track is not None:
            self.get(self._active_track)

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
        dur_bucket = int(round(float(m.get("duration") or 0) / 5.0) * 5)
        return f"q:{m.get('title') or ''}|{m.get('artist') or ''}|{dur_bucket}"

    def get(self, m: dict) -> Lyrics | None:
        """返回已缓存的歌词；没有时在后台获取，取到后发 ready(key)。"""
        if not cfg.get("media.lyrics", True) or not m.get("title"):
            return None
        dur = float(m.get("duration") or 0)
        if not m.get("song_id") and not m.get("kugou_hash"):
            if not (30 <= dur <= 15 * 60):
                # SMTC 切歌初期 duration 常暂时为 0，等待下次轮询拿到有效时长后再查，避免把 None 写死进缓存
                return None
        k = self.key(m)
        switched = k != self._active_key
        if switched:
            self._retry_timer.stop()
            self._active_key = k
        self._active_track = dict(m)
        dur_bucket = int(round(dur / 5.0) * 5)
        now = time.monotonic()
        rem_retry_ms = 0
        in_backoff = False
        with self._lock:
            if k in self._cache:
                return self._cache[k]
            miss_ent = self._miss_until.get(k)
            if miss_ent is not None:
                miss_dur, miss_exp = miss_ent
                if miss_dur == dur_bucket and now < miss_exp:
                    return None
                self._miss_until.pop(k, None)
            err_ent = self._err_backoff.get(k)
            if err_ent is not None and now < err_ent[1]:
                in_backoff = True
                if switched or not self._retry_timer.isActive():
                    rem_retry_ms = max(50, int((err_ent[1] - now) * 1000) + 50)
            elif k in self._pending:
                return None
            else:
                self._pending.add(k)
        if in_backoff:
            if rem_retry_ms > 0:
                self._retry_timer.start(rem_retry_ms)
            return None
        cache_none = (dur != 60.0)
        run_bg(
            self._fetch,
            dict(m),
            on_done=lambda res, k=k, cn=cache_none, db=dur_bucket: self._done(k, res, cn, is_error=False, dur_bucket=db),
            on_error=lambda e, k=k, db=dur_bucket: self._done(k, None, False, is_error=True, dur_bucket=db),
        )
        return None

    def _done(self, k: str, res, cache_none: bool = True, is_error: bool = False, dur_bucket: int = 0):
        now = time.monotonic()
        retry_ms = 0
        with self._lock:
            self._pending.discard(k)
            if res is not None:
                self._miss_until.pop(k, None)
                self._err_backoff.pop(k, None)
                self._cache[k] = res
                if len(self._cache) > 200:
                    self._cache.pop(next(iter(self._cache)))
            elif is_error:
                prev_cnt = self._err_backoff.get(k, (0, 0.0))[0] + 1
                wait_s = min(300.0, 5.0 * (2 ** min(prev_cnt - 1, 5)))
                self._err_backoff[k] = (prev_cnt, now + wait_s)
                if k == self._active_key and self._active_track is not None:
                    retry_ms = int(wait_s * 1000) + 50
            else:
                # 确认无匹配使用有限期缓存（试听 60s 为 30s，常规为 300s），不写死永久空缓存
                ttl = 300.0 if cache_none else 30.0
                self._miss_until[k] = (dur_bucket, now + ttl)
        if retry_ms > 0:
            self._retry_timer.start(retry_ms)
        self.ready.emit(k)

    def _ensure_disk_cache(self):
        with self._disk_lock:
            if self._disk_loaded:
                return
            self._disk_loaded = True
            try:
                if _DISK_CACHE_FILE.is_file():
                    raw = json.loads(_DISK_CACHE_FILE.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        now = time.time()
                        self._disk_cache = {
                            k: v for k, v in raw.items()
                            if isinstance(v, dict) and now - float(v.get("ts") or 0) < _DISK_CACHE_TTL
                        }
            except Exception:  # noqa: BLE001
                self._disk_cache = {}

    def _disk_get(self, k: str) -> Lyrics | None:
        self._ensure_disk_cache()
        with self._disk_lock:
            ent = self._disk_cache.get(k)
            if not ent:
                return None
            if time.time() - float(ent.get("ts") or 0) >= _DISK_CACHE_TTL:
                self._disk_cache.pop(k, None)
                return None
            return Lyrics.from_dict(ent)

    def _disk_put(self, k: str, lyr: Lyrics):
        self._ensure_disk_cache()
        with self._disk_lock:
            self._disk_cache.pop(k, None)
            ent = lyr.to_dict()
            ent["ts"] = int(time.time())
            self._disk_cache[k] = ent
            while len(self._disk_cache) > _DISK_CACHE_MAX:
                self._disk_cache.pop(next(iter(self._disk_cache)))
            try:
                _DISK_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                tmp = _DISK_CACHE_FILE.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._disk_cache, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, _DISK_CACHE_FILE)
            except Exception:  # noqa: BLE001
                pass

    # ---------------- 网络 ----------------
    def _fetch(self, m: dict) -> Lyrics | None:
        k = self.key(m)
        cached = self._disk_get(k)
        if cached:
            return cached
        res = self._fetch_remote(m)
        if res:
            self._disk_put(k, res)
        return res

    def _fetch_remote(self, m: dict) -> Lyrics | None:
        last_net_err: Exception | None = None
        if m.get("kugou_hash"):
            try:
                lyr = self._fetch_kugou_by_hash(
                    str(m["kugou_hash"]),
                    float(m.get("duration") or 0),
                    f"{self._clean_title(m.get('title') or '')} {self._clean_artist(m.get('artist') or '')}".strip(),
                )
                if lyr:
                    return lyr
            except (httpx.HTTPError, OSError) as exc:
                last_net_err = exc
        if m.get("song_id"):
            order = (self._fetch_netease, self._fetch_kugou, self._fetch_qq, self._fetch_lrclib)
        else:
            # 优先酷狗（支持 KRC 逐字卡拉 OK 歌词），其次 QQ / LRCLIB / 网易云
            order = (self._fetch_kugou, self._fetch_qq, self._fetch_lrclib, self._fetch_netease)
        for fn in order:
            try:
                lyr = fn(m)
            except (httpx.HTTPError, OSError) as exc:
                last_net_err = exc
                continue
            if lyr:
                return lyr
        if last_net_err is not None:
            raise last_net_err
        return None

    def _download_kugou_candidate(self, cand: dict) -> Lyrics | None:
        """优先尝试下载并解密酷狗 KRC 逐字歌词，失败时回退 LRC。"""
        last_net_err: Exception | None = None
        for fmt in ("krc", "lrc"):
            try:
                rd = self._client.get(
                    "http://lyrics.kugou.com/download",
                    params={
                        "ver": 1,
                        "client": "pc",
                        "id": cand["id"],
                        "accesskey": cand["accesskey"],
                        "fmt": fmt,
                        "charset": "utf8",
                    },
                )
                rd.raise_for_status()
                b64 = rd.json().get("content") or ""
                if not b64:
                    continue
                if fmt == "krc":
                    text = decrypt_krc(b64)
                    if text:
                        lines, words = parse_krc(text)
                        if lines:
                            return Lyrics(lines, [], words=words)
                else:
                    text = base64.b64decode(b64).decode("utf-8", "replace")
                    lines = parse_lrc(text)
                    if lines:
                        return Lyrics(lines, [])
            except (httpx.HTTPError, OSError) as exc:
                last_net_err = exc
                continue
            except Exception:  # noqa: BLE001
                continue
        if last_net_err is not None:
            raise last_net_err
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
            return self._download_kugou_candidate(cands[0])
        except (httpx.HTTPError, OSError):
            raise
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
            # 非会员试听片段通常为 30~65 秒：若歌名与歌手均精确匹配，放行完整版歌词
            is_vip_preview = (25.0 <= q_dur <= 65.0) and cand_dur > 65.0 and exact_title and bool(qa_tokens) and artist_ok
            if not is_vip_preview:
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
        except (httpx.HTTPError, OSError):
            raise
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
        except (httpx.HTTPError, OSError):
            raise
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
            return self._download_kugou_candidate(cands[0])
        except (httpx.HTTPError, OSError):
            raise
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
        except (httpx.HTTPError, OSError):
            raise
        except Exception:  # noqa: BLE001
            return None
