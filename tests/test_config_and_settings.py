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
            wth_page = win.pages[2]
            self.assertEqual(wth_page.ed_api_key.edit.placeholderText(), "粘贴 API Key（可选）")
            self.assertEqual(wth_page.row_api.sub_lbl.text(), "留空时使用免费的 Open-Meteo 服务")
            wth_page._cities = [{"name": "杭州", "region": "浙江", "lat": 30.2741, "lon": 120.1551}]
            wth_page._rebuild_cities_ui()
            city_row_widget = wth_page.cities_lay.itemAt(0).widget()
            self.assertIn("30.2741", city_row_widget.toolTip())

            # 5) 检查数据页危险按钮属性
            data_page = win.pages[5]
            self.assertTrue(data_page.btn_reset.danger)
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
