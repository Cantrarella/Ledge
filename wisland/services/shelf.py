# -*- coding: utf-8 -*-
"""文件置物架服务：仅保存本地文件绝对路径（data/shelf.json），不复制文件内容。
支持大小写不敏感路径去重、原子写入、损坏文件自动备份恢复、打开文件与资源管理器定位。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from PySide6 import QtCore

from ..core.config import DATA_DIR, cfg


def norm_file_key(path_str: str) -> str:
    """Windows 下不区分大小写的规范化路径键，用于去重比较。"""
    try:
        p = os.path.abspath(os.path.expanduser(os.path.expandvars(str(path_str or "").strip())))
        return os.path.normcase(os.path.normpath(p))
    except Exception:  # noqa: BLE001
        return str(path_str or "").strip().lower()


def clean_abs_path(path_str: str) -> str:
    """规范化为干净的绝对路径字符串（保留原始大小写）。"""
    p = os.path.abspath(os.path.expanduser(os.path.expandvars(str(path_str or "").strip())))
    return os.path.normpath(p)


class ShelfService(QtCore.QObject):
    changed = QtCore.Signal()
    notice = QtCore.Signal(str, bool)   # (message, is_error)

    def __init__(self, path: Path | None = None):
        super().__init__()
        self._lock = threading.RLock()
        self.path: Path = Path(path) if path is not None else (DATA_DIR / "shelf.json")
        self.items: list[dict] = []     # [{"path": "D:\\...", "added_at": 1700000000.0}]
        self.load()

    def is_enabled(self) -> bool:
        return bool(cfg.get("tools.shelf_enabled", True))

    def load(self):
        """从 shelf.json 加载记录；若文件损坏，先备份为 .corrupt.bak 再重置为空列表，绝不崩溃。"""
        with self._lock:
            self.items = []
            if not self.path.exists():
                return
            try:
                raw = self.path.read_text(encoding="utf-8-sig")
                data = json.loads(raw)
                rows = data.get("items") if isinstance(data, dict) else (data if isinstance(data, list) else None)
                if not isinstance(rows, list):
                    raise ValueError("shelf.json 格式无效")
                seen: set[str] = set()
                loaded: list[dict] = []
                for r in rows:
                    p_str = r.get("path") if isinstance(r, dict) else (r if isinstance(r, str) else "")
                    if not isinstance(p_str, str) or not p_str.strip():
                        continue
                    abs_p = clean_abs_path(p_str)
                    k = norm_file_key(abs_p)
                    if k not in seen:
                        seen.add(k)
                        at = float(r.get("added_at", 0) or 0) if isinstance(r, dict) else 0.0
                        loaded.append({"path": abs_p, "added_at": at})
                self.items = loaded
            except Exception:  # noqa: BLE001
                self._backup_corrupted()
                self.items = []

    def _backup_corrupted(self):
        try:
            bak = self.path.with_suffix(".corrupt.bak")
            shutil.copy2(self.path, bak)
        except Exception:  # noqa: BLE001
            pass

    def save(self):
        """原子写入 data/shelf.json，不记录任何文件正文内容。"""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "items": self.items}
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)

    def list_items(self) -> list[dict]:
        """返回所有条目及其当前文件存在状态。"""
        with self._lock:
            out = []
            for it in self.items:
                p = it["path"]
                exists = False
                try:
                    exists = Path(p).is_file()
                except Exception:  # noqa: BLE001
                    exists = False
                out.append({
                    "path": p,
                    "name": os.path.basename(p) or p,
                    "dir": os.path.dirname(p),
                    "exists": exists,
                    "added_at": it.get("added_at", 0.0),
                })
            return out

    def paths(self) -> list[str]:
        with self._lock:
            return [it["path"] for it in self.items]

    def add_files(self, file_paths: list[str | Path]) -> tuple[int, str]:
        """添加多个本地文件路径（Windows 下大小写不敏感去重）。返回 (新增数量, 提示文案)。"""
        if not self.is_enabled():
            msg = "文件置物架已关闭"
            self.notice.emit(msg, True)
            return 0, msg

        added = 0
        skipped_dup = 0
        invalid = 0
        with self._lock:
            seen = {norm_file_key(it["path"]) for it in self.items}
            now = time.time()
            for raw_p in file_paths or []:
                s = str(raw_p or "").strip()
                if not s:
                    continue
                abs_p = clean_abs_path(s)
                try:
                    if not Path(abs_p).is_file():
                        invalid += 1
                        continue
                except Exception:  # noqa: BLE001
                    invalid += 1
                    continue
                k = norm_file_key(abs_p)
                if k in seen:
                    skipped_dup += 1
                    continue
                seen.add(k)
                self.items.append({"path": abs_p, "added_at": now})
                added += 1
            if added > 0:
                self.save()

        if added > 0:
            self.changed.emit()
            msg = f"已添加 {added} 个文件" + (f"（跳过 {skipped_dup} 个重复文件）" if skipped_dup else "")
            self.notice.emit(msg, False)
            return added, msg
        if invalid > 0 and skipped_dup == 0:
            msg = "添加文件失败：文件不存在或不是有效本地文件"
            self.notice.emit(msg, True)
            return 0, msg
        if skipped_dup > 0:
            msg = "文件已在置物架中，未重复添加"
            self.notice.emit(msg, False)
            return 0, msg
        return 0, ""

    def remove_file(self, path_str: str) -> bool:
        """仅从置物架移除一条路径记录，绝不删除磁盘上的原文件。"""
        target_k = norm_file_key(path_str)
        removed = False
        with self._lock:
            new_items = [it for it in self.items if norm_file_key(it["path"]) != target_k]
            if len(new_items) != len(self.items):
                self.items = new_items
                self.save()
                removed = True
        if removed:
            self.changed.emit()
        return removed

    def clear(self):
        """清空置物架全部路径记录，绝不删除磁盘上的原文件。"""
        with self._lock:
            if not self.items:
                return
            self.items = []
            self.save()
        self.changed.emit()
        self.notice.emit("已清空置物架（原文件未删除）", False)

    def open_file(self, path_str: str) -> tuple[bool, str]:
        """调用系统默认程序打开原文件；若文件不存在或失败则返回明确错误提示。"""
        abs_p = clean_abs_path(path_str)
        try:
            if not Path(abs_p).is_file():
                msg = "文件已移动或删除"
                self.notice.emit(msg, True)
                self.changed.emit()
                return False, msg
            os.startfile(abs_p)  # noqa: S606
            return True, ""
        except Exception as e:  # noqa: BLE001
            msg = f"打开文件失败：{e}"
            self.notice.emit(msg, True)
            return False, msg

    def reveal_in_explorer(self, path_str: str) -> tuple[bool, str]:
        """在 Windows 资源管理器中选中并定位该文件。"""
        abs_p = clean_abs_path(path_str)
        try:
            if not Path(abs_p).exists():
                msg = "文件已移动或删除"
                self.notice.emit(msg, True)
                self.changed.emit()
                return False, msg
            subprocess.Popen(
                ["explorer.exe", f"/select,{abs_p}"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
            return True, ""
        except Exception as e:  # noqa: BLE001
            msg = f"定位文件失败：{e}"
            self.notice.emit(msg, True)
            return False, msg
