# -*- coding: utf-8 -*-
"""设置窗口各分类子页面导出。"""
from .about import AboutSettingsPage, get_process_memory_mb
from .data import DataSettingsPage
from .general import GeneralSettingsPage
from .island_pet import IslandPetSettingsPage
from .notify import NotifySettingsPage
from .pages_nav import PagesNavSettingsPage
from .subtitles import SubtitlesSettingsPage
from .tools_life import ToolsLifeSettingsPage
from .weather import WeatherSettingsPage

__all__ = [
    "GeneralSettingsPage",
    "IslandPetSettingsPage",
    "PagesNavSettingsPage",
    "WeatherSettingsPage",
    "NotifySettingsPage",
    "SubtitlesSettingsPage",
    "ToolsLifeSettingsPage",
    "DataSettingsPage",
    "AboutSettingsPage",
    "get_process_memory_mb",
]
