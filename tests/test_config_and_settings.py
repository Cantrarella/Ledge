# -*- coding: utf-8 -*-
"""Wisland 配置草稿、校验、恢复、未知字段保留、设置窗口交互与打包冒烟测试。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PySide6 import QtWidgets

from wisland.core.config import Config, DEFAULTS, is_valid_hotkey, validate_config
from wisland.ui.settings_window import SettingsWindow


class TestConfigAndSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cfg_path = Path(self.tmpdir.name) / "config.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_01_draft_save_and_atomic_write(self):
        """测试草稿修改并统一保存后，内存和磁盘 config.json 均正确更新，且无残留 .tmp 文件。"""
        c = Config(path=self.cfg_path)
        draft = c.create_draft()
        draft.set("island.edge", "bottom")
        draft.set("island.pos", 0.35)
        draft.set("weather.city", "北京")

        # 提交前原配置未变
        self.assertEqual(c.get("island.edge"), "top")
        self.assertEqual(c.get("weather.city"), "上海")

        ok, errors = c.commit_draft(draft, strict=True)
        self.assertTrue(ok, f"Expected commit_draft to succeed, got errors: {errors}")
        self.assertEqual(errors, [])
        self.assertEqual(c.get("island.edge"), "bottom")
        self.assertEqual(c.get("island.pos"), 0.35)
        self.assertEqual(c.get("weather.city"), "北京")

        # 验证磁盘文件内容与无临时文件残留
        self.assertFalse(self.cfg_path.with_suffix(".tmp").exists())
        disk_data = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(disk_data["island"]["edge"], "bottom")
        self.assertEqual(disk_data["weather"]["city"], "北京")

    def test_02_cancel_does_not_write(self):
        """测试取消设置窗口修改时，不写入磁盘也不改动内存配置。"""
        c = Config(path=self.cfg_path)
        orig_mtime = self.cfg_path.stat().st_mtime_ns
        orig_text = self.cfg_path.read_text(encoding="utf-8")

        win = SettingsWindow(ctx=None, config_obj=c)
        # 在 UI 控件中修改多个值
        gen_page = win.pages[0]
        isl_page = win.pages[1]
        gen_page.cb_theme.set_value("rouge")
        isl_page.cb_edge.set_value("left")
        isl_page.sp_pos.setValue(80)
        win.collect_to_draft()

        # 点击取消
        win.cancel_and_close()

        self.assertEqual(c.get("island.theme"), "auto")
        self.assertEqual(c.get("island.edge"), "top")
        self.assertEqual(self.cfg_path.read_text(encoding="utf-8"), orig_text)
        self.assertEqual(self.cfg_path.stat().st_mtime_ns, orig_mtime)

    def test_03_validation_failure_preserves_original_config(self):
        """测试校验失败时不覆盖原内存配置与原磁盘文件。"""
        c = Config(path=self.cfg_path)
        orig_text = self.cfg_path.read_text(encoding="utf-8")

        draft = c.create_draft()
        draft.set("island.edge", "invalid_edge")
        draft.set("island.pos", 2.5)
        draft.set("weather.interval_min", -10)
        draft.set("hotkeys.toggle", "Invalid++Key")

        ok, errors = c.commit_draft(draft, strict=True)
        self.assertFalse(ok)
        self.assertGreaterEqual(len(errors), 4)

        # 原配置未被污染
        self.assertEqual(c.get("island.edge"), "top")
        self.assertEqual(c.get("island.pos"), 0.5)
        self.assertEqual(c.get("weather.interval_min"), 15)
        self.assertEqual(self.cfg_path.read_text(encoding="utf-8"), orig_text)

    def test_04_corrupted_config_recovery(self):
        """测试损坏的 config.json 不会导致崩溃，并能安全回退至默认值。"""
        self.cfg_path.write_text("{broken json: [1, 2,", encoding="utf-8")
        c = Config(path=self.cfg_path)
        self.assertEqual(c.get("island.edge"), DEFAULTS["island"]["edge"])
        self.assertEqual(c.get("weather.city"), DEFAULTS["weather"]["city"])
        # 损坏的源文件不应在启动时被静默覆盖，除非用户主动保存
        self.assertEqual(self.cfg_path.read_text(encoding="utf-8"), "{broken json: [1, 2,")

    def test_05_unknown_fields_preserved(self):
        """测试未知顶层字段与嵌套扩展字段在加载、草稿编辑和保存后完整保留。"""
        custom_initial = {
            "island": {"edge": "top", "custom_island_flag": "keep_me"},
            "daozai_quota": {"providers": ["deepseek", "minimax"], "refresh_s": 60},
        }
        self.cfg_path.write_text(json.dumps(custom_initial, ensure_ascii=False), encoding="utf-8")

        c = Config(path=self.cfg_path)
        self.assertEqual(c.get("island.custom_island_flag"), "keep_me")
        self.assertEqual(c.get("daozai_quota.providers"), ["deepseek", "minimax"])

        # 通过设置窗口保存新设置后，未知字段依然保留
        win = SettingsWindow(ctx=None, config_obj=c)
        win.pages[0].cb_theme.set_value("dark")
        saved = win.save_settings()
        self.assertTrue(saved)

        disk_after = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(disk_after["island"]["theme"], "dark")
        self.assertEqual(disk_after["island"]["custom_island_flag"], "keep_me")
        self.assertEqual(disk_after["daozai_quota"]["providers"], ["deepseek", "minimax"])

    def test_06_hotkey_and_bounds_validator(self):
        """测试热键解析校验器与边界条件。"""
        self.assertTrue(is_valid_hotkey("Ctrl+Alt+I"))
        self.assertTrue(is_valid_hotkey("Shift+F10"))
        self.assertTrue(is_valid_hotkey(""))
        self.assertFalse(is_valid_hotkey("Ctrl+Alt"))
        self.assertFalse(is_valid_hotkey("Ctrl+Alt+I+O"))
        ok, errs, _ = validate_config({"island": {"scale": 9.9}}, strict=True)
        self.assertFalse(ok)
        self.assertTrue(any("缩放" in e for e in errs))

    def test_07_island_fps_and_packaging_smoke(self):
        """测试灵动岛动态 60 FPS (16ms) / 空闲 30 FPS (33ms) 帧率切换逻辑与打包产物完整性。"""
        from wisland.ui.island import Island
        from wisland.ui.pages.base import Page

        class _DummyPage(Page):
            key = "home"
            glyph = "\uE80F"
            title = "主页"

        dummy_page = _DummyPage()
        isl = Island([dummy_page])
        try:
            # 1) 刚初始化/运动过渡时为 60 FPS (16ms)
            self.assertEqual(isl.timer.interval(), 16)
            # 2) 收起态静止且无音乐/提醒/表情活动时（compact.busy() == False），_tick 降为 30 FPS (33ms)
            isl.mode = "anchored"
            isl.expanded = False
            isl.sw.snap(isl.compact.size_hint()[0])
            isl.sh.snap(isl.compact.size_hint()[1])
            isl.compact.set_view("clock")
            isl.compact.activity = None
            isl._tick()
            self.assertEqual(isl.timer.interval(), 33)
            # 3) 当有活动通知（busy() == True）时，立即恢复 60 FPS (16ms)
            isl.activity({"glyph": "\uE73E", "title": "测试活动"}, 2.0)
            isl._tick()
            self.assertEqual(isl.timer.interval(), 16)
        finally:
            isl.close()

        repo_root = Path(__file__).resolve().parents[1]
        portable_exe = repo_root / "dist" / "WIsland" / "WIsland.exe"
        portable_zip = repo_root / "dist" / "WIsland-portable.zip"
        if portable_exe.exists():
            self.assertGreater(portable_exe.stat().st_size, 1_000_000)
        if portable_zip.exists():
            self.assertGreater(portable_zip.stat().st_size, 10_000_000)

    def test_08_native_tray_menu_structure_and_actions(self):
        """测试原生 QMenu 托盘菜单结构、动态展开/收起文案、可勾选项及移除的旧维护项。"""
        from wisland.app import populate_tray_menu
        from wisland.ui.island import Island
        from wisland.ui.pages.base import Page

        class _DummyPage(Page):
            key = "home"
            glyph = "\uE80F"
            title = "主页"

        isl = Island([_DummyPage()])
        try:
            menu = QtWidgets.QMenu()
            # 1) 岛收起态
            isl.expanded = False
            populate_tray_menu(menu, isl, lambda: None, lambda: None)
            acts = menu.actions()
            self.assertEqual(len(acts), 8)
            self.assertEqual(acts[0].text(), "展开 Wisland")
            self.assertEqual(acts[1].text(), "打开 Wisland 设置…")
            self.assertTrue(acts[2].isSeparator())
            self.assertEqual(acts[3].text(), "可穿透")
            self.assertTrue(acts[3].isCheckable())
            self.assertEqual(acts[4].text(), "在截图中显示")
            self.assertTrue(acts[4].isCheckable())
            self.assertEqual(acts[5].text(), "开机启动")
            self.assertTrue(acts[5].isCheckable())
            self.assertTrue(acts[6].isSeparator())
            self.assertEqual(acts[7].text(), "退出 Wisland")

            # 确保无子菜单、无旧维护项
            for a in acts:
                self.assertIsNone(a.menu())
                self.assertNotIn(a.text(), ("高级", "编辑配置 config.json", "重新加载配置", "打开数据目录", "主题", "展开方式", "参与截图"))

            # 2) 岛展开态动态文案刷新
            isl.expanded = True
            populate_tray_menu(menu, isl, lambda: None, lambda: None)
            self.assertEqual(menu.actions()[0].text(), "收起 Wisland")
        finally:
            isl.close()

    def test_09_settings_ui_dirty_state_and_copy(self):
        """测试设置窗口未保存状态指示、保存按钮禁用/启用、窄宽响应式布局、天气经纬度提示与字幕文案。"""
        c = Config(path=self.cfg_path)
        win = SettingsWindow(ctx=None, config_obj=c)
        try:
            # 1) 初始无修改：保存按钮禁用，顶部无未保存提示
            self.assertFalse(win.is_dirty())
            self.assertFalse(win.btn_save.isEnabled())
            self.assertEqual(win.dirty_lbl.text(), "")

            # 2) 修改任意设置项后：is_dirty 为 True，保存按钮启用，顶栏显示“● 有未保存修改”
            win.pages[0].sw_passthrough.setChecked(True, emit=True)
            self.assertTrue(win.is_dirty())
            self.assertTrue(win.btn_save.isEnabled())
            self.assertIn("有未保存修改", win.dirty_lbl.text())

            # 3) 校验错误就地显示在对应 SettingRow
            win.pages[0].ed_hotkey.setText("Bad++Hotkey")
            ok = win.save_settings()
            self.assertFalse(ok)
            self.assertFalse(win.pages[0].row_hotkey.err_lbl.isHidden())
            self.assertIn("热键", win.pages[0].row_hotkey.err_lbl.text())

            # 4) 检查天气页 API Key 占位符、说明与已保存城市（不直接显示经纬度，经纬度在 tooltip）
            pg_map = {pg.key: pg for pg in win.pages}
            wth_page = pg_map["weather"]
            self.assertEqual(wth_page.ed_api_key.edit.placeholderText(), "粘贴 API Key（可选）")
            self.assertEqual(wth_page.row_api.sub_lbl.text(), "留空时使用免费的 Open-Meteo 服务")
            wth_page._cities = [{"name": "杭州", "region": "浙江", "lat": 30.2741, "lon": 120.1551}]
            wth_page._rebuild_cities_ui()
            city_row_widget = wth_page.cities_lay.itemAt(0).widget()
            self.assertIn("30.2741", city_row_widget.toolTip())

            # 5) 检查数据页危险按钮属性
            data_page = pg_map["data"]
            self.assertTrue(data_page.btn_reset.danger)
        finally:
            win.close()

    def test_10_page_registry_order_visibility_and_rebuild(self):
        """测试页面注册表、自由排序、隐藏首页与最近可见页切换、至少一个可见页限制及未知页面配置保留。"""
        from types import SimpleNamespace
        from wisland.ui.island import Island
        from wisland.ui.page_registry import PageRegistry
        from wisland.ui.pages.base import Page

        # 预先写入含未注册页面（如 mail、ai_quota）的配置，验证只显示 4 个真实页面但保留未注册项
        init_cfg = {
            "schema_version": 2,
            "pages": {
                "order": ["home", "mail", "msg", "weather", "trans", "ai_quota"],
                "visible": {"home": True, "mail": False, "msg": True, "weather": True, "trans": True, "ai_quota": True},
            },
        }
        self.cfg_path.write_text(json.dumps(init_cfg, ensure_ascii=False), encoding="utf-8")
        c = Config(path=self.cfg_path)

        reg = PageRegistry()
        for k, g, t in (("home", "\uE80F", "主页"), ("msg", "\uE8BD", "消息"),
                        ("weather", "\uE706", "天气"), ("trans", "\uE720", "实时字幕")):
            pg = type(f"Page_{k}", (Page,), {"key": k, "glyph": g, "title": t})()
            reg.register(pg)
        reg.sync_from_config(c)

        orig_ids = {k: id(p) for k, p in reg.pages_dict().items()}
        ctx = SimpleNamespace(registry=reg)
        isl = Island(reg.visible_pages(), registry=reg)
        isl.show_page("home")
        win = SettingsWindow(ctx=ctx, config_obj=c)
        win.show()
        try:
            pg_map = {pg.key: pg for pg in win.pages}
            nav_pg = pg_map["pages_nav"]

            # 1) 设置中只列出 4 个真实页面，不出现 mail / ai_quota 等未注册页面
            self.assertEqual(nav_pg.ordered_keys(), ["home", "msg", "weather", "trans"])

            # 2) 将 weather 移到第一位，隐藏 home，点击取消 -> 不修改任何配置，恢复原顺序与显示状态
            nav_pg.move_page("weather", -1)
            nav_pg.move_page("weather", -1)
            nav_pg.set_page_visible("home", False)
            self.assertEqual(nav_pg.ordered_keys()[0], "weather")
            win.cancel_and_close(prompt_if_dirty=False)
            self.assertEqual(c.get("pages.order"), ["home", "mail", "msg", "weather", "trans", "ai_quota"])
            self.assertTrue(c.get("pages.visible.home"))
            self.assertEqual(isl.nav.keys(), ["home", "msg", "weather", "trans"])

            # 3) 重新打开设置，将 trans 移到首位，隐藏 home 和 msg，点击保存 -> 窗口保持打开，顺序与隐藏立即生效
            win.show()
            nav_pg.move_page("trans", -1)
            nav_pg.move_page("trans", -1)
            nav_pg.move_page("trans", -1)
            nav_pg.set_page_visible("home", False)
            nav_pg.set_page_visible("msg", False)
            saved = win.save_settings()
            self.assertTrue(saved)
            self.assertTrue(win.isVisible(), "保存设置后不应关闭设置窗口")

            reg.sync_from_config(c)
            isl.rebuild_pages(reg.visible_pages())

            # 验证岛体导航只包含可见的 trans 与 weather，且由于原当前页 home 被隐藏，自动切到第一个可见页 trans
            self.assertEqual(isl.nav.keys(), ["trans", "weather"])
            self.assertEqual(isl._page_key, "trans")
            # 滚轮只能在 trans 与 weather 之间切换，不能切到隐藏的 home 或 msg
            isl.nav.step_wheel(-120)
            self.assertEqual(isl.nav.current(), "weather")
            isl.nav.step_wheel(-120)
            self.assertEqual(isl.nav.current(), "weather")

            # 验证 Page 实例未重复创建
            after_ids = {k: id(p) for k, p in reg.pages_dict().items()}
            self.assertEqual(orig_ids, after_ids)

            # 验证未注册页面配置项在 config.json 中依然保留
            disk_cfg = json.loads(self.cfg_path.read_text(encoding="utf-8"))
            self.assertIn("mail", disk_cfg["pages"]["order"])
            self.assertIn("ai_quota", disk_cfg["pages"]["order"])
            self.assertEqual(disk_cfg["pages"]["visible"]["mail"], False)
            self.assertEqual(disk_cfg["pages"]["visible"]["ai_quota"], True)

            # 4) 验证重启（重新加载 Config 与 PageRegistry）后顺序保持不变
            c2 = Config(path=self.cfg_path)
            reg.sync_from_config(c2)
            self.assertEqual([e.key for e in reg.visible_entries()], ["trans", "weather"])

            # 5) 验证“至少保留一个可见页面”限制有效
            nav_pg.set_page_visible("trans", False)
            self.assertFalse(nav_pg.visible_map()["trans"])
            # 此时只剩 weather 可见，尝试关闭 weather 应被拒绝并保持 True
            nav_pg.set_page_visible("weather", False)
            self.assertTrue(nav_pg.visible_map()["weather"])
            self.assertIn("至少需要保留一个可见页面", nav_pg.tip_lbl.text())
        finally:
            win.close()
            isl.close()

    def test_11_shelf_service_dedup_missing_and_corrupt_recovery(self):
        """测试文件置物架：大小写不敏感去重、移除/清空不删原文件、文件删除状态识别、重启恢复与损坏备份。"""
        from wisland.services.shelf import ShelfService

        shelf_path = Path(self.tmpdir.name) / "shelf.json"
        # 创建 6 个真实本地测试文件（含 .log 文件）
        sample_files: list[Path] = []
        for i in range(6):
            ext = ".log" if i % 2 == 0 else ".txt"
            fp = Path(self.tmpdir.name) / f"sample_{i}{ext}"
            fp.write_text(f"hello-{i}", encoding="utf-8")
            sample_files.append(fp)

        shelf = ShelfService(path=shelf_path)
        added, _ = shelf.add_files([str(p) for p in sample_files])
        self.assertEqual(added, 6)

        # Windows 大小写不敏感去重验证
        dup_upper = str(sample_files[0]).upper()
        dup_lower = str(sample_files[0]).lower()
        added_dup, _ = shelf.add_files([dup_upper, dup_lower])
        self.assertEqual(added_dup, 0)
        self.assertEqual(len(shelf.list_items()), 6)

        # 移除单条记录：不删除磁盘上的原文件
        removed = shelf.remove_file(str(sample_files[0]))
        self.assertTrue(removed)
        self.assertTrue(sample_files[0].exists(), "移除置物架记录绝不能删除原文件")
        self.assertEqual(len(shelf.list_items()), 5)

        # 模拟外部删除原文件后，list_items 显示 exists=False，但仍可从置物架移除记录
        sample_files[1].unlink()
        items_now = shelf.list_items()
        missing_entry = next(x for x in items_now if Path(x["path"]).name == sample_files[1].name)
        self.assertFalse(missing_entry["exists"])
        ok_open, err_msg = shelf.open_file(missing_entry["path"])
        self.assertFalse(ok_open)
        self.assertEqual(err_msg, "文件已移动或删除")
        self.assertTrue(shelf.remove_file(missing_entry["path"]))

        # 重启恢复验证
        shelf2 = ShelfService(path=shelf_path)
        self.assertEqual(len(shelf2.list_items()), 4)

        # 清空置物架：不删除磁盘上的原文件
        shelf2.clear()
        self.assertEqual(len(shelf2.list_items()), 0)
        for p in sample_files[2:]:
            self.assertTrue(p.exists(), "清空置物架绝不能删除原文件")

        # 损坏文件自动备份为 .corrupt.bak 且不崩溃
        shelf_path.write_text("{corrupt shelf json", encoding="utf-8")
        shelf3 = ShelfService(path=shelf_path)
        self.assertEqual(shelf3.list_items(), [])
        self.assertTrue(shelf_path.with_suffix(".corrupt.bak").exists())

    def test_12_clipboard_history_limits_pin_sensitive_and_corrupt_recovery(self):
        """测试文字剪贴板：30条未固定 + 20条固定上限、20000字符上限、去重保固定状态、敏感格式拦截与重启恢复。"""
        from wisland.services.clipboard_history import (
            ClipboardHistoryService,
            FMT_CAN_INCLUDE_HISTORY,
            FMT_EXCLUDE_MONITOR,
            FMT_VIEWER_IGNORE,
            MAX_CHARS,
            MAX_PINNED,
            MAX_RECENT,
            check_sensitive_formats,
        )

        clip_path = Path(self.tmpdir.name) / "clipboard.json"
        clip = ClipboardHistoryService(path=clip_path, start_watcher=False)

        # 1) 空文本、纯空白与超过 20,000 字符不记录
        self.assertFalse(clip.push(""))
        self.assertFalse(clip.push("   \n\t  "))
        self.assertFalse(clip.push("a" * (MAX_CHARS + 1)))
        self.assertTrue(clip.push("a" * MAX_CHARS))
        clip.clear(include_pinned=True)

        # 2) 自身从历史复制（from_history=True）不产生新变化
        clip.push("first", at="10:00")
        clip.push("second", at="10:01")
        self.assertFalse(clip.push("first", at="10:02", from_history=True))
        self.assertEqual([x["text"] for x in clip.list_items()], ["second", "first"])

        # 3) 固定与重复文本提升到最前且保留固定状态
        clip.toggle_pin(1)   # 固定 "first"
        self.assertTrue(clip.list_items()[1]["pinned"])
        clip.push("first", at="10:05")
        items = clip.list_items()
        self.assertEqual(items[0]["text"], "first")
        self.assertTrue(items[0]["pinned"], "重复复制已固定条目时应移到最前并保留 pinned=True")

        # 4) 验证固定上限 20 条 + 最近未固定上限 30 条（互不挤占名额，总计可达 50 条）
        clip.clear(include_pinned=True)
        for i in range(MAX_PINNED + 5):
            clip.push(f"pin_{i}")
            ok_pin, _ = clip.toggle_pin(0)
            if i < MAX_PINNED:
                self.assertTrue(ok_pin)
            else:
                self.assertFalse(ok_pin, "超过 20 条固定上限时应拒绝固定")

        for j in range(MAX_RECENT + 10):
            clip.push(f"recent_{j}")

        recent_cnt, pinned_cnt = clip.counts()
        self.assertEqual(pinned_cnt, MAX_PINNED)
        self.assertEqual(recent_cnt, MAX_RECENT)
        self.assertEqual(len(clip.list_items()), MAX_PINNED + MAX_RECENT)

        # 5) 重启恢复验证
        clip2 = ClipboardHistoryService(path=clip_path, start_watcher=False)
        self.assertEqual(clip2.counts(), (MAX_RECENT, MAX_PINNED))

        # 6) 清空未固定记录（保留固定项） vs 全部清空
        clip2.clear(include_pinned=False)
        self.assertEqual(clip2.counts(), (0, MAX_PINNED))
        clip2.clear(include_pinned=True)
        self.assertEqual(clip2.counts(), (0, 0))

        # 7) 密码管理器敏感格式判定验证
        self.assertTrue(check_sensitive_formats({FMT_EXCLUDE_MONITOR}))
        self.assertTrue(check_sensitive_formats({FMT_VIEWER_IGNORE}))
        self.assertTrue(check_sensitive_formats({FMT_CAN_INCLUDE_HISTORY}, can_include_dword=0))
        self.assertFalse(check_sensitive_formats({FMT_CAN_INCLUDE_HISTORY}, can_include_dword=1))
        self.assertFalse(check_sensitive_formats(set()))

        # 8) 损坏文件备份与恢复验证
        clip_path.write_text("not a valid json", encoding="utf-8")
        clip3 = ClipboardHistoryService(path=clip_path, start_watcher=False)
        self.assertEqual(clip3.list_items(), [])
        self.assertTrue(clip_path.with_suffix(".corrupt.bak").exists())

    def test_13_tools_page_and_subtitle_page_migration(self):
        """测试 SubtitlePage 保持 key='trans'、ToolsPage 注册与拖放过滤、隐藏时后台剪贴板不触发 UI 重建。"""
        from types import SimpleNamespace
        from PySide6 import QtCore
        from wisland.services.clipboard_history import ClipboardHistoryService
        from wisland.services.shelf import ShelfService
        from wisland.ui.pages.subtitle import SubtitlePage
        from wisland.ui.pages.tools import ToolsPage, extract_local_files

        self.assertEqual(SubtitlePage.key, "trans")
        self.assertEqual(SubtitlePage.title, "实时字幕")
        self.assertEqual(ToolsPage.key, "tools")
        self.assertEqual(ToolsPage.title, "工具")

        shelf = ShelfService(path=Path(self.tmpdir.name) / "shelf_ui.json")
        clip = ClipboardHistoryService(path=Path(self.tmpdir.name) / "clip_ui.json", start_watcher=False)
        ctx = SimpleNamespace(shelf=shelf, clipboard=clip)
        tpage = ToolsPage(ctx)
        try:
            # 工具页隐藏（active=False）时，后台剪贴板写入只标记 _clip_dirty，不立即重建 UI
            tpage.active = False
            clip.push("background copied text")
            self.assertTrue(tpage._clip_dirty)

            # 当切换到剪贴板标签并展示页面时，才刷新 UI
            tpage.switch_sub_tab("clipboard")
            tpage.on_show()
            self.assertFalse(tpage._clip_dirty)

            # 验证拖放过滤：仅接受真实本地文件，忽略纯文本与目录
            mime_text = QtCore.QMimeData()
            mime_text.setText("just plain text")
            self.assertEqual(extract_local_files(mime_text), [])

            test_file = Path(self.tmpdir.name) / "drop_test.log"
            test_file.write_text("log data", encoding="utf-8")
            mime_urls = QtCore.QMimeData()
            mime_urls.setUrls([
                QtCore.QUrl.fromLocalFile(str(test_file)),
                QtCore.QUrl.fromLocalFile(str(self.tmpdir.name)),  # 文件夹应被忽略
            ])
            extracted = extract_local_files(mime_urls)
            self.assertEqual(len(extracted), 1)
            self.assertEqual(Path(extracted[0]).name, "drop_test.log")
        finally:
            tpage.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
