# -*- coding: utf-8 -*-
"""实时字幕关键故障路径回归测试。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6 import QtWidgets

from wisland.live import credentials as live_credentials
from wisland.live.paths import _resolve_models_path
from wisland.live.pipeline import (
    FAILED_TRANSLATION_LIMIT,
    Pipeline,
    raw_history_frames,
    translation_retry_delay,
)
from wisland.live.service import LiveService
from wisland.ui.pages.subtitle import LiveView


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


class _Config:
    def get(self, key, default=None):
        values = {
            "asr.final_threads": 1,
            "asr.draft_mode": "off",
            "asr.draft_interval_ms": 500,
        }
        return values.get(key, default)


def _pipeline() -> Pipeline:
    return Pipeline(_Config())


def test_audio_error_stops_pipeline_and_reports_status():
    pipe = _pipeline()
    errors: list[str] = []
    statuses: list[str] = []
    pipe.sig_error.connect(errors.append)
    pipe.sig_status.connect(statuses.append)
    try:
        pipe._on_audio_error("音频采集出错：设备断开")
        assert pipe._stop.is_set()
        assert errors == ["音频采集出错：设备断开"]
        assert statuses == ["音频设备异常，已停止"]
    finally:
        pipe._shutdown_pools()


def test_long_segment_history_and_retry_delay():
    assert raw_history_frames(60) >= int(60 * 16000 / 512)
    assert translation_retry_delay(429, "3.5") == 3.5
    assert translation_retry_delay(429, None) == 2.0
    assert translation_retry_delay(500, None) == 0.2


def test_failed_translation_cache_is_bounded():
    pipe = _pipeline()
    try:
        for index in range(FAILED_TRANSLATION_LIMIT + 20):
            pipe._remember_translation_failure(f"s{index}", "失败")
        assert len(pipe._failed_translations) == FAILED_TRANSLATION_LIMIT
        assert "s0" not in pipe._failed_translations
        assert f"s{FAILED_TRANSLATION_LIMIT + 19}" in pipe._failed_translations
    finally:
        pipe._shutdown_pools()


def test_late_live_translation_cannot_overwrite_newer_draft(qt_app):
    view = LiveView()
    view.on_live("L2", "新句译文")
    view.on_live("L1", "迟到译文")
    assert view.partial_zh == "新句译文"

    view.on_segment("final-1", "日本語")
    assert view.rows[0]["zh"] == "新句译文"
    view.on_live("L2", "最终后的迟到增量")
    assert view.partial_zh == ""
    view.clear()
    view.on_live("L2", "清空后的迟到增量")
    assert view.partial_zh == ""
    view.close()


def test_live_service_handles_natural_pipeline_finish(qt_app):
    service = LiveService()
    pipe = object()
    service.pipe = pipe
    service.running = True
    try:
        service._thread_finished(pipe)
        assert service.running is False
        assert service.pipe is None
    finally:
        service.deleteLater()


def test_legacy_api_key_migrates_to_credential_store():
    class _Cfg:
        def __init__(self):
            self.value = "legacy-test-key"
            self.cleared = False

        def get(self, key, default=None):
            return self.value if key == "translate.api_key" else default

        def set(self, key, value, save=True):
            assert key == "translate.api_key"
            assert value == ""
            assert save is True
            self.value = value
            self.cleared = True

    class _Store:
        def __init__(self):
            self.data: dict[str, str] = {}

        def read_secret(self, target):
            return self.data.get(target)

        def save_secret(self, target, username, secret):
            assert username == "Ledge"
            self.data[target] = secret
            return True

        def delete_secret(self, target):
            self.data.pop(target, None)
            return True

    config = _Cfg()
    store = _Store()
    assert live_credentials.get_translate_api_key(config=config, store=store) == "legacy-test-key"
    assert config.cleared
    assert store.data[live_credentials.TRANSLATE_CREDENTIAL_KEY] == "legacy-test-key"
    assert live_credentials.get_translate_api_key(config=config, store=store) == "legacy-test-key"
    assert live_credentials.save_translate_api_key("", config=config, store=store)
    assert live_credentials.TRANSLATE_CREDENTIAL_KEY not in store.data


def test_dist_ledges_reuses_source_models(tmp_path: Path):
    source = tmp_path / "source"
    dist = source / "dist" / "Ledge"
    models = source / "models"
    models.mkdir(parents=True)
    dist.mkdir(parents=True)
    assert _resolve_models_path(dist, Path("models")) == models


def test_live_service_prevents_restart_while_stopping_and_waits_on_exit(monkeypatch):
    from wisland.live.service import LiveService

    svc = LiveService()
    waited = {"stop_calls": 0, "wait_calls": 0, "started_new": 0}

    class _FakePipe:
        def __init__(self):
            self._running = True

        def isRunning(self):
            return self._running

        def stop(self, max_wait=None):
            waited["stop_calls"] += 1
            if max_wait is not None:
                self._running = False
                return True
            # 模拟后台 stop 超时且线程仍在运行
            return False

        def wait(self):
            waited["wait_calls"] += 1
            self._running = False

    fake_pipe = _FakePipe()
    svc.pipe = fake_pipe
    svc.running = True

    bg_tasks = []
    import wisland.core.bg as bg_mod
    monkeypatch.setattr(
        bg_mod,
        "run_bg",
        lambda fn, *a, on_done=None, on_error=None: bg_tasks.append((fn, on_done)),
    )
    monkeypatch.setattr(svc, "available", lambda: (True, ""))
    import wisland.live.pipeline as pipe_mod
    monkeypatch.setattr(
        pipe_mod,
        "Pipeline",
        lambda *_a, **_kw: waited.__setitem__("started_new", waited["started_new"] + 1),
    )

    # 1) 异步停止尚未完成时，再次 start_listen() 不得启动第二条流水线
    svc.stop(wait=False)
    assert svc.running is False
    assert svc._stopping is fake_pipe
    svc.start_listen()
    assert waited["started_new"] == 0

    # 2) 后台 stop() 超时返回 False 且线程仍在跑时，_stopping 不应被清空
    fn, on_done = bg_tasks.pop()
    res = fn()
    if on_done:
        on_done(res)
    assert svc._stopping is fake_pipe

    # 3) 退出时 stop(wait=True) 必须等待旧流水线结束
    svc.stop(wait=True)
    assert waited["stop_calls"] >= 2
    assert svc._stopping is None


def test_live_service_preload_skips_missing_model_and_sets_openblas_env(monkeypatch):
    import os
    from wisland.live.service import LiveService

    svc = LiveService()
    bg_calls = []
    import wisland.core.bg as bg_mod
    monkeypatch.setattr(bg_mod, "run_bg", lambda fn, *a, **kw: bg_calls.append(fn))

    # 1) 模型未下载时，preload() 不触发后台导入
    monkeypatch.setattr(svc, "available", lambda: (False, "缺少识别模型"))
    svc.preload()
    assert svc._preloaded is False
    assert bg_calls == []

    # 2) 模型已下载时，preload() 设置单线程环境变量并只调度一次
    monkeypatch.delenv("OPENBLAS_NUM_THREADS", raising=False)
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    monkeypatch.setattr(svc, "available", lambda: (True, ""))
    svc.preload()
    assert svc._preloaded is True
    assert len(bg_calls) == 1
    assert os.environ.get("OPENBLAS_NUM_THREADS") == "1"
    assert os.environ.get("OMP_NUM_THREADS") == "1"


def test_subtitle_page_on_show_does_not_eagerly_preload(monkeypatch):
    """仅路过字幕页时不自动触发重型依赖（numpy/soundcard/soxr）预加载，避免常驻内存上涨。"""
    from types import SimpleNamespace
    from PySide6 import QtWidgets
    from wisland.live.service import LiveService
    from wisland.ui.pages.subtitle import SubtitlePage

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    svc = LiveService()
    bg_calls = []
    import wisland.core.bg as bg_mod
    monkeypatch.setattr(bg_mod, "run_bg", lambda fn, *a, **kw: bg_calls.append(fn))

    ctx = SimpleNamespace(live=svc, open_settings=lambda *a, **kw: None)
    pg = SubtitlePage(ctx)

    dl_calls = []
    monkeypatch.setattr(svc, "available", lambda: (False, "缺少识别模型"))
    monkeypatch.setattr(svc, "_download", lambda: dl_calls.append(True))
    pg.on_show()
    assert bg_calls == []
    assert dl_calls == []

    monkeypatch.setattr(svc, "available", lambda: (True, ""))
    pg.on_show()
    assert bg_calls == []
    pg.close()


def test_live_service_late_stop_callback_after_qthread_deleted(monkeypatch):
    from PySide6 import QtCore, QtWidgets
    from wisland.live.service import LiveService

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    svc = LiveService()

    class _RealThread(QtCore.QThread):
        def run(self):
            pass

        def stop(self, max_wait=None):
            return False

    old_t = _RealThread()
    old_t.finished.connect(svc._on_pipe_finished)
    old_t.finished.connect(old_t.deleteLater)
    svc.pipe = old_t
    svc.running = True
    old_t.start()
    old_t.wait(2000)

    bg_tasks = []
    import wisland.core.bg as bg_mod
    monkeypatch.setattr(
        bg_mod,
        "run_bg",
        lambda fn, *a, on_done=None, on_error=None: bg_tasks.append((fn, on_done)),
    )

    svc.stop(wait=False)
    # 先让 QThread finished 与 deleteLater() 生效，销毁底层 C++ 对象
    app.processEvents()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()

    # 再交付迟到的后台 stop 回调：不得抛出 RuntimeError，也不得污染新流水线状态
    new_sentinel = object()
    svc.pipe = new_sentinel
    svc.running = True
    fn, on_done = bg_tasks.pop()
    res = fn()
    on_done(res)
    assert svc.pipe is new_sentinel
    assert svc.running is True
    assert svc._stopping is None
