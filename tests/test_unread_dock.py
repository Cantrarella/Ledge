# -*- coding: utf-8 -*-
"""岛旁未读快捷胶囊与前台自动清除测试。"""
from __future__ import annotations

import pathlib
import sys
import time

import pytest
from PySide6 import QtCore, QtGui, QtWidgets


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    return app

from wisland.core.config import Config
from wisland.services.notifications.controller import NotificationController
from wisland.services.notifications.foreground import ForegroundClientWatcher
from wisland.services.notifications.identity import WindowCandidate
from wisland.services.notifications.models import NotificationEvent
from wisland.ui.island import Island
from wisland.ui.page_registry import PageRegistry
from wisland.ui.pages.base import Page
from wisland.ui.unread_dock import DOCK_WIDTH, ICON_SIZE, MAX_DOCK_HEIGHT, UnreadDock


def _mouse_ev(ev_type: QtCore.QEvent.Type, pos: QtCore.QPointF,
              btn: QtCore.Qt.MouseButton = QtCore.Qt.MouseButton.NoButton,
              btns: QtCore.Qt.MouseButton = QtCore.Qt.MouseButton.NoButton) -> QtGui.QMouseEvent:
    return QtGui.QMouseEvent(
        ev_type, pos, pos, btn, btns,
        QtCore.Qt.KeyboardModifier.NoModifier,
        QtGui.QPointingDevice.primaryPointingDevice(),
    )


def _step_dock(dock: UnreadDock, steps: int = 80, dt: float = 0.016):
    for _ in range(steps):
        dock._last_tick = time.perf_counter() - dt
        dock._on_tick()


def _make_ctrl(tmp_path, candidates_by_source=None, focus_ok=True):
    c = Config(path=tmp_path / "cfg.json")
    cand_map = candidates_by_source or {}

    def _enum(src):
        return list(cand_map.get(src, []))

    def _focus(_hwnd):
        return bool(focus_ok)

    return NotificationController(enumerator=_enum, focuser=_focus), c


