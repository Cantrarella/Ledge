# -*- coding: utf-8 -*-
"""配置：项目根目录下的 config.json，缺省值见 DEFAULTS。点号路径读写：cfg.get("weather.city")。
支持事务式草稿（ConfigDraft）、字段校验（validate_config）、原子写入与未知字段保留。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import threading
from pathlib import Path

from PySide6 import QtCore

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

DEFAULTS: dict = {
    "schema_version": 2,
    "pages": {
        "order": ["home", "mail", "weather", "trans", "tools", "life"],
        "visible": {
            "home": True,
            "mail": True,
            "weather": True,
            "trans": True,
            "tools": True,
            "life": True,
        },
    },
    "tools": {
        "shelf_enabled": True,
        "clipboard_enabled": True,
    },
    "life": {
        "water_remind": True,
        "water_goal_ml": 2000,
        "water_serving_ml": 250,
        "water_interval_min": 45,
        "sit_remind": True,
        "sit_interval_min": 60,
        "remind_start_hour": 9,
        "remind_end_hour": 22,
        "lunch_rest": True,
        "lunch_start": "12:00",
        "lunch_end": "13:30",
    },
    "island": {
        "edge": "top",              # top / bottom / left / right
        "pos": 0.5,                 # 沿边位置 0..1
        "screen": 0,
        "theme": "auto",            # auto / light / dark / rouge / sky
        "hide_fullscreen": False,   # True = 前台全屏（游戏/视频）时隐藏灵动岛
        "hover_expand_ms": 120,
        "collapse_delay_ms": 320,
        "exclude_from_capture": False,  # False = 灵动岛出现在截图 / 录屏里（取背景时只临时排除自己）
        "blur_fps": 8,
        "scale": 1.0,
        "default_tab": "home",
        "expand_mode": "hover",     # hover = 悬停展开 / always = 常驻展开 / never = 保持收起
        "pinned": False,            # 旧版"保持展开"（兼容）
        "compact_view": "clock",    # 收起态视图：clock / pet / music（滚轮切换）
        "auto_cycle_s": 30,         # 时间天气与小形象自动轮换的间隔（秒），0 = 不轮换
        "passthrough": False,       # 可穿透：鼠标穿过灵动岛，只响应右键（不影响展开 / 收起）
    },
    "hotkeys": {
        "toggle": "Ctrl+Alt+I",
    },
    "media": {
        "auto_show": True,           # 开始播放时收起态自动切到音乐（暂停 8 秒后切回）
        "lyrics": True,              # 收起态显示歌词（网易云公开歌词接口）
        "netease_log": True,         # 读取网易云本地日志获得播放状态与进度（网易云默认不接入系统媒体控件）
        "priority": ["cloudmusic", "bilibili", "chrome", "zen", "msedge", "firefox"],
    },
    "notify": {
        # wpndatabase 中的 NotificationHandler.PrimaryId 前缀 -> 显示名 / 类型
        "toast_apps": {
            "QQ": {"name": "QQ", "kind": "qq"},
            "ThunderbirdToast": {"name": "邮件", "kind": "mail"},
            "Weixin": {"name": "微信", "kind": "wechat"},
        },
        # 任务栏闪烁（FlashWindow）的进程 -> 显示名 / 类型
        "flash_processes": {
            "weixin.exe": {"name": "微信", "kind": "wechat"},
            "wechat.exe": {"name": "微信", "kind": "wechat"},
            "qq.exe": {"name": "QQ", "kind": "qq"},
            "thunderbird.exe": {"name": "邮件", "kind": "mail"},
        },
        "open": {
            "qq": "",          # 例：C:/Program Files/Tencent/QQNT/QQ.exe
            "wechat": "",      # 例：C:/Program Files/Tencent/Weixin/Weixin.exe
            "mail": "",        # 例：C:/Program Files/Mozilla Thunderbird/thunderbird.exe
        },
        "enabled": True,             # 开启 Windows 通知读取
        "wechat_enabled": True,      # 微信通知开关
        "qq_enabled": True,          # QQ 通知开关
        "other_enabled": True,       # 其他系统通知开关
        "flash_compat": True,        # 任务栏闪烁兼容模式
        "preview_enabled": False,    # 是否显示通知摘要（默认关闭以保护隐私）
        "display_seconds": 4,        # 通知横幅显示时长（秒）
        "dnd_enabled": False,        # 免打扰时段开关
        "dnd_start": "23:00",        # 免打扰开始时间
        "dnd_end": "07:00",          # 免打扰结束时间
    },
    "weather": {
        "api_key": "",         # 留空 = 使用 Open-Meteo（免费、无需 Key）；填 OpenWeather Key 则改用 OpenWeather
        "city": "上海",
        "lat": None,
        "lon": None,
        "units": "metric",
        "lang": "zh_cn",
        "interval_min": 15,
        "cities": [],          # [{"name": "上海", "lat": 31.23, "lon": 121.47}]，第一个显示在收起态
    },
    "translate": {
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "import_from": "",           # 可选：从另一个 JSON 配置的 translate.api_key 读取 Key
    },
    "apps": {
        # 托盘里的应用（窗口被隐藏）：发送应用自己的"显示窗口"热键让它恢复（微信默认 Ctrl+Alt+W）
        "restore_hotkey": {
            "weixin.exe": "Ctrl+Alt+W",
            "wechat.exe": "Ctrl+Alt+W",
        },
    },
    "mail": {
        "interval_s": 120,
        "sync_read_to_server": False,
        "otp_enabled": True,
        "otp_hide_seconds": 180,
        # IMAP 账户（首次运行自动从 Thunderbird 读取服务器和用户名）；填入授权码 / 应用专用密码后才会检查
        "imap": [],
        # 多账号邮件元数据（凭据仅存 Windows 凭据管理器，这里只保存 credential_key 引用）
        "accounts": [],
    },
    "live": {
        "models_dir": "models",     # parakeet-ja-0.6b-int8/ 与 silero_vad.onnx 所在目录
        "audio": {"source": "loopback", "device": "", "block_ms": 80},
        "vad": {"threshold": 0.4, "min_silence_ms": 350, "min_speech_ms": 200, "max_segment_s": 10},
        "asr": {"partial": True, "draft_mode": "rolling", "draft_interval_ms": 500, "draft_threads": 4,
                "final_engine": "parakeet", "final_threads": 6, "max_segment_s": 10, "context_prompt": False},
        "translate": {"enabled": True, "live": True, "live_min_interval_ms": 900, "live_min_new_chars": 4,
                      "context_lines": 3},
    },
}

VALID_EDGES = ("top", "bottom", "left", "right")
VALID_THEMES = ("auto", "light", "dark", "rouge", "sky")
VALID_EXPAND_MODES = ("hover", "always", "never")
VALID_COMPACT_VIEWS = ("clock", "pet", "music")
VALID_AUDIO_SOURCES = ("loopback", "mic")
REGISTERED_PAGE_KEYS = ("home", "mail", "weather", "trans", "tools", "life")
_MOD_NAMES = {"alt", "ctrl", "control", "shift", "win"}


def _merge(base: dict, over: dict) -> dict:
    """递归合并字典，完整保留 over 中的未知顶层或嵌套字段。"""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("alerts",):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(str(path or "")))


def is_valid_hotkey(spec: str) -> bool:
    """校验热键字符串（空字符串表示禁用热键，视为合法；非空需含有效主键）。"""
    s = (spec or "").strip()
    if not s:
        return True
    parts = [x.strip().lower() for x in s.split("+") if x.strip()]
    if not parts:
        return False
    vk_count = 0
    for x in parts:
        if x in _MOD_NAMES:
            continue
        if len(x) == 1 and x.isalnum():
            vk_count += 1
        elif x.startswith("f") and x[1:].isdigit() and 1 <= int(x[1:]) <= 24:
            vk_count += 1
        elif x == "space":
            vk_count += 1
        else:
            return False
    return vk_count == 1


def validate_config(raw: dict, strict: bool = True) -> tuple[bool, list[str], dict]:
    """校验并规范化配置字典。
    - strict=True：用于设置界面保存，遇到越界/非法值返回 (False, errors, merged) 且拒绝覆盖；
    - strict=False：用于启动加载容错，自动将非法字段回退或夹紧到安全范围。
    未知字段始终完整保留。
    """
    if not isinstance(raw, dict):
        return False, ["配置根节点必须是 JSON 对象"], copy.deepcopy(DEFAULTS)

    errors: list[str] = []
    merged = _merge(DEFAULTS, raw)

    def _err(msg: str):
        errors.append(msg)

    # schema_version
    sv = merged.get("schema_version", 2)
    if not isinstance(sv, int) or isinstance(sv, bool) or sv < 1:
        if strict:
            _err(f"无效的 schema_version: {sv}")
        else:
            merged["schema_version"] = 2

    # pages (order & visible，保留未注册页面的配置项，但要求已注册页面至少保留一个可见)
    pgs = merged.get("pages")
    if not isinstance(pgs, dict):
        _err("pages 配置必须是对象")
        merged["pages"] = copy.deepcopy(DEFAULTS["pages"])
        pgs = merged["pages"]
    else:
        raw_order = pgs.get("order")
        if not isinstance(raw_order, list) or not all(isinstance(x, str) and x.strip() for x in raw_order):
            _err("pages.order 必须是非空字符串列表")
            if not strict:
                raw_order = list(DEFAULTS["pages"]["order"])
        if isinstance(raw_order, list):
            seen: list[str] = []
            for k in raw_order:
                if isinstance(k, str) and k != "msg" and k not in seen:
                    seen.append(k)
            pgs["order"] = seen or list(DEFAULTS["pages"]["order"])

        raw_vis = pgs.get("visible")
        if not isinstance(raw_vis, dict):
            _err("pages.visible 必须是对象")
            if not strict:
                raw_vis = copy.deepcopy(DEFAULTS["pages"]["visible"])
                pgs["visible"] = raw_vis
        if isinstance(raw_vis, dict):
            raw_user_vis = (raw.get("pages") or {}).get("visible") if isinstance(raw.get("pages"), dict) else None
            vis_out = copy.deepcopy(raw_user_vis) if isinstance(raw_user_vis, dict) else copy.deepcopy(raw_vis)
            vis_out.pop("msg", None)
            for k, v in list(vis_out.items()):
                if not isinstance(v, bool):
                    _err(f"pages.visible.{k} 必须是布尔值")
                    if not strict:
                        vis_out[k] = bool(v)
            if not any(vis_out.get(rk, True) is True for rk in REGISTERED_PAGE_KEYS):
                _err("至少需要保留一个可见页面")
                if not strict:
                    first_reg = next((k for k in pgs.get("order", REGISTERED_PAGE_KEYS) if k in REGISTERED_PAGE_KEYS), "home")
                    vis_out[first_reg] = True
            pgs["visible"] = vis_out

    isl = merged.get("island")
    if not isinstance(isl, dict):
        _err("island 配置必须是对象")
        merged["island"] = copy.deepcopy(DEFAULTS["island"])
        isl = merged["island"]

    # edge
    edge = isl.get("edge", "top")
    if edge not in VALID_EDGES:
        _err(f"无效的停靠边: {edge}")
        if not strict:
            isl["edge"] = "top"

    # pos
    pos = isl.get("pos", 0.5)
    if not isinstance(pos, (int, float)) or isinstance(pos, bool) or not (0.0 <= float(pos) <= 1.0):
        _err(f"沿边位置超出范围 (0.0~1.0): {pos}")
        if not strict:
            isl["pos"] = 0.5
    else:
        isl["pos"] = round(float(pos), 4)

    # screen
    scr = isl.get("screen", 0)
    if not isinstance(scr, int) or isinstance(scr, bool) or scr < 0:
        _err(f"显示器编号无效: {scr}")
        if not strict:
            isl["screen"] = 0

    # theme
    thm = isl.get("theme", "auto")
    if thm not in VALID_THEMES:
        _err(f"无效的主题模式: {thm}")
        if not strict:
            isl["theme"] = "auto"

    # hover_expand_ms
    he_ms = isl.get("hover_expand_ms", 120)
    if not isinstance(he_ms, (int, float)) or isinstance(he_ms, bool) or not (0 <= int(he_ms) <= 5000):
        _err(f"悬停展开延迟需在 0~5000 毫秒之间: {he_ms}")
        if not strict:
            isl["hover_expand_ms"] = 120
    else:
        isl["hover_expand_ms"] = int(he_ms)

    # collapse_delay_ms
    cd_ms = isl.get("collapse_delay_ms", 320)
    if not isinstance(cd_ms, (int, float)) or isinstance(cd_ms, bool) or not (0 <= int(cd_ms) <= 10000):
        _err(f"收起延迟需在 0~10000 毫秒之间: {cd_ms}")
        if not strict:
            isl["collapse_delay_ms"] = 320
    else:
        isl["collapse_delay_ms"] = int(cd_ms)

    # blur_fps
    bfps = isl.get("blur_fps", 8)
    if not isinstance(bfps, (int, float)) or isinstance(bfps, bool) or not (1 <= float(bfps) <= 60):
        _err(f"毛玻璃刷新帧率需在 1~60 之间: {bfps}")
        if not strict:
            isl["blur_fps"] = 8
    else:
        isl["blur_fps"] = int(bfps) if float(bfps).is_integer() else round(float(bfps), 1)

    # scale
    scale = isl.get("scale", 1.0)
    if not isinstance(scale, (int, float)) or isinstance(scale, bool) or not (0.5 <= float(scale) <= 3.0):
        _err(f"缩放比例需在 0.5~3.0 之间: {scale}")
        if not strict:
            isl["scale"] = 1.0
    else:
        isl["scale"] = round(float(scale), 2)

    # expand_mode
    emode = isl.get("expand_mode", "hover")
    if emode not in VALID_EXPAND_MODES:
        _err(f"无效的展开模式: {emode}")
        if not strict:
            isl["expand_mode"] = "hover"
    isl["pinned"] = (isl.get("expand_mode") == "always")

    # compact_view
    cview = isl.get("compact_view", "clock")
    if cview not in VALID_COMPACT_VIEWS:
        _err(f"无效的收起态视图: {cview}")
        if not strict:
            isl["compact_view"] = "clock"

    # auto_cycle_s
    acyc = isl.get("auto_cycle_s", 30)
    if not isinstance(acyc, (int, float)) or isinstance(acyc, bool) or not (0 <= int(acyc) <= 3600):
        _err(f"自动轮换间隔需在 0~3600 秒之间: {acyc}")
        if not strict:
            isl["auto_cycle_s"] = 30
    else:
        isl["auto_cycle_s"] = int(acyc)

    # hotkeys
    hks = merged.get("hotkeys")
    if not isinstance(hks, dict):
        _err("hotkeys 配置必须是对象")
        merged["hotkeys"] = copy.deepcopy(DEFAULTS["hotkeys"])
    else:
        for hk_name, hk_val in hks.items():
            if not isinstance(hk_val, str) or not is_valid_hotkey(hk_val):
                _err(f"无效的热键格式 ({hk_name}): {hk_val}")
                if not strict:
                    hks[hk_name] = DEFAULTS["hotkeys"].get(hk_name, "")

    # weather
    wth = merged.get("weather")
    if not isinstance(wth, dict):
        _err("weather 配置必须是对象")
        merged["weather"] = copy.deepcopy(DEFAULTS["weather"])
    else:
        iv_min = wth.get("interval_min", 15)
        if not isinstance(iv_min, (int, float)) or isinstance(iv_min, bool) or not (1 <= int(iv_min) <= 1440):
            _err(f"天气刷新间隔需在 1~1440 分钟之间: {iv_min}")
            if not strict:
                wth["interval_min"] = 15
        else:
            wth["interval_min"] = int(iv_min)
        city = str(wth.get("city", "") or "").strip()
        if not city:
            _err("默认城市名称不能为空")
            if not strict:
                wth["city"] = "上海"
        else:
            wth["city"] = city

    # mail
    ml = merged.get("mail")
    if not isinstance(ml, dict):
        _err("mail 配置必须是对象")
        merged["mail"] = copy.deepcopy(DEFAULTS["mail"])
    else:
        m_iv = ml.get("interval_s", 120)
        if not isinstance(m_iv, (int, float)) or isinstance(m_iv, bool) or not (15 <= int(m_iv) <= 86400):
            _err(f"邮件检查间隔需在 15~86400 秒之间: {m_iv}")
            if not strict:
                ml["interval_s"] = 120
        else:
            ml["interval_s"] = int(m_iv)
        ml["sync_read_to_server"] = bool(ml.get("sync_read_to_server", False))
        ml["otp_enabled"] = bool(ml.get("otp_enabled", True))
        hide_s = ml.get("otp_hide_seconds", 180)
        if isinstance(hide_s, bool) or not isinstance(hide_s, (int, float)) or int(hide_s) not in (30, 60, 180, 300):
            _err(f"验证码停留时长需为 60 / 180 / 300 秒: {hide_s}")
            if not strict:
                ml["otp_hide_seconds"] = 180
        else:
            ml["otp_hide_seconds"] = int(hide_s)
        raw_accs = ml.get("accounts", [])
        if not isinstance(raw_accs, list):
            _err("mail.accounts 必须是数组")
            if not strict:
                ml["accounts"] = []
        else:
            _bad_keys = {"password", "passwd", "pwd", "auth_code", "secret", "cookie", "cookies", "token", "oauth_token"}
            ml["accounts"] = [
                {k: v for k, v in x.items() if str(k).lower() not in _bad_keys}
                for x in raw_accs if isinstance(x, dict)
            ]

    # live
    lv = merged.get("live")
    if not isinstance(lv, dict):
        _err("live 配置必须是对象")
        merged["live"] = copy.deepcopy(DEFAULTS["live"])
    else:
        aud = lv.get("audio")
        if isinstance(aud, dict):
            src = aud.get("source", "loopback")
            if src not in VALID_AUDIO_SOURCES:
                _err(f"无效的字幕音频源: {src}")
                if not strict:
                    aud["source"] = "loopback"
        vad = lv.get("vad")
        if isinstance(vad, dict):
            th = vad.get("threshold", 0.4)
            if not isinstance(th, (int, float)) or isinstance(th, bool) or not (0.05 <= float(th) <= 0.95):
                _err(f"VAD 阈值需在 0.05~0.95 之间: {th}")
                if not strict:
                    vad["threshold"] = 0.4
            else:
                vad["threshold"] = round(float(th), 2)
            ms = vad.get("max_segment_s", 10)
            if not isinstance(ms, (int, float)) or isinstance(ms, bool) or not (2 <= int(ms) <= 60):
                _err(f"最长单句时长需在 2~60 秒之间: {ms}")
                if not strict:
                    vad["max_segment_s"] = 10
            else:
                vad["max_segment_s"] = int(ms)
            if isinstance(lv.get("asr"), dict):
                lv["asr"]["max_segment_s"] = vad["max_segment_s"]

    # life
    lf = merged.get("life")
    if not isinstance(lf, dict):
        _err("life 配置必须是对象")
        merged["life"] = copy.deepcopy(DEFAULTS["life"])
    else:
        def _chk_int(k: str, lo: int, hi: int, label: str, default_v: int):
            v = lf.get(k, default_v)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not (lo <= int(v) <= hi):
                _err(f"{label}需在 {lo}~{hi} 之间: {v}")
                if not strict:
                    lf[k] = default_v
            else:
                lf[k] = int(v)

        _chk_int("water_goal_ml", 500, 6000, "每日饮水目标(ml)", 2000)
        _chk_int("water_serving_ml", 50, 1000, "每次饮水记录(ml)", 250)
        _chk_int("water_interval_min", 5, 180, "喝水提醒间隔(分钟)", 45)
        _chk_int("sit_interval_min", 15, 180, "久坐提醒间隔(分钟)", 60)
        _chk_int("remind_start_hour", 0, 23, "提醒开始小时", 9)
        _chk_int("remind_end_hour", 1, 24, "提醒结束小时", 22)
        if lf.get("remind_start_hour", 9) >= lf.get("remind_end_hour", 22):
            _err("提醒结束时间必须晚于开始时间")
            if not strict:
                lf["remind_start_hour"] = 9
                lf["remind_end_hour"] = 22

        for lk, ldef in (("lunch_start", "12:00"), ("lunch_end", "13:30")):
            s = str(lf.get(lk, ldef) or "").strip()
            parts = s.split(":")
            ok_hm = (len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit()
                     and 0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59)
            if not ok_hm:
                _err(f"午休时间格式应为 HH:MM: {s}")
                if not strict:
                    lf[lk] = ldef
            else:
                lf[lk] = f"{int(parts[0]):02d}:{int(parts[1]):02d}"

    return (len(errors) == 0), errors, merged


class ConfigDraft:
    """事务式配置草稿：在设置界面中编辑，不直接修改全局 cfg 或写盘；提交时统一校验并原子保存。"""

    def __init__(self, initial_data: dict):
        self.data: dict = copy.deepcopy(initial_data)

    def get(self, key: str, default=None):
        cur = self.data
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return copy.deepcopy(cur)

    def set(self, key: str, value):
        parts = key.split(".")
        cur = self.data
        for part in parts[:-1]:
            if not isinstance(cur.get(part), dict):
                cur[part] = {}
            cur = cur[part]
        cur[parts[-1]] = copy.deepcopy(value)

    def to_dict(self) -> dict:
        return copy.deepcopy(self.data)


class Config(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self, path: Path | None = None):
        super().__init__()
        self._lock = threading.RLock()
        self.path: Path = Path(path) if path is not None else CONFIG_PATH
        self.data: dict = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self):
        user = {}
        existed = self.path.exists()
        parse_ok = True
        if existed:
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if isinstance(loaded, dict):
                    user = loaded
                else:
                    parse_ok = False
                    print("config.json 根节点不是对象，使用默认值")
            except Exception as e:  # noqa: BLE001
                parse_ok = False
                print("config.json 解析失败，使用默认值：", e)
        with self._lock:
            _, _, cleaned = validate_config(_merge(DEFAULTS, user), strict=False)
            self.data = cleaned
        if not existed and parse_ok:
            self.save()

    def save(self):
        with self._lock:
            text = json.dumps(self.data, ensure_ascii=False, indent=2)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)

    def create_draft(self) -> ConfigDraft:
        """创建当前配置的深拷贝草稿（包含所有未知字段）。"""
        with self._lock:
            return ConfigDraft(self.data)

    def validate(self, draft_or_dict: ConfigDraft | dict, strict: bool = True) -> tuple[bool, list[str], dict]:
        raw = draft_or_dict.to_dict() if isinstance(draft_or_dict, ConfigDraft) else draft_or_dict
        if not isinstance(raw, dict):
            return False, ["配置数据必须是字典"], copy.deepcopy(self.data)
        with self._lock:
            # 将当前配置中的未知字段作底，再合并草稿修改，确保未知字段不丢失
            combined = _merge(self.data, raw)
            # 对草稿中显式替换的字典字段（如 cities、open 等列表/字典）以草稿为准
            for k, v in raw.items():
                if isinstance(v, dict) and isinstance(combined.get(k), dict):
                    combined[k] = _merge(combined[k], v)
                else:
                    combined[k] = copy.deepcopy(v)
        return validate_config(combined, strict=strict)

    def commit_draft(self, draft_or_dict: ConfigDraft | dict, strict: bool = True) -> tuple[bool, list[str]]:
        """校验并提交草稿：校验失败时不修改内存与磁盘原配置；成功后原子写入并发出 changed 信号。"""
        ok, errors, cleaned = self.validate(draft_or_dict, strict=strict)
        if not ok:
            return False, errors
        with self._lock:
            self.data = cleaned
            self.save()
        self.changed.emit()
        return True, []

    def reset_to_defaults(self, preserve_unknown: bool = True):
        """恢复默认配置（可选保留未知扩展字段），原子写盘并通知。"""
        with self._lock:
            if preserve_unknown:
                unknowns = {k: copy.deepcopy(v) for k, v in self.data.items() if k not in DEFAULTS}
                new_data = copy.deepcopy(DEFAULTS)
                new_data.update(unknowns)
                self.data = new_data
            else:
                self.data = copy.deepcopy(DEFAULTS)
            self.save()
        self.changed.emit()

    def get(self, key: str, default=None):
        with self._lock:
            cur = self.data
            for part in key.split("."):
                if not isinstance(cur, dict) or part not in cur:
                    return default
                cur = cur[part]
            return cur

    def set(self, key: str, value, save: bool = True):
        with self._lock:
            parts = key.split(".")
            cur = self.data
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = value
        if save:
            self.save()


cfg = Config()
