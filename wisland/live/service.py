# -*- coding: utf-8 -*-
"""实时字幕服务：包装移植来的 Pipeline，供翻译页和灵动岛收起态使用。"""
from __future__ import annotations

import json
import os

from PySide6 import QtCore

from ..core.config import cfg, expand


class _LiveConfig:
    """给 Pipeline / Translator 用的配置适配器：读 wisland config.json 的 live.* 节，
    翻译 Key 与 wisland 翻译页共用（translate.api_key，或 translate.import_from 指向的译声配置）。"""

    def get(self, path: str, default=None):
        if path == "translate.api_key":
            return _api_key()
        if path in ("translate.base_url", "translate.model"):
            v = cfg.get(path)
            if v:
                return v
        v = cfg.get("live." + path)
        return default if v is None else v

    def set(self, path, value, save=True):
        cfg.set("live." + path, value, save)


def _api_key() -> str:
    from .credentials import get_translate_api_key

    key = get_translate_api_key()
    if key:
        return key
    src = expand(cfg.get("translate.import_from") or "")
    if src and os.path.exists(src):
        try:
            return str(json.load(open(src, encoding="utf-8")).get("translate", {}).get("api_key") or "")
        except Exception:  # noqa: BLE001
            return ""
    return ""


class LiveService(QtCore.QObject):
    status = QtCore.Signal(str)
    error = QtCore.Signal(str)
    partial = QtCore.Signal(str)                 # 正在说的日语（流式草稿）
    segment = QtCore.Signal(str, str)            # (sid, 日语整句)
    live_zh = QtCore.Signal(str, str)            # (sid, 实时译文)
    final_zh = QtCore.Signal(str, str)           # (sid, 最终译文)
    level = QtCore.Signal(float)
    running_changed = QtCore.Signal(bool)
    downloading_changed = QtCore.Signal(bool)    # 正在下载识别模型

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pipe = None
        self.running = False
        self._dl = None
        self._stopping = None
        self._last_error = ""
        self._preloaded = False

    def preload(self):
        """按需在后台预导入语音识别重型依赖，避免未启用实时字幕时常驻占用数百 MB 提交内存。"""
        if self._preloaded:
            return
        self._preloaded = True
        from ..core.bg import run_bg
        run_bg(lambda: __import__("wisland.live.pipeline"))

    def available(self) -> tuple[bool, str]:
        from .paths import MODELS
        model_dir = MODELS / "parakeet-ja-0.6b-int8"
        required = (
            (model_dir / "model.int8.onnx", 600_000_000),
            (model_dir / "tokens.txt", 10_000),
            (MODELS / "silero_vad.onnx", 500_000),
        )
        if not all(path.is_file() and path.stat().st_size >= minimum for path, minimum in required):
            return False, "缺少识别模型（约 630MB）"
        return True, ""

    @property
    def downloading(self) -> bool:
        return bool(self._dl and self._dl.running)

    def toggle(self):
        if self.downloading:
            self._dl.cancel()
        elif self.running:
            self.stop()
        else:
            self.start_listen()

    def start_listen(self):
        if self.running or self.downloading:
            return
        ok, msg = self.available()
        if not ok:
            self._download()
            return
        from .pipeline import Pipeline       # 重依赖（numpy / sherpa-onnx）用到时才导入
        p = Pipeline(_LiveConfig())
        p.sig_status.connect(self.status)
        p.sig_error.connect(self._pipeline_error)
        p.sig_partial.connect(self.partial)
        p.sig_segment.connect(lambda seg: self.segment.emit(seg.sid, seg.jp))
        p.sig_live_zh.connect(self.live_zh)
        p.sig_final_zh.connect(self.final_zh)
        p.sig_level.connect(self.level)
        p.finished.connect(lambda p=p: self._thread_finished(p))
        self.pipe = p
        self.running = True
        self._last_error = ""
        p.start()
        self.running_changed.emit(True)

    def _download(self):
        """第一次使用：在后台下载模型，下完自动开始聆听。"""
        from .download import ModelDownloader
        if self._dl is None:
            self._dl = ModelDownloader(self)
            self._dl.progress.connect(lambda f, text: self.status.emit(text))
            self._dl.finished.connect(self._downloaded)
        self.status.emit("正在连接下载服务器…")
        self._dl.start()
        self.downloading_changed.emit(True)

    def _downloaded(self, ok: bool, msg: str):
        self.downloading_changed.emit(False)
        if ok:
            self.status.emit("模型下载完成")
            self.start_listen()
        else:
            self.error.emit(msg)

    def stop(self, wait: bool = False):
        """停止聆听。等待音频线程结束放到后台，不阻塞界面（退出程序时 wait=True）。"""
        p, self.pipe = self.pipe, None
        self._last_error = ""
        self._finished()
        if p is None:
            return
        self._stopping = p
        if wait:
            try:
                if not p.stop(max_wait=20.0):
                    p.wait()
            except Exception:  # noqa: BLE001
                pass
            return
        from ..core.bg import run_bg
        run_bg(lambda: p.stop(), on_done=lambda _r: setattr(self, "_stopping", None))

    def _thread_finished(self, pipe):
        if self._stopping is pipe:
            self._stopping = None
        if self.pipe is pipe:
            self._finished()

    def _pipeline_error(self, message: str):
        self._last_error = message
        self.error.emit(message)

    def _finished(self):
        if self.running:
            self.running = False
            self.running_changed.emit(False)
            if self._last_error:
                self.status.emit(self._last_error)
        self.pipe = None

    def start(self):
        pass
