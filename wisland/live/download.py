# -*- coding: utf-8 -*-
"""在 wisland 里直接下载实时字幕模型（约 630MB），不用另开命令行。

- 依次尝试 HF_ENDPOINT 环境变量、huggingface.co、hf-mirror.com（国内镜像）
- 有系统代理（Clash 等）时先走代理，失败再直连
- 断点续传：下载中断后再点一次会接着下（临时文件 *.part）
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import httpx
from PySide6 import QtCore

from .http_util import system_proxy
from .paths import MODELS

PARAKEET = "csukuangfj/sherpa-onnx-nemo-parakeet-tdt_ctc-0.6b-ja-35000-int8"
VAD_REPO = "csukuangfj/sherpa-onnx-vad-models"
VAD_GITHUB = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"

# (保存位置, 仓库, 仓库内文件名, 大概大小 字节 —— 拿不到 Content-Length 时用来估算进度)
FILES = [
    ("silero_vad.onnx", VAD_REPO, "silero_vad.onnx", 644_000),
    ("parakeet-ja-0.6b-int8/tokens.txt", PARAKEET, "tokens.txt", 29_000),
    ("parakeet-ja-0.6b-int8/model.int8.onnx", PARAKEET, "model.int8.onnx", 655_542_604),
]


def _is_valid_file(path: Path, expected_size: int) -> bool:
    return path.is_file() and path.stat().st_size >= int(expected_size * 0.9)


def missing() -> list[tuple[str, str, str, int]]:
    return [f for f in FILES if not _is_valid_file(MODELS / f[0], f[3])]


def _parse_content_range_total(header_val: str | None) -> int | None:
    """解析 Content-Range（如 'bytes */655542604' 或 'bytes 0-99/655542604'）中的远端总字节数。"""
    if not header_val or "/" not in header_val:
        return None
    tail = header_val.rsplit("/", 1)[-1].strip()
    if tail.isdigit():
        return int(tail)
    return None


def _endpoints() -> list[str]:
    out = []
    for e in (os.environ.get("HF_ENDPOINT"), "https://huggingface.co", "https://hf-mirror.com"):
        e = (e or "").strip().rstrip("/")
        if e and e not in out:
            out.append(e)
    return out


def _urls(repo: str, name: str) -> list[str]:
    urls = [f"{e}/{repo}/resolve/main/{name}" for e in _endpoints()]
    if name == "silero_vad.onnx":
        urls.insert(1, VAD_GITHUB)
    return urls


def _clients() -> list[httpx.Client]:
    t = httpx.Timeout(20.0, connect=8.0)
    hdr = {"User-Agent": "wisland"}
    out = []
    px = system_proxy()
    if px:
        out.append(httpx.Client(proxy=px, timeout=t, follow_redirects=True, headers=hdr, trust_env=False))
    out.append(httpx.Client(timeout=t, follow_redirects=True, headers=hdr, trust_env=False))
    return out


class ModelDownloader(QtCore.QObject):
    progress = QtCore.Signal(float, str)     # (0..1, 说明文字)
    finished = QtCore.Signal(bool, str)      # (成功, 失败原因)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        if self.running:
            return
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run, name="wisland-models", daemon=True)
        self._thread.start()

    def cancel(self):
        self._cancel.set()

    # ---------------- 后台线程 ----------------
    def _run(self):
        clients: list[httpx.Client] = []
        finished_emitted = False
        try:
            todo = missing()
            total = sum(f[3] for f in todo) or 1
            done = 0
            clients = _clients()
            for rel, repo, name, size in todo:
                dest = MODELS / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                err = ""
                downloaded = False
                for url in _urls(repo, name):
                    for c in clients:
                        try:
                            self._get(c, url, dest, lambda n, d=done: self._report(d + n, total), size)
                            if not _is_valid_file(dest, size):
                                raise RuntimeError("下载结果文件不完整")
                            err = ""
                            downloaded = True
                            break
                        except _Cancelled:
                            finished_emitted = True
                            self.finished.emit(False, "已取消")
                            return
                        except Exception as e:  # noqa: BLE001
                            err = f"{type(e).__name__}: {e}"[:160]
                    if downloaded:
                        break
                if not downloaded:
                    finished_emitted = True
                    self.finished.emit(False, "模型下载失败（" + (err or "网络错误") + "），检查网络或代理后再点一次会接着下载")
                    return
                done += size
            finished_emitted = True
            self.finished.emit(True, "")
        except _Cancelled:
            finished_emitted = True
            self.finished.emit(False, "已取消")
        except Exception as e:  # noqa: BLE001
            finished_emitted = True
            self.finished.emit(False, f"模型下载失败（{type(e).__name__}: {e}）"[:180])
        finally:
            for c in clients:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
            if not finished_emitted:
                self.finished.emit(False, "模型下载异常中断")

    def _report(self, n: int, total: int):
        f = min(1.0, n / total)
        self.progress.emit(f, f"正在下载识别模型 {f * 100:.0f}%（{n / 1e6:.0f} / {total / 1e6:.0f} MB）")

    def _get(self, c: httpx.Client, url: str, dest: Path, on_bytes, expected_size: int = 0):
        part = dest.with_name(dest.name + ".part")
        for attempt in range(2):
            have = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={have}-"} if have else {}
            with c.stream("GET", url, headers=headers) as r:
                if r.status_code == 416:
                    remote_total = _parse_content_range_total(r.headers.get("Content-Range"))
                    min_ok = int(expected_size * 0.9) if expected_size > 0 else 1
                    if remote_total is not None and have == remote_total and have >= min_ok:
                        part.replace(dest)
                        return
                    # 本地分片长度与远端总长度不符（例如分片损坏或过大），清理后从头重下
                    part.unlink(missing_ok=True)
                    if attempt == 0:
                        continue
                    raise RuntimeError("断点续传分片无效（HTTP 416）")
                r.raise_for_status()
                if have and r.status_code != 206:  # 服务器不支持续传：从头下
                    have = 0
                remote_total = _parse_content_range_total(r.headers.get("Content-Range"))
                if remote_total is None and r.status_code == 200:
                    cl = (r.headers.get("Content-Length") or "").strip()
                    if cl.isdigit():
                        remote_total = int(cl)
                with open(part, "ab" if have else "wb") as f:
                    n = have
                    on_bytes(n)
                    for chunk in r.iter_bytes(1 << 20):
                        if self._cancel.is_set():
                            raise _Cancelled()
                        f.write(chunk)
                        n += len(chunk)
                        on_bytes(n)
            if remote_total is not None and n != remote_total:
                part.unlink(missing_ok=True)
                raise RuntimeError(f"下载长度不匹配（收到 {n} / 期望 {remote_total}）")
            if expected_size > 0 and n < int(expected_size * 0.9):
                part.unlink(missing_ok=True)
                raise RuntimeError(f"下载文件过小（收到 {n} 字节）")
            part.replace(dest)
            return


class _Cancelled(Exception):
    pass
