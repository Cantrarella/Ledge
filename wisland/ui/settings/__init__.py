# -*- coding: utf-8 -*-
"""设置窗口各分类子页面导出。"""
from .about import AboutSettingsPage, get_process_memory_mb
from .battery import BatterySettingsPage
from .data import DataSettingsPage
from .general import GeneralSettingsPage
from .island_pet import IslandPetSettingsPage
from .mail_accounts import MailAccountsSettingsPage
from .notify import NotifySettingsPage
from .pages_nav import PagesNavSettingsPage
from .quota import QuotaSettingsPage
from .subtitles import SubtitlesSettingsPage
from .tools_life import ToolsLifeSettingsPage
from .weather import WeatherSettingsPage

__all__ = [
    "GeneralSettingsPage",
    "IslandPetSettingsPage",
    "PagesNavSettingsPage",
    "WeatherSettingsPage",
    "NotifySettingsPage",
    "MailAccountsSettingsPage",
    "QuotaSettingsPage",
    "SubtitlesSettingsPage",
    "ToolsLifeSettingsPage",
    "DataSettingsPage",
    "AboutSettingsPage",
    "BatterySettingsPage",
    "get_process_memory_mb",
]
