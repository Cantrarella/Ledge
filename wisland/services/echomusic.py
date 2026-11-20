# -*- coding: utf-8 -*-
"""Echo Music 本地播放元数据读取（只读 echomusic.sqlite，提取精确酷狗 hash 与封面地址）。"""
from __future__ import annotations

import os
import re
import sqlite3

ECHO_DB = os.path.expandvars(r"%APPDATA%\echo-music\echomusic.sqlite")


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_·.,，。!！?？'\"`~()（）\[\]【】/、]+", "", (s or "")).lower()


def lookup_echo_track(title: str, artist: str = "", db_path: str = ECHO_DB) -> dict | None:
    """从 Echo Music 的 SQLite 最近播放记录中查找当前曲目的酷狗 hash 与封面 URL。"""
    if not title or not os.path.isfile(db_path):
        return None
    nt = _norm(title)
    if not nt:
        return None
    con = None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.4)
        rows = con.execute(
            "SELECT s.source_id, s.hash, s.title, s.artist, s.duration, s.cover_url "
            "FROM play_history h JOIN songs s ON h.song_key = s.song_key "
            "ORDER BY h.last_played_at DESC LIMIT 10"
        ).fetchall()
        for source_id, kg_hash, row_title, row_artist, dur, cover_url in rows:
            rt = _norm(row_title or "")
            if nt == rt or nt in rt:
                # 若标题只是包含关系，顺便核对歌手（若有）
                if nt != rt and artist:
                    ra = _norm(row_artist or "")
                    na = _norm(artist)
                    if na and ra and na not in ra and ra not in na:
                        continue
                return {
                    "source_id": str(source_id or ""),
                    "kugou_hash": str(kg_hash or ""),
                    "duration": float(dur or 0),
                    "cover": str(cover_url or ""),
                }
        return None
    except (sqlite3.Error, OSError):
        return None
    finally:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass
