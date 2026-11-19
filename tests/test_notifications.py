# -*- coding: utf-8 -*-
"""Windows 通知控制层单元测试与 Win10/Win11 集成测试。"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

from PySide6 import QtWidgets

from wisland.core.config import ConfigDraft, cfg
from wisland.services.notifications.controller import NotificationController
from wisland.services.notifications.identity import (
    WindowCandidate,
    bind_client_target,
    classify_source,
    focus_event_target,
)
from wisland.services.notifications.models import (
    NotificationEvent,
    PermissionStatus,
    compute_content_hash,
    mask_sensitive_text,
)
from wisland.services.notifications.sources.taskbar import TaskbarFlashSource
from wisland.services.notifications.sources.windows import WindowsNotificationSource, raw_record_to_event
from wisland.services.notify import NotifyService
from wisland.ui.settings.notify import NotifySettingsPage


class _FakeWinBackend:
    """模拟 Windows 10 / 11 UserNotificationListener 后端。"""

    def __init__(self, perm: PermissionStatus = PermissionStatus.ALLOWED, os_major: int = 10):
        self.perm = perm
        self.os_major = os_major
        self.records: list[dict] = []
        self.released = False

    def is_supported(self) -> bool:
        return self.os_major >= 10

    def get_access_status(self) -> PermissionStatus:
        return self.perm if self.is_supported() else PermissionStatus.UNSUPPORTED

    async def request_access(self) -> PermissionStatus:
        return self.get_access_status()

    async def fetch_toasts(self) -> list[dict]:
        return list(self.records)

    def release(self):
        self.released = True


def _make_ev(
    nid: str = "1",
    app_id: str = "Tencent.Weixin",
    source_key: str = "wechat",
    app_name: str = "微信",
    title: str = "张三",
    body: str = "你好",
    creation_time: int = 1000,
    raw_kind: str = "winrt_toast",
    pid: int | None = 101,
    hwnd: int | None = 2001,
    started_at: float | None = 500.0,
) -> NotificationEvent:
    sender = title if title != app_name else ""
    return NotificationEvent(
        event_id=f"win:{app_id}:{nid}:{creation_time}",
        source_key=source_key,
        app_name=app_name,
        app_identity=app_id,
        pid=pid,
        window_handle=hwnd,
        process_started_at=started_at,
        title=title,
        sender=sender,
        body=body,
        created_at=time.time(),
        updated_at=time.time(),
        content_hash=compute_content_hash(title, sender, body),
        can_open=True,
        raw_kind=raw_kind,
        notification_id=nid,
        creation_time=creation_time,
    )


class TestNotifications(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def setUp(self):
        self._orig_notify = dict(cfg.get("notify") or {})
        cfg.set("notify.enabled", True, save=False)
        cfg.set("notify.wechat_enabled", True, save=False)
        cfg.set("notify.qq_enabled", True, save=False)
        cfg.set("notify.other_enabled", True, save=False)
        cfg.set("notify.flash_compat", True, save=False)
        cfg.set("notify.preview_enabled", False, save=False)
        cfg.set("notify.dnd_enabled", False, save=False)
        cfg.set("notify.display_seconds", 4, save=False)

    def tearDown(self):
        cfg.set("notify", self._orig_notify, save=False)

    def test_01_baseline_dedupe_update_collision_and_cache_limit(self):
        """测试基线不回放、同键去重、同ID更新不重复计数、创建时间区分、跨应用不串号与缓存上限。"""
        ctrl = NotificationController(max_dedupe=10)
        emitted: list[NotificationEvent] = []
        ctrl.notified.connect(lambda d: emitted.append(d["event"]))

        # 1) Baseline 快照不触发任何通知或未读
        old_ev = _make_ev(nid="10", body="启动前的旧消息", creation_time=100)
        ctrl.seed_baseline([old_ev])
        self.assertEqual(emitted, [])
        self.assertEqual(ctrl.counts, {})

        # 2) 相同 ID + 相同内容不重复
        self.assertEqual(ctrl.ingest_event(old_ev), "ignored")
        self.assertEqual(ctrl.counts, {})

        # 3) 相同 ID + 新内容 -> 原地更新，仅触发更新，不新增未读
        ev_new = _make_ev(nid="20", body="第一条", creation_time=200)
        self.assertEqual(ctrl.ingest_event(ev_new), "new")
        self.assertEqual(ctrl.counts.get("wechat"), 1)
        ev_upd = _make_ev(nid="20", body="第二条（原地更新）", creation_time=200)
        self.assertEqual(ctrl.ingest_event(ev_upd), "updated")
        self.assertEqual(ctrl.counts.get("wechat"), 1)
        self.assertEqual(ctrl.current_banner.body, "第二条（原地更新）")

        # 4) 相同 ID + 不同 creation_time 视为新通知
        ev_reused = _make_ev(nid="20", body="系统复用ID的新通知", creation_time=300)
        self.assertEqual(ctrl.ingest_event(ev_reused), "new")
        self.assertEqual(ctrl.counts.get("wechat"), 2)

        # 5) 不同应用相同 ID 不串号
        ev_qq = _make_ev(nid="20", app_id="Tencent.QQ", source_key="qq", app_name="QQ", body="QQ消息", creation_time=300)
        self.assertEqual(ctrl.ingest_event(ev_qq), "new")
        self.assertEqual(ctrl.counts.get("qq"), 1)
        self.assertEqual(ctrl.counts.get("wechat"), 2)

        # 6) 去重缓存有上限（max_dedupe=10）
        for i in range(25):
            ctrl.ingest_event(_make_ev(nid=f"bulk_{i}", body=f"m{i}", creation_time=400 + i))
        self.assertLessEqual(len(ctrl._dedupe), 10)
        ctrl.stop()

    def test_02_taskbar_flash_dedupe_and_independence(self):
        """测试任务栏闪烁不伪装正文、不与 Toast 重复计数，且微信/QQ 各自独立。"""
        proc_map = {
            1001: (111, "weixin.exe", 10.0),
            1002: (222, "qq.exe", 20.0),
        }
        flash_src = TaskbarFlashSource(proc_resolver=lambda h: proc_map.get(h, (None, "", None)))
        ctrl = NotificationController(flash_source=flash_src)

        # Toast 到来后，短时间内微信任务栏闪烁不得重复计数
        ctrl.ingest_event(_make_ev(nid="101", source_key="wechat", app_name="微信"))
        self.assertEqual(ctrl.counts.get("wechat"), 1)
        self.assertEqual(ctrl.on_flash(1001), "ignored")
        self.assertEqual(ctrl.counts.get("wechat"), 1)

        # QQ 独立不受微信闪烁/Toast 抑制，且正文为空、显示“有新消息”
        self.assertEqual(ctrl.on_flash(1002), "new")
        self.assertEqual(ctrl.counts.get("qq"), 1)
        qq_ev = ctrl.history[0]
        self.assertEqual(qq_ev.body, "")
        self.assertEqual(ctrl.format_banner_sub(qq_ev), "有新消息")
        ctrl.stop()

    def test_03_fifo_queue_dismiss_and_source_clear(self):
        """测试 FIFO 队列不互相覆盖、关闭横幅不清未读、点击来源只清对应未读。"""
        focused: list[int] = []
        cands = [WindowCandidate(hwnd=9001, pid=101, exe="weixin.exe", started_at=500.0, visible=True)]
        ctrl = NotificationController(
            enumerator=lambda _src: cands,
            focuser=lambda hw: focused.append(hw) or True,
        )

        ev1 = _make_ev(nid="1", source_key="wechat", app_name="微信", title="张三")
        ev2 = _make_ev(nid="2", source_key="qq", app_name="QQ", app_id="Tencent.QQ", title="李四")
        ev3 = _make_ev(nid="3", source_key="wechat", app_name="微信", title="王五")

        ctrl.ingest_event(ev1)
        ctrl.ingest_event(ev2)
        ctrl.ingest_event(ev3)

        # 当前展示 ev1，ev2/ev3 在队列中不被覆盖
        self.assertEqual(ctrl.current_banner.event_id, ev1.event_id)
        self.assertEqual(len(ctrl._queue), 2)
        self.assertEqual(ctrl.counts, {"wechat": 2, "qq": 1})

        # 关闭横幅只关闭显示并推进队列，绝不清未读
        ctrl.dismiss_current_banner()
        self.assertEqual(ctrl.current_banner.event_id, ev2.event_id)
        self.assertEqual(ctrl.counts, {"wechat": 2, "qq": 1})

        # 打开微信事件：聚焦窗口并只清除 wechat 未读，qq 未读保留
        self.assertTrue(ctrl.open_event(ev1))
        self.assertEqual(focused, [9001])
        self.assertEqual(ctrl.counts.get("wechat"), 0)
        self.assertEqual(ctrl.counts.get("qq"), 1)
        ctrl.stop()

    def test_04_identity_classification_pid_reuse_and_exit_states(self):
        """测试来源识别防误判、PID 复用防误绑、多实例无法识别账号提示、最小化/托盘/退出三态。"""
        self.assertEqual(classify_source("Tencent.Weixin")[0], "wechat")
        self.assertEqual(classify_source("Tencent.QQNT")[0], "qq")
        self.assertEqual(classify_source("QQExtension_Helper", "微信转账助手")[0], "other")

        # 1) 多实例且无 PID -> 标记 account_ambiguous，绝不猜测打开
        multi = [
            WindowCandidate(hwnd=11, pid=100, exe="weixin.exe", started_at=10.0, visible=True),
            WindowCandidate(hwnd=12, pid=200, exe="weixin.exe", started_at=20.0, visible=True),
        ]
        b_amb = bind_client_target("wechat", enumerator=lambda _s: multi)
        self.assertTrue(b_amb.account_ambiguous)
        self.assertFalse(b_amb.can_open)

        ev_amb = _make_ev(nid="99", pid=None, hwnd=None, started_at=None)
        ev_amb.account_ambiguous = True
        ok, msg = focus_event_target(ev_amb, enumerator=lambda _s: multi, focuser=lambda _h: True)
        self.assertFalse(ok)
        self.assertIn("无法识别账号", msg)

        # 2) PID 复用（相同 pid=101 但 started_at 已改变）-> 拒绝聚焦
        reused = [WindowCandidate(hwnd=2001, pid=101, exe="weixin.exe", started_at=999.0, visible=True)]
        ev_old = _make_ev(nid="1", pid=101, hwnd=2001, started_at=500.0)
        ok2, msg2 = focus_event_target(ev_old, enumerator=lambda _s: reused, focuser=lambda _h: True)
        self.assertFalse(ok2)
        self.assertIn("无法确认账号", msg2)

        # 3) 托盘隐藏窗（visible=False 但有窗口句柄）可恢复，客户端退出则明确提示
        tray = [WindowCandidate(hwnd=3001, pid=101, exe="weixin.exe", started_at=500.0, visible=False, title="微信")]
        focused = []
        ok3, _ = focus_event_target(
            _make_ev(nid="2", pid=101, hwnd=3001, started_at=500.0),
            enumerator=lambda _s: tray,
            focuser=lambda hw: focused.append(hw) or True,
        )
        self.assertTrue(ok3)
        self.assertEqual(focused, [3001])

        ok4, msg4 = focus_event_target(ev_old, enumerator=lambda _s: [], focuser=lambda _h: True)
        self.assertFalse(ok4)
        self.assertIn("已退出", msg4)

    def test_04b_main_window_selection_and_controller_focuser(self):
        """选择面积最大的真实主窗，并让生产控制器使用注入的窗口服务聚焦器。"""
        candidates = [
            WindowCandidate(hwnd=11, pid=101, exe="weixin.exe", started_at=500.0, visible=False, title="微信", area=100 * 100),
            WindowCandidate(hwnd=12, pid=101, exe="weixin.exe", started_at=500.0, visible=True, title="微信", area=900 * 700),
        ]
        binding = bind_client_target("wechat", enumerator=lambda _src: candidates)
        self.assertTrue(binding.can_open)
        self.assertEqual(binding.window_handle, 12)

        focused: list[int] = []
        ctrl = NotificationController(enumerator=lambda _src: candidates)
        ctrl.set_focuser(lambda hwnd: focused.append(hwnd) or True)
        ctrl.ingest_event(_make_ev(nid="main-window", pid=101, hwnd=12, started_at=500.0))
        self.assertTrue(ctrl.open("wechat"))
        self.assertEqual(focused, [12])
        self.assertEqual(ctrl.counts.get("wechat", 0), 0)
        ctrl.stop()

    def test_04c_hidden_wechat_uses_restore_hotkey_without_showwindow(self):
        """托盘隐藏微信通过配置热键恢复，禁止直接 ShowWindow 强制显示。"""
        from wisland.services import system

        class _FakeUser32:
            @staticmethod
            def IsWindow(_hwnd):
                return True

            @staticmethod
            def IsWindowVisible(_hwnd):
                return False

            @staticmethod
            def IsIconic(_hwnd):
                return False

            @staticmethod
            def ShowWindow(_hwnd, _cmd):
                raise AssertionError("hidden WeChat must not use ShowWindow")

        original_hotkeys = dict(cfg.get("apps.restore_hotkey") or {})
        original_u32 = system._u32
        original_pid = system._pid
        original_send = system.send_hotkey
        original_single = system.QtCore.QTimer.singleShot
        calls: list[str] = []
        try:
            cfg.set(
                "apps.restore_hotkey",
                {**original_hotkeys, "weixin.exe": "Ctrl+Alt+W"},
                save=False,
            )
            system._u32 = _FakeUser32()
            system._pid = lambda _hwnd: 101
            system.send_hotkey = lambda spec: calls.append(spec) or True
            system.QtCore.QTimer.singleShot = staticmethod(lambda _delay, _fn: None)
            svc = system.WindowService()
            svc._exe_for = lambda _pid: r"C:\Tencent\Weixin\Weixin.exe"
            svc._process_running = lambda exe: exe == "weixin.exe"

            self.assertTrue(svc.activate(3001))
            self.assertEqual(calls, ["Ctrl+Alt+W"])
        finally:
            cfg.set("apps.restore_hotkey", original_hotkeys, save=False)
            system._u32 = original_u32
            system._pid = original_pid
            system.send_hotkey = original_send
            system.QtCore.QTimer.singleShot = original_single

    def test_05_privacy_masking_and_preview_toggle(self):
        """测试默认隐藏正文与开启摘要后的敏感验证码脱敏。"""
        ev = _make_ev(nid="55", title="安全中心", body="您的登录验证码是 839201，请勿泄露。")
        cfg.set("notify.preview_enabled", False, save=False)
        self.assertEqual(NotificationController.format_banner_sub(ev), "安全中心")

        cfg.set("notify.preview_enabled", True, save=False)
        masked = NotificationController.format_banner_sub(ev)
        self.assertNotIn("839201", masked)
        self.assertIn("****", masked)

    def test_06_win10_win11_permission_integration_and_cleanup(self):
        """集成测试：Win10/Win11 权限允许/拒绝、基线建立、原地更新、设置页开关与退出清理。"""
        for os_ver in (10, 11):
            # A) 权限允许
            backend_ok = _FakeWinBackend(PermissionStatus.ALLOWED, os_major=os_ver)
            backend_ok.records = [{
                "notification_id": "1",
                "creation_time": 1000,
                "created_at": 100.0,
                "app_identity": "Tencent.Weixin",
                "app_name": "微信",
                "title": "历史联系人",
                "body": "旧消息",
            }]
            src_ok = WindowsNotificationSource(backend=backend_ok, wpndb_lookup=lambda _id: "")
            ctrl = NotificationController(win_source=src_ok)
            ctrl.start()

            deadline = time.monotonic() + 2.0
            while not src_ok._baseline_done and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.02)
            self.assertTrue(src_ok._baseline_done)
            self.assertEqual(ctrl.counts, {})

            # 注入新通知 -> 命中
            backend_ok.records.append({
                "notification_id": "2",
                "creation_time": 2000,
                "created_at": 200.0,
                "app_identity": "Tencent.QQNT",
                "app_name": "QQ",
                "title": "群消息",
                "body": "新到一条",
            })
            deadline = time.monotonic() + 2.5
            while ctrl.counts.get("qq", 0) == 0 and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.02)
            self.assertEqual(ctrl.counts.get("qq"), 1)

            # 停止监听 -> 线程退出且后端资源释放
            ctrl.stop()
            self.assertFalse(src_ok.is_running())
            self.assertTrue(backend_ok.released)

            # B) 权限拒绝 -> 状态明确 + 任务栏兼容仍可工作
            backend_denied = _FakeWinBackend(PermissionStatus.DENIED, os_major=os_ver)
            src_denied = WindowsNotificationSource(backend=backend_denied, wpndb_lookup=lambda _id: "")
            ctrl_d = NotificationController(
                win_source=src_denied,
                flash_source=TaskbarFlashSource(proc_resolver=lambda _h: (555, "qq.exe", 12.0)),
            )
            ctrl_d.start()
            deadline = time.monotonic() + 1.5
            while ctrl_d._perm_status != PermissionStatus.DENIED.value and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.02)
            self.assertEqual(ctrl_d._perm_status, PermissionStatus.DENIED.value)
            self.assertIn("拒绝", ctrl_d.status_snapshot()["permission_label"])
            self.assertEqual(ctrl_d.on_flash(888), "new")
            self.assertEqual(ctrl_d.counts.get("qq"), 1)
            ctrl_d.stop()

    def test_07_settings_page_and_notify_service_compat(self):
        """测试 NotifySettingsPage 草稿读写与 NotifyService 兼容层生命周期。"""
        svc = NotifyService(NotificationController(win_source=WindowsNotificationSource(backend=_FakeWinBackend())))
        page = NotifySettingsPage(ctx=type("Ctx", (), {"notify": svc})())
        draft = ConfigDraft(cfg.data)
        page.load_from_draft(draft)
        page.sw_preview.setChecked(True)
        page.sw_flash.setChecked(False)
        page.sp_display_s.setValue(6)
        page.save_to_draft(draft)
        self.assertTrue(draft.get("notify.preview_enabled"))
        self.assertFalse(draft.get("notify.flash_compat"))
        self.assertEqual(draft.get("notify.display_seconds"), 6)
        page.close()
        svc.stop()

        # 验证 wpndb 缺失时 raw_record_to_event 不会崩溃
        ev = raw_record_to_event({"notification_id": "1", "app_identity": "", "app_name": "未知应用"}, lambda _i: "")
        self.assertEqual(ev.source_key, "other")

        # 验证 mailcheck 与 app 入口所需兼容导出完整
        from wisland.services.mailcheck import MailCheckService
        from wisland.services.notify import thunderbird_profile, inbox_files
        from wisland.ui.compact import CompactView, H_IDLE, H_NOTIFY
        import wisland.app  # noqa: F401
        self.assertIsNotNone(MailCheckService)
        self.assertTrue(callable(thunderbird_profile))
        self.assertTrue(callable(inbox_files))

        # 验证收起态通知专用高度与 190-360px 宽度范围
        self.assertEqual(H_IDLE, 34)
        self.assertEqual(H_NOTIFY, 36)
        cv = CompactView()
        cv.show_activity({"type": "notify", "title": "QQ", "sub": "Liv-Bot"}, 4.0)
        nw, nh = cv.size_hint()
        self.assertEqual(nh, 36)
        self.assertGreaterEqual(nw, 190.0)
        self.assertLessEqual(nw, 360.0)
        cv.show_activity({"type": "notify", "title": "微信", "sub": "这是一条非常长的群聊通知摘要用于测试最大宽度上限"}, 4.0)
        nw2, nh2 = cv.size_hint()
        self.assertEqual(nh2, 36)
        self.assertEqual(nw2, 360.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
