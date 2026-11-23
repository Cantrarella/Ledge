# -*- coding: utf-8 -*-
"""wisland 入口：创建服务、页面、灵动岛，把信号连成"活动"提示。"""
from __future__ import annotations

import os
import sys
import time
import traceback
import winreg
from pathlib import Path
from types import SimpleNamespace

from PySide6 import QtCore, QtGui, QtWidgets

from .core import bg
from .core.branding import (
    APP_ID,
    DISPLAY_NAME,
    TRAY_COLLAPSE,
    TRAY_EXPAND,
    TRAY_OPEN_SETTINGS,
    TRAY_QUIT,
    TRAY_TOOLTIP,
)
from .core.config import DATA_DIR, ROOT, cfg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = DISPLAY_NAME
LEGACY_AUTOSTART_NAME = APP_ID


def _G():
    from .ui.theme import G
    return G


def _query_reg_value(key, name: str) -> str | None:
    try:
        val, _ = winreg.QueryValueEx(key, name)
        return str(val)
    except OSError:
        return None


def autostart_enabled(run_key: str = RUN_KEY) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key) as k:
            return (_query_reg_value(k, AUTOSTART_NAME) is not None) or (
                _query_reg_value(k, LEGACY_AUTOSTART_NAME) is not None
            )
    except OSError:
        return False


def _autostart_target_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    ledge_vbs = ROOT / "Ledge.vbs"
    return ledge_vbs if ledge_vbs.is_file() else (ROOT / "wisland.vbs")


def _autostart_cmd() -> str:
    target = _autostart_target_path()
    if getattr(sys, "frozen", False):
        return f'"{target}"'
    # 开机自启命令：通过 Ledge.vbs 启动（它会选用不带控制台的 pythonw，不会弹出终端窗口）
    wscript = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wscript.exe")
    return f'"{wscript}" "{target}"'


def fix_autostart(run_key: str = RUN_KEY):
    """安全迁移并修复开机启动项：
    - 将旧 wisland 启动项安全迁移为 Ledge（先确认目标存在并写入 Ledge，成功后再删除旧 wisland）
    - 若 Ledge 与 wisland 同时存在，保留 Ledge 并删除旧 wisland 避免启动两次
    - 写入失败时保留旧项，绝不破坏已有开机自启
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key) as k:
            cur_new = _query_reg_value(k, AUTOSTART_NAME)
            cur_old = _query_reg_value(k, LEGACY_AUTOSTART_NAME)
    except OSError:
        return

    if cur_new is None and cur_old is None:
        return

    target = _autostart_target_path()
    want = _autostart_cmd()

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE) as kw:
            if cur_new is None:
                if not target.is_file():
                    return
                winreg.SetValueEx(kw, AUTOSTART_NAME, 0, winreg.REG_SZ, want)
            elif cur_new != want:
                if target.is_file():
                    winreg.SetValueEx(kw, AUTOSTART_NAME, 0, winreg.REG_SZ, want)

            if cur_old is not None:
                try:
                    winreg.DeleteValue(kw, LEGACY_AUTOSTART_NAME)
                except OSError:
                    pass
    except OSError:
        pass


def set_autostart(on: bool, run_key: str = RUN_KEY):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, _autostart_cmd())
            try:
                winreg.DeleteValue(k, LEGACY_AUTOSTART_NAME)
            except OSError:
                pass
        else:
            for name in (AUTOSTART_NAME, LEGACY_AUTOSTART_NAME):
                try:
                    winreg.DeleteValue(k, name)
                except OSError:
                    pass


def populate_tray_menu(menu: QtWidgets.QMenu, island, open_settings_fn, quit_fn):
    """构建原生系统托盘与右键菜单（每次展开前动态刷新文案与勾选状态）。"""
    menu.clear()
    toggle_label = TRAY_COLLAPSE if island.expanded else TRAY_EXPAND
    act_toggle = menu.addAction(toggle_label)
    act_toggle.triggered.connect(lambda _checked=False: island.toggle())

    act_settings = menu.addAction(TRAY_OPEN_SETTINGS)
    act_settings.triggered.connect(lambda: open_settings_fn())

    menu.addSeparator()

    act_pt = menu.addAction("可穿透")
    act_pt.setCheckable(True)
    act_pt.setChecked(island.passthrough())
    act_pt.triggered.connect(lambda checked=False: (
        island.set_passthrough(bool(checked)),
        act_pt.setChecked(island.passthrough()),
    ))

    act_cap = menu.addAction("在截图中显示")
    act_cap.setCheckable(True)
    act_cap.setChecked(island.capture_visible())
    act_cap.triggered.connect(lambda checked=False: (
        island.set_capture_visible(bool(checked)),
        act_cap.setChecked(island.capture_visible()),
    ))

    act_auto = menu.addAction("开机启动")
    act_auto.setCheckable(True)
    act_auto.setChecked(autostart_enabled())
    act_auto.triggered.connect(lambda checked=False: (
        set_autostart(bool(checked)),
        act_auto.setChecked(autostart_enabled()),
    ))

    menu.addSeparator()
    act_quit = menu.addAction(TRAY_QUIT)
    act_quit.triggered.connect(quit_fn)


def main():
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(DISPLAY_NAME)
    app.setApplicationDisplayName(DISPLAY_NAME)
    bg.init()

    lock = QtCore.QLockFile(str(DATA_DIR / "wisland.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        print(f"{DISPLAY_NAME} 已在运行")
        return 0
    fix_autostart()

    from .services.audio import AudioService
    from .services.clipboard_history import ClipboardHistoryService
    from .services.health_reminders import HealthReminderService
    from .services.media import MediaService
    from .services.notify import NotifyService
    from .services.shelf import ShelfService
    from .services.system import WindowService
    from .services.todo import TodoService
    from .services.water import WaterService
    from .services.lyrics import LyricsService
    from .services.mail import MailAccountManager
    from .services.weather import WeatherService, weather_glyph
    from .ui.island import Island
    from .ui.page_registry import PageRegistry
    from .ui.theme import KIND_COLORS, G, island_theme, settings_theme
    from .ui.widgets import glyph_pixmap

    island_theme.set_mode(cfg.get("island.theme", "auto"))
    settings_theme.set_mode(cfg.get("settings_window.theme", "light"))
    ctx = SimpleNamespace()
    ctx.media = MediaService()
    ctx.audio = AudioService()
    ctx.windows = WindowService()
    ctx.notify = NotifyService()
    ctx.weather = WeatherService()
    ctx.lyrics = LyricsService()
    ctx.mailcheck = None
    ctx.mail = MailAccountManager()
    from .services.mail.monitor import MailMonitorService
    ctx.mail_monitor = MailMonitorService(ctx.mail, controller=ctx.notify.controller)
    from .services.quota import QuotaManager
    ctx.quota = QuotaManager()
    ctx.shelf = ShelfService()
    ctx.clipboard = ClipboardHistoryService()
    ctx.todo = TodoService()
    ctx.water = WaterService()
    ctx.health = HealthReminderService(ctx.water, ctx.todo)
    from .live.service import LiveService
    ctx.live = LiveService()
    from .services.battery import BatteryService
    ctx.battery = BatteryService()
    ctx.notify.controller.set_focuser(ctx.windows.activate)
    ctx.windows.notice.connect(lambda t: ctx.island and ctx.island.activity({"glyph": _G().MESSAGE, "title": t}, 5.0))
    ctx.island = None

    registry = PageRegistry.create_default(ctx)
    ctx.registry = registry
    ctx.pages = registry.pages_dict()
    P = ctx.pages

    def peak():
        try:
            return ctx.audio.peak()
        except Exception:  # noqa: BLE001
            return 0.0

    island = Island(registry.visible_pages(), peak_fn=peak, registry=registry)
    ctx.island = island
    compact = island.compact

    # ---------------- 媒体 ----------------
    media_st = {"key": None, "lyr": None, "prev_view": None, "playing": False}
    back_timer = QtCore.QTimer(app, singleShot=True, interval=8000)

    def update_lyrics(m):
        k = ctx.lyrics.key(m) if m.get("title") else None
        if k != media_st["lyr"]:
            media_st["lyr"] = k
            compact.set_lyrics(None)
        if not k:
            ctx.lyrics.cancel_retry()
            return
        lyr = ctx.lyrics.get(m)
        if lyr is not None and compact.lyrics is not lyr:
            compact.set_lyrics(lyr)

    def on_lyrics_ready(k):
        if k == media_st["lyr"]:
            update_lyrics(compact.media)
            island._kick()
    ctx.lyrics.ready.connect(on_lyrics_ready)

    def back_to_view():
        # 暂停一段时间后回到原来的收起视图（时间天气 / 小形象）
        if not compact.playing() and media_st["prev_view"] and compact.view == "music":
            compact.set_view(media_st["prev_view"])
            island._kick()
        media_st["prev_view"] = None
    back_timer.timeout.connect(back_to_view)

    def on_media(m):
        compact.set_media(m)
        P["home"].media.set_media(m)
        update_lyrics(m)
        playing = bool(m.get("playing") and m.get("title"))
        key = (m.get("app_id"), m.get("title"), m.get("artist"))
        if cfg.get("media.auto_show", True):
            if playing and (not media_st["playing"] or key != media_st["key"]):
                back_timer.stop()
                if compact.view in ("clock", "pet"):
                    media_st["prev_view"] = compact.view
                    compact.set_view("music")
            elif not playing and media_st["playing"] and media_st["prev_view"]:
                back_timer.start()
        media_st["playing"], media_st["key"] = playing, key
        island._kick()
    ctx.media.changed.connect(on_media)

    def on_cover(img):
        compact.set_cover(img)
        P["home"].media.set_cover(img)
        island.update()
        island._kick()
    ctx.media.cover_changed.connect(on_cover)

    # ---------------- 音量（外部改动也弹出提示，像手机一样） ----------------
    vol_state = {"init": True, "last": None}

    def on_volume(v, muted):
        P["home"].set_muted(muted)
        key = (round(v, 3), muted)
        if vol_state["init"]:
            vol_state["init"] = False
        elif key != vol_state["last"] and not island.expanded:
            act = {"type": "volume", "glyph": G.MUTE if muted else G.VOLUME, "value": v, "muted": muted}
            if compact.activity and compact.activity.get("type") == "volume":
                compact.update_activity(**act, until=time.monotonic() + 1.8)
            else:
                island.activity(act, 1.8)
            island._kick()
        vol_state["last"] = key
    ctx.audio.volume_changed.connect(on_volume)
    compact.set_view(cfg.get("island.compact_view", "clock"))
    compact.set_clock_appearance(
        style=cfg.get("island.clock_style", "minimal"),
        color=cfg.get("island.clock_color", "white"),
        custom_color=cfg.get("island.clock_custom_color", "#FFBF5E"),
        gradient=cfg.get("island.clock_gradient", ["#5AE0FF", "#FF6BA8"]),
        show_weather=bool(cfg.get("island.compact_weather", True)),
    )

    def on_wheel(d):
        if island.edge in ("left", "right"):
            # 侧边不加小表情：仅在 clock ↔ music 间切换
            v = "music" if compact.view != "music" else "clock"
            compact.set_view(v)
            compact._view_t = time.monotonic()
            cfg.set("island.compact_view", v)
            island._kick()
            return
        v = compact.cycle_view(d)
        cfg.set("island.compact_view", v)
        island._kick()
    island.wheel_compact.connect(on_wheel)

    # ---------------- 消息提醒 ----------------
    def on_notified(n):
        compact.pet.react("surprise")
        P["home"].pet.face.react("surprise")

    def on_banner_changed(ev):
        if ev is None:
            compact.clear_activity("notify")
            island._kick()
            return
        kind = ev.source_key
        svg_key = kind if kind in ("wechat", "qq") else ""
        glyph = {"mail": G.MAIL, "qq": G.CHAT, "wechat": G.MESSAGE}.get(kind, G.BELL)
        color = None if svg_key else KIND_COLORS.get(kind, KIND_COLORS["other"])
        sub = ctx.notify.controller.format_banner_sub(ev)
        dur = max(1.0, min(30.0, float(cfg.get("notify.display_seconds", 4) or 4)))
        island.activity({
            "type": "notify",
            "event_id": ev.event_id,
            "svg_key": svg_key,
            "glyph": glyph,
            "color": color,
            "title": ev.app_name,
            "sub": sub,
        }, dur + 0.5)
        island._kick()

    def on_counts(c):
        compact.badges = dict(c)
        island.unread_dock.sync_counts(c)
        fg_watcher.update_unread_counts(c)
        island._kick()

    from .services.notifications.foreground import ForegroundClientWatcher
    fg_watcher = ForegroundClientWatcher(
        parent=app,
        ambiguous_fn=ctx.notify.controller.is_source_ambiguous,
    )
    fg_watcher.foreground_matched.connect(ctx.notify.clear)

    def on_unread_dock_clicked(source_key: str):
        if not ctx.notify.open(source_key):
            island.unread_dock.trigger_fail_shake(source_key)

    island.unread_dock.item_clicked.connect(on_unread_dock_clicked)
    ctx.notify.notified.connect(on_notified)
    ctx.notify.banner_changed.connect(on_banner_changed)
    ctx.notify.counts_changed.connect(on_counts)
    ctx.notify.notice.connect(lambda t, _err: island.activity({"glyph": G.BELL, "title": t}, 3.2))
    def open_mail_notification(ev):
        meta = getattr(ev, "meta", None) or {}
        acc_id = str(meta.get("account_id") or getattr(ev, "account_key", "") or "")
        uid = str(meta.get("uid") or "")
        uv = str(meta.get("uid_validity") or "")
        if "mail" in P:
            island.expand("mail")
            return bool(P["mail"].open_notification_target(acc_id, uid, uv))
        return False

    ctx.notify.controller.mail_opener = open_mail_notification
    island.flash.connect(ctx.notify.on_flash)
    island.notify_banner_clicked.connect(ctx.notify.open_current_banner)
    island.notify_banner_dismissed.connect(ctx.notify.dismiss_current_banner)

    # ---------------- 天气 ----------------
    def on_weather(d):
        P["weather"].set_data(d)
        if d.get("ok") or d.get("temp") is not None:
            g, fam = weather_glyph(d.get("icon"))
            compact.weather = (g, f"{round(d.get('temp', 0))}°", fam, d.get("icon") or "")
        island._kick()
    ctx.weather.updated.connect(on_weather)

    # ---------------- 实时字幕：收起态显示最新一句 ----------------
    # 收起态两行显示：上面日语、下面中文；有人说话时优先显示，安静几秒后回到原视图（不打断音乐）
    sub_st = {"sid": None}

    def on_partial(jp):
        if jp:
            compact.set_sub(jp=jp)
            island._kick()

    def on_segment(sid, jp):
        if sid != sub_st["sid"]:
            sub_st["sid"] = sid
        compact.set_sub(jp=jp)
        island._kick()

    def on_zh(sid, zh):
        if zh:
            compact.set_sub(zh=zh)
            island._kick()

    def on_running(on):
        compact.set_sub_running(on)
        island._kick()
    ctx.live.partial.connect(on_partial)
    ctx.live.segment.connect(on_segment)
    ctx.live.live_zh.connect(on_zh)
    ctx.live.final_zh.connect(on_zh)
    ctx.live.running_changed.connect(on_running)
    island.play_pause_requested.connect(lambda: ctx.media.play_pause())
    ctx.live.error.connect(lambda m: island.activity({"glyph": G.LIVE, "title": "实时字幕", "sub": m}, 4))
    ctx.battery.low_battery.connect(
        lambda _id, name, pct, _k: island.activity(
            {"glyph": G.BOLT, "title": f"{name} 电量低", "sub": f"剩余 {pct}%，请及时充电", "color": "#f87171"},
            5.0,
        )
    )
    ctx.battery.full_battery.connect(
        lambda _id, name, _k: island.activity(
            {"glyph": G.BOLT, "title": f"{name} 已充满", "sub": "电量 100%，可拔掉电源", "color": "#34d399"},
            5.0,
        )
    )

    # ---------------- 生活提醒事件（岛内队列：water/sit 使用主题 SVG 图标；todo 保持不变） ----------------
    def on_life_reminder(kind: str, title: str, sub: str):
        if kind in ("water", "sit"):
            island.show_life_reminder(kind, title, sub, duration=8.0)
            return
        island.activity({
            "glyph": G.CHECK if kind == "todo" else G.BELL,
            "color": QtGui.QColor("#ff9f0a") if kind == "todo" else KIND_COLORS["other"],
            "title": title,
            "sub": sub,
        }, 5.0)
    ctx.health.reminder_triggered.connect(on_life_reminder)

    def on_life_reminder_clicked(kind: str):
        if "life" in P:
            island.expand("life")
            if kind in ("water", "sit"):
                P["life"].switch_view(kind)
    island.life_reminder_clicked.connect(on_life_reminder_clicked)

    # ---------------- 热键 ----------------
    def on_hotkey(name):
        if name == "toggle":
            island.toggle()
    island.hotkey.connect(on_hotkey)

    def on_resume_from_sleep():
        try:
            ctx.weather.refresh()
        except Exception:
            pass
        try:
            ctx.quota.refresh_all()
        except Exception:
            pass
        try:
            ctx.mail_monitor.apply_config()
        except Exception:
            pass
    island.resumed_from_sleep.connect(on_resume_from_sleep)

    # ---------------- 菜单 / 托盘 / 设置窗口 ----------------
    def open_settings(category: str | None = None, account_id: str | None = None):
        from .ui.settings_window import SettingsWindow
        SettingsWindow.open_instance(ctx, category=category, account_id=account_id)
    ctx.open_settings = open_settings

    cycle_timer = QtCore.QTimer(app)

    def apply_runtime_config(show_toast: bool = False):
        island_theme.set_mode(cfg.get("island.theme", "auto"))
        settings_theme.set_mode(cfg.get("settings_window.theme", "light"))
        compact.set_clock_appearance(
            style=cfg.get("island.clock_style", "minimal"),
            color=cfg.get("island.clock_color", "white"),
            custom_color=cfg.get("island.clock_custom_color", "#FFBF5E"),
            gradient=cfg.get("island.clock_gradient", ["#5AE0FF", "#FF6BA8"]),
            show_weather=bool(cfg.get("island.compact_weather", True)),
        )
        island.apply_config()
        if not island.expanded and compact.view in ("clock", "pet", "music"):
            want_view = cfg.get("island.compact_view", "clock")
            if want_view in ("clock", "pet", "music") and not compact.playing():
                compact.set_view(want_view)
        cyc_s = int(cfg.get("island.auto_cycle_s", 30) or 0)
        if cyc_s > 0:
            cycle_timer.setInterval(cyc_s * 1000)
            if not cycle_timer.isActive():
                cycle_timer.start()
        else:
            cycle_timer.stop()
        ctx.weather.refresh()
        if cfg.get("tools.clipboard_enabled", True):
            ctx.clipboard.start()
        else:
            ctx.clipboard.stop()
        ctx.notify.apply_config()
        if "tools" in P:
            P["tools"].refresh_shelf()
            P["tools"].refresh_clipboard()
        if "life" in P:
            P["life"].refresh_all()
        if show_toast:
            island.activity({"glyph": G.CHECK, "title": "设置已生效"}, 1.8)

    cfg.changed.connect(lambda: apply_runtime_config(show_toast=True))

    quota_vis_st = {"show": bool(P["quota"].should_show()) if "quota" in P else False}

    def sync_quota_page_visibility(*_):
        if "quota" not in P:
            return
        cur_show = bool(P["quota"].should_show())
        if cur_show != quota_vis_st["show"]:
            quota_vis_st["show"] = cur_show
            registry.sync_from_config(cfg)
            island.rebuild_pages(registry.visible_pages())

    ctx.quota.accounts_changed.connect(sync_quota_page_visibility)
    ctx.quota.account_state_changed.connect(sync_quota_page_visibility)
    cfg.changed.connect(sync_quota_page_visibility)

    def reload_cfg():
        cfg.load()
        apply_runtime_config(show_toast=False)
        island.activity({"glyph": G.CHECK, "title": "配置已重新加载"}, 1.8)
    ctx.reload_cfg = reload_cfg

    def build_menu(menu: QtWidgets.QMenu):
        populate_tray_menu(menu, island, open_settings, app.quit)

    def show_menu(pos):
        import ctypes
        from ctypes import wintypes
        m = QtWidgets.QMenu()
        build_menu(m)
        island.locks.add("menu")
        m.aboutToHide.connect(lambda: island.locks.discard("menu"))
        m.aboutToHide.connect(m.deleteLater)
        m.popup(pos)
        try:
            u32 = ctypes.WinDLL("user32", use_last_error=True)
            u32.SetForegroundWindow.argtypes = [wintypes.HWND]
            u32.SetForegroundWindow.restype = wintypes.BOOL
            u32.SetForegroundWindow(int(m.winId()))
        except Exception:  # noqa: BLE001
            pass
        m.activateWindow()
        m.setFocus(QtCore.Qt.FocusReason.PopupFocusReason)
    island.menu_requested.connect(show_menu)

    # 可穿透时收起态直接显示小形象（首页不再提供快捷按钮，保留运行时同步）
    def on_pt(on):
        if on:
            compact.set_view("pet")            # 穿透时直接显示小形象
            island._kick()
    island.passthrough_changed.connect(on_pt)
    if island.passthrough():
        compact.set_view("pet")

    icon_path = Path(__file__).resolve().parent / "icon.png"
    if icon_path.exists():
        app_icon = QtGui.QIcon(str(icon_path))
    else:
        app_icon = QtGui.QIcon(glyph_pixmap(G.APPS, 32, QtGui.QColor("#4f8cff")))
    app.setWindowIcon(app_icon)
    tray = QtWidgets.QSystemTrayIcon(app_icon, app)
    tray.setToolTip(TRAY_TOOLTIP)
    tmenu = QtWidgets.QMenu()
    tmenu.aboutToShow.connect(lambda: build_menu(tmenu))
    tray.setContextMenu(tmenu)
    tray.activated.connect(lambda r: island.toggle() if r == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()

    # ---------------- 收起态：时间天气与小形象每隔几十秒自动轮换 ----------------
    def auto_cycle():
        if island.expanded or island.passthrough() or island.edge in ("left", "right") or compact.view not in ("clock", "pet"):
            return
        compact.set_view("pet" if compact.view == "clock" else "clock")
        island._kick()
    cycle_timer.timeout.connect(auto_cycle)
    cyc = int(cfg.get("island.auto_cycle_s", 30) or 0)
    if cyc > 0:
        cycle_timer.setInterval(cyc * 1000)
        cycle_timer.start()

    # ---------------- 全屏时隐藏 ----------------
    def check_fullscreen():
        if not cfg.get("island.hide_fullscreen", True):
            island.set_fullscreen_hidden(False)
            return
        try:
            island.set_fullscreen_hidden(ctx.windows.foreground_is_fullscreen(int(island.winId())))
        except Exception:  # noqa: BLE001
            pass
    fs_timer = QtCore.QTimer(app, interval=1000, timeout=check_fullscreen)

    # ---------------- 启动 ----------------
    island.show()
    for s in (ctx.media, ctx.audio, ctx.windows, ctx.notify,
              ctx.weather, ctx.mail_monitor, ctx.quota, ctx.battery, ctx.clipboard, ctx.health):
        try:
            if hasattr(s, "start"):
                s.start()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
    fs_timer.start()
    from .core.memory import trim_working_set
    QtCore.QTimer.singleShot(8000, lambda: trim_working_set(collect_gc=True))

    def shutdown():
        fs_timer.stop()
        cycle_timer.stop()
        try:
            island.close()
        except Exception:
            pass
        fg_watcher.stop()
        ctx.live.stop(wait=True)
        ctx.mail_monitor.stop()
        ctx.mail.shutdown()
        ctx.quota.stop()
        ctx.battery.stop()
        for s in (ctx.media, ctx.audio, ctx.notify,
                  ctx.weather, ctx.clipboard, ctx.health):
            try:
                s.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            cfg.save()
        except Exception:  # noqa: BLE001
            pass
        bg.shutdown(wait=False)
        lock.unlock()
    app.aboutToQuit.connect(shutdown)
    return app.exec()
