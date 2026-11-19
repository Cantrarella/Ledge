# -*- coding: utf-8 -*-
"""AI 额度 Windows 凭据管理器封装（使用原生 advapi32 CredWriteW/CredReadW/CredDeleteW）。
凭据键格式固定为 Wisland/quota/<account_id>，支持超长会话自动分块存储，绝不把 API Key、Token 或 Cookie 写入配置文件或日志。
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import uuid
from ctypes import wintypes

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168

_CHUNK_MAX_UTF16_BYTES = 1800
_MANIFEST_PREFIX = "__WISLAND_CRED_MANIFEST_V1__:"


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_advapi32.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
_advapi32.CredWriteW.restype = wintypes.BOOL
_advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]
_advapi32.CredReadW.restype = wintypes.BOOL
_advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
_advapi32.CredDeleteW.restype = wintypes.BOOL
_advapi32.CredFree.argtypes = [ctypes.c_void_p]
_advapi32.CredFree.restype = None


def make_quota_credential_key(account_id: str) -> str:
    """生成固定格式的 Windows 凭据管理器键名：Wisland/quota/<account_id>。"""
    clean = "".join(c for c in str(account_id or "") if c.isalnum() or c in ("-", "_"))
    if not clean:
        raise ValueError("account_id 不能为空")
    return f"Wisland/quota/{clean}"


def derive_secret_identity(provider: str, secret: str) -> str:
    """基于密钥生成不可逆的单向指纹标识，用于账号去重且绝不泄露任何密钥字符。"""
    norm = str(secret or "").strip().encode("utf-8")
    digest = hashlib.sha256(norm).hexdigest()[:12]
    p = str(provider or "quota").strip().lower()
    return f"{p}-key:{digest}"


def _split_secret_chunks(secret: str, max_utf16_bytes: int = _CHUNK_MAX_UTF16_BYTES) -> list[str]:
    """按 UTF-16-LE 字节上限切分字符串，保证每个分块不超过 Windows Generic Credential 限制。"""
    chunks: list[str] = []
    buf: list[str] = []
    cur_bytes = 0
    for ch in secret:
        ch_bytes = len(ch.encode("utf-16-le"))
        if buf and cur_bytes + ch_bytes > max_utf16_bytes:
            chunks.append("".join(buf))
            buf = [ch]
            cur_bytes = ch_bytes
        else:
            buf.append(ch)
            cur_bytes += ch_bytes
    if buf:
        chunks.append("".join(buf))
    return chunks


class WindowsQuotaCredentialStore:
    """Windows Credential Manager 存储实现（专用于 AI 额度凭据，支持大内容安全分块）。"""

    def __init__(self):
        self.last_error: int = 0
        self.last_error_message: str = ""

    def _clear_error(self):
        self.last_error = 0
        self.last_error_message = ""

    def _record_win_error(self, fallback: str):
        err_code = int(ctypes.get_last_error() or 0)
        self.last_error = err_code
        if err_code:
            try:
                detail = str(ctypes.FormatError(err_code) or "").strip()
            except Exception:
                detail = ""
            self.last_error_message = f"WinError {err_code}: {detail}" if detail else f"WinError {err_code}"
        else:
            self.last_error_message = fallback

    def _write_single(self, target: str, username: str, secret: str) -> bool:
        blob = secret.encode("utf-16-le")
        arr = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = _CREDENTIALW()
        cred.Flags = 0
        cred.Type = CRED_TYPE_GENERIC
        cred.TargetName = target
        cred.Comment = "Wisland Quota Credential"
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(arr, ctypes.POINTER(ctypes.c_ubyte))
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE
        cred.AttributeCount = 0
        cred.Attributes = None
        cred.TargetAlias = None
        cred.UserName = username or target
        ctypes.set_last_error(0)
        ok = bool(_advapi32.CredWriteW(ctypes.byref(cred), 0))
        if not ok:
            self._record_win_error("CredWriteW 失败")
        return ok

    def _read_single(self, target: str) -> str | None:
        if not target:
            return None
        p_cred = ctypes.POINTER(_CREDENTIALW)()
        ctypes.set_last_error(0)
        if not _advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(p_cred)):
            err_code = int(ctypes.get_last_error() or 0)
            if err_code and err_code != ERROR_NOT_FOUND:
                self._record_win_error("CredReadW 失败")
            return None
        try:
            cred = p_cred.contents
            if not cred.CredentialBlob or cred.CredentialBlobSize <= 0:
                return ""
            raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            return raw.decode("utf-16-le", errors="replace")
        finally:
            _advapi32.CredFree(p_cred)

    def _delete_single(self, target: str) -> bool:
        if not target:
            return False
        ctypes.set_last_error(0)
        ok = bool(_advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0))
        if not ok:
            err_code = int(ctypes.get_last_error() or 0)
            if err_code == ERROR_NOT_FOUND:
                return True
            self._record_win_error("CredDeleteW 失败")
        return ok

    @staticmethod
    def _parse_manifest(raw: str | None) -> tuple[bool, dict | None]:
        """判断主 target 是否为分块 manifest。返回 (是否为manifest, 解析后的dict_or_None)。"""
        if not isinstance(raw, str) or not raw.startswith(_MANIFEST_PREFIX):
            return False, None
        body = raw[len(_MANIFEST_PREFIX):]
        try:
            obj = json.loads(body)
        except Exception:
            return True, None
        if not isinstance(obj, dict):
            return True, None
        if obj.get("v") != 1:
            return True, None
        chunks = obj.get("chunks")
        digest = obj.get("sha256")
        if not isinstance(chunks, int) or chunks <= 0 or not isinstance(digest, str) or not digest:
            return True, None
        return True, obj

    def _cleanup_chunks(self, prefix: str, count: int) -> bool:
        all_ok = True
        for i in range(max(0, count)):
            if not self._delete_single(f"{prefix}/{i}"):
                all_ok = False
        return all_ok

    def save_secret(self, target: str, username: str, secret: str) -> bool:
        self._clear_error()
        if not target or not secret:
            self.last_error_message = "凭据目标或内容为空"
            return False

        old_raw = self._read_single(target)
        old_is_manifest, old_manifest = self._parse_manifest(old_raw)

        # 小内容：保持单条 Generic Credential，写成功后清理旧分块（如有）
        if len(secret.encode("utf-16-le")) <= _CHUNK_MAX_UTF16_BYTES and not secret.startswith(_MANIFEST_PREFIX):
            if not self._write_single(target, username, secret):
                return False
            if old_is_manifest and old_manifest is not None:
                old_prefix = str(old_manifest.get("prefix") or f"{target}/chunk")
                self._cleanup_chunks(old_prefix, int(old_manifest.get("chunks") or 0))
            return True

        # 大内容：分块写入，最后写入主 target manifest；未完整写入前绝不覆盖旧有效凭据
        chunks = _split_secret_chunks(secret, _CHUNK_MAX_UTF16_BYTES)
        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        prefix = f"{target}/chunk/{uuid.uuid4().hex[:8]}" if old_is_manifest else f"{target}/chunk"

        written_count = 0
        for idx, chunk in enumerate(chunks):
            if not self._write_single(f"{prefix}/{idx}", username, chunk):
                saved_err = self.last_error
                saved_msg = self.last_error_message
                self._cleanup_chunks(prefix, written_count)
                self.last_error = saved_err
                self.last_error_message = saved_msg
                return False
            written_count += 1

        manifest_json = json.dumps(
            {
                "v": 1,
                "chunks": len(chunks),
                "sha256": digest,
                "prefix": prefix,
            },
            separators=(",", ":"),
            ensure_ascii=True,
        )
        if not self._write_single(target, username, f"{_MANIFEST_PREFIX}{manifest_json}"):
            saved_err = self.last_error
            saved_msg = self.last_error_message
            self._cleanup_chunks(prefix, len(chunks))
            self.last_error = saved_err
            self.last_error_message = saved_msg
            return False

        if old_is_manifest and old_manifest is not None:
            old_prefix = str(old_manifest.get("prefix") or f"{target}/chunk")
            if old_prefix != prefix:
                self._cleanup_chunks(old_prefix, int(old_manifest.get("chunks") or 0))
        return True

    def read_secret(self, target: str) -> str | None:
        self._clear_error()
        if not target:
            return None
        raw = self._read_single(target)
        if raw is None:
            return None
        is_manifest, manifest = self._parse_manifest(raw)
        if not is_manifest:
            return raw
        if manifest is None:
            self.last_error_message = "保存的分块凭据清单损坏"
            return ""
        prefix = str(manifest.get("prefix") or f"{target}/chunk")
        count = int(manifest["chunks"])
        expected_sha256 = str(manifest["sha256"])

        parts: list[str] = []
        for idx in range(count):
            part = self._read_single(f"{prefix}/{idx}")
            if part is None:
                if not self.last_error_message:
                    self.last_error_message = f"保存的分块凭据缺失 (chunk {idx})"
                return ""
            parts.append(part)

        joined = "".join(parts)
        actual_sha256 = hashlib.sha256(joined.encode("utf-8")).hexdigest()
        if actual_sha256 != expected_sha256:
            self.last_error_message = "保存的分块凭据校验和不一致"
            return ""
        return joined

    def delete_secret(self, target: str) -> bool:
        self._clear_error()
        if not target:
            return False
        raw = self._read_single(target)
        is_manifest, manifest = self._parse_manifest(raw)

        ok = self._delete_single(target)
        if is_manifest and manifest is not None:
            prefix = str(manifest.get("prefix") or f"{target}/chunk")
            if not self._cleanup_chunks(prefix, int(manifest.get("chunks") or 0)):
                ok = False

        # 额外清理默认 target/chunk/{i} 残留分块
        idx = 0
        while idx < 256:
            chunk_target = f"{target}/chunk/{idx}"
            if self._read_single(chunk_target) is None:
                break
            if not self._delete_single(chunk_target):
                ok = False
            idx += 1
        return ok


class InMemoryQuotaCredentialStore:
    """测试专用内存凭据存储，绝不读写系统真实 Windows 凭据。"""

    def __init__(self):
        self._data: dict[str, tuple[str, str]] = {}
        self.last_error: int = 0
        self.last_error_message: str = ""

    def save_secret(self, target: str, username: str, secret: str) -> bool:
        self.last_error = 0
        self.last_error_message = ""
        if not target or not secret:
            self.last_error_message = "凭据目标或内容为空"
            return False
        self._data[target] = (str(username or target), str(secret))
        return True

    def read_secret(self, target: str) -> str | None:
        item = self._data.get(target)
        return item[1] if item is not None else None

    def delete_secret(self, target: str) -> bool:
        self.last_error = 0
        self.last_error_message = ""
        if not target:
            return False
        self._data.pop(target, None)
        return True
