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
