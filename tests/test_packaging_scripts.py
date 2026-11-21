# -*- coding: utf-8 -*-
"""打包配置与构建脚本可移植性校验。"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_FORBIDDEN_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:[/\\]"),
    re.compile(r"wisland-exe", re.IGNORECASE),
)


def test_ledge_spec_is_portable_and_valid_python():
    spec_path = ROOT / "Ledge.spec"
    src = spec_path.read_text(encoding="utf-8")
    ast.parse(src, filename=str(spec_path))

    for pat in _FORBIDDEN_PATH_PATTERNS:
        assert not pat.search(src), f"Ledge.spec 包含硬编码路径或外部目录引用: {pat.pattern}"

    assert 'ROOT / "wisland" / "assets"' in src
    assert 'name="Ledge"' in src


def test_build_ps1_is_portable_without_hardcoded_user_paths():
    ps1_path = ROOT / "scripts" / "build.ps1"
    src = ps1_path.read_text(encoding="utf-8")

    for pat in _FORBIDDEN_PATH_PATTERNS:
        assert not pat.search(src), f"scripts/build.ps1 包含硬编码路径或外部目录引用: {pat.pattern}"

    assert ".venv" in src
    assert "LOCALAPPDATA" in src
    assert "& $PythonExe -c" in src
