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
        gen_page = win.leaf_pages[0]
        isl_page = win.leaf_pages[1]
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
        win.leaf_pages[0].cb_theme.set_value("dark")
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
        portable_exe = repo_root / "dist" / "Ledge" / "Ledge.exe"
        portable_zip = repo_root / "dist" / "Ledge-portable.zip"
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
            self.assertEqual(acts[0].text(), "展开 Ledge")
            self.assertEqual(acts[1].text(), "打开 Ledge 设置…")
            self.assertTrue(acts[2].isSeparator())
            self.assertEqual(acts[3].text(), "可穿透")
            self.assertTrue(acts[3].isCheckable())
            self.assertEqual(acts[4].text(), "在截图中显示")
            self.assertTrue(acts[4].isCheckable())
            self.assertEqual(acts[5].text(), "开机启动")
            self.assertTrue(acts[5].isCheckable())
            self.assertTrue(acts[6].isSeparator())
            self.assertEqual(acts[7].text(), "退出 Ledge")

            # 确保无子菜单、无旧维护项
            for a in acts:
                self.assertIsNone(a.menu())
                self.assertNotIn(a.text(), ("高级", "编辑配置 config.json", "重新加载配置", "打开数据目录", "主题", "展开方式", "参与截图"))

            # 2) 岛展开态动态文案刷新
            isl.expanded = True
            populate_tray_menu(menu, isl, lambda: None, lambda: None)
            self.assertEqual(menu.actions()[0].text(), "收起 Ledge")
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
            win.leaf_pages[0].sw_passthrough.setChecked(True, emit=True)
            self.assertTrue(win.is_dirty())
            self.assertTrue(win.btn_save.isEnabled())
            self.assertIn("有未保存修改", win.dirty_lbl.text())

            # 3) 校验错误就地显示在对应 SettingRow
            win.leaf_pages[0].ed_hotkey.setText("Bad++Hotkey")
            ok = win.save_settings()
            self.assertFalse(ok)
            self.assertFalse(win.leaf_pages[0].row_hotkey.err_lbl.isHidden())
            self.assertIn("热键", win.leaf_pages[0].row_hotkey.err_lbl.text())

            # 4) 检查天气页 API Key 占位符、说明与已保存城市（不直接显示经纬度，经纬度在 tooltip）
            pg_map = {pg.key: pg for pg in win.leaf_pages}
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

        # 预先写入含已废弃页面（msg）和未注册页面（如 ai_quota）的配置：
        # 加载时应清理 msg，同时只显示 4 个真实页面并保留 ai_quota 等未知字段
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
        for k, g, t in (("home", "\uE80F", "主页"), ("mail", "\uE715", "邮箱"),
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
            pg_map = {pg.key: pg for pg in win.leaf_pages}
            nav_pg = pg_map["pages_nav"]

            # 1) 设置中只列出 4 个真实页面，不出现 msg / ai_quota 等页面
            self.assertEqual(nav_pg.ordered_keys(), ["home", "mail", "weather", "trans"])

            # 2) 将 weather 移到第一位，隐藏 home，点击取消 -> 不修改任何配置，恢复原顺序与显示状态
            #    加载时已清理废弃的 msg，保留 ai_quota
            nav_pg.move_page("weather", -1)
            nav_pg.move_page("weather", -1)
            nav_pg.set_page_visible("home", False)
            self.assertEqual(nav_pg.ordered_keys()[0], "weather")
            win.cancel_and_close(prompt_if_dirty=False)
            self.assertEqual(c.get("pages.order"), ["home", "mail", "weather", "trans", "ai_quota"])
            self.assertNotIn("msg", c.get("pages.visible"))
            self.assertTrue(c.get("pages.visible.home"))
            self.assertEqual(isl.nav.keys(), ["home", "weather", "trans"])

            # 3) 重新打开设置，将 trans 移到首位，隐藏 home 和 mail，点击保存 -> 窗口保持打开，顺序与隐藏立即生效
            win.show()
            nav_pg.move_page("trans", -1)
            nav_pg.move_page("trans", -1)
            nav_pg.move_page("trans", -1)
            nav_pg.set_page_visible("home", False)
            nav_pg.set_page_visible("mail", False)
            saved = win.save_settings()
            self.assertTrue(saved)
            self.assertTrue(win.isVisible(), "保存设置后不应关闭设置窗口")

            reg.sync_from_config(c)
            isl.rebuild_pages(reg.visible_pages())

            # 验证岛体导航只包含可见的 trans 与 weather，且由于原当前页 home 被隐藏，自动切到第一个可见页 trans
            self.assertEqual(isl.nav.keys(), ["trans", "weather"])
            self.assertEqual(isl._page_key, "trans")
            # 滚轮只能在 trans 与 weather 之间切换，不能切到隐藏的 home 或 mail
            isl.nav.step_wheel(-120)
            self.assertEqual(isl.nav.current(), "weather")
            isl.nav.step_wheel(-120)
            self.assertEqual(isl.nav.current(), "weather")

            # 验证 Page 实例未重复创建
            after_ids = {k: id(p) for k, p in reg.pages_dict().items()}
            self.assertEqual(orig_ids, after_ids)

            # 验证未注册页面 ai_quota 在 config.json 中依然保留，废弃的 msg 已被清理
            disk_cfg = json.loads(self.cfg_path.read_text(encoding="utf-8"))
            self.assertNotIn("msg", disk_cfg["pages"]["order"])
            self.assertNotIn("msg", disk_cfg["pages"]["visible"])
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

    def test_14_header_centering_panel_sizes_and_no_nav_badge(self):
        """测试统一顶部栏日期时间严格居中、页面自定义尺寸策略以及底部导航不再绘制红点。"""
        from types import SimpleNamespace
        from PySide6 import QtCore
        from wisland.services.clipboard_history import ClipboardHistoryService
        from wisland.services.shelf import ShelfService
        from wisland.ui.header import PageHeader
        from wisland.ui.island import EXP_W, Island
        from wisland.ui.nav import NavBar
        from wisland.ui.pages.info import WeatherPage
        from wisland.ui.pages.subtitle import SubtitlePage
        from wisland.ui.pages.tools import ToolsPage
        from wisland.ui.widgets import PillButton

        # 1) 顶部栏严格居中测试（左右控件宽度不等时中心位置不偏移）
        hdr = PageHeader(ctx=None)
        hdr.resize(419, 30)
        hdr.add_left(PillButton("非常长的左侧按钮文案", height=24))
        hdr.add_right(PillButton("短", height=24))
        hdr._layout_parts()
        c_rect = hdr.center_lbl.geometry()
        self.assertLessEqual(abs((c_rect.x() + c_rect.width() / 2.0) - (hdr.width() / 2.0)), 1.0)

        # 2) 页面尺寸接口与 Island.expanded_size() 测试
        dummy_weather = SimpleNamespace(cities_updated=QtCore.QObject().destroyed, city_added=QtCore.QObject().destroyed, refresh=lambda: None)
        # 用真实 Signal 对象
        class _SigObj(QtCore.QObject):
            cities_updated = QtCore.Signal(list)
            city_added = QtCore.Signal(bool, str)
            segment = QtCore.Signal(int, str)
            live_zh = QtCore.Signal(int, str)
            final_zh = QtCore.Signal(int, str)
            partial = QtCore.Signal(str)
            level = QtCore.Signal(float)
            status = QtCore.Signal(str)
            error = QtCore.Signal(str)
            running_changed = QtCore.Signal(bool)
            downloading_changed = QtCore.Signal(bool)
            def refresh(self): pass
            def available(self): return (True, "")
            def toggle(self): pass

        sig = _SigObj()
        ctx = SimpleNamespace(
            weather=sig,
            live=sig,
            shelf=ShelfService(path=Path(self.tmpdir.name) / "s14.json"),
            clipboard=ClipboardHistoryService(path=Path(self.tmpdir.name) / "c14.json", start_watcher=False),
        )
        p_weather = WeatherPage(ctx)
        p_sub = SubtitlePage(ctx)
        p_tools = ToolsPage(ctx)

        self.assertEqual((p_weather.preferred_panel_width(), p_weather.preferred_panel_height()), (419, 232))
        self.assertEqual((p_sub.preferred_panel_width(), p_sub.preferred_panel_height()), (419, 185))
        self.assertEqual((p_tools.preferred_panel_width(), p_tools.preferred_panel_height()), (419, 140))

        isl = Island([p_weather, p_sub, p_tools])
        try:
            isl.show_page("weather")
            self.assertEqual(isl.expanded_size(), (419.0, 232.0))
            isl.show_page("trans")
            self.assertEqual(isl.expanded_size(), (419.0, 185.0))
            isl.show_page("tools")
            self.assertEqual(isl.expanded_size(), (419.0, 140.0))
            self.assertNotEqual(EXP_W, 419)
        finally:
            isl.close()

        # 3) 底部导航不再保存或绘制红点
        nav = NavBar([("home", "", "主页"), ("mail", "", "邮箱")])
        nav.set_badge("mail", 99)
        self.assertEqual(nav.badges, {})

    def test_15_life_todo_water_sit_and_reminders(self):
        """测试生活页三视图（待办、喝水、久坐）业务逻辑、跨天重置、重复规则、免打扰与损坏恢复。"""
        import datetime as dt
        from types import SimpleNamespace
        from wisland.core.config import cfg
        from wisland.services.health_reminders import HealthReminderService
        from wisland.services.todo import REPEAT_WORKDAYS, TodoService, done_for, reminder_due
        from wisland.services.water import WaterService
        from wisland.ui.pages.life import LifePage

        # 1) TodoService：排序、重复第二天自动恢复未完成、一次性/重复提醒只触发一次、损坏备份
        todo_path = Path(self.tmpdir.name) / "todo.json"
        todo = TodoService(path=todo_path)
        t_once = todo.add("买牛奶", due="2026-11-18 09:00", repeat=0)
        # 周一=0x01..周五=0x1F (工作日)
        t_rep = todo.add("写日报", due="2026-11-18 18:00", repeat=REPEAT_WORKDAYS)

        # 周三(2026-11-18) 勾选重复待办 -> 当天已完成，周四(2026-11-19) 自动恢复未完成；周六(2026-11-21) 不在计划内
        todo.toggle(t_rep["id"], day="2026-11-18")
        self.assertTrue(done_for(todo.get_by_id(t_rep["id"])[1], "2026-11-18"))
        self.assertFalse(done_for(todo.get_by_id(t_rep["id"])[1], "2026-11-19"))
        self.assertEqual(todo.progress("2026-11-18"), (1, 2))
        self.assertEqual(todo.progress("2026-11-21"), (0, 1))  # 周六只有一次性待办在计划中

        # 排序规则：今天未完成 -> 其他日期未完成 -> 已完成沉底
        order_wed = [it["id"] for _, it in todo.list_ordered("2026-11-18")]
        self.assertEqual(order_wed, [t_once["id"], t_rep["id"]])

        # 到点提醒只触发一次
        due1 = todo.pop_due_reminders(dt.datetime(2026, 11, 18, 9, 5))
        self.assertEqual([x["id"] for x in due1], [t_once["id"]])
        due2 = todo.pop_due_reminders(dt.datetime(2026, 11, 18, 9, 10))
        self.assertEqual(due2, [])
        # 重复待办 18:00 触发（即使当天已勾选完成则不再触发）
        self.assertFalse(reminder_due(todo.get_by_id(t_rep["id"])[1], "2026-11-18 18:05"))

        # 重启恢复与损坏备份
        todo2 = TodoService(path=todo_path)
        self.assertEqual(len(todo2.items), 2)
        todo_path.write_text("{corrupt todo", encoding="utf-8")
        todo3 = TodoService(path=todo_path)
        self.assertEqual(todo3.items, [])
        self.assertTrue(todo_path.with_suffix(".corrupt.bak").exists())

        # 2) WaterService：饮水累加、撤销、达标、跨天清零与损坏备份
        water_path = Path(self.tmpdir.name) / "water.json"
        water = WaterService(path=water_path)
        water.drink(250, hour=10)
        water.drink(250, hour=10)
        self.assertEqual(water.drunk_ml, 500)
        self.assertEqual(water.hours[10], 500)
        self.assertTrue(water.undo_last())
        self.assertEqual(water.drunk_ml, 250)
        self.assertTrue(water.ensure_today("2099-01-01"))
        self.assertEqual(water.drunk_ml, 0)
        water_path.write_text("{corrupt water", encoding="utf-8")
        water2 = WaterService(path=water_path)
        self.assertEqual(water2.drunk_ml, 0)
        self.assertTrue(water_path.with_suffix(".corrupt.bak").exists())

        # 3) HealthReminderService：提醒时段、午休免打扰、达标停止喝水提醒、久坐重置
        health = HealthReminderService(water, todo3)
        fired: list[tuple[str, str, str]] = []
        health.reminder_triggered.connect(lambda k, t, s: fired.append((k, t, s)))

        orig_lunch = cfg.get("life.lunch_rest")
        orig_ls = cfg.get("life.lunch_start")
        orig_le = cfg.get("life.lunch_end")
        orig_sh = cfg.get("life.remind_start_hour")
        orig_eh = cfg.get("life.remind_end_hour")
        try:
            cfg.set("life.lunch_rest", True, save=False)
            cfg.set("life.lunch_start", "12:00", save=False)
            cfg.set("life.lunch_end", "13:30", save=False)
            cfg.set("life.remind_start_hour", 9, save=False)
            cfg.set("life.remind_end_hour", 22, save=False)

            base_mono = 10000.0
            health.water_last_ts = base_mono
            health.sit_start_ts = base_mono

            # 午休时间 (12:30) 即使超过 60 分钟也不提醒
            health.tick(dt.datetime(2026, 11, 18, 12, 30), now_mono=base_mono + 4000, check_lock=False)
            self.assertEqual(fired, [])

            # 时段外 (23:00) 不提醒
            health.tick(dt.datetime(2026, 11, 18, 23, 0), now_mono=base_mono + 4000, check_lock=False)
            self.assertEqual(fired, [])

            # 正常时段 (14:00) 触发喝水与久坐提醒
            health.tick(dt.datetime(2026, 11, 18, 14, 0), now_mono=base_mono + 4000, check_lock=False)
            self.assertEqual({k for k, _, _ in fired}, {"water", "sit"})

            # 点击“起身了”重置久坐计时
            health.stood_up()
            self.assertFalse(health.sit_overdue)

            # 喝水达到每日目标后停止喝水提醒
            fired.clear()
            today_d = dt.date.today()
            water.drink(2500, hour=14)
            self.assertTrue(water.reached_goal())
            health.tick(dt.datetime(today_d.year, today_d.month, today_d.day, 15, 0), now_mono=base_mono + 8000, check_lock=False)
            self.assertNotIn("water", [k for k, _, _ in fired])
        finally:
            cfg.set("life.lunch_rest", orig_lunch, save=False)
            cfg.set("life.lunch_start", orig_ls, save=False)
            cfg.set("life.lunch_end", orig_le, save=False)
            cfg.set("life.remind_start_hour", orig_sh, save=False)
            cfg.set("life.remind_end_hour", orig_eh, save=False)

        # 4) LifePage UI 尺寸、动态标题、待办滚动条策略与久坐文案验证
        from PySide6 import QtCore
        ctx = SimpleNamespace(todo=todo3, water=water, health=health)
        lp = LifePage(ctx)
        self.assertEqual((lp.preferred_panel_width(), lp.preferred_panel_height()), (419, 140))
        self.assertEqual((lp.title_w._head, lp.title_w._tail), ("待办", "清单"))
        # 空待办时隐藏滚动条
        self.assertEqual(lp.todo_view.scroll.verticalScrollBarPolicy(), QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 超过 3 条待办时开启极细滚动条
        for idx in range(4):
            todo3.add(f"测试待办_{idx}")
        lp.todo_view.refresh()
        self.assertEqual(lp.todo_view.scroll.verticalScrollBarPolicy(), QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        lp.switch_view("water", animate=False)
        self.assertEqual(lp.tabs.current_key(), "water")
        self.assertEqual((lp.title_w._head, lp.title_w._tail), ("喝水", "提醒"))
        self.assertEqual(lp.water_view.btn_cfg.text, "设置")

        lp.switch_view("sit", animate=False)
        self.assertEqual(lp.tabs.current_key(), "sit")
        self.assertEqual((lp.title_w._head, lp.title_w._tail), ("久坐", "提醒"))
        # 关闭久坐提醒时显示“久坐提醒已关闭”
        orig_sit_on = cfg.get("life.sit_remind")
        cfg.set("life.sit_remind", False, save=False)
        lp.sit_view.refresh()
        self.assertEqual(lp.sit_view.lbl_sub.text(), "久坐提醒已关闭")
        self.assertEqual(lp.sit_view.lbl_unit.text(), "分钟")
        cfg.set("life.sit_remind", orig_sit_on, save=False)
        lp.close()

    def test_16_life_tools_polish_and_wheel_rule(self):
        """测试生活页按钮坐标一致、工具页置物架/剪贴板精修、两步清空与统一滚轮事件规则。"""
        from types import SimpleNamespace
        from PySide6 import QtCore, QtGui
        from wisland.services.clipboard_history import ClipboardHistoryService
        from wisland.services.health_reminders import HealthReminderService
        from wisland.services.shelf import ShelfService
        from wisland.services.todo import TodoService
        from wisland.services.water import WaterService
        from wisland.ui.island import Island
        from wisland.ui.page_registry import PageRegistry
        from wisland.ui.pages.life import LifePage
        from wisland.ui.pages.tools import ToolsPage
        from wisland.ui.widgets import WheelScrollArea

        # 1) 生活页按钮尺寸、撤销位置与喝水/久坐坐标一致性
        todo = TodoService(path=Path(self.tmpdir.name) / "todo.json")
        water = WaterService(path=Path(self.tmpdir.name) / "water.json")
        health = HealthReminderService(water, todo)
        lp = LifePage(SimpleNamespace(todo=todo, water=water, health=health))
        lp.resize(395, 98)
        lp.switch_view("water", animate=False)
        self.app.processEvents()
        self.assertGreaterEqual(lp.water_view.btn_main.minimumWidth(), 66)
        self.assertGreaterEqual(lp.water_view.btn_cfg.minimumWidth(), 60)
        self.assertEqual(lp.water_view.btn_row.height(), 22)
        self.assertIs(lp.water_view.btn_undo.parentWidget(), lp.water_view.sub_row)
        w_btn_y = lp.water_view.btn_row.y()
        w_prog_y = lp.water_view.prog_bar.y()

        lp.switch_view("sit", animate=False)
        self.app.processEvents()
        self.assertGreaterEqual(lp.sit_view.btn_main.minimumWidth(), 66)
        self.assertGreaterEqual(lp.sit_view.btn_cfg.minimumWidth(), 60)
        self.assertEqual(lp.sit_view.btn_row.y(), w_btn_y)
        self.assertEqual(lp.sit_view.prog_bar.y(), w_prog_y)
        lp.close()

        # 2) 工具页标签、顶部提示、单层拖放、两步清空与剪贴板滚动条策略
        shelf = ShelfService(path=Path(self.tmpdir.name) / "shelf.json")
        clip = ClipboardHistoryService(path=Path(self.tmpdir.name) / "clip.json", start_watcher=False)
        tp = ToolsPage(SimpleNamespace(shelf=shelf, clipboard=clip, open_settings_section=lambda _s: None))
        tp.resize(395, 98)
        tp.show()
        self.app.processEvents()

        self.assertEqual(tp.tab_bar.tabs[0], ("shelf", "置物架"))
        self.assertFalse(tp.btn_clear_bottom.isVisible())

        # 拖放层：隐藏主列表且不残留
        tp.set_drop_hover(True)
        self.app.processEvents()
        self.assertTrue(tp.drop_overlay.isVisible())
        self.assertTrue(tp.sub_stack.isHidden())
        tp.set_drop_hover(False)
        self.app.processEvents()
        self.assertFalse(tp.drop_overlay.isVisible())
        self.assertFalse(tp.sub_stack.isHidden())

        # 添加文件：Header 左侧临时显示提示，且右下角出现两步清空按钮
        sample = Path(self.tmpdir.name) / "demo.log"
        sample.write_text("abc", encoding="utf-8")
        shelf.add_files([str(sample)])
        tp.refresh_shelf()
        self.app.processEvents()
        self.assertEqual(tp.left_stack.currentIndex(), 1)
        self.assertIn("已添加", tp.header_notice_lbl.text())
        self.assertTrue(tp.btn_clear_bottom.isVisible())

        # 两步清空：第1次变为确认，第2次执行清空并隐藏按钮
        tp.btn_clear_bottom.clicked.emit()
        self.assertTrue(tp.btn_clear_bottom.confirming)
        self.assertEqual(len(shelf.paths()), 1)
        tp.btn_clear_bottom.clicked.emit()
        self.app.processEvents()
        self.assertEqual(len(shelf.paths()), 0)
        self.assertFalse(tp.btn_clear_bottom.isVisible())

        # 剪贴板：<=4 条隐藏滚动条，>4 条显示 2px 滚动条
        tp.switch_sub_tab("clipboard")
        for i in range(4):
            clip.push(f"clip_row_{i}")
        tp.refresh_clipboard()
        self.assertEqual(tp.clip_scroll.verticalScrollBarPolicy(), QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertTrue(tp.btn_clear_clip.isVisible())
        clip.push("clip_row_5")
        tp.refresh_clipboard()
        self.assertEqual(tp.clip_scroll.verticalScrollBarPolicy(), QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # 横向 WheelScrollArea 把垂直滚轮映射到横向滚动并始终消费事件
        horiz = WheelScrollArea(orientation="horizontal")
        horiz.resize(200, 70)
        horiz.inner.setMinimumWidth(600)
        horiz.show()
        self.app.processEvents()
        self.assertEqual(horiz.horizontalScrollBar().value(), 0)
        ev_wheel = QtGui.QWheelEvent(
            QtCore.QPointF(50, 30), QtCore.QPointF(50, 30),
            QtCore.QPoint(0, 0), QtCore.QPoint(0, -120),
            QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier,
            QtCore.Qt.ScrollPhase.NoScrollPhase, False,
        )
        horiz.wheelEvent(ev_wheel)
        self.assertTrue(ev_wheel.isAccepted())
        self.assertGreater(horiz.horizontalScrollBar().value(), 0)
        horiz.close()

        # 3) 统一滚轮规则：在岛内滚动区到顶/底时绝不触发页面切换，滚动区外才翻页
        lp2 = LifePage(SimpleNamespace(todo=todo, water=water, health=health))
        isl = Island([tp, lp2])
        try:
            isl.expand()
            isl.show_page("tools")
            tp.switch_sub_tab("clipboard")
            tp.refresh_clipboard()
            self.app.processEvents()
            cur_before = isl.stack.currentIndex()

            # 鼠标位于剪贴板内部滚动区（已在顶部），向上滚轮不应切换岛页面
            ev_inside = QtGui.QWheelEvent(
                QtCore.QPointF(20, 20), QtCore.QPointF(20, 20),
                QtCore.QPoint(0, 0), QtCore.QPoint(0, 120),
                QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier,
                QtCore.Qt.ScrollPhase.NoScrollPhase, False,
            )
            self.app.sendEvent(tp.clip_scroll.viewport(), ev_inside)
            self.assertEqual(isl.stack.currentIndex(), cur_before)

            # 鼠标位于 Header（滚动区外），滚轮允许切换页面
            ev_header = QtGui.QWheelEvent(
                QtCore.QPointF(100, 10), QtCore.QPointF(100, 10),
                QtCore.QPoint(0, 0), QtCore.QPoint(0, -120),
                QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier,
                QtCore.Qt.ScrollPhase.NoScrollPhase, False,
            )
            self.app.sendEvent(tp.header, ev_header)
            self.assertNotEqual(isl.stack.currentIndex(), cur_before)
        finally:
            isl.close()
        lp2.close()
        tp.close()

    def test_17_life_reminder_icons_queue_compact_expanded_and_timers(self):
        """测试喝水/久坐 SVG 图标、主题映射、收起/展开岛内提醒队列、计时隔离与不触发系统通知。"""
        from types import SimpleNamespace
        from PySide6 import QtCore, QtGui
        from wisland.services.health_reminders import HealthReminderService
        from wisland.services.todo import TodoService
        from wisland.services.water import WaterService
        from wisland.ui.icons import _SVG_CACHE, draw_life_svg_icon, get_life_svg_renderer
        from wisland.ui.island import Island
        from wisland.ui.pages.life import LifePage
        from wisland.ui.theme import theme

        _SVG_CACHE.clear()

        # 1) 图标加载、缓存与主题映射（dark/rouge -> dark SVG; light/sky -> light SVG）
        r_wd = get_life_svg_renderer("water", True)
        r_wl = get_life_svg_renderer("water", False)
        r_sd = get_life_svg_renderer("sit", True)
        r_sl = get_life_svg_renderer("sit", False)
        self.assertIsNotNone(r_wd)
        self.assertIsNotNone(r_wl)
        self.assertIsNotNone(r_sd)
        self.assertIsNotNone(r_sl)
        self.assertIsNone(get_life_svg_renderer("todo", True))
        self.assertIs(get_life_svg_renderer("water", True), r_wd)

        # 导出 22px 实际尺寸预览图并校验透明背景
        out_dir = Path(r"C:\Users\User\.gemini\antigravity\brain\8d8a99c7-d155-4d86-87b9-c28576636299\scratch")
        out_dir.mkdir(parents=True, exist_ok=True)
        preview = QtGui.QImage(120, 40, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        preview.fill(QtCore.Qt.GlobalColor.transparent)
        pp = QtGui.QPainter(preview)
        pp.fillRect(0, 0, 60, 40, QtGui.QColor("#181920"))
        pp.fillRect(60, 0, 60, 40, QtGui.QColor("#f3f5f9"))
        self.assertTrue(draw_life_svg_icon(pp, QtCore.QRectF(6, 9, 22, 22), "water", True))
        self.assertTrue(draw_life_svg_icon(pp, QtCore.QRectF(32, 9, 22, 22), "sit", True))
        self.assertTrue(draw_life_svg_icon(pp, QtCore.QRectF(66, 9, 22, 22), "water", False))
        self.assertTrue(draw_life_svg_icon(pp, QtCore.QRectF(92, 9, 22, 22), "sit", False))
        pp.end()
        self.assertTrue(preview.save(str(out_dir / "life-icons-22px-preview.png")))

        # 2) 队列不覆盖、不触发系统通知、收起/展开态均可看见、点击跳转对应生活视图
        todo = TodoService(path=Path(self.tmpdir.name) / "todo17.json")
        water = WaterService(path=Path(self.tmpdir.name) / "water17.json")
        health = HealthReminderService(water, todo)
        lp = LifePage(SimpleNamespace(todo=todo, water=water, health=health))
        isl = Island([lp])
        clicked_kinds: list[str] = []
        isl.life_reminder_clicked.connect(lambda k: (clicked_kinds.append(k), isl.expand("life"), lp.switch_view(k)))

        try:
            # 队列去重与顺序：先喝水、后久坐，绝不覆盖喝水；同 kind 不重复排队
            self.assertTrue(isl.show_life_reminder("water", "该喝水啦", "每次 250 ml · 今日已喝 0 ml", duration=8.0))
            self.assertFalse(isl.show_life_reminder("water", "该喝水啦", "重复", duration=8.0))
            self.assertTrue(isl.show_life_reminder("sit", "该起身活动一下了", "已连续久坐 60 分钟", duration=8.0))
            self.assertEqual(isl.current_life_reminder()["kind"], "water")
            self.assertEqual(isl.compact.activity["type"], "life-reminder")
            self.assertEqual(isl.compact.activity["kind"], "water")

            # 尺寸与收起态渲染截图（dark/light × water/sit）
            def _save_compact_shot(mode: str, fname: str):
                theme.set_mode(mode)
                cw, ch = isl.compact.size_hint()
                isl.sw.snap(cw)
                isl.sh.snap(ch)
                isl._update_geometry(force=True)
                self.app.processEvents()
                pix = QtGui.QPixmap(int(cw) + 24, int(ch) + 20)
                pix.fill(QtGui.QColor("#111318") if theme.dark else QtGui.QColor("#eef1f7"))
                p = QtGui.QPainter(pix)
                p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
                body_r = QtCore.QRectF(12, 10, cw, ch)
                path = QtGui.QPainterPath()
                path.addRoundedRect(body_r, ch / 2.0, ch / 2.0)
                p.fillPath(path, theme.tint_brush(body_r))
                p.strokePath(path, QtGui.QPen(theme.outline(), 1.2))
                isl.compact._paint_activity(p, body_r, 1.0)
                p.end()
                self.assertTrue(pix.save(str(out_dir / fname)))

            _save_compact_shot("dark", "life-compact-water-dark.png")
            _save_compact_shot("light", "life-compact-water-light.png")

            # 结束当前喝水提醒 -> 自动推进显示久坐提醒
            isl.dismiss_life_reminder(advance=True)
            self.assertEqual(isl.current_life_reminder()["kind"], "sit")
            _save_compact_shot("dark", "life-compact-sit-dark.png")
            _save_compact_shot("light", "life-compact-sit-light.png")

            # 展开态提醒可见性、不改变 419×140 尺寸、不遮挡齿轮/圆点
            orig_size = isl.expanded_size()
            isl.expand("life")
            isl.content.show()
            isl._place_life_banner()
            self.app.processEvents()
            self.assertEqual(isl.expanded_size(), orig_size)
            self.assertTrue(isl._life_banner.isVisibleTo(isl.content))
            self.assertFalse(isl._life_banner.geometry().intersects(lp.gear_btn.geometry()))
            self.assertFalse(isl._life_banner.geometry().intersects(isl.nav.geometry()))

            for mode, fname in (("dark", "life-expanded-dark.png"), ("light", "life-expanded-light.png")):
                theme.set_mode(mode)
                self.app.processEvents()
                pix = QtGui.QPixmap(isl.content.size())
                pix.fill(QtGui.QColor("#181920") if theme.dark else QtGui.QColor("#f3f5f9"))
                isl.content.render(pix)
                self.assertTrue(pix.save(str(out_dir / fname)))

            # 点击提醒跳转到对应生活页视图，并自动关闭浮层恢复原页面
            isl._on_life_banner_clicked("sit")
            self.assertEqual(clicked_kinds, ["sit"])
            self.assertEqual(lp.tabs.current_key(), "sit")
            self.assertIsNone(isl.current_life_reminder())
            self.assertFalse(isl._life_banner.isVisible())

            # 3) 计时规则隔离：喝一口只重置喝水；起身了只重置久坐；重启从零开始（无持久化字段）
            health.water_last_ts = 100.0
            health.sit_start_ts = 100.0
            lp.water_view._on_drink()
            self.assertGreater(health.water_last_ts, 100.0)
            self.assertEqual(health.sit_start_ts, 100.0)
            lp.sit_view._on_stand()
            self.assertGreater(health.sit_start_ts, 100.0)
        finally:
            theme.set_mode("light")
            isl.close()
            lp.close()


class ThemeDecouplingAndClassicSkinTests(unittest.TestCase):
    """岛体皮肤与设置窗口主题彻底解耦 + 经典纯黑岛体皮肤回归测试。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_config_defaults_and_validation_for_settings_window_theme(self):
        from wisland.core.config import (
            DEFAULTS,
            VALID_SETTINGS_THEMES,
            VALID_THEMES,
            validate_config,
        )

        self.assertEqual(DEFAULTS["settings_window"]["theme"], "light")
        self.assertIn("classic", VALID_THEMES)
        self.assertEqual(VALID_SETTINGS_THEMES, ("light", "dark"))

        # 严格校验拒绝 auto / classic / rouge / sky
        for bad in ("auto", "classic", "rouge", "sky", "unknown"):
            raw = {"island": {"theme": "classic"}, "settings_window": {"theme": bad, "extra_keep": 42}}
            ok, errs, _ = validate_config(raw, strict=True)
            self.assertFalse(ok)
            self.assertTrue(any("设置窗口主题" in e for e in errs))

            # 非严格模式回退到 light，绝不从 island.theme 推导，且保留未知字段
            _, _, norm = validate_config(raw, strict=False)
            self.assertEqual(norm["island"]["theme"], "classic")
            self.assertEqual(norm["settings_window"]["theme"], "light")
            self.assertEqual(norm["settings_window"]["extra_keep"], 42)

    def test_theme_instances_are_isolated_and_auto_luminance_never_leaks(self):
        from wisland.ui.theme import (
            ISLAND_THEME_NAMES,
            SETTINGS_THEME_NAMES,
            island_theme,
            settings_theme,
            theme,
        )

        self.assertIs(theme, island_theme)
        self.assertIsNot(island_theme, settings_theme)
        self.assertIn(("classic", "经典纯黑"), ISLAND_THEME_NAMES)
        self.assertEqual(SETTINGS_THEME_NAMES, [("light", "浅色"), ("dark", "深色")])

        orig_isl = island_theme.mode
        orig_set = settings_theme.mode
        try:
            island_theme.set_mode("light")
            settings_theme.set_mode("light")
            isl_hits = []
            set_hits = []
            island_theme.changed.connect(lambda: isl_hits.append(island_theme.pal_name))
            settings_theme.changed.connect(lambda: set_hits.append(settings_theme.pal_name))

            # 改岛体皮肤不影响 settings_theme
            island_theme.set_mode("classic")
            self.assertTrue(island_theme.solid)
            self.assertTrue(island_theme.dark)
            self.assertEqual(settings_theme.pal_name, "light")
            self.assertFalse(settings_theme.dark)
            self.assertEqual(set_hits, [])

            # settings_theme 拒绝 auto / classic / rouge / sky
            for bad in ("auto", "classic", "rouge", "sky"):
                settings_theme.set_mode(bad)
                self.assertEqual(settings_theme.pal_name, "light")

            # 改 settings_theme 不影响 island_theme
            settings_theme.set_mode("dark")
            self.assertEqual(settings_theme.pal_name, "dark")
            self.assertTrue(settings_theme.dark)
            self.assertEqual(island_theme.pal_name, "classic")

            # auto 岛体随背景亮度切换，不影响 settings_theme
            island_theme.set_mode("auto")
            island_theme._apply("light")
            island_theme._lum = 0.9
            island_theme._flip_since = None
            island_theme.feed_luminance(0.1)
            island_theme._flip_since = -1e9
            island_theme._lum = 0.1
            island_theme.feed_luminance(0.1)
            self.assertEqual(island_theme.pal_name, "dark")
            self.assertEqual(settings_theme.pal_name, "dark")

            # settings_theme.feed_luminance 永远是空操作
            settings_theme.set_mode("light")
            settings_theme.feed_luminance(0.0)
            self.assertEqual(settings_theme.pal_name, "light")
        finally:
            island_theme.set_mode(orig_isl)
            settings_theme.set_mode(orig_set)

    def test_settings_window_and_classic_island_decoupled_ui_and_screenshots(self):
        import tempfile
        from wisland.core.config import Config
        from wisland.ui.island import Island
        from wisland.ui.page_registry import PageRegistry
        from wisland.ui.pages.home import HomePage
        from wisland.ui.pages.mail import MailPage
        from wisland.ui.pages.quota import QuotaPage
        from wisland.ui.settings.general import GeneralSettingsPage
        from wisland.ui.settings_window import SettingsWindow
        from wisland.ui.theme import island_theme, resolve_theme, settings_theme

        orig_isl = island_theme.mode
        orig_set = settings_theme.mode
        win = None
        isl = None
        try:
            with tempfile.TemporaryDirectory() as td:
                shot_dir = Path(td) / "shots"
                shot_dir.mkdir(parents=True, exist_ok=True)
                cfg_path = Path(td) / "config.json"
                mgr = Config(cfg_path)
                win = SettingsWindow(config_obj=mgr)
                win.show()
                self.app.processEvents()

                gen_pg = next(pg for pg in win.leaf_pages if isinstance(pg, GeneralSettingsPage))
                self.assertIs(resolve_theme(gen_pg), settings_theme)
                self.assertIs(resolve_theme(gen_pg.cb_theme), settings_theme)
                self.assertIs(resolve_theme(gen_pg.cb_settings_theme), settings_theme)

                isl_vals = [gen_pg.cb_theme.itemData(i) for i in range(gen_pg.cb_theme.count())]
                set_vals = [gen_pg.cb_settings_theme.itemData(i) for i in range(gen_pg.cb_settings_theme.count())]
                self.assertIn("classic", isl_vals)
                self.assertEqual(set_vals, ["light", "dark"])

                # 4 种组合依次切换并保存截图
                combos = [
                    ("classic", "light", "decoupled-classic-island-light-settings.png"),
                    ("light", "dark", "decoupled-light-island-dark-settings.png"),
                    ("rouge", "light", "decoupled-rouge-island-light-settings.png"),
                    ("sky", "dark", "decoupled-sky-island-dark-settings.png"),
                ]
                for isl_mode, set_mode, fname in combos:
                    gen_pg.cb_theme.set_value(isl_mode)
                    gen_pg.cb_settings_theme.set_value(set_mode)
                    self.assertTrue(win.save_settings())
                    island_theme.set_mode(mgr.get("island.theme", "auto"))
                    self.app.processEvents()
                    self.assertEqual(island_theme.mode, isl_mode)
                    self.assertEqual(settings_theme.mode, set_mode)
                    self.assertEqual(settings_theme.dark, set_mode == "dark")

                    pix = win.grab()
                    self.assertTrue(pix.save(str(shot_dir / fname)))

                # 验证 classic 纯黑岛体渲染（纯黑不透明底，无毛玻璃透底）
                from PySide6 import QtCore, QtGui
                island_theme.set_mode("classic")

                class _Sig(QtCore.QObject):
                    changed = QtCore.Signal(object)
                    running_changed = QtCore.Signal(bool)
                    volume_changed = QtCore.Signal(float, bool)

                sig = _Sig()
                mock_ctx = type(
                    "MockCtx",
                    (),
                    {
                        "media": type("M", (), {"changed": sig.changed, "state": lambda *_: {}, "previous": lambda *_: None, "play_pause": lambda *_: None, "next": lambda *_: None})(),
                        "audio": type("A", (), {"volume_changed": sig.volume_changed, "toggle_mute": lambda *_: None, "muted": lambda *_: False})(),
                        "live": type("L", (), {"running_changed": sig.running_changed, "is_running": lambda *_: False})(),
                        "open_settings": lambda *a, **k: None,
                    },
                )()
                reg = PageRegistry()
                home_pg = HomePage(mock_ctx)
                quota_pg = QuotaPage(mock_ctx)
                mail_pg = MailPage(mock_ctx)
                reg.register(home_pg)
                reg.register(quota_pg)
                reg.register(mail_pg)
                isl = Island([home_pg, quota_pg, mail_pg], registry=reg)
                isl.show()
                self.app.processEvents()

                # 测试 _paint_glass 在 classic 下绘制纯黑 #000000
                glass_img = QtGui.QImage(200, 60, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
                glass_img.fill(QtCore.Qt.GlobalColor.white)
                gp = QtGui.QPainter(glass_img)
                rect_path = QtGui.QPainterPath()
                rect_path.addRoundedRect(QtCore.QRectF(10, 10, 180, 40), 20, 20)
                isl._paint_glass(gp, rect_path, crossfade=False)
                gp.end()
                center_px = glass_img.pixelColor(100, 30)
                self.assertEqual((center_px.red(), center_px.green(), center_px.blue()), (0, 0, 0))

                # 截图：classic 收起态
                self.assertTrue(isl.grab().save(str(shot_dir / "classic-island-compact.png")))

                # 截图：classic 展开态 - 主页 / AI 额度 / 邮箱
                for key, fname in (
                    ("home", "classic-island-home.png"),
                    ("quota", "classic-island-quota.png"),
                    ("mail", "classic-island-mail.png"),
                ):
                    isl.expand(key)
                    isl.content.show()
                    self.app.processEvents()
                    pix = QtGui.QPixmap(isl.content.size())
                    pix.fill(QtGui.QColor("#000000"))
                    isl.content.render(pix)
                    self.assertTrue(pix.save(str(shot_dir / fname)))
        finally:
            if isl is not None:
                isl.close()
            if win is not None:
                win.close()
            island_theme.set_mode(orig_isl)
            settings_theme.set_mode(orig_set)


class SettingsConsolidationAndTrayToggleTests(unittest.TestCase):
    """7 个一级分类合并、二级标签、深链兼容、防重复 load/save 与托盘收起修复回归测试。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_01_seven_groups_subtabs_deep_links_and_single_load_save(self):
        import tempfile
        from wisland.ui.settings_window import LEAF_TO_GROUP, SettingsGroupPage

        with tempfile.TemporaryDirectory() as td:
            c = Config(path=Path(td) / "config.json")
            win = SettingsWindow(ctx=None, config_obj=c)
            win.show()
            self.app.processEvents()
            try:
                # 1) 固定 7 个一级分类、11 个叶子页面
                self.assertEqual(len(win.pages), 7)
                self.assertEqual(len(win.leaf_pages), 11)
                self.assertEqual(
                    [t for _, _, t, _ in win._group_specs],
                    ["外观与岛体", "页面与导航", "消息与邮件", "天气", "AI 与字幕", "工具与生活", "数据与关于"],
                )
                self.assertEqual(len(win.nav_buttons), 7)

                # 2) 无双层纵向 QScrollArea 嵌套
                for grp in win.pages:
                    if isinstance(grp, SettingsGroupPage):
                        outer_scrolls = [sa for sa in grp.findChildren(QtWidgets.QScrollArea) if sa.parent() is grp]
                        self.assertEqual(outer_scrolls, [])

                # 3) 所有旧叶子 key 深链均正确定位到对应一级分类与二级标签
                for leaf_key, (grp_key, exp_leaf) in LEAF_TO_GROUP.items():
                    win.switch_category(leaf_key)
                    self.app.processEvents()
                    self.assertTrue(win.nav_buttons[grp_key].selected)
                    cur = win.stack.currentWidget()
                    if isinstance(cur, SettingsGroupPage):
                        self.assertEqual(cur.current_leaf().key, exp_leaf)
                    else:
                        self.assertEqual(cur.key, exp_leaf)

                # 4) 切换二级标签不改变窗口尺寸、不丢失草稿、不自动保存
                win.resize(820, 600)
                orig_sz = win.size()
                win.switch_category("general")
                gen_pg = win.leaf_pages[0]
                gen_pg.sw_passthrough.setChecked(True, emit=True)
                self.assertTrue(win.is_dirty())
                win.switch_category("island_pet")
                self.app.processEvents()
                self.assertEqual(win.size(), orig_sz)
                self.assertTrue(win.is_dirty())
                self.assertFalse(c.get("island.passthrough"))  # 未点保存不得落盘

                # 5) 每次 _load_all_from_draft / collect_to_draft 只调用每个叶子页面一次
                load_counts = {pg.key: 0 for pg in win.leaf_pages}
                save_counts = {pg.key: 0 for pg in win.leaf_pages}
                for pg in win.leaf_pages:
                    orig_l = pg.load_from_draft
                    orig_s = pg.save_to_draft
                    pg.load_from_draft = lambda d, k=pg.key, fn=orig_l: (load_counts.__setitem__(k, load_counts[k] + 1), fn(d))[1]
                    pg.save_to_draft = lambda d, k=pg.key, fn=orig_s: (save_counts.__setitem__(k, save_counts[k] + 1), fn(d))[1]

                win._load_all_from_draft()
                self.assertTrue(all(v == 1 for v in load_counts.values()))
                for k in save_counts:
                    save_counts[k] = 0
                win.collect_to_draft()
                self.assertTrue(all(v == 1 for v in save_counts.values()))
            finally:
                win.close()

    def test_02_tray_menu_toggle_never_passes_false_and_collapses_pinned(self):
        from wisland.app import populate_tray_menu

        class _FakeIsland:
            def __init__(self):
                self.expanded = False
                self._pinned = False
                self.locks: set[str] = set()
                self.calls: list[tuple[str, object]] = []
                self._page_key = "quota"

            def pinned(self) -> bool:
                return self._pinned

            def set_pinned(self, on: bool):
                self._pinned = bool(on)
                self.calls.append(("set_pinned", on))

            def expand(self, page=None):
                self.calls.append(("expand", page))
                if page is False:
                    raise AssertionError("page 不得接收 QAction.triggered 的 False 参数")
                self.expanded = True

            def collapse(self):
                self.calls.append(("collapse", None))
                self.expanded = False

            def toggle(self, page: str | None = None):
                self.calls.append(("toggle", page))
                if page is False:
                    raise AssertionError("toggle 不得接收 QAction.triggered 的 False 参数")
                if self.expanded and (page is None or page == self._page_key):
                    if self.pinned():
                        self.set_pinned(False)
                    self.collapse()
                else:
                    self.expand(page)
                    self.locks.add("pin")

            def passthrough(self) -> bool:
                return False

            def capture_visible(self) -> bool:
                return False

        isl = _FakeIsland()
        menu = QtWidgets.QMenu()
        try:
            # 1) 收起态 -> 文案“展开 Ledge”，act.trigger()（底层发射 triggered(False)）触发展开且不传 False
            populate_tray_menu(menu, isl, lambda: None, lambda: None)
            act = menu.actions()[0]
            self.assertEqual(act.text(), "展开 Ledge")
            act.trigger()
            self.assertTrue(isl.expanded)
            self.assertEqual(isl._page_key, "quota")

            # 2) 展开态 -> 刷新菜单文案“收起 Ledge”，act.trigger() 立即收起
            populate_tray_menu(menu, isl, lambda: None, lambda: None)
            act = menu.actions()[0]
            self.assertEqual(act.text(), "收起 Ledge")
            act.trigger()
            self.assertFalse(isl.expanded)
            self.assertEqual(isl._page_key, "quota")

            # 3) 常驻展开态 (pinned=True) -> 触发“收起 Ledge”先取消常驻再收起
            isl.expanded = True
            isl._pinned = True
            populate_tray_menu(menu, isl, lambda: None, lambda: None)
            act = menu.actions()[0]
            self.assertEqual(act.text(), "收起 Ledge")
            act.trigger()
            self.assertFalse(isl._pinned)
            self.assertFalse(isl.expanded)
            self.assertNotIn("menu", isl.locks)
        finally:
            menu.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
