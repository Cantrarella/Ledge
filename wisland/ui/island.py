# -*- coding: utf-8 -*-
"""灵动岛主窗口。

几何有三种模式：
- anchored：吸附在某条屏幕边上，贴边一侧与屏幕边缘通过两侧"表面张力"内凹圆角相连；
- drag：长按后跟随鼠标（弹簧跟随产生拖拽惯性），本体按速度变成泪滴形；
         离开原边缘时液桥被拉长、变细，超过阈值后断开，边缘残留的小水滴缩回；
         靠近任意边缘时会重新"伸出"液桥；
- snap：松手后吸附到最近的边，垂直方向欠阻尼弹簧带回弹，冲过边缘时转化为挤压形变。

窗口本身是固定大小的透明分层窗口，跟着本体移动；透明区域不接收鼠标。
毛玻璃 = 截取身后画面（本窗口已排除在截图之外）-> 缩小模糊 -> 纹理画刷填充形状。
"""
from __future__ import annotations

import ctypes
import math
import time
from ctypes import wintypes

from PySide6 import QtCore, QtGui, QtWidgets

from ..core.branding import DISPLAY_NAME
from ..core.config import cfg
from .anim import Spring, clamp, lerp, smooth
from .compact import CompactView
from .glass import Backdrop, exclude_from_capture
from .island_overlays import (
    BlankDoubleClick as _BlankDoubleClick,
    LifeReminderBanner as _LifeReminderBanner,
    Veil as _Veil,
)
from .nav import NavBar
from .shape import body_path, body_points, deform, drip_path, edge_corners, neck_path, poly_path
from .unread_dock import DOCK_WIDTH, UnreadDock
from .theme import theme
from .win32_hooks import MOD, MOD_NOREPEAT, RightClickHook as _RightClickHook, parse_hotkey

Qt = QtCore.Qt
user32 = ctypes.WinDLL("user32")

WIN_W, WIN_H = 900, 680          # 窗口（逻辑像素），需容纳展开尺寸 + 液桥 + 阴影
EXP_W = 640
PAD = 12
NAV_H = 18
FILLET = 13.0                    # 贴边两侧内凹圆角
BREAK = 64.0                     # 液桥拉断距离
JOIN = 30.0                      # 靠近到这个距离时液桥伸出
EDGE_MARGIN = 6

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
WM_HOTKEY = 0x0312
WM_DISPLAYCHANGE = 0x007E
WM_POWERBROADCAST = 0x0218
WM_DPICHANGED = 0x02E0
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
HSHELL_FLASH = 0x8006

user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetForegroundWindow.restype = wintypes.HWND
user32.RegisterWindowMessageW.restype = wintypes.UINT


