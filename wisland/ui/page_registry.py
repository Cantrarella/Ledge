# -*- coding: utf-8 -*-
"""页面注册表：统一管理真实页面的实例（单例复用）、排序（order）与可见性（visible）。
当前真实页面：home（主页）、mail（邮箱）、weather（天气）、trans（实时字幕）、tools（工具）、life（生活）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..core.config import ConfigDraft, cfg

if TYPE_CHECKING:
    from .pages.base import Page


@dataclass
class PageEntry:
    key: str
    glyph: str
    title: str
    page: Page
    visible: bool = True
    order: int = 0


class PageRegistry:
    """灵动岛页面注册表：负责维护真实 Page 实例、同步配置顺序与可见状态。"""

    def __init__(self):
        self._entries: dict[str, PageEntry] = {}
        self._reg_order: list[str] = []

    def register(self, page: Page, visible: bool = True, order: int | None = None) -> PageEntry:
        key = page.key
        idx = len(self._reg_order) if order is None else order
        entry = PageEntry(
            key=key,
            glyph=page.glyph,
            title=page.title,
            page=page,
            visible=bool(visible),
            order=idx,
        )
        self._entries[key] = entry
        if key not in self._reg_order:
            self._reg_order.append(key)
        return entry

    @classmethod
    def create_default(cls, ctx) -> PageRegistry:
        """创建并注册当前项目的真实页面（主页、邮箱、天气、实时字幕、工具、生活）。"""
        from .pages.home import HomePage
        from .pages.info import WeatherPage
        from .pages.life import LifePage
        from .pages.mail import MailPage
        from .pages.subtitle import SubtitlePage
        from .pages.tools import ToolsPage

        reg = cls()
        for pg in (HomePage(ctx), MailPage(ctx), WeatherPage(ctx), SubtitlePage(ctx), ToolsPage(ctx), LifePage(ctx)):
            reg.register(pg)
        reg.sync_from_config(cfg)
        return reg

    def registered_keys(self) -> list[str]:
        return list(self._reg_order)

    def get_entry(self, key: str) -> PageEntry | None:
        return self._entries.get(key)

    def pages_dict(self) -> dict[str, Page]:
        """返回所有已注册页面的 {key: Page} 字典（即使页面在导航中隐藏，后台服务仍可更新其数据）。"""
        return {k: e.page for k, e in self._entries.items()}

    def sync_from_config(self, source=None):
        """从 Config 或 ConfigDraft 读取 pages.order 与 pages.visible，忽略未注册的 key。"""
        src = source or cfg
        raw_order = src.get("pages.order") if hasattr(src, "get") else None
        raw_vis = src.get("pages.visible") if hasattr(src, "get") else None

        order_list = raw_order if isinstance(raw_order, list) else []
        vis_map = raw_vis if isinstance(raw_vis, dict) else {}

        ordered_reg_keys: list[str] = []
        for k in order_list:
            if isinstance(k, str) and k in self._entries and k not in ordered_reg_keys:
                ordered_reg_keys.append(k)
        for k in self._reg_order:
            if k not in ordered_reg_keys:
                ordered_reg_keys.append(k)

        for idx, k in enumerate(ordered_reg_keys):
            entry = self._entries[k]
            entry.order = idx
            entry.visible = bool(vis_map.get(k, True))

        # 兜底：至少保留一个可见页面
        if self._entries and not any(e.visible for e in self._entries.values()):
            first_key = ordered_reg_keys[0]
            self._entries[first_key].visible = True

    def entries_for_draft(self, draft: ConfigDraft) -> list[PageEntry]:
        """根据草稿中的 pages.order 和 pages.visible 生成当前真实页面的排序与可见快照（不修改注册表自身状态）。"""
        raw_order = draft.get("pages.order")
        raw_vis = draft.get("pages.visible")
        order_list = raw_order if isinstance(raw_order, list) else []
        vis_map = raw_vis if isinstance(raw_vis, dict) else {}

        ordered_reg_keys: list[str] = []
        for k in order_list:
            if isinstance(k, str) and k in self._entries and k not in ordered_reg_keys:
                ordered_reg_keys.append(k)
        for k in self._reg_order:
            if k not in ordered_reg_keys:
                ordered_reg_keys.append(k)

        out: list[PageEntry] = []
        for idx, k in enumerate(ordered_reg_keys):
            base = self._entries[k]
            out.append(PageEntry(
                key=base.key,
                glyph=base.glyph,
                title=base.title,
                page=base.page,
                visible=bool(vis_map.get(k, True)),
                order=idx,
            ))
        if out and not any(e.visible for e in out):
            out[0].visible = True
        return out

    def save_to_draft(self, draft: ConfigDraft, ordered_reg_keys: list[str], vis_reg_map: dict[str, bool]):
        """将已注册页面的顺序和可见状态写入草稿，同时完整保留配置中尚未注册的其它页面键。"""
        old_order = draft.get("pages.order")
        old_order_list = old_order if isinstance(old_order, list) else []
        reg_set = set(self._entries.keys())

        # 保留未知页面在原列表中的位置，或追加在末尾；已注册页面按新顺序排列
        new_order: list[str] = []
        reg_iter = iter([k for k in ordered_reg_keys if k in reg_set])
        for k in old_order_list:
            if isinstance(k, str) and k in reg_set:
                nxt = next(reg_iter, None)
                if nxt is not None and nxt not in new_order:
                    new_order.append(nxt)
            elif isinstance(k, str) and k not in new_order:
                new_order.append(k)
        for nxt in reg_iter:
            if nxt not in new_order:
                new_order.append(nxt)
        for rk in self._reg_order:
            if rk not in new_order:
                new_order.append(rk)

        old_vis = draft.get("pages.visible")
        new_vis = dict(old_vis) if isinstance(old_vis, dict) else {}
        for rk in self._reg_order:
            if rk in vis_reg_map:
                new_vis[rk] = bool(vis_reg_map[rk])

        draft.set("pages.order", new_order)
        draft.set("pages.visible", new_vis)

    def all_entries(self) -> list[PageEntry]:
        """按 order 返回所有已注册页面项。"""
        return sorted(self._entries.values(), key=lambda e: (e.order, self._reg_order.index(e.key)))

    def visible_entries(self) -> list[PageEntry]:
        """按 order 返回所有可见的已注册页面项（至少包含一个页面）。"""
        vis = [e for e in self.all_entries() if e.visible]
        if not vis and self._entries:
            all_e = self.all_entries()
            all_e[0].visible = True
            return [all_e[0]]
        return vis

    def visible_pages(self) -> list[Page]:
        """返回排序后的可见 Page 实例列表。"""
        return [e.page for e in self.visible_entries()]
