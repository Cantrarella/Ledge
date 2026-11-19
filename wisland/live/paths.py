# -*- coding: utf-8 -*-
"""实时字幕所需模型的位置：config.json → live.models_dir（相对路径以项目根目录为准）。"""
from __future__ import annotations

from pathlib import Path

from ..core.config import ROOT, cfg, expand


def models_dir() -> Path:
    p = Path(expand(cfg.get("live.models_dir") or "models"))
    return p if p.is_absolute() else _resolve_models_path(ROOT, p)


def _resolve_models_path(root: Path, configured: Path) -> Path:
    primary = root / configured
    if primary.exists():
        return primary
    # 本地开发时根目录版和 dist/Ledge 版共用同一份约 630MB 模型，避免重复下载。
    if root.name.lower() == "ledge" and root.parent.name.lower() == "dist":
        source_models = root.parent.parent / configured
        if source_models.exists():
            return source_models
    return primary


MODELS = models_dir()
