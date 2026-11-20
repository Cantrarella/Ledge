# -*- coding: utf-8 -*-
"""GitHub Releases 更新检查服务（仅在用户主动触发时联网请求最新版本信息）。"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from ..core.branding import APP_VERSION, LATEST_RELEASE_API, RELEASES_URL

_SEMVER_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?")


def parse_semver(version: str) -> tuple[tuple[int, int, int], int, str]:
    """解析语义化版本号，返回 `((major, minor, patch), is_stable, pre_tag)`。"""
    raw = str(version or "").strip()
    m = _SEMVER_RE.match(raw)
    if not m:
        return ((0, 0, 0), 0, raw.lower())
    major = int(m.group(1) or 0)
    minor = int(m.group(2) or 0)
    patch = int(m.group(3) or 0)
    pre = (m.group(4) or "").strip().lower()
    is_stable = 0 if pre else 1
    return ((major, minor, patch), is_stable, pre)


def is_newer_version(remote_tag: str, current_version: str = APP_VERSION) -> bool:
    """判断远端版本是否高于当前版本。"""
    r_nums, r_stable, r_pre = parse_semver(remote_tag)
    c_nums, c_stable, c_pre = parse_semver(current_version)
    if r_nums != c_nums:
        return r_nums > c_nums
    if r_stable != c_stable:
        return r_stable > c_stable
    return r_pre > c_pre


def check_latest_release(
    timeout_s: float = 8.0,
    api_url: str = LATEST_RELEASE_API,
    current_version: str = APP_VERSION,
) -> dict:
    """查询 GitHub Latest Release 并对比版本号。"""
    req = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Ledge/{current_version}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {
                "ok": True,
                "has_update": False,
                "current_version": current_version,
                "latest_version": current_version,
                "release_url": RELEASES_URL,
                "release_notes": "",
                "published_at": "",
                "message": "仓库暂无已发布的正式 Release",
            }
        return {
            "ok": False,
            "has_update": False,
            "current_version": current_version,
            "latest_version": "",
            "release_url": RELEASES_URL,
            "release_notes": "",
            "published_at": "",
            "error": f"GitHub 接口返回状态码 {e.code}",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "has_update": False,
            "current_version": current_version,
            "latest_version": "",
            "release_url": RELEASES_URL,
            "release_notes": "",
            "published_at": "",
            "error": f"检查更新失败：{e}",
        }

    tag = str(payload.get("tag_name") or payload.get("name") or "").strip()
    html_url = str(payload.get("html_url") or RELEASES_URL).strip()
    notes = str(payload.get("body") or "").strip()
    published = str(payload.get("published_at") or "").strip()
    has_update = bool(tag) and is_newer_version(tag, current_version)
    return {
        "ok": True,
        "has_update": has_update,
        "current_version": current_version,
        "latest_version": tag or current_version,
        "release_url": html_url,
        "release_notes": notes,
        "published_at": published,
        "message": f"发现新版本 {tag}" if has_update else f"当前已是最新版本 (v{current_version})",
    }
