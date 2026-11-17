# -*- coding: utf-8 -*-
"""设置窗口各分类子页面导出。"""
from .about import AboutSettingsPage, get_process_memory_mb
from .data import DataSettingsPage
from .general import GeneralSettingsPage
from .island_pet import IslandPetSettingsPage
from .notify import NotifySettingsPage
from .subtitles import SubtitlesSettingsPage
from .weather import WeatherSettingsPage

__all__ = [
    "GeneralSettingsPage",
    "IslandPetSettingsPage",
    "WeatherSettingsPage",
    "NotifySettingsPage",
    "SubtitlesSettingsPage",
    "DataSettingsPage",
    "AboutSettingsPage",
    "get_process_memory_mb",
]