class Island(QtWidgets.QWidget):
    expanded_changed = QtCore.Signal(bool)
    hotkey = QtCore.Signal(str)
    flash = QtCore.Signal(int)
    wheel_compact = QtCore.Signal(int)
    menu_requested = QtCore.Signal(QtCore.QPoint)
    lock_changed = QtCore.Signal(bool)
    passthrough_changed = QtCore.Signal(bool)
    pin_changed = QtCore.Signal(bool)
    mode_changed = QtCore.Signal(str)

    play_pause_requested = QtCore.Signal()      # 收起态音乐视图的播放 / 暂停键
    notify_banner_clicked = QtCore.Signal()     # 点击收起态通知横幅
    notify_banner_dismissed = QtCore.Signal()   # 关闭收起态通知横幅（不清未读）
    life_reminder_clicked = QtCore.Signal(str)  # 点击生活提醒 ("water"|"sit")
    resumed_from_sleep = QtCore.Signal()        # 系统从睡眠/休眠唤醒

    def __init__(self, pages: list, peak_fn=None, registry=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setWindowTitle(DISPLAY_NAME)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.resize(WIN_W, WIN_H)
        self.peak_fn = peak_fn or (lambda: 0.0)

        self.backdrop = Backdrop()
        self.compact = CompactView()
        self.registry = registry
        self.pages = list(pages)

        # ---- 几何状态 ----
        self.edge: str = cfg.get("island.edge", "top")
        if self.edge not in ("top", "bottom", "left", "right"):
            self.edge = "top"
        raw_pos = cfg.get("island.pos", 0.5)
        self.pos_frac: float = clamp(float(0.5 if raw_pos is None else raw_pos), 0.0, 1.0)
        self._hooked_screens: set[int] = set()
        self.scr: QtGui.QScreen = self._pick_screen()
        self._hook_screen_signals(self.scr)
        self.compact.set_edge(self.edge)
        self.compact.on_state_changed = self._on_compact_state_changed
        self.mode = "anchored"
        self.expanded = False
        cw, ch = self.compact.size_hint(avail_max_w=self._avail_max_banner_w())
        self.sw = Spring(cw, 300, 0.62, 0.2)
        self.sh = Spring(ch, 300, 0.62, 0.2)
        self.fx = Spring(0, 900, 0.78, 0.2)
        self.fy = Spring(0, 900, 0.78, 0.2)
        self.along = Spring(0, 260, 0.72, 0.2)
        self.gap = Spring(0, 320, 0.40, 0.2)
        self.neck_edge: str | None = self.edge
        self.neck_s = Spring(1, 520, 1.0, 0.01)
        self.drips: list[dict] = []
        self.content_a = Spring(0, 700, 1.0, 0.01)
        self.compact_a = Spring(1, 700, 1.0, 0.01)
        self.drag_off = QtCore.QPointF()
        self.locks: set[str] = set()
        self._t0 = time.perf_counter()
        self._last = self._t0
        self._last_cap = 0.0
        self._win_origin = QtCore.QPoint(0, 0)
        self.path = QtGui.QPainterPath()
        self.body = QtCore.QRectF()
        self._hover_since = None
        self._out_since = None
        self._press: QtCore.QPointF | None = None
        self._press_ctx: tuple | None = None
        self._page_reveal_seq: int = 0
        self._pending_page_idx: int | None = None
        self._shown_page_idx: int | None = None
        self._pending_call_on_show: bool = False
        self._anim_w0: float = float(cw)
        self._anim_h0: float = float(ch)
        self._fs_hidden = False
        self._prev_fg = None
        self._registered_hotkeys: dict[int, str] = {}
        self._rhook = _RightClickHook(self)
        self._mask_rect = None
        self._floor_t: dict = {}
        self.poke = Spring(0, 380, 0.55, 0.004)
        self._poke_pt = QtCore.QPointF()
        self._poke_sm = QtCore.QPointF()          # 平滑后的按压位置（凹陷跟着鼠标滑动）
        self._floor_on: dict = {}
        self._side_clamp_h: float = float(ch)
        self._collapsing_from_panel: bool = False
        self._bd_shown = None
        self._bd_prev = None
        self._bd_fade_t0 = 0.0
        self._shell_msg = 0
        self._dirty = QtCore.QRect()

        # ---- 展开内容 ----
        self.content = QtWidgets.QWidget(self)
        self.nav = NavBar([(pg.key, pg.glyph, pg.title) for pg in pages], self.content, NAV_H)
        self.stack = QtWidgets.QStackedWidget(self.content)
        for pg in pages:
            self.stack.addWidget(pg)
            pg.island = self
        lay = QtWidgets.QVBoxLayout(self.content)
        lay.setSpacing(8)
        self._content_lay = lay
        self._nav_bottom = None
        self._place_nav()
        self.nav.changed.connect(self.show_page)
        self.nav.drag_requested.connect(self.begin_drag)
        self.content.hide()
        self._life_queue: list[dict] = []
        self._active_life_reminder: dict | None = None
        self._life_timer = QtCore.QTimer(self, singleShot=True, timeout=self._on_life_reminder_timeout)
        self._life_banner = _LifeReminderBanner(self.content)
        self._life_banner.clicked_kind.connect(self._on_life_banner_clicked)
        self._life_banner.dismissed.connect(lambda: self.dismiss_life_reminder(advance=True))
        self.veil = _Veil(self)
        self.unread_dock = UnreadDock(self)
        self.unread_dock.layout_changed.connect(self._place_unread_dock)
        self._opacity: QtWidgets.QGraphicsOpacityEffect | None = None
        self._collapse_snap: QtGui.QImage | None = None
        self._collapse_snap_t0: float = 0.0
        self._collapse_snap_size: tuple[float, float] = (0.0, 0.0)
        default = cfg.get("island.last_tab") or cfg.get("island.default_tab", "home")
        self._page_key = default if default in self.nav.keys() else self.nav.keys()[0]
        self.nav.set_current(self._page_key)
        self.stack.setCurrentIndex(self.nav.keys().index(self._page_key))

        # ---- 计时器 ----
        self.timer = QtCore.QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.poll = QtCore.QTimer(self, interval=25, timeout=self._poll_hover)
        self.lp_timer = QtCore.QTimer(self, singleShot=True, interval=420, timeout=self._long_press)
        self._click_timer = QtCore.QTimer(self, singleShot=True, timeout=self._single_click)
        self._dbl_filter = _BlankDoubleClick(self)
        app_inst = QtWidgets.QApplication.instance()
        if app_inst is not None:
            app_inst.installEventFilter(self._dbl_filter)
            if hasattr(app_inst, "screenAdded"):
                app_inst.screenAdded.connect(self._on_screens_changed)
            if hasattr(app_inst, "screenRemoved"):
                app_inst.screenRemoved.connect(self._on_screens_changed)
            if hasattr(app_inst, "primaryScreenChanged"):
                app_inst.primaryScreenChanged.connect(self._on_screens_changed)
        self.backdrop.ready.connect(self._on_backdrop)
        theme.changed.connect(self.update)

        self._place_initial()

    # ================================================================ 屏幕 / 位置
    def _pick_screen(self) -> QtGui.QScreen:
        screens = list(QtGui.QGuiApplication.screens() or [])
        i = int(cfg.get("island.screen", 0) or 0)
        if 0 <= i < len(screens):
            return screens[i]
        prim = QtGui.QGuiApplication.primaryScreen()
        if prim is not None:
            return prim
        return screens[0] if screens else None  # type: ignore[return-value]

    def _hook_screen_signals(self, scr: QtGui.QScreen | None):
        if scr is None:
            return
        sid = id(scr)
        if sid in self._hooked_screens:
            return
        self._hooked_screens.add(sid)
        for sig_name in ("availableGeometryChanged", "geometryChanged", "logicalDotsPerInchChanged"):
            sig = getattr(scr, sig_name, None)
            if sig is not None:
                try:
                    sig.connect(self._on_screens_changed)
                except Exception:
                    pass

    def _ensure_valid_screen(self) -> QtGui.QScreen | None:
        screens = list(QtGui.QGuiApplication.screens() or [])
        if self.scr is None or (screens and self.scr not in screens):
            self.scr = self._pick_screen()
            self._hook_screen_signals(self.scr)
        return self.scr

    def _on_compact_state_changed(self, _full_state: tuple):
        """当收起态布局方向、模式或横幅实例切换时，立即作废未完成的按压手势。"""
        self._press = None
        self._press_ctx = None

    def _on_screens_changed(self, *_args):
        """显示器热插拔、主屏切换、分辨率或 DPI 变化时，保持当前有效目标屏幕或回退可用屏幕并清空旧毛玻璃帧。"""
        screens = list(QtGui.QGuiApplication.screens() or [])
        if self.scr is None or (screens and self.scr not in screens):
            self.scr = self._pick_screen()
        self._hook_screen_signals(self.scr)
        self._bd_shown = None
        self._bd_prev = None
        self.neck_edge = self.edge
        if self.expanded and not self.can_fit_expanded():
            if self.pinned():
                self.set_pinned(False)
            self.collapse()
        self._place_nav()
        self._update_geometry(force=True)
        self._kick()

    def _eff_win_size(self, sg: QtCore.QRect | None = None) -> tuple[int, int]:
        """当屏幕逻辑尺寸小于 900×680 时，动态收缩顶层容器窗口，防止 clamp 反转与跨屏 DPI 污染。"""
        sg = sg if sg is not None else self._screen_geom()
        ew = max(120, min(WIN_W, int(sg.width())))
        eh = max(120, min(WIN_H, int(sg.height())))
        return ew, eh

    def _avail_max_banner_w(self, R: QtCore.QRectF | None = None, edge: str | None = None) -> float:
        """计算当前屏幕可用区允许的唯一横幅最大宽度（下限为 36px 竖条宽度）。"""
        R = R if R is not None else self._avail()
        e = edge if edge is not None else self.edge
        fillet_pad = FILLET if e in ("left", "right") else (2.0 * FILLET)
        return max(36.0, float(R.width() - 2.0 * EDGE_MARGIN - fillet_pad))

    def can_fit_expanded(self, pw: float | None = None, ph: float | None = None) -> bool:
        """检查当前屏幕可用区是否能完整放下目标展开面板。"""
        if pw is None or ph is None:
            pw, ph = self.expanded_size()
        R = self._avail()
        if self.edge in ("left", "right"):
            need_w = float(pw) + FILLET + EDGE_MARGIN
            need_h = float(ph) + 2.0 * (FILLET + EDGE_MARGIN)
        else:
            need_w = float(pw) + 2.0 * (FILLET + EDGE_MARGIN)
            need_h = float(ph) + FILLET + EDGE_MARGIN
        return R.width() >= need_w and R.height() >= need_h

    def _resolve_drag_screen(self, gpos: QtCore.QPoint | None, c: QtCore.QPointF) -> QtGui.QScreen:
        """拖动期与松手瞬间统一以光标位置优先解析目标显示器。"""
        scr = None
        if gpos is not None:
            scr = QtGui.QGuiApplication.screenAt(gpos)
        if scr is None:
            scr = QtGui.QGuiApplication.screenAt(c.toPoint())
        if scr is None:
            scr = self._ensure_valid_screen()
        return scr

    def _switch_active_screen(self, new_scr: QtGui.QScreen, gcur: QtCore.QPoint | None = None):
        """跨屏进入新显示器时，在同一帧内切换目标屏幕、断开旧屏液桥并同步 DPI 坐标系。"""
        if new_scr is None or new_scr is self.scr:
            return
        self.scr = new_scr
        self._hook_screen_signals(self.scr)
        if self.neck_edge:
            self._break_neck()
        self.drips.clear()
        self._bd_shown = None
        self._bd_prev = None
        if gcur is not None and self.mode == "drag":
            cp = QtCore.QPointF(gcur) + self.drag_off
            self.fx.snap(cp.x())
            self.fy.snap(cp.y())

    def _avail(self) -> QtCore.QRectF:
        scr = self._ensure_valid_screen()
        if scr is None:
            return QtCore.QRectF(0, 0, 1920, 1080)
        try:
            r = QtCore.QRectF(scr.availableGeometry())
            return r if (r.width() > 0 and r.height() > 0) else QtCore.QRectF(0, 0, 1920, 1080)
        except RuntimeError:
            self.scr = self._pick_screen()
            return QtCore.QRectF(self.scr.availableGeometry()) if self.scr else QtCore.QRectF(0, 0, 1920, 1080)

    def _screen_geom(self) -> QtCore.QRect:
        scr = self._ensure_valid_screen()
        if scr is None:
            return QtCore.QRect(0, 0, 1920, 1080)
        try:
            r = scr.geometry()
            return r if (r.width() > 0 and r.height() > 0) else QtCore.QRect(0, 0, 1920, 1080)
        except RuntimeError:
            self.scr = self._pick_screen()
            return self.scr.geometry() if self.scr else QtCore.QRect(0, 0, 1920, 1080)

    def _line(self, edge: str, R: QtCore.QRectF | None = None) -> float:
        R = R or self._avail()
        return {"top": R.top(), "bottom": R.bottom(), "left": R.left(), "right": R.right()}[edge]

    def _along_range(self, edge: str, w: float, h: float) -> tuple[float, float]:
        R = self._avail()
        if edge in ("top", "bottom"):
            half = w / 2 + FILLET + EDGE_MARGIN
            return R.left() + half, R.right() - half
        half = h / 2 + FILLET + EDGE_MARGIN
        return R.top() + half, R.bottom() - half

    def _side_clamp_height(self, edge: str, h: float, update_state: bool = True) -> float:
        """左右侧边在收起态下的沿边夹紧高度：
        - 横幅显示期间（orientation == 'horizontal'）：在已有停靠态下锁定横幅弹出时的竖条锚定高度（banner_anchor_height），
          防止通知显示中首次收到天气导致横幅跳位；在拖拽松手回弹（snap）中则按当前最新竖条高度（vertical_rest_height）预留空间；
        - 横幅回收与纯竖条自身高度变化（如首次收到天气 92→136 或增减未读点）：取 max(当前实际高度 h, 已锁定的 _side_clamp_h)，
          并在达到最终竖条高度后锁定为 vertical_rest_height()，防止弹簧过冲回落引起底角二次抖动。
        """
        if edge not in ("left", "right") or self.expanded:
            if update_state:
                self._side_clamp_h = float(h)
            return float(h)
        orientation, _m = self.compact.layout_state()
        if orientation == "horizontal":
            if self.mode == "snap":
                rest_h = self.compact.vertical_rest_height()
                if update_state:
                    self.compact.pin_banner_anchor_height(rest_h)
                    self._side_clamp_h = max(float(h), rest_h)
                return max(float(h), rest_h)
            val = max(float(h), self.compact.banner_anchor_height())
            if update_state:
                self._side_clamp_h = val
            return val
        rest_h = self.compact.vertical_rest_height()
        if self._collapsing_from_panel:
            if not self.sw.rest or not self.sh.rest:
                val = float(h)
                if update_state:
                    self._side_clamp_h = val
                return val
            if update_state:
                self._collapsing_from_panel = False
                self._side_clamp_h = rest_h
            return rest_h
        if self.compact.has_outgoing_horizontal(self.sw.x) or self.sw.x > 36.5:
            val = min(max(float(h), float(self._side_clamp_h)), max(float(self._side_clamp_h), rest_h))
            if update_state:
                self._side_clamp_h = val
            return val
        if not self.sh.rest:
            val = float(h)
            if update_state:
                self._side_clamp_h = val
            return val
        if update_state:
            self.compact._banner_pinned_rest_h = None
            self._side_clamp_h = rest_h
        return rest_h

    def _anchor_along(self, w: float, h: float, update_state: bool = True) -> float:
        R = self._avail()
        if self.edge in ("top", "bottom"):
            a = R.left() + self.pos_frac * R.width()
            clamp_h = h
        else:
            a = R.top() + self.pos_frac * R.height()
            clamp_h = self._side_clamp_height(self.edge, h, update_state=update_state)
        lo, hi = self._along_range(self.edge, w, clamp_h)
        return clamp(a, lo, hi) if lo <= hi else (lo + hi) / 2

    def _center_on_edge(self, edge: str, along: float, gap: float, w: float, h: float) -> QtCore.QPointF:
        line = self._line(edge)
        if edge == "top":
            return QtCore.QPointF(along, line + gap + h / 2)
        if edge == "bottom":
            return QtCore.QPointF(along, line - gap - h / 2)
        if edge == "left":
            return QtCore.QPointF(line + gap + w / 2, along)
        return QtCore.QPointF(line - gap - w / 2, along)

    def _place_initial(self):
        self._update_geometry(force=True)

    # ================================================================ 展开 / 收起
    def _nav_bpad(self) -> int:
        return 10 if self.edge == "bottom" else 6

    def _target_page_index(self) -> int:
        idx = self._pending_page_idx if self._pending_page_idx is not None else self.stack.currentIndex()
        return int(clamp(idx, 0, max(0, len(self.pages) - 1)))

    def _page_panel_size(self, pg) -> tuple[float, float]:
        pw = pg.preferred_panel_width()
        ph = pg.preferred_panel_height()
        w = float(pw) if pw is not None else float(EXP_W)
        if ph is not None:
            return w, float(ph)
        return w, PAD + pg.preferred_height() + 6 + NAV_H + self._nav_bpad()

    def expanded_size(self) -> tuple[float, float]:
        """打开时的尺寸：直接读取待显现页（或当前页）声明的最终面板尺寸 (panel_width × panel_height)。"""
        pg = self.pages[self._target_page_index()]
        return self._page_panel_size(pg)

    def target_size(self) -> tuple[float, float]:
        if self.expanded:
            return self.expanded_size()
        return self.compact.size_hint(avail_max_w=self._avail_max_banner_w())

    def _panel_size_ready(self, w: float, h: float) -> bool:
        """按宽高变化方向判定外壳是否已就绪：变大等到足以容纳面板，变小等到收敛至目标附近。"""
        if w >= self._anim_w0 - 0.5:
            w_ok = self.sw.x >= w - 1.0
        else:
            w_ok = (self.sw.x >= w - 1.0) and (self.sw.x <= w + 1.5)

        if h >= self._anim_h0 - 0.5:
            h_ok = self.sh.x >= h - 1.0
        else:
            h_ok = (self.sh.x <= h + 1.5) and (self.sh.x >= h - 1.0)
        return bool(w_ok and h_ok)

    def _content_shown(self) -> bool:
        """检查展开内容层是否已处于显示态（兼容未 show() 顶层窗口的离屏测试与实机窗口）。"""
        return not self.content.isHidden()

    def _reveal_pending_page(self):
        """当外壳尺寸就绪时显现目标页面并开放点击；仅跨页或初次展开时触发 on_show()。"""
        if not self.expanded:
            return
        idx = self._target_page_index()
        call_on_show = self._pending_call_on_show
        self._pending_page_idx = None
        self._pending_call_on_show = False
        if self.stack.currentIndex() != idx:
            self.stack.setCurrentIndex(idx)
        self._shown_page_idx = idx
        tw, th = self.target_size()
        self._floor_t["w"] = tw
        self._floor_t["h"] = th
        self._floor_on["w"] = True
        self._floor_on["h"] = True
        self.content_a.snap(0)
        self.content_a.to(1, 700, 1.0)
        lbody = self.body if not self.body.isEmpty() else None
        self.content.setGeometry(self._content_rect(tw, th, lbody))
        self.content.clearMask()
        self._mask_rect = None
        self.content.show()
        self._place_life_banner()
        self.veil.setGeometry(self.content.geometry())
        self.veil.alpha = 1.0
        self.veil.show()
        self.veil.raise_()
        if call_on_show and 0 <= idx < len(self.pages):
            self._defer_show(self.pages[idx], self._page_reveal_seq)

    def expand(self, page: str | None = None):
        if self.mode != "anchored" or self._fs_hidden:
            return
        keys = self.nav.keys()
        want_key = page or self._page_key
        target_idx = keys.index(want_key) if want_key in keys else self.stack.currentIndex()
        target_idx = int(clamp(target_idx, 0, max(0, len(self.pages) - 1)))
        pw, ph = self._page_panel_size(self.pages[target_idx])
        if not self.can_fit_expanded(pw, ph):
            return

        if self.expanded:
            if page:
                self.show_page(page)
            self._kick()
            return

        self._page_reveal_seq += 1
        self._page_key = self.pages[target_idx].key
        self.nav.set_current(self._page_key)
        self.stack.setCurrentIndex(target_idx)
        self._pending_page_idx = target_idx
        self._shown_page_idx = None
        self._pending_call_on_show = True
        self._anim_w0, self._anim_h0 = float(self.sw.x), float(self.sh.x)
        self._floor_t.clear()
        self._floor_on.clear()

        self.expanded = True
        self._collapsing_from_panel = False
        self._collapse_snap = None
        self.compact_a.to(0, 900, 1.0)
        self.poke.snap(0.0)                       # 展开时清掉按压形变
        w, h = self.target_size()
        self.sw.to(w, 420, 0.62)
        self.sh.to(h, 420, 0.62)
        self.content_a.snap(0)
        self.content.hide()
        self.veil.hide()
        self.content.setGeometry(self._content_rect(w, h))   # 先隐藏，形状就绪后再显示
        if self.passthrough():
            self._apply_passthrough()
        self.unread_dock.set_island_state(True, 0.0)
        self.expanded_changed.emit(True)
        if self._panel_size_ready(w, h):
            self._reveal_pending_page()
        self._kick()

    def _capture_collapse_snap(self):
        """收起隐藏面板前截取透明底高 DPI 快照，供收起前段在当前轮廓内短暂淡出，消除两层之间的内容淡空。"""
        if not self._content_shown():
            self._collapse_snap = None
            return
        cw = self.content.width()
        ch = self.content.height()
        if cw <= 0 or ch <= 0:
            self._collapse_snap = None
            return
        dpr = max(1.0, float(self.devicePixelRatioF()))
        img = QtGui.QImage(
            max(1, round(cw * dpr)),
            max(1, round(ch * dpr)),
            QtGui.QImage.Format.Format_ARGB32_Premultiplied,
        )
        img.setDevicePixelRatio(dpr)
        img.fill(QtCore.Qt.GlobalColor.transparent)
        self.content.render(
            img,
            QtCore.QPoint(0, 0),
            QtGui.QRegion(),
            QtWidgets.QWidget.RenderFlag.DrawChildren,
        )
        self._collapse_snap = img
        self._collapse_snap_t0 = time.monotonic()
        self._collapse_snap_size = (float(cw), float(ch))

    def collapse(self):
        if not self.expanded or self.pinned():     # 图钉：保持展开
            return
        shown_idx = self._shown_page_idx
        self._page_reveal_seq += 1
        self._pending_page_idx = None
        self._shown_page_idx = None
        self._pending_call_on_show = False
        self._floor_t.clear()
        self._floor_on.clear()
        self._capture_collapse_snap()
        self.expanded = False
        self._collapsing_from_panel = True
        self.content_a.snap(0)
        self.content.hide()                   # 立即停用面板点击热区，快照在主画布轮廓内短暂淡出
        self.veil.hide()
        self.compact_a.to(1, 560, 1.0)
        self.release_focus()
        if self.passthrough():
            self._apply_passthrough()
        self.locks.discard("pin")
        if shown_idx is not None and 0 <= shown_idx < len(self.pages):
            self.pages[shown_idx].on_hide()
        self.unread_dock.set_island_state(False, 0.0)
        if cfg.get("island.last_tab") != self._page_key:
            cfg.set("island.last_tab", self._page_key)          # 下次（包括重启后）打开仍是这一页
        self.expanded_changed.emit(False)
        self._kick()

    def toggle(self, page: str | None = None):
        if self.expanded and (page is None or page == self._page_key):
            if self.pinned():
                self.set_pinned(False)
            self.collapse()
        else:
            self.expand(page)
            if self.expanded:
                seq = getattr(self, "_pin_seq", 0) + 1
                self._pin_seq = seq
                self.locks.add("pin")
                QtCore.QTimer.singleShot(2500, lambda s=seq: self.locks.discard("pin") if getattr(self, "_pin_seq", 0) == s else None)

    def show_page(self, key: str):
        keys = self.nav.keys()
        if key not in keys:
            return
        prev_target = self._target_page_index()
        i = keys.index(key)

        if not self.expanded:
            self._page_key = key
            self.nav.set_current(key)
            self.stack.setCurrentIndex(i)
            self._pending_page_idx = None
            self._pending_call_on_show = False
            self._kick()
            return

        pw, ph = self._page_panel_size(self.pages[i])
        if not self.can_fit_expanded(pw, ph):
            if self.pinned():
                self.set_pinned(False)
            self.collapse()
            return

        if i == prev_target and self._pending_page_idx is None and self._content_shown():
            w, h = self.target_size()
            self.sw.to(w, 440, 0.6)
            self.sh.to(h, 440, 0.6)
            self._kick()
            return

        self._page_reveal_seq += 1
        was_already_shown_same = (self._shown_page_idx == i)
        if self._shown_page_idx is not None and 0 <= self._shown_page_idx < len(self.pages) and self._shown_page_idx != i:
            self.pages[self._shown_page_idx].on_hide()
            self._shown_page_idx = None

        self._page_key = key
        self.nav.set_current(key)
        self.stack.setCurrentIndex(i)
        self._pending_page_idx = i
        self._pending_call_on_show = not was_already_shown_same
        self._anim_w0, self._anim_h0 = float(self.sw.x), float(self.sh.x)
        self._floor_t.clear()
        self._floor_on.clear()
        w, h = self.target_size()
        self.sw.to(w, 440, 0.6)
        self.sh.to(h, 440, 0.6)
        if self._panel_size_ready(w, h):
            self._reveal_pending_page()
        else:
            self.content_a.snap(0)
            self.content.hide()
            self.veil.hide()
        self._kick()

    def rebuild_pages(self, visible_pages: list | None = None):
        """根据可见且已排序的页面列表动态重建 QStackedWidget 与底部圆点导航，复用原 Page 实例。
        若当前页被隐藏，平滑切换到最近的可见页面；首页隐藏时默认进入第一个可见页面。
        """
        if visible_pages is None:
            if self.registry is not None:
                self.registry.sync_from_config(cfg)
                visible_pages = self.registry.visible_pages()
            else:
                visible_pages = self.pages
        visible_pages = list(visible_pages)
        if not visible_pages:
            return

        old_pages = list(self.pages)
        old_keys = [p.key for p in old_pages]
        new_keys = [p.key for p in visible_pages]
        cur_key = self._page_key

        # 若当前页被隐藏：首页隐藏时默认进入第一个可见页面；其它页面隐藏时切换到最近的可见页面
        if cur_key in new_keys:
            target_key = cur_key
        elif cur_key == "home":
            target_key = new_keys[0]
        else:
            if cur_key in old_keys:
                old_idx = old_keys.index(cur_key)
                # 寻找旧列表中距离 old_idx 最近的仍在 new_keys 中的页面
                best_key = new_keys[0]
                best_dist = 999
                for dist in range(1, len(old_keys) + 1):
                    for cand_i in (old_idx - dist, old_idx + dist):
                        if 0 <= cand_i < len(old_keys) and old_keys[cand_i] in new_keys:
                            best_key = old_keys[cand_i]
                            best_dist = dist
                            break
                    if best_dist < 999:
                        break
                target_key = best_key
            else:
                target_key = new_keys[0]

        if old_keys == new_keys and target_key == cur_key:
            return

        if self.expanded and cur_key != target_key and cur_key in old_keys:
            old_pages[old_keys.index(cur_key)].on_hide()

        self.pages = visible_pages
        while self.stack.count():
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
        for pg in self.pages:
            pg.island = self
            self.stack.addWidget(pg)

        self._page_key = target_key
        target_idx = new_keys.index(target_key)
        self.stack.setCurrentIndex(target_idx)
        self.nav.set_items([(pg.key, pg.glyph, pg.title) for pg in self.pages], current_key=target_key)

        if cfg.get("island.last_tab") != target_key:
            cfg.set("island.last_tab", target_key, save=False)

        if self.expanded:
            self._page_reveal_seq += 1
            self._pending_page_idx = target_idx
            self._pending_call_on_show = (cur_key != target_key)
            self._anim_w0, self._anim_h0 = float(self.sw.x), float(self.sh.x)
            self._floor_t.clear()
            self._floor_on.clear()
            w, h = self.target_size()
            self.sw.to(w, 440, 0.62)
            self.sh.to(h, 440, 0.62)
            if self._panel_size_ready(w, h):
                self._reveal_pending_page()
            else:
                self.content_a.snap(0)
                self.content.hide()
                self.veil.hide()
        self._kick()

    def _defer_show(self, pg, seq: int | None = None):
        """页面的数据刷新稍后执行，让形变动画先跑起来；携带序号防止快速切页或收起后过时回调误触发。"""
        expect_seq = self._page_reveal_seq if seq is None else int(seq)
        QtCore.QTimer.singleShot(
            40,
            lambda s=expect_seq: pg.on_show()
            if (
                self.expanded
                and s == self._page_reveal_seq
                and self._pending_page_idx is None
                and self._content_shown()
                and self.pages[self.stack.currentIndex()] is pg
            )
            else None,
        )

    def page_size_changed(self):
        """页面内部高度/尺寸变化时由页面调用（如邮箱页 419×140 列表 ↔ 419×236 详情）：只调整显现，不重复调用 on_show()。"""
        if not self.expanded:
            return
        cur_idx = self.stack.currentIndex()
        self._pending_page_idx = cur_idx
        self._pending_call_on_show = False
        w, h = self.target_size()
        if not self.can_fit_expanded(w, h):
            self._pending_page_idx = None
            if self.pinned():
                self.set_pinned(False)
            self.collapse()
            return
        self._page_reveal_seq += 1
        self._anim_w0, self._anim_h0 = float(self.sw.x), float(self.sh.x)
        self._floor_t.clear()
        self._floor_on.clear()
        self.sw.to(w, 440, 0.62)
        self.sh.to(h, 440, 0.62)
        if self._panel_size_ready(w, h):
            self._reveal_pending_page()
        else:
            self.content_a.snap(0)
            self.content.hide()
            self.veil.hide()
        self._kick()

    def activity(self, act: dict, duration: float = 2.4):
        self.compact.show_activity(act, duration)
        self._kick()

    # ================================================================ 岛内生活提醒队列
    def show_life_reminder(self, kind: str, title: str, sub: str = "", duration: float = 8.0) -> bool:
        """岛内生活提醒入队（water / sit）：同一时间只显示一条，不覆盖当前提醒，不触发系统通知。"""
        k = str(kind or "").strip().lower()
        if k not in ("water", "sit"):
            return False
        if self._active_life_reminder and self._active_life_reminder.get("kind") == k:
            return False
        if any(it.get("kind") == k for it in self._life_queue):
            return False
        item = {
            "type": "life-reminder",
            "kind": k,
            "title": str(title or ""),
            "sub": str(sub or ""),
            "duration": max(1.0, float(duration)),
        }
        if self._active_life_reminder is None:
            self._activate_life_reminder(item)
        else:
            self._life_queue.append(item)
        return True

    def _activate_life_reminder(self, item: dict):
        self._active_life_reminder = dict(item)
        dur = max(1.0, float(item.get("duration", 8.0)))
        self._life_timer.start(round(dur * 1000))
        self.compact.show_activity(item, dur + 0.25)
        self._life_banner.set_reminder(item)
        self._place_life_banner()
        if self.expanded:
            self._life_banner.show()
            self._life_banner.raise_()
        self._kick()

    def _on_life_reminder_timeout(self):
        self.dismiss_life_reminder(advance=True)

    def _on_life_banner_clicked(self, kind: str):
        self.dismiss_life_reminder(advance=True)
        self.life_reminder_clicked.emit(kind)

    def dismiss_life_reminder(self, advance: bool = True):
        self._life_timer.stop()
        self._active_life_reminder = None
        self.compact.clear_activity("life-reminder")
        self._life_banner.hide()
        if advance and self._life_queue:
            nxt = self._life_queue.pop(0)
            self._activate_life_reminder(nxt)
        self._kick()

    def current_life_reminder(self) -> dict | None:
        return dict(self._active_life_reminder) if self._active_life_reminder is not None else None

    def _place_life_banner(self):
        if self._active_life_reminder is None:
            self._life_banner.hide()
            return
        cw = max(200, self.content.width())
        ch = max(100, self.content.height())
        bw = max(180, cw - PAD * 2)
        # 避开顶部 PageHeader（含齿轮/刷新按钮）和底部 NavBar（页面圆点）
        top_y = PAD + 30
        max_y = max(top_y, ch - NAV_H - self._nav_bpad() - self._life_banner.height() - 6)
        y = min(top_y, max_y)
        self._life_banner.setGeometry(PAD, y, bw, self._life_banner.height())
        if self.expanded:
            self._life_banner.show()
            self._life_banner.raise_()

    def _set_content_opacity(self, a: float):
        """不再使用 QGraphicsOpacityEffect：在透明分层窗口里，带效果的子控件会单独重绘，
        偶尔出现"有内容、没有玻璃底"的帧。内容改为：形状长到约 70% 后直接显示（被形状裁剪），收起时立即隐藏。"""
        return

    # ================================================================ 拖动
    def begin_drag(self, gpos: QtCore.QPoint | None = None):
        if self.mode == "drag" or self.passthrough():
            return
        gpos_pt = QtCore.QPoint(gpos) if gpos is not None else QtGui.QCursor.pos()
        gpos_f = QtCore.QPointF(gpos_pt)
        c = self.body.center() + QtCore.QPointF(self._win_origin)
        if self.expanded:
            shown_idx = self._shown_page_idx
            self._page_reveal_seq += 1
            self._pending_page_idx = None
            self._shown_page_idx = None
            self._pending_call_on_show = False
            self._floor_t.clear()
            self._floor_on.clear()
            self.expanded = False
            self._collapse_snap = None
            self.content_a.snap(0)
            self._set_content_opacity(0)
            self.content.hide()
            self.veil.hide()
            self.release_focus()
            self.locks.discard("pin")
            if shown_idx is not None and 0 <= shown_idx < len(self.pages):
                self.pages[shown_idx].on_hide()
            self.expanded_changed.emit(False)
            # 收成胶囊，中心向鼠标靠拢
            c = QtCore.QPointF(gpos_f)
        self.compact_a.to(1, 500, 1.0)
        # 拖动期间固定当前停靠边的布局尺寸，保持抓手偏移恒定
        cw, ch = self.target_size()
        self.sw.to(cw, 300, 0.55)
        self.sh.to(ch, 300, 0.55)
        self.mode = "drag"
        self.fx.snap(c.x())
        self.fy.snap(c.y())
        self.drag_off = c - gpos_f
        if not (self.body.contains(gpos_f - QtCore.QPointF(self._win_origin))):
            self.drag_off = QtCore.QPointF(0, 0)
        QtWidgets.QApplication.setOverrideCursor(Qt.CursorShape.ClosedHandCursor)
        self._kick()

    def _end_drag(self, gpos: QtCore.QPoint | None = None):
        QtWidgets.QApplication.restoreOverrideCursor()
        c = QtCore.QPointF(self.fx.x, self.fy.x)
        gcur = QtCore.QPoint(gpos) if gpos is not None else QtGui.QCursor.pos()
        scr = self._resolve_drag_screen(gcur, c)
        if scr is not None and scr is not self.scr:
            self._switch_active_screen(scr, None)
        R = self._avail()
        # Step 1: 用拖拽中的几何尺寸确定目标边一次
        w_drag, h_drag = self.sw.target, self.sh.target
        body_drag = QtCore.QRectF(c.x() - w_drag / 2, c.y() - h_drag / 2, w_drag, h_drag)
        gaps = {
            "top": body_drag.top() - R.top(),
            "bottom": R.bottom() - body_drag.bottom(),
            "left": body_drag.left() - R.left(),
            "right": R.right() - body_drag.right(),
        }
        # 横向的边更"吸"一点（沿用现行有符号距离定边基线）
        weights = {"top": 1.0, "bottom": 1.0, "left": 1.35, "right": 1.35}
        edge = min(gaps, key=lambda k: gaps[k] * weights[k])
        self.edge = edge
        self.compact.set_edge(edge)

        # Step 2: 根据目标边和当前收起模式（含天气、未读色点与横幅）计算新尺寸
        nw, nh = self.target_size()
        self.sw.to(nw, 300, 0.55)
        self.sh.to(nh, 300, 0.55)

        # Step 3: 以新尺寸夹紧沿边位置，并以当前实际轮廓衔接贴边距离
        horiz = edge in ("top", "bottom")
        along0 = c.x() if horiz else c.y()
        v_along = self.fx.v if horiz else self.fy.v
        # 速度投影：朝边缘运动 => gap 减小
        v_toward = (-self.fy.v if edge == "top" else self.fy.v if edge == "bottom"
                    else -self.fx.v if edge == "left" else self.fx.v)
        self.along.snap(along0)
        self.along.v = v_along
        lo, hi = self._along_range(edge, nw, self._side_clamp_height(edge, nh))
        target_along = clamp(along0, lo, hi) if lo <= hi else (lo + hi) * 0.5
        self.along.to(target_along, 240, 0.74)

        cur_w, cur_h = max(36.0, self.sw.x), max(24.0, self.sh.x)
        cur_body = QtCore.QRectF(c.x() - cur_w / 2, c.y() - cur_h / 2, cur_w, cur_h)
        cur_gap = self._gap_to(edge, cur_body, self._line(edge, R))
        self.gap.snap(max(-4.0, cur_gap))
        self.gap.v = -v_toward
        self.gap.to(0, 300, 0.38)
        self.mode = "snap"
        if self.neck_edge and self.neck_edge != edge:
            self._break_neck()
        self._kick()

    def _break_neck(self):
        if not self.neck_edge:
            return
        e = self.neck_edge
        horiz = e in ("top", "bottom")
        c = self.body.center() + QtCore.QPointF(self._win_origin)
        s = Spring(7.5, 160, 0.45, 0.05)
        s.to(0)
        self.drips.append({"edge": e, "line": self._line(e), "c": c.x() if horiz else c.y(), "r": s})
        self.neck_edge = None
        self.neck_s.snap(0)

    # ================================================================ 每帧
    def _kick(self):
        if not self.timer.isActive():
            self._last = time.perf_counter()
            self.timer.start()

    def _needs_frames(self) -> bool:
        if self.mode != "anchored" or self.drips or self._bd_prev is not None or self._collapse_snap is not None:
            return True
        for s in (self.sw, self.sh, self.content_a, self.compact_a, self.neck_s):
            if not s.rest:
                return True
        if not self.expanded and not self._fs_hidden:
            return True                  # 收起态：常态动画（圆角呼吸 + 边缘流光）持续运行
        return False

    def _tick(self):
        now = time.perf_counter()
        dt = min(1 / 30, max(1 / 240, now - self._last))
        self._last = now

        if self.mode == "drag":
            gcur = QtGui.QCursor.pos()
            cand_scr = QtGui.QGuiApplication.screenAt(gcur)
            if cand_scr is not None and cand_scr is not self.scr:
                self._switch_active_screen(cand_scr, gcur)
            if not (user32.GetAsyncKeyState(0x01) & 0x8000):
                self._end_drag(gcur)
            else:
                cp = QtCore.QPointF(gcur) + self.drag_off
                self.fx.to(cp.x())
                self.fy.to(cp.y())
        if not self.expanded:
            cw, ch = self.target_size()
            if abs(self.sw.target - cw) > 0.5 or abs(self.sh.target - ch) > 0.5:
                if self.content_a.x < 0.12:
                    side_idle_resize = (
                        self.edge in ("left", "right")
                        and abs(self.sw.target - cw) <= 0.5
                        and self.sw.x <= 36.5
                        and not self.compact.has_outgoing_horizontal(self.sw.x)
                    )
                    side_growing_recovery = (
                        self.edge in ("left", "right")
                        and self.compact.has_outgoing_horizontal(self.sw.x)
                        and ch > self._side_clamp_h + 0.5
                    )
                    if side_idle_resize or side_growing_recovery:
                        self.sw.to(cw, 260, 0.92)
                        self.sh.to(ch, 260, 0.92)
                    else:
                        self.sw.to(cw, 460, 0.64)
                        self.sh.to(ch, 460, 0.64)
                    if self.mode == "snap":
                        lo, hi = self._along_range(self.edge, cw, self._side_clamp_height(self.edge, ch))
                        t_along = clamp(self.along.target, lo, hi) if lo <= hi else (lo + hi) * 0.5
                        self.along.to(t_along, 240, 0.74)
                    if self.compact_a.target < 1:
                        self.compact_a.to(1, 420, 1.0)
        else:
            w, h = self.target_size()
            # 按宽高变化方向满足显现条件后显示内容并开放点击
            if self._panel_size_ready(w, h) and (not self._content_shown() or self._pending_page_idx is not None or self.content_a.target < 1):
                self._reveal_pending_page()

        for s in (self.sw, self.sh, self.fx, self.fy, self.along, self.gap, self.neck_s,
                  self.content_a, self.compact_a, self.poke):
            s.step(dt)
        if abs(self.poke.x) > 0.01 or self.poke.target:
            if self._poke_sm.isNull():
                self._poke_sm = QtCore.QPointF(self._poke_pt)
            self._poke_sm += (self._poke_pt - self._poke_sm) * 0.3
        if not self.expanded:
            # 小形象看向光标；靠近时开心
            c = self.body.center() + QtCore.QPointF(self._win_origin)
            rel = QtCore.QPointF(QtGui.QCursor.pos()) - c
            self.compact.look = rel
            self.compact.near = abs(rel.x()) < self.body.width() / 2 + 40 and abs(rel.y()) < self.body.height() / 2 + 40
        if self.expanded and self._content_shown() and self._pending_page_idx is None:
            # 展开且内容已显现：越过目标后不再回落到目标以下（保留过冲回弹，但不会比内容小）
            for sp, key in ((self.sw, "w"), (self.sh, "h")):
                if self._floor_t.get(key) != sp.target:
                    self._floor_t[key] = sp.target
                    self._floor_on[key] = True
                if sp.x >= sp.target:
                    self._floor_on[key] = True
                elif self._floor_on.get(key):
                    sp.x, sp.v = sp.target, 0.0
        for d in self.drips:
            d["r"].step(dt)
        self.drips = [d for d in self.drips if not (d["r"].rest or d["r"].x < 0.3 and d["r"].v < 0)]

        if self.mode == "snap" and self.along.rest and self.gap.rest and self.sw.rest and self.sh.rest:
            self.sw.snap(self.sw.target)
            self.sh.snap(self.sh.target)
            self.gap.snap(0.0)
            self.along.snap(self.along.target)
            self.mode = "anchored"
            self._place_nav()
            if self.pinned():
                QtCore.QTimer.singleShot(80, self.expand)
            R = self._avail()
            horiz = self.edge in ("top", "bottom")
            self.pos_frac = clamp(((self.along.x - R.left()) / R.width()) if horiz
                                  else ((self.along.x - R.top()) / R.height()), 0.0, 1.0)
            cfg.set("island.edge", self.edge, save=False)
            cfg.set("island.pos", round(self.pos_frac, 4), save=False)
            screens = QtGui.QGuiApplication.screens()
            cfg.set("island.screen", screens.index(self.scr) if self.scr in screens else 0)
            self.neck_edge = self.edge
            self.neck_s.to(1)

        # 收起时：内容淡出完成后隐藏
        if not self.expanded and self._content_shown() and self.content_a.x < 0.05:
            self.content.hide()
            self._set_content_opacity(0)
        if not self.veil.isHidden():
            a = clamp(1.0 - self.content_a.x, 0.0, 1.0)
            if a <= 0.01 or not self._content_shown():
                self.veil.hide()
            else:
                self.veil.alpha = a
                if self.veil.geometry() != self.content.geometry():
                    self.veil.setGeometry(self.content.geometry())
                self.veil.update()

        if self.expanded:
            cw, _ch = self.compact.size_hint(avail_max_w=self._avail_max_banner_w())
            tw, _th = self.target_size()
            prog = clamp((self.sw.x - cw) / max(1.0, tw - cw), 0.0, 1.0)
            self.unread_dock.set_island_state(True, prog)
        else:
            self.unread_dock.set_island_state(False, 0.0)

        self._update_geometry()
        if not self._needs_frames() and self.mode == "anchored":
            self.timer.stop()
            self.timer.setInterval(16)
        elif self.mode == "anchored" and self.sw.rest and self.sh.rest and not self.expanded:
            # 只有常态动画：表情 / 音乐律动 / 提醒时 60fps，空闲（只有圆角呼吸）时 30fps
            self.timer.setInterval(16 if self.compact.busy() else 33)
        else:
            self.timer.setInterval(16)

    # ================================================================ 形状计算
    def _nearest_edge(self, body: QtCore.QRectF) -> tuple[str, float]:
        R = self._avail()
        gaps = {
            "top": body.top() - R.top(),
            "bottom": R.bottom() - body.bottom(),
            "left": body.left() - R.left(),
            "right": R.right() - body.right(),
        }
        e = min(gaps, key=gaps.get)
        return e, gaps[e]

    @staticmethod
    def _gap_to(edge: str, body: QtCore.QRectF, line: float) -> float:
        return {"top": body.top() - line, "bottom": line - body.bottom(),
                "left": body.left() - line, "right": line - body.right()}[edge]

    def _update_geometry(self, force: bool = False):
        w, h = max(36.0, self.sw.x), max(24.0, self.sh.x)
        vx = vy = 0.0
        squash = 0.0
        if self.mode == "anchored":
            a = self._anchor_along(w, h)
            c = self._center_on_edge(self.edge, a, 0, w, h)
            self.neck_edge = self.edge
        elif self.mode == "snap":
            g = self.gap.x
            if g < 0:                       # 冲过边缘 -> 挤压
                squash = -g
                g = 0.0
            horiz = self.edge in ("top", "bottom")
            if horiz:
                h2 = max(h * 0.62, h - squash)
                w = w + squash * 1.1
                h = h2
            else:
                w2 = max(w * 0.62, w - squash)
                h = h + squash * 1.1
                w = w2
            c = self._center_on_edge(self.edge, self.along.x, g, w, h)
            va, vg = self.along.v, self.gap.v
            vx, vy = {"top": (va, vg), "bottom": (va, -vg), "left": (vg, va), "right": (-vg, va)}[self.edge]
            if self.neck_edge is None and g < JOIN:
                self.neck_edge = self.edge
                self.neck_s.snap(0)
                self.neck_s.to(1, 520, 1.0)
        else:  # drag
            c = QtCore.QPointF(self.fx.x, self.fy.x)
            vx, vy = self.fx.v, self.fy.v
        body = QtCore.QRectF(c.x() - w / 2, c.y() - h / 2, w, h)

        if self.mode == "drag":
            if self.neck_edge:
                gn = self._gap_to(self.neck_edge, body, self._line(self.neck_edge))
                if gn > BREAK:
                    self._break_neck()
            if not self.neck_edge:
                e, g = self._nearest_edge(body)
                if g < JOIN:
                    self.neck_edge = e
                    self.neck_s.snap(0)
                    self.neck_s.to(1, 520, 1.0)

        # ---- 窗口位置：让本体大致居中，并夹在屏幕内（避免跨屏缩放） ----
        sg = self._screen_geom()
        eff_win_w, eff_win_h = self._eff_win_size(sg)
        if self.width() != eff_win_w or self.height() != eff_win_h:
            self.resize(eff_win_w, eff_win_h)
        if self.mode == "anchored":
            # 吸附时窗口贴住吸附边、位置固定：展开 / 收起 / 切页都不移动窗口
            # （移动分层窗口与重绘不是原子操作，会出现内容错位、没有玻璃底的帧）
            line = self._line(self.edge)
            # 窗口沿边锚点使用最终收起态尺寸而非当前瞬时动画尺寸，防止收起/展开动画途中窗口原点频繁搬家导致掉帧
            win_w, win_h = (w, h) if self.expanded else self.target_size()
            a_win = self._anchor_along(win_w, win_h, update_state=False)
            if self.edge in ("top", "bottom"):
                cx_, cy_ = a_win, (line - 4 + eff_win_h / 2) if self.edge == "top" else (line + 4 - eff_win_h / 2)
            else:
                cx_, cy_ = ((line - 4 + eff_win_w / 2) if self.edge == "left" else (line + 4 - eff_win_w / 2)), a_win
            wx = int(clamp(round(cx_ - eff_win_w / 2), sg.left(), sg.right() + 1 - eff_win_w))
            wy = int(clamp(round(cy_ - eff_win_h / 2), sg.top(), sg.bottom() + 1 - eff_win_h))
        else:
            wx = int(clamp(round(c.x() - eff_win_w / 2), sg.left(), sg.right() + 1 - eff_win_w))
            wy = int(clamp(round(c.y() - eff_win_h / 2), sg.top(), sg.bottom() + 1 - eff_win_h))
        origin = QtCore.QPoint(wx, wy)
        origin_changed = (origin != self._win_origin)
        if origin_changed or force:
            delta = QtCore.QPointF(origin - self._win_origin)
            self._win_origin = origin
            self.move(origin)
            if origin_changed and not delta.isNull():
                # 窗口原点变化时，在同一帧同步换算局部点击热区与按压坐标
                if self.compact.play_rect is not None:
                    self.compact.play_rect = self.compact.play_rect.translated(-delta)
                if self.compact._notify_dismiss_rect is not None:
                    self.compact._notify_dismiss_rect = self.compact._notify_dismiss_rect.translated(-delta)
                if self._press is not None:
                    self._press = self._press - delta
        off = QtCore.QPointF(-wx, -wy)
        lbody = body.translated(off)

        # ---- 半径 ----
        base_r = min(h / 2, w / 2, 30.0)
        if self.expanded or h > 60:
            base_r = min(30.0, h / 2, w / 2)
        t_now = time.perf_counter() - self._t0
        fillet = FILLET
        if self.mode == "anchored" and not self.expanded:
            fillet *= 1.0 + 0.07 * math.sin(t_now * 1.7)            # 常态：圆角轻微"呼吸"
        fillet += min(12.0, (abs(self.sw.v) + abs(self.sh.v)) * 0.012)   # 形变越快，液面拉得越开

        path = QtGui.QPainterPath()
        neck = None
        gap_n = 0.0
        if self.neck_edge:
            ne = self.neck_edge
            line = self._line(ne)
            gap_n = max(0.0, self._gap_to(ne, body, line))
            t = clamp(gap_n / BREAK, 0, 1)
            corner_r = base_r * smooth(t * 1.8)
            corner_r = lerp(base_r, corner_r, self.neck_s.x)
            radii = tuple(corner_r if flag else base_r for flag in edge_corners(ne))
            horiz = ne in ("top", "bottom")
            along_c = body.center().x() if horiz else body.center().y()
            length = w if horiz else h
            lline = line + (-wy if horiz else -wx)
            neck = neck_path(ne, lline, along_c + (-wx if horiz else -wy), length, gap_n, t,
                             self.neck_s.x, fillet, corner_r)
        else:
            radii = (base_r,) * 4

        speed = math.hypot(vx, vy)
        playing_wave = (self.mode == "anchored" and not self.expanded and self.compact.mode() == "media"
                        and self.compact.playing())
        poking = self.mode == "anchored" and abs(self.poke.x) > 0.01
        if speed > 30 or playing_wave or poking:
            pts = body_points(lbody, radii, 4.0)
            wave = None
            poke_params = None
            if poking:
                # 岛外悬停吸附：面向鼠标的外侧边缘朝光标位置温和拉出液态微凸（控制最大幅度约 3px）
                ppt = self._poke_sm - QtCore.QPointF(wx, wy)
                amp = clamp(self.poke.x, -0.25, 1.0) * 3.0
                farv = {"top": (0, 1), "bottom": (0, -1), "left": (1, 0), "right": (-1, 0)}[self.edge]
                horiz = farv[0] == 0
                c_u = lbody.center().x() if horiz else lbody.center().y()
                half = max(20.0, (lbody.width() if horiz else lbody.height()) / 2)
                depth = lbody.height() if horiz else lbody.width()
                u0 = clamp((ppt.x() if horiz else ppt.y()), c_u - half, c_u + half)
                sig = max(22.0, min(42.0, half * 0.45))
                near_line = {"top": lbody.top(), "bottom": lbody.bottom(), "left": lbody.left(), "right": lbody.right()}[self.edge]
                poke_params = (amp, farv, horiz, u0, sig, near_line, depth, ppt, lbody.center(), half)
            else:
                poke_params = None
            if playing_wave:
                pk = clamp(self.peak_fn() * 1.5, 0, 1)
                far = {"top": (0, 1), "bottom": (0, -1), "left": (1, 0), "right": (-1, 0)}[self.edge]
                tt = t_now

                prev = wave

                def wave(px, py, nx, ny, pk=pk, far=far, tt=tt, prev=prev):
                    extra = prev(px, py, nx, ny) if prev else 0.0
                    wgt = max(0.0, nx * far[0] + ny * far[1])
                    if wgt < 0.3:
                        return extra
                    u = px if far[0] == 0 else py
                    return extra + pk * 2.4 * wgt * wgt * math.sin(u * 0.09 - tt * 7.0)
            pts = deform(pts, lbody.center().x(), lbody.center().y(), vx, vy,
                         min(w, h) / 2 + 12, wave)
            if poke_params is not None:
                amp, farv, horiz, u0, sig, near_line, depth, ppt, bc, half = poke_params
                mvx, mvy = ppt.x() - bc.x(), ppt.y() - bc.y()
                md = math.hypot(mvx, mvy) or 1.0
                mdx, mdy = mvx / md, mvy / md
                out = []
                for px, py in pts:
                    v = abs((py if horiz else px) - near_line)          # 离屏幕边的距离
                    wgt = clamp((v - depth * 0.25) / (depth * 0.75), 0.0, 1.0)
                    if wgt > 0:
                        d = ((px if horiz else py) - u0) / sig
                        g = math.exp(-0.5 * d * d)
                        disp = amp * wgt * wgt * (g - 0.14 * (1.0 - g))   # 光标处朝外微凸吸附，两侧轻微内收
                        px += farv[0] * disp
                        py += farv[1] * disp
                    end_u = abs((px - bc.x()) if horiz else (py - bc.y()))
                    if v > depth * 0.15 and end_u > half * 0.65:
                        dist_pt = math.hypot(ppt.x() - px, ppt.y() - py)
                        g_end = math.exp(-0.5 * (dist_pt / 32.0) ** 2)
                        pull = amp * 0.35 * g_end * clamp((end_u - half * 0.65) / (half * 0.35), 0.0, 1.0)
                        if horiz:
                            px += mdx * pull
                        else:
                            py += mdy * pull
                    out.append((px, py))
                pts = out
            body_p = poly_path(pts)
        else:
            body_p = body_path(lbody, radii)
        path = body_p
        if neck is not None and not neck.isEmpty():
            path = path.united(neck)
        for d in self.drips:
            e = d["edge"]
            horiz = e in ("top", "bottom")
            lline = d["line"] + (-wy if horiz else -wx)
            dp = drip_path(e, lline, d["c"] + (-wx if horiz else -wy), max(0.0, d["r"].x))
            if not dp.isEmpty():
                path = path.united(dp)
        path.setFillRule(Qt.FillRule.WindingFill)

        old = self.path.boundingRect().toAlignedRect()
        self.path = path
        self.body = lbody
        new = path.boundingRect().toAlignedRect()
        dirty = old.united(new).adjusted(-16, -16, 16, 16)

        # ---- 内容区 ----
        if self._content_shown() and self.expanded:   # 收起淡出时保持原尺寸，不再挤压内容
            tw, th = self.target_size()
            cr = self._content_rect(tw, th, lbody)
            if cr != self.content.geometry():
                self.content.setGeometry(cr)
            self._place_life_banner()
            vis = lbody.toAlignedRect().intersected(cr).translated(-cr.topLeft())
            # 只在遮罩真正变化时设置：每帧 setMask/clearMask 会让内容单独重绘一遍，出现没有玻璃底的帧
            want = vis if vis.size() != cr.size() else None
            if want != self._mask_rect:
                self._mask_rect = want
                if want is None:
                    self.content.clearMask()
                else:
                    self.content.setMask(QtGui.QRegion(want))

        # ---- 毛玻璃采样（后台线程）：覆盖当前形状 + 目标形状，一次截图够整段展开动画用 ----
        self._place_unread_dock(lbody)
        self._request_backdrop()
        if origin_changed:
            self.update()
        else:
            self.update(dirty)

    def _place_unread_dock(self, lbody: QtCore.QRectF | None = None):
        """将未读快捷区定位到主岛外侧（默认右侧 8px，放不下时自动翻到左侧，顶边停靠下移 8px）。"""
        if not self.unread_dock.isVisible():
            return
        lb = lbody if lbody is not None else self.body
        if lb.isEmpty():
            return
        eff_win_w, eff_win_h = self._eff_win_size()
        avail = self._avail().translated(-self._win_origin.x(), -self._win_origin.y())
        dock_h = self.unread_dock.height()
        gap = 8.0

        # 默认主岛右侧 8px；若右侧空间不足或右侧贴边则自动翻到主岛左侧
        rx = round(lb.right() + gap)
        lx = round(lb.left() - gap - DOCK_WIDTH)
        if (self.edge == "right" and self.mode == "anchored") or (rx + DOCK_WIDTH > avail.right() and lx >= avail.left()):
            x = lx
            self.unread_dock.set_side("left")
        else:
            x = rx
            self.unread_dock.set_side("right")
        x = int(clamp(x, 0, max(0, eff_win_w - DOCK_WIDTH)))

        if self.edge == "top" and self.mode == "anchored":
            y = round(lb.top() + 8.0)
        elif self.edge == "bottom" and self.mode == "anchored":
            y = round(lb.bottom() - dock_h - 8.0)
        else:
            y = round(lb.top() + 8.0)
            if y + dock_h > avail.bottom():
                y = round(avail.bottom() - dock_h)
        y = int(clamp(y, 0, max(0, eff_win_h - dock_h)))
        if self.unread_dock.pos() != QtCore.QPoint(x, y):
            self.unread_dock.move(x, y)
        self.unread_dock.raise_()

    def _content_rect(self, tw, th, lbody: QtCore.QRectF | None = None) -> QtCore.QRect:
        """内容区：贴着吸附边、沿边居中（窗口坐标）。"""
        if lbody is None:
            c = self._center_on_edge(self.edge, self._anchor_along(tw, th), 0, tw, th) - QtCore.QPointF(self._win_origin)
            return QtCore.QRect(round(c.x() - tw / 2), round(c.y() - th / 2), round(tw), round(th))
        if self.edge == "left":
            cr = QtCore.QRect(round(lbody.left()), round(lbody.center().y() - th / 2), round(tw), round(th))
            return cr
        if self.edge == "right":
            cr = QtCore.QRect(round(lbody.right() - tw), round(lbody.center().y() - th / 2), round(tw), round(th))
            return cr
        cr = QtCore.QRect(round(lbody.center().x() - tw / 2), round(lbody.top()), round(tw), round(th))
        if self.edge == "bottom" and self.mode == "anchored":
            cr.moveBottom(round(lbody.bottom()))
        return cr

    def _place_nav(self):
        """底部页码圆点始终放在面板最下方居中（底边停靠时底部多留 4px 边距）。"""
        bpad = self._nav_bpad()
        if self._nav_bottom == bpad:
            return
        self._nav_bottom = bpad
        lay = self._content_lay
        lay.removeWidget(self.nav)
        lay.removeWidget(self.stack)
        lay.setSpacing(6)
        lay.setContentsMargins(PAD, PAD, PAD, bpad)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.nav)

    def _request_backdrop(self):
        moving = self.mode != "anchored" or not (self.sw.rest and self.sh.rest)
        if self.mode == "anchored":
            tw, th = self.target_size()
            tc = self._center_on_edge(self.edge, self._anchor_along(tw, th, update_state=False), 0, tw, th)
            g = QtCore.QRectF(tc.x() - tw / 2, tc.y() - th / 2, tw, th).toAlignedRect()
            if not self.expanded:
                # 收起时也预先截取展开尺寸的背景：打开的第一帧就有正确的玻璃，不会闪（窄屏拒止展开时跳过）
                ew, eh = self.expanded_size()
                if self.can_fit_expanded(ew, eh):
                    ec = self._center_on_edge(self.edge, self._anchor_along(ew, eh, update_state=False), 0, ew, eh)
                    g = g.united(QtCore.QRectF(ec.x() - ew / 2, ec.y() - eh / 2, ew, eh).toAlignedRect())
            if not (self.sw.rest and self.sh.rest):
                g = g.united(self.body.toAlignedRect().translated(self._win_origin))
            pad = 40                      # 覆盖两侧表面张力圆角
        else:
            g = self.path.boundingRect().toAlignedRect().translated(self._win_origin)
            pad = 90
        g = g.adjusted(-pad, -pad, pad, pad)
        q = 16                            # 对齐网格：区域不随轮廓的细微变化而抖动
        x0, y0 = (g.left() // q) * q, (g.top() // q) * q
        x1, y1 = -((-(g.right() + 1)) // q) * q, -((-(g.bottom() + 1)) // q) * q
        g = QtCore.QRect(x0, y0, x1 - x0, y1 - y0)
        if moving:
            interval = 1 / 40
        else:
            fps = max(2.0, float(cfg.get("island.blur_fps", 10)))
            interval = 1 / (fps if self.expanded else min(fps, 3.0))   # 收起静止时身后画面变化不敏感
            if self.capture_visible():
                # 参与截图时，每次取背景都要临时把自己排除约 17ms：静止时降低频率，
                # 让截图 / 录屏几乎总能拍到灵动岛（动画、拖动时仍高频刷新）
                interval = 1 / (1.5 if self.expanded else 1.0)
        need = self.path.boundingRect().toAlignedRect().translated(self._win_origin).intersected(self.scr.geometry())
        self.backdrop.request(self.scr, g, interval, urgent=not self.backdrop.covers(need))

    def _on_backdrop(self, changed: bool):
        theme.feed_luminance(self.backdrop.luminance)
        if not changed:
            return
        if self._bd_shown is not None and self._bd_shown[0] is not self.backdrop.state[0]:
            self._bd_prev = self._bd_shown            # 旧背景淡出、新背景淡入
            self._bd_fade_t0 = time.perf_counter()
        self._kick()

    # ================================================================ 绘制
    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        path = self.path
        if path.isEmpty():
            return
        # 1) 柔和阴影（几层半透明描边，廉价近似）
        sp = path.translated(0, 1.5)
        for wdt, a in ((14, 5), (8, 9), (4, 14)):
            p.strokePath(sp, QtGui.QPen(QtGui.QColor(0, 0, 0, a), wdt))
        self._paint_glass(p, path)
        bb = self.body
        # 4) 轮廓：外侧极淡暗线 + 内侧高光边
        p.strokePath(path, QtGui.QPen(theme.outline(), 1.6))
        rg = QtGui.QLinearGradient(0, bb.top(), 0, bb.bottom())
        rg.setColorAt(0, theme.rim_top())
        rg.setColorAt(1, theme.rim_bottom())
        p.strokePath(path, QtGui.QPen(QtGui.QBrush(rg), 1.0))
        self._paint_rest(p, path, bb)

    def _paint_glass(self, p: QtGui.QPainter, path: QtGui.QPainterPath, crossfade: bool = True):
        """毛玻璃底 + 色调 + 光泽（灵动岛本体与内容渐显面纱共用）。"""
        if theme.solid:
            p.fillPath(path, theme.tint())
            return
        base_opacity = p.opacity()
        # 2) 毛玻璃底（新截图与上一张交叉淡入，避免跳变）
        state = self.backdrop.state
        br = self.backdrop.brush_for(state, self._win_origin)
        fade = (time.perf_counter() - self._bd_fade_t0) / 0.25 if self._bd_prev is not None else 1.0
        if fade < 1.0:
            prev = self.backdrop.brush_for(self._bd_prev, self._win_origin)
            if prev is not None:
                p.fillPath(path, prev)
            if br is not None:
                p.setOpacity(base_opacity * max(0.0, fade))
                p.fillPath(path, br)
                p.setOpacity(base_opacity)
        else:
            if crossfade:
                self._bd_prev = None
            if br is not None:
                p.fillPath(path, br)
            else:
                p.fillPath(path, QtGui.QColor(128, 128, 128, 200))
        if crossfade:
            self._bd_shown = state
        # 3) 色调 + 纵向光泽
        p.fillPath(path, theme.tint_brush(self.body))
        bb = self.body
        g = QtGui.QLinearGradient(0, bb.top(), 0, bb.bottom())
        g.setColorAt(0, QtGui.QColor(255, 255, 255, 26 if not theme.dark else 14))
        g.setColorAt(0.5, QtGui.QColor(255, 255, 255, 0))
        g.setColorAt(1, QtGui.QColor(255, 255, 255, 10))
        p.fillPath(path, g)

    def _paint_rest(self, p: QtGui.QPainter, path: QtGui.QPainterPath, bb: QtCore.QRectF):
        # 5) 常态动画：沿边缘缓慢游走的流光（纯黑实体皮肤跳过）
        t = time.perf_counter() - self._t0
        if not self.expanded and not theme.solid:
            ph = (math.sin(t * 0.55) * 0.5 + 0.5)
            far_y = bb.bottom() if self.edge != "bottom" else bb.top()
            pt = QtCore.QPointF(bb.left() + bb.width() * ph, far_y)
            if self.edge in ("left", "right"):
                pt = QtCore.QPointF(bb.right() if self.edge == "left" else bb.left(), bb.top() + bb.height() * ph)
            rad = QtGui.QRadialGradient(pt, max(60.0, bb.width() * 0.35))
            rad.setColorAt(0, QtGui.QColor(255, 255, 255, 80 if not theme.dark else 55))
            rad.setColorAt(1, QtGui.QColor(255, 255, 255, 0))
            p.strokePath(path, QtGui.QPen(QtGui.QBrush(rad), 1.0))
        # 6) 收起前段面板快照短暂淡出 + 紧凑内容及时接续（消除两层之间的内容淡空）
        snap_fade = 0.0
        snap_prog = 1.0
        if self._collapse_snap is not None and not self.expanded:
            snap_prog = clamp((time.monotonic() - self._collapse_snap_t0) / 0.13, 0.0, 1.0)
            if snap_prog >= 1.0:
                self._collapse_snap = None
            else:
                snap_fade = (1.0 - snap_prog) ** 2

        compact_vis = float(self.compact_a.x)
        if self._collapse_snap is not None and not self.expanded:
            compact_vis = max(compact_vis, smooth(clamp((snap_prog - 0.18) / 0.52, 0.0, 1.0)))

        if snap_fade > 0.015 or compact_vis > 0.01:
            p.save()
            p.setClipPath(path)               # 内容（含快照与竖条）严格裁剪在当前灵动岛轮廓内
            if snap_fade > 0.015 and self._collapse_snap is not None:
                sw_s, sh_s = self._collapse_snap_size
                if self.edge == "left":
                    sx_s, sy_s = bb.left(), bb.center().y() - sh_s * 0.5
                elif self.edge == "right":
                    sx_s, sy_s = bb.right() - sw_s, bb.center().y() - sh_s * 0.5
                elif self.edge == "bottom":
                    sx_s, sy_s = bb.center().x() - sw_s * 0.5, bb.bottom() - sh_s
                else:
                    sx_s, sy_s = bb.center().x() - sw_s * 0.5, bb.top()
                p.setOpacity(snap_fade)
                p.drawImage(QtCore.QRectF(sx_s, sy_s, sw_s, sh_s), self._collapse_snap)
                p.setOpacity(1.0)

            if compact_vis > 0.01:
                self.compact.press = (0.0, 0.0)
                self.compact.squash = (1.0, 1.0)
                compact_bb = bb
                if (
                    self.edge in ("left", "right")
                    and self.compact.layout_state()[0] == "vertical"
                    and bb.width() > 36.5
                ):
                    slot_x = bb.left() if self.edge == "left" else (bb.right() - 36.0)
                    compact_bb = QtCore.QRectF(slot_x, bb.top(), 36.0, bb.height())
                self.compact.paint(
                    p,
                    compact_bb,
                    compact_vis,
                    self.peak_fn() if self.compact.mode() == "media" else 0.0,
                )
            p.restore()
        p.end()

    # ================================================================ 鼠标
    def _inside(self, gp: QtCore.QPoint) -> bool:
        if self.unread_dock.hit_test_global(gp):
            return True
        lp = QtCore.QPointF(gp - self._win_origin)
        if self.expanded and self.path.contains(lp):
            return True
        return self.body.adjusted(-2, -2, 2, 2).contains(lp)

    def _poll_hover(self):
        # 输入框的键盘焦点：切到别的窗口后立即释放，否则会一直保持展开
        if "focus" in self.locks and user32.GetForegroundWindow() != int(self.winId()):
            self._prev_fg = None
            self.release_focus()
        if self._fs_hidden or self.mode != "anchored":
            self._hover_since = self._out_since = None
            return
        inside = self._inside(QtGui.QCursor.pos())
        now = time.monotonic()
        if not self.expanded and self.mode == "anchored" and not self._fs_hidden:
            if self.passthrough():
                if self.poke.target != 0.0:
                    self.poke.to(0.0, 260, 0.72)
                    self._kick()
            else:
                # 岛外悬停吸附：鼠标在灵动岛外围附近时，边缘朝鼠标温和凸起；移远后平滑复位
                gp = QtCore.QPointF(QtGui.QCursor.pos())
                lp = gp - QtCore.QPointF(self._win_origin)
                b = self.body
                dx = max(b.left() - lp.x(), 0.0, lp.x() - b.right())
                dy = max(b.top() - lp.y(), 0.0, lp.y() - b.bottom())
                dist_out = math.hypot(dx, dy)
                hover_range = 42.0
                if 0.0 < dist_out < hover_range:
                    target = smooth(1.0 - dist_out / hover_range)
                    self._poke_pt = gp
                    if abs(self.poke.target - target) > 0.01:
                        self.poke.to(target, 280, 0.78)
                        self._kick()
                elif dist_out == 0.0:
                    nx = (lp.x() - b.center().x()) / max(1.0, b.width() / 2)
                    ny = (lp.y() - b.center().y()) / max(1.0, b.height() / 2)
                    edge_w = min(1.0, math.hypot(nx, ny))
                    target = 0.35 + 0.65 * edge_w
                    self._poke_pt = gp
                    if abs(self.poke.target - target) > 0.01:
                        self.poke.to(target, 280, 0.78)
                        self._kick()
                elif self.poke.target != 0.0:
                    self.poke.to(0.0, 260, 0.72)
                    self._kick()
        local_cur = QtCore.QPointF(QtGui.QCursor.pos()) - QtCore.QPointF(self._win_origin)
        on_btn = inside and self._on_play_button(local_cur)
        hv = 1.0 if on_btn and not self.passthrough() else 0.0
        if abs(self.compact.play_hover - hv) > 0.01:
            self.compact.play_hover += (hv - self.compact.play_hover) * 0.5
            self._kick()
        if self.expanded and self.compact.mode() != "activity":
            pass
        r_life, on_life_dismiss = self._on_life_banner(local_cur) if (inside and not self.passthrough()) else (False, False)
        _, on_dismiss = self._on_notify_banner(local_cur) if (inside and not self.passthrough()) else (False, False)
        nhv = 1.0 if (on_dismiss or on_life_dismiss) else 0.0
        if abs(self.compact.notify_dismiss_hover - nhv) > 0.01:
            self.compact.notify_dismiss_hover += (nhv - self.compact.notify_dismiss_hover) * 0.5
            self._kick()
        if (on_btn or on_dismiss or on_life_dismiss or r_life) and not self.expanded:
            self._hover_since = None              # 在播放键、通知关闭键或生活提醒上停留不误触发悬停展开
            return
        if inside and self.passthrough():
            inside = False                        # 可穿透：鼠标视为不在灵动岛上（只响应右键）
            self._hover_since = None
            if not self.expanded:
                return
        if inside:
            self._out_since = None
            self.locks.discard("pin")
            if not self.expanded and self.expand_mode() != "never":
                if self._press is not None or self._click_timer.isActive() or (user32.GetAsyncKeyState(0x01) & 0x8000):
                    self._hover_since = now       # 鼠标按下或等待双击期间不自动展开，防止打断双击长关
                elif self._hover_since is None:
                    self._hover_since = now
                elif (now - self._hover_since) * 1000 >= cfg.get("island.hover_expand_ms", 220):
                    self._hover_since = None
                    self.expand()
        else:
            self._hover_since = None
            if self.expanded:
                if self.locks or self.pinned():
                    self._out_since = None
                    return
                if self._out_since is None:
                    self._out_since = now
                elif (now - self._out_since) * 1000 >= cfg.get("island.collapse_delay_ms", 450):
                    self._out_since = None
                    self.collapse()

    def _classify_hit_ctx(self, pos: QtCore.QPointF) -> tuple[str, str, int, str] | None:
        """在当前可见岛体 self.body 内判定点击上下文 (mode, act_type, act_seq, zone)。"""
        if self.expanded:
            return None
        orientation, m = self.compact.layout_state()
        if orientation == "vertical":
            if self._on_play_button(pos):
                return (m, "", 0, "play")
            if self.body.adjusted(-2, -2, 2, 2).contains(pos):
                return (m, "", 0, "island")
            return None
        if self._on_play_button(pos):
            return (m, "", 0, "play")
        is_lb, is_lb_dismiss = self._on_life_banner(pos)
        if is_lb:
            seq = self.compact.current_activity_seq()
            return ("activity", "life-reminder", seq, "dismiss" if is_lb_dismiss else "banner_body")
        is_nb, is_nb_dismiss = self._on_notify_banner(pos)
        if is_nb:
            seq = self.compact.current_activity_seq()
            return ("activity", "notify", seq, "dismiss" if is_nb_dismiss else "banner_body")
        if self.body.adjusted(-2, -2, 2, 2).contains(pos):
            return (m, "", 0, "island")
        return None

    def mousePressEvent(self, e):
        if not self._inside(e.globalPosition().toPoint()):
            e.ignore()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.position()
            self._press_ctx = self._classify_hit_ctx(e.position())
            self._hover_since = time.monotonic()
        elif e.button() == Qt.MouseButton.RightButton:
            self.menu_requested.emit(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        # 按住并移动即开始拖动（不需要长按）；固定时不允许拖动
        if self._press is not None and (e.position() - self._press).manhattanLength() > 4:
            self.lp_timer.stop()
            self._press = None
            self._press_ctx = None
            self.begin_drag(QtGui.QCursor.pos())

    def _on_play_button(self, local: QtCore.QPointF) -> bool:
        orientation, m = self.compact.layout_state()
        if self.expanded or m != "media":
            return False
        r = self.compact.play_rect
        vis_body = self.body if not self.body.isEmpty() else QtCore.QRectF(self.rect())
        if orientation == "vertical":
            return (r is not None and vis_body.adjusted(-2, -2, 2, 2).contains(local)
                    and r.adjusted(-4, -4, 4, 4).intersected(vis_body).contains(local))
        return (r is not None and vis_body.adjusted(-2, -2, 2, 2).contains(local)
                and r.adjusted(-6, -8, 6, 8).contains(local))

    def _on_notify_banner(self, pos: QtCore.QPointF) -> tuple[bool, bool]:
        """返回 (is_notify_banner, is_dismiss_button)，命中区域严格限制在当前可见岛体 self.body 内。"""
        orientation, m = self.compact.layout_state()
        if self.expanded or orientation == "vertical" or m != "activity":
            return False, False
        act = self.compact.activity or {}
        if act.get("type") != "notify":
            return False, False
        r = QtCore.QRectF(self.body)
        if r.isEmpty() or not r.contains(pos):
            return False, False
        dr = self.compact._notify_dismiss_rect
        seq = int(act.get("seq") or 0)
        if (dr is not None and self.compact._dismiss_rect_seq == seq
                and dr.adjusted(-5, -5, 5, 5).intersected(r).contains(pos)):
            return True, True
        return True, False

    def _on_life_banner(self, pos: QtCore.QPointF) -> tuple[bool, bool]:
        """返回 (is_life_banner, is_dismiss_button)，命中区域严格限制在当前可见岛体 self.body 内。"""
        orientation, m = self.compact.layout_state()
        if self.expanded or orientation == "vertical" or m != "activity":
            return False, False
        act = self.compact.activity or {}
        if act.get("type") != "life-reminder":
            return False, False
        r = QtCore.QRectF(self.body)
        if r.isEmpty() or not r.contains(pos):
            return False, False
        dr = self.compact._notify_dismiss_rect
        seq = int(act.get("seq") or 0)
        if (dr is not None and self.compact._dismiss_rect_seq == seq
                and dr.adjusted(-5, -5, 5, 5).intersected(r).contains(pos)):
            return True, True
        return True, False

    def mouseReleaseEvent(self, e):
        self.lp_timer.stop()
        press_pos = self._press
        press_ctx = self._press_ctx
        self._press = None
        self._press_ctx = None
        if press_pos is None or press_ctx is None:
            return
        rel_ctx = self._classify_hit_ctx(e.position())
        if rel_ctx is None or rel_ctx != press_ctx:
            return
        _mode, act_type, _act_seq, zone = rel_ctx
        if zone == "play":
            self.play_pause_requested.emit()
            return
        if act_type == "life-reminder":
            kind = str((self.compact.activity or {}).get("kind") or "")
            if zone == "dismiss":
                self.dismiss_life_reminder(advance=True)
            else:
                self._on_life_banner_clicked(kind)
            self._kick()
            return
        if act_type == "notify":
            if zone == "dismiss":
                self.notify_banner_dismissed.emit()
            else:
                self.notify_banner_clicked.emit()
            self._kick()
            return
        if zone == "island" and self.mode == "anchored" and not self.expanded \
                and self.expand_mode() != "never":
            self._click_timer.start(QtWidgets.QApplication.doubleClickInterval())

    def _single_click(self):
        if self.mode == "anchored" and not self.expanded and self.expand_mode() != "never":
            self.expand()
            self.locks.add("pin")
            QtCore.QTimer.singleShot(600, lambda: self.locks.discard("pin"))

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._on_play_button(e.position()):
            self.play_pause_requested.emit()       # 快速连点播放键：当作第二次点击，不切换长关
            return
        if e.button() == Qt.MouseButton.LeftButton and self._inside(e.globalPosition().toPoint()):
            self._click_timer.stop()
            self._press = None
            self.toggle_long_off()

    def toggle_long_off(self):
        """双击：悬停展开 ↔ 长关（常驻展开时双击也切到长关）。"""
        new = "hover" if self.expand_mode() == "never" else "never"
        self.set_expand_mode(new)
        if new == "never" and self.expanded:
            # 双击长关：立即收起，不等鼠标移出；也不显示任何提示文字
            self.locks.discard("pin")
            self.release_focus()
            self.collapse()

    def _long_press(self):
        pass

    # ---------------- 穿透模式 ----------------
    def passthrough(self) -> bool:
        return bool(cfg.get("island.passthrough", False))

    def set_passthrough(self, on: bool):
        cfg.set("island.passthrough", bool(on))
        self._apply_passthrough()
        if on and self.expanded and not self.pinned():
            self.collapse()
        self.passthrough_changed.emit(bool(on))

    def _apply_passthrough(self):
        """穿透 = 鼠标完全穿过（WS_EX_TRANSPARENT），只靠钩子响应右键。"""
        hwnd = int(self.winId())
        ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        on = self.passthrough()
        transparent = on
        ex = (ex | WS_EX_TRANSPARENT) if transparent else (ex & ~WS_EX_TRANSPARENT)
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)
        if on:
            self._rhook.install()
        else:
            self._rhook.remove()

    def capture_visible(self) -> bool:
        return not bool(cfg.get("island.exclude_from_capture", False))

    def set_capture_visible(self, on: bool):
        cfg.set("island.exclude_from_capture", not on)
        self.apply_capture_mode()

    def apply_capture_mode(self):
        """参与屏幕捕获：截图 / 录屏里能看到灵动岛；毛玻璃取背景时只在那一瞬间临时排除自己。
        不参与：一直排除（截图里看不到灵动岛）。"""
        hwnd = int(self.winId())
        visible = self.capture_visible()
        exclude_from_capture(hwnd, not visible)
        self.backdrop.hwnd = hwnd
        self.backdrop.self_exclude = visible

    def expand_mode(self) -> str:
        m = cfg.get("island.expand_mode")
        if m not in ("hover", "always", "never"):
            m = "always" if cfg.get("island.pinned", False) else "hover"
        return m

    def set_expand_mode(self, mode: str):
        """hover = 鼠标悬停展开、离开收起；always = 一直展开；never = 一直收起（悬停 / 单击都不展开）。"""
        if mode not in ("hover", "always", "never"):
            return
        cfg.set("island.expand_mode", mode, save=False)
        cfg.set("island.pinned", mode == "always")
        if mode == "always":
            self.expand()
        elif mode == "never":
            # 不立即收起：鼠标离开灵动岛后才收起（之后悬停 / 单击都不再展开）
            self.locks.discard("pin")
            if not self._inside(QtGui.QCursor.pos()):
                self.collapse()
        self.mode_changed.emit(mode)
        self.pin_changed.emit(mode == "always")

    def pinned(self) -> bool:
        return self.expand_mode() == "always"

    def set_pinned(self, on: bool):
        self.set_expand_mode("always" if on else "hover")
        return

    def _set_pinned_old(self, on: bool):
        """图钉：保持展开（不会因鼠标移开而收起），仍可拖动。"""
        cfg.set("island.pinned", bool(on))
        if on and not self.expanded:
            self.expand()
        self.pin_changed.emit(bool(on))

    def wheelEvent(self, e):
        dy = e.angleDelta().y() or e.angleDelta().x()
        if not dy:
            return
        if not self.expanded:
            self.wheel_compact.emit(1 if dy > 0 else -1)
        elif "focus" not in self.locks and "drop" not in self.locks:
            self.nav.step_wheel(dy)
            e.accept()

    # ================================================================ 本地文件拖放（置物架）
    def _tools_page(self):
        for pg in self.pages:
            if pg.key == "tools":
                return pg
        if self.registry is not None:
            e = self.registry.get_entry("tools")
            if e is not None and e.visible:
                return e.page
        return None

    def dragEnterEvent(self, e):
        if self.passthrough() or self.mode != "anchored" or not cfg.get("tools.shelf_enabled", True):
            e.ignore()
            return
        from .pages.tools import extract_local_files
        files = extract_local_files(e.mimeData())
        tpg = self._tools_page()
        if not files or tpg is None:
            e.ignore()
            return
        self.locks.add("drop")
        if not self.expanded:
            self.expand("tools")
        elif self._page_key != "tools":
            self.show_page("tools")
        if hasattr(tpg, "set_drop_hover"):
            tpg.set_drop_hover(True)
        e.setDropAction(Qt.DropAction.CopyAction)
        e.accept()

    def dragMoveEvent(self, e):
        from .pages.tools import extract_local_files
        files = extract_local_files(e.mimeData())
        tpg = self._tools_page()
        if files and tpg is not None and cfg.get("tools.shelf_enabled", True):
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self.locks.discard("drop")
        tpg = self._tools_page()
        if tpg is not None and hasattr(tpg, "set_drop_hover"):
            tpg.set_drop_hover(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        self.locks.discard("drop")
        tpg = self._tools_page()
        if tpg is not None and hasattr(tpg, "set_drop_hover"):
            tpg.set_drop_hover(False)
        from .pages.tools import extract_local_files
        files = extract_local_files(e.mimeData())
        if files and tpg is not None and cfg.get("tools.shelf_enabled", True):
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()
            if hasattr(tpg, "_on_files_dropped"):
                tpg._on_files_dropped(files)
        else:
            e.ignore()

    # ================================================================ 焦点（输入框需要键盘）
    def request_focus(self):
        hwnd = int(self.winId())
        fg = user32.GetForegroundWindow()
        if fg and fg != hwnd:
            self._prev_fg = fg
        ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex & ~WS_EX_NOACTIVATE)
        cur = user32.GetCurrentThreadId() if hasattr(user32, "GetCurrentThreadId") else ctypes.windll.kernel32.GetCurrentThreadId()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        if fg_tid and fg_tid != cur:
            user32.AttachThreadInput(cur, fg_tid, True)
        user32.SetForegroundWindow(hwnd)
        self.activateWindow()
        if fg_tid and fg_tid != cur:
            user32.AttachThreadInput(cur, fg_tid, False)
        self.locks.add("focus")

    def release_focus(self):
        if "focus" not in self.locks:
            return
        self.locks.discard("focus")
        hwnd = int(self.winId())
        ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE)
        fw = QtWidgets.QApplication.focusWidget()
        if fw is not None:
            fw.clearFocus()
        if self._prev_fg and user32.IsWindow(self._prev_fg):
            user32.SetForegroundWindow(self._prev_fg)
        self._prev_fg = None

    # ================================================================ 原生：热键、闪烁、样式
    def showEvent(self, e):
        super().showEvent(e)
        hwnd = int(self.winId())
        ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE)
        self.apply_capture_mode()
        if not self._shell_msg:
            self._shell_msg = user32.RegisterWindowMessageW("SHELLHOOK")
            user32.RegisterShellHookWindow(hwnd)
        if not self._registered_hotkeys:
            self.register_hotkeys()
        self._apply_passthrough()
        if self.pinned():
            QtCore.QTimer.singleShot(600, self.expand)
        self.poll.start()
        self.backdrop.start()
        self._kick()

    def register_hotkeys(self):
        hwnd = int(self.winId())
        for hid in list(self._registered_hotkeys):
            user32.UnregisterHotKey(hwnd, hid)
        self._registered_hotkeys.clear()
        for i, (name, spec) in enumerate((cfg.get("hotkeys") or {}).items()):
            hk = parse_hotkey(spec)
            if not hk:
                continue
            hid = 0xB000 + i
            if user32.RegisterHotKey(hwnd, hid, hk[0], hk[1]):
                self._registered_hotkeys[hid] = name
            else:
                print(f"热键注册失败（可能被占用）：{name} = {spec}")

    def apply_config(self):
        """设置窗口保存或重新加载配置后，动态应用位置、停靠边、屏幕、穿透、截图与热键。"""
        new_edge = cfg.get("island.edge", "top")
        if new_edge not in ("top", "bottom", "left", "right"):
            new_edge = "top"
        raw_pos = cfg.get("island.pos", 0.5)
        new_pos = clamp(float(0.5 if raw_pos is None else raw_pos), 0.0, 1.0)
        new_scr = self._pick_screen()
        geom_changed = (new_edge != self.edge or abs(new_pos - self.pos_frac) > 1e-4 or new_scr != self.scr)
        self.edge = new_edge
        self.pos_frac = new_pos
        self.scr = new_scr
        self._hook_screen_signals(self.scr)
        self.compact.set_edge(self.edge)
        if geom_changed:
            self.neck_edge = self.edge
            if not self.expanded:
                cw, ch = self.target_size()
                self.sw.snap(cw)
                self.sh.snap(ch)
            elif not self.can_fit_expanded():
                if self.pinned():
                    self.set_pinned(False)
                self.collapse()
            self._place_nav()
            self._update_geometry(force=True)
        self.rebuild_pages()
        self.set_expand_mode(self.expand_mode())
        self._apply_passthrough()
        self.passthrough_changed.emit(self.passthrough())
        self.apply_capture_mode()
        self.register_hotkeys()
        self._kick()

    def nativeEvent(self, etype, message):
        try:
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                name = self._registered_hotkeys.get(int(msg.wParam))
                if name:
                    self.hotkey.emit(name)
                    return True, 0
            elif msg.message == WM_POWERBROADCAST:
                if int(msg.wParam) in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC):
                    self._last = time.perf_counter()
                    self._on_screens_changed()
                    self.resumed_from_sleep.emit()
            elif msg.message in (WM_DISPLAYCHANGE, WM_DPICHANGED):
                self._on_screens_changed()
            elif self._shell_msg and msg.message == self._shell_msg:
                if int(msg.wParam) == HSHELL_FLASH:
                    self.flash.emit(int(msg.lParam or 0))
        except Exception:  # noqa: BLE001
            pass
        return False, 0

    def set_fullscreen_hidden(self, hidden: bool):
        if hidden == self._fs_hidden:
            return
        self._fs_hidden = hidden
        if hidden:
            self.collapse()
            self.hide()
        else:
            self.show()
            self._kick()

    def closeEvent(self, e):
        hwnd = int(self.winId())
        for hid in self._registered_hotkeys:
            user32.UnregisterHotKey(hwnd, hid)
        try:
            user32.DeregisterShellHookWindow(hwnd)
        except Exception:  # noqa: BLE001
            pass
        self.timer.stop()
        self.poll.stop()
        self.lp_timer.stop()
        self._click_timer.stop()
        self._life_timer.stop()
        self.unread_dock._timer.stop()
        self.backdrop.stop()
        self._rhook.remove()
        try:
            theme.changed.disconnect(self.update)
        except Exception:
            pass
        app_inst = QtWidgets.QApplication.instance()
        if app_inst is not None:
            try:
                app_inst.removeEventFilter(self._dbl_filter)
            except Exception:
                pass
        cfg.save()
        super().closeEvent(e)