def test_01_single_multi_scroll_hover_press_and_no_mail(qt_app, tmp_path):
    dock = UnreadDock()
    dock.set_island_state(True, 1.0)

    # 邮件与 other 等其他系统通知绝不放入未读快捷胶囊
    dock.sync_counts({"mail": 5, "other": 3})
    assert dock.active_keys() == []
    assert not dock.isVisible()

    # 1) 单个未读：40×40 胶囊，完整复用主岛主题玻璃背景，无红点
    dock.sync_counts({"qq": 2, "mail": 3, "other": 1})
    _step_dock(dock)
    assert dock.active_keys() == ["qq"]
    assert dock.width() == DOCK_WIDTH == 40
    assert dock.height() == 40
    assert dock._capsule_bg_alpha.target == 1.0
    assert ICON_SIZE == 22

    # 悬停单项：放大 1.05，上移 1px；按压：0.94；不改变宽度
    dock.mouseMoveEvent(_mouse_ev(QtCore.QEvent.Type.MouseMove, QtCore.QPointF(20, 20)))
    assert dock._items[0].scale.target == 1.05
    assert dock._items[0].offset_y.target == -1.0
    assert dock.width() == 40

    # 点击只播按压反馈，绝不提前清除未读
    clicked: list[str] = []
    dock.item_clicked.connect(clicked.append)
    dock.mousePressEvent(
        _mouse_ev(QtCore.QEvent.Type.MouseButtonPress, QtCore.QPointF(20, 20),
                  QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton)
    )
    assert dock._items[0].scale.target == 0.94
    assert dock.active_keys() == ["qq"]
    dock.mouseReleaseEvent(
        _mouse_ev(QtCore.QEvent.Type.MouseButtonRelease, QtCore.QPointF(20, 20),
                  QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.NoButton)
    )
    assert clicked == ["qq"]
    assert dock.active_keys() == ["qq"]

    # 2) 两个未读：40×76 共享胶囊；悬停一项时其他项透明度降至 0.65
    dock.sync_counts({"qq": 2, "wechat": 1})
    _step_dock(dock)
    assert dock.active_keys() == ["qq", "wechat"]
    assert dock.size() == QtCore.QSize(40, 76)
    assert dock._capsule_bg_alpha.target == 1.0

    dock.mouseMoveEvent(_mouse_ev(QtCore.QEvent.Type.MouseMove, QtCore.QPointF(20, 16)))
    assert dock._items[0].scale.target == 1.05
    assert dock._items[1].opacity.target == 0.65

    # 3) 三个未读：40×112 胶囊
    dock._apply_cleaned_counts({"qq": 2, "wechat": 1, "extra1": 1})
    _step_dock(dock)
    assert dock.size() == QtCore.QSize(40, 112)

    # 4) 四个以上时最大高度保持 112px，滚轮消费且不透传
    dock._apply_cleaned_counts({"qq": 2, "wechat": 1, "extra1": 1, "extra2": 1})
    _step_dock(dock)
    assert dock.height() == MAX_DOCK_HEIGHT == 112
    wev = QtGui.QWheelEvent(
        QtCore.QPointF(20, 20),
        QtCore.QPointF(20, 20),
        QtCore.QPoint(0, 0),
        QtCore.QPoint(0, -120),
        QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        QtCore.Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    dock.wheelEvent(wev)
    assert wev.isAccepted()
    assert dock._scroll_y > 0.0
    dock.close()


def test_02_clear_animations_top_bottom_last_and_fail_shake(qt_app):
    dock = UnreadDock()
    dock.set_island_state(True, 1.0)
    dock.sync_counts({"qq": 1, "wechat": 1})
    _step_dock(dock)
    assert dock.height() == 76

    # 清除顶部项目（qq）：下方 wechat 向上补位，胶囊高度收缩到 40
    dock.sync_counts({"wechat": 1})
    qq_item = next(it for it in dock._items if it.source_key == "qq")
    wc_item = next(it for it in dock._items if it.source_key == "wechat")
    assert qq_item.removing is True
    assert qq_item.scale.target == 0.92
    assert qq_item.opacity.target == 0.0
    assert wc_item.y_pos.target == 2.0
    _step_dock(dock)
    assert dock.active_keys() == ["wechat"]
    assert dock.height() == 40

    # 再加上底部项并清除底部项目：胶囊高度从 76 回弹收缩到 40
    dock.sync_counts({"qq": 1, "wechat": 1})
    _step_dock(dock)
    assert dock.height() == 76
    dock.sync_counts({"qq": 1})
    _step_dock(dock)
    assert dock.height() == 40

    # 打开失败时触发晃动且不播放清除动画
    dock.trigger_fail_shake("qq")
    assert dock.active_keys() == ["qq"]
    assert dock._items[0].removing is False
    assert dock._items[0].shake_until > time.monotonic()

    # 最后一项清除：平滑淡出后再隐藏，不瞬间消失
    dock.sync_counts({})
    assert dock.isVisible() is True  # 淡出动画刚开始时不瞬间隐藏
    _step_dock(dock, steps=120)
    assert dock.isVisible() is False
    dock.close()


def test_03_foreground_watcher_auto_clear_and_click_open(qt_app, tmp_path):
    cands = {
        "qq": [WindowCandidate(hwnd=101, pid=1001, exe="qq.exe", started_at=1.0, visible=True, title="QQ")],
        "wechat": [WindowCandidate(hwnd=202, pid=2002, exe="weixin.exe", started_at=2.0, visible=True, title="微信")],
    }
    ctrl, _cfg = _make_ctrl(tmp_path, cands, focus_ok=True)
    ev_qq = NotificationEvent(event_id="q1", source_key="qq", app_name="QQ", app_identity="qq", title="hi")
    ev_wc = NotificationEvent(event_id="w1", source_key="wechat", app_name="微信", app_identity="wechat", title="hi")
    ctrl.ingest_event(ev_qq)
    ctrl.ingest_event(ev_wc)
    assert ctrl.counts["qq"] == 1 and ctrl.counts["wechat"] == 1
    assert ctrl.current_banner is not None and ctrl.current_banner.source_key == "qq"

    fg_state = {"hwnd": 999, "active": True}
    proc_map = {1001: ("qq.exe", 1.0), 2002: ("weixin.exe", 2.0), 9999: ("notepad.exe", 3.0)}
    pid_map = {101: 1001, 202: 2002, 999: 9999}

    watcher = ForegroundClientWatcher(
        foreground_hwnd_fn=lambda: fg_state["hwnd"],
        is_active_fn=lambda hw: fg_state["active"],
        pid_of_hwnd_fn=lambda hw: pid_map.get(hw, 0),
        process_info_fn=lambda pid: proc_map.get(pid, ("", None)),
        enumerator=lambda src: cands.get(src, []),
        ambiguous_fn=ctrl.is_source_ambiguous,
        use_win_hook=False,
    )
    watcher.foreground_matched.connect(ctrl.clear)
    ctrl.counts_changed.connect(watcher.update_unread_counts)
    watcher.update_unread_counts(ctrl.counts)
    assert watcher.is_watching is True

    # 1) 客户端仅后台运行（前台是 notepad）时不清除
    watcher.check_foreground_once()
    assert ctrl.counts["qq"] == 1 and ctrl.counts["wechat"] == 1

    # 2) 用户手动打开 QQ 后：QQ 未读和 QQ 横幅自动清除，微信保留
    fg_state["hwnd"] = 101
    watcher.check_foreground_once()
    assert ctrl.counts["qq"] == 0
    assert ctrl.counts["wechat"] == 1
    assert ctrl.current_banner is not None and ctrl.current_banner.source_key == "wechat"

    # 3) 用户手动打开微信后：微信未读和横幅清除，未读清零后自动停止监听
    fg_state["hwnd"] = 202
    watcher.check_foreground_once()
    assert ctrl.counts["wechat"] == 0
    assert ctrl.current_banner is None
    assert watcher.is_watching is False

    # 4) 多实例时不猜测清除
    cands["qq"].append(WindowCandidate(hwnd=102, pid=1002, exe="qq.exe", started_at=4.0, visible=True, title="QQ2"))
    ctrl.ingest_event(NotificationEvent(event_id="q2", source_key="qq", app_name="QQ", app_identity="qq", title="hi2"))
    watcher.update_unread_counts(ctrl.counts)
    fg_state["hwnd"] = 101
    watcher.check_foreground_once()
    assert ctrl.counts["qq"] == 1
    # 5) 配置自定义客户端 exe 路径时也能精确识别进程映射
    from wisland.core.config import cfg as global_cfg
    from wisland.services.notifications.foreground import _build_exe_map
    orig_qq_path = global_cfg.get("notify.open.qq", "")
    try:
        global_cfg.set("notify.open.qq", r"D:\Portable\MyCustomQQ.exe")
        exe_map = _build_exe_map()
        assert exe_map.get("mycustomqq.exe") == "qq"
        assert exe_map.get("qqnt.exe") == "qq"
        assert exe_map.get("weixin.exe") == "wechat"
    finally:
        global_cfg.set("notify.open.qq", orig_qq_path)
    watcher.stop()


def test_04_click_fail_keeps_unread_and_island_hover_hit_and_no_msg(qt_app, tmp_path):
    cands = {
        "qq": [WindowCandidate(hwnd=101, pid=1001, exe="qq.exe", started_at=1.0, visible=True, title="QQ")],
    }
    ctrl, _cfg = _make_ctrl(tmp_path, cands, focus_ok=False)
    ctrl.ingest_event(NotificationEvent(event_id="q1", source_key="qq", app_name="QQ", app_identity="qq", title="hi"))
    assert ctrl.open("qq") is False
    assert ctrl.counts["qq"] == 1

    # 旧通知带 account_ambiguous=True，但多余实例关闭、且过滤隐藏/无效窗口后只剩 1 个主客户端时：
    # 点击应重新枚举并成功聚焦清除未读
    cands_recheck = {
        "qq": [
            WindowCandidate(hwnd=101, pid=1001, exe="qq.exe", started_at=1.0, visible=True, title="QQ"),
            WindowCandidate(hwnd=102, pid=1002, exe="qq.exe", started_at=2.0, visible=False, title=""),
            WindowCandidate(hwnd=0, pid=1003, exe="qq.exe", started_at=3.0, visible=True, title="无效窗"),
        ]
    }
    ctrl_ok, _ = _make_ctrl(tmp_path, cands_recheck, focus_ok=True)
    ctrl_ok.ingest_event(
        NotificationEvent(
            event_id="q_amb",
            source_key="qq",
            app_name="QQ",
            app_identity="qq",
            title="hi",
            account_ambiguous=True,
        )
    )
    assert ctrl_ok.open("qq") is True
    assert ctrl_ok.counts["qq"] == 0

    # Island 展开态时：未读区位于主岛右侧且计入 _inside 命中范围，不触发收起
    reg = PageRegistry()
    for k, g, t in (("home", "\uE80F", "主页"), ("mail", "\uE715", "邮箱")):
        pg = type(f"DummyPage_{k}", (Page,), {"key": k, "glyph": g, "title": t})()
        reg.register(pg)
    isl = Island(reg.visible_pages(), registry=reg)
    isl.show()
    isl.expand("home")
    isl.unread_dock.sync_counts({"qq": 2, "wechat": 1})
    isl.unread_dock.set_island_state(True, 1.0)
    isl._place_unread_dock()
    qt_app.processEvents()

    assert isl.unread_dock.isVisible()
    gp = isl.unread_dock.mapToGlobal(QtCore.QPoint(20, 20))
    assert isl._inside(gp) is True

    # 确认页面注册表与 app.py 中无 msg / P["msg"] 依赖
    reg_src = (pathlib.Path(__file__).resolve().parents[1] / "wisland" / "ui" / "page_registry.py").read_text(encoding="utf-8")
    app_src = (pathlib.Path(__file__).resolve().parents[1] / "wisland" / "app.py").read_text(encoding="utf-8")
    assert "MessagesPage" not in reg_src
    assert 'P["msg"]' not in app_src
    isl.close()
