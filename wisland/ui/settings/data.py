# -*- coding: utf-8 -*-
"""数据与维护设置页：编辑配置 config.json、重新加载配置、打开数据目录、导入导出配置 JSON、恢复默认设置。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6 import QtWidgets

from ...core.branding import DISPLAY_NAME
from ...core.config import CONFIG_PATH, DATA_DIR, DEFAULTS, ConfigDraft, _merge, cfg
from ..theme import G
from ..widgets import ConfirmDialog, PillButton, SettingRow
from .base import SettingsPage


class DataSettingsPage(SettingsPage):
    key = "data"
    title = "数据"
    glyph = G.WINDOWS

    def __init__(self, ctx=None, parent=None, on_reload_draft=None):
        super().__init__(ctx, parent)
        self._on_reload_draft = on_reload_draft
        self._draft_ref: ConfigDraft | None = None

        btn_open_cfg = PillButton("编辑配置 config.json", G.OPEN, height=30)
        btn_open_cfg.clicked.connect(self._open_config_file)

        btn_reload_cfg = PillButton("重新加载配置", G.REFRESH, height=30)
        btn_reload_cfg.clicked.connect(self._reload_from_disk)

        btn_open_dir = PillButton("打开数据目录", G.OPEN, height=30)
        btn_open_dir.clicked.connect(lambda: os.startfile(str(DATA_DIR)))  # noqa: S606

        btn_export = PillButton("导出配置备份...", height=30)
        btn_export.clicked.connect(self._export_config)

        btn_import = PillButton("导入配置文件...", height=30)
        btn_import.clicked.connect(self._import_config)

        self.btn_reset = PillButton("恢复默认设置", G.REFRESH, height=30, danger=True)
        self.btn_reset.clicked.connect(self._reset_defaults)

        self.finish_layout(
            self.section("配置文件与维护", "直接编辑或从磁盘重新加载 config.json，或打开本地数据存储目录"),
            SettingRow("编辑配置 config.json", str(CONFIG_PATH), btn_open_cfg),
            SettingRow("重新加载配置", f"从磁盘重新读取 config.json 并立即应用到正在运行的 {DISPLAY_NAME}", btn_reload_cfg),
            SettingRow("打开数据目录", str(DATA_DIR), btn_open_dir),
            self.section("配置备份与恢复", "导入或恢复默认后将载入当前草稿，点击右下角「保存设置」后正式写入生效"),
            SettingRow("导出当前配置", "将当前已保存的 config.json 导出备份为独立的 JSON 文件", btn_export),
            SettingRow("导入配置文件", "从备份 JSON 文件读取配置并经校验后载入到当前设置草稿", btn_import),
            SettingRow("恢复出厂默认设置", "配置将恢复默认，原配置会先自动备份至 data/config.backup.json", self.btn_reset),
        )

    def _open_config_file(self):
        if not CONFIG_PATH.exists():
            cfg.save()
        os.startfile(str(CONFIG_PATH))  # noqa: S606

    def _reload_from_disk(self):
        if self.ctx and hasattr(self.ctx, "reload_cfg"):
            self.ctx.reload_cfg()
        else:
            cfg.load()
            cfg.changed.emit()
        if self._draft_ref is not None:
            self._draft_ref.data = cfg.create_draft().to_dict()
        if callable(self._on_reload_draft):
            self._on_reload_draft()

    def load_from_draft(self, draft: ConfigDraft):
        self._draft_ref = draft

    def _export_config(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "导出配置备份", "wisland-config-backup.json", "JSON 配置文件 (*.json)"
        )
        if not path:
            return
        try:
            text = json.dumps(cfg.data, ensure_ascii=False, indent=2)
            Path(path).write_text(text, encoding="utf-8")
            ConfirmDialog.info(self.window(), "导出成功", f"配置已导出至：\n{path}")
        except Exception as e:  # noqa: BLE001
            ConfirmDialog.info(self.window(), "导出失败", f"写入文件失败：{e}")

    def _import_config(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "导入配置文件", "", "JSON 配置文件 (*.json)"
        )
        if not path:
            return
        try:
            loaded = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            ok, errors, cleaned = cfg.validate(loaded, strict=True)
            if not ok:
                ConfirmDialog.info(self.window(), "导入校验未通过", "配置文件存在以下错误：\n• " + "\n• ".join(errors[:6]))
                return
            if self._draft_ref is not None:
                self._draft_ref.data = cleaned
            if callable(self._on_reload_draft):
                self._on_reload_draft()
            self.changed.emit()
            ConfirmDialog.info(self.window(), "已载入草稿", "配置已通过校验并载入界面，请点击右下角「保存设置」使其正式生效。")
        except Exception as e:  # noqa: BLE001
            ConfirmDialog.info(self.window(), "导入失败", f"无法解析 JSON 配置文件：{e}")

    def _reset_defaults(self):
        if not ConfirmDialog.ask(
            self.window(),
            "确认恢复出厂默认设置？",
            "配置将恢复默认，原配置会先备份。",
            confirm_text="恢复默认",
        ):
            return
        # 先备份原配置；备份失败时中止恢复默认，保留原草稿并提示原因
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            backup_path = DATA_DIR / "config.backup.json"
            backup_path.write_text(json.dumps(cfg.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            ConfirmDialog.info(
                self.window(),
                "备份原配置失败",
                f"无法备份当前配置至 data/config.backup.json，已取消恢复默认设置：\n{e}",
            )
            return
        if self._draft_ref is not None:
            unknowns = {k: v for k, v in self._draft_ref.data.items() if k not in DEFAULTS}
            reset_data = _merge(DEFAULTS, unknowns)
            self._draft_ref.data = reset_data
        if callable(self._on_reload_draft):
            self._on_reload_draft()
        self.changed.emit()
