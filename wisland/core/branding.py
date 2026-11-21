# -*- coding: utf-8 -*-
"""Ledge 对外品牌文案常量（内部技术标识、配置、日志与凭据前缀继续保留 wisland）。"""
from __future__ import annotations

DISPLAY_NAME = "Ledge"
LEGACY_DISPLAY_NAME = "Wisland"
APP_ID = "wisland"
APP_VERSION = "1.0.7"
APP_VERSION_TAG = f"v{APP_VERSION}"
GITHUB_REPO = "Cantrarella/Ledge"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

SETTINGS_TITLE = f"{DISPLAY_NAME} 设置"
ABOUT_TITLE = f"{DISPLAY_NAME} 灵动岛"

OPEN_SETTINGS_LABEL = "打开设置"
OPEN_SETTINGS_TOOLTIP = f"打开 {DISPLAY_NAME} 设置"
TRAY_OPEN_SETTINGS = f"打开 {DISPLAY_NAME} 设置…"
TRAY_EXPAND = f"展开 {DISPLAY_NAME}"
TRAY_COLLAPSE = f"收起 {DISPLAY_NAME}"
TRAY_QUIT = f"退出 {DISPLAY_NAME}"
TRAY_TOOLTIP = DISPLAY_NAME
STARTUP_ERROR_TITLE = f"{DISPLAY_NAME} 启动失败"
