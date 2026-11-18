# -*- coding: utf-8 -*-
"""多账号邮件基础设施包。"""
from .credentials import InMemoryCredentialStore, WindowsCredentialStore, make_credential_key
from .imap import DEFAULT_TIMEOUT_S, MailCancelledError, fetch_recent_headers, test_imap_connection
from .manager import DEFAULT_FETCH_LIMIT, MAX_RUNTIME_HEADERS, MailAccountManager
from .models import MailAccount, MailHeader
from .providers import PROVIDER_ORDER, PROVIDERS, get_provider, is_unsupported_microsoft_email, validate_account_params

__all__ = [
    "DEFAULT_FETCH_LIMIT",
    "DEFAULT_TIMEOUT_S",
    "InMemoryCredentialStore",
    "MAX_RUNTIME_HEADERS",
    "MailAccount",
    "MailAccountManager",
    "MailCancelledError",
    "MailHeader",
    "PROVIDER_ORDER",
    "PROVIDERS",
    "WindowsCredentialStore",
    "fetch_recent_headers",
    "get_provider",
    "is_unsupported_microsoft_email",
    "make_credential_key",
    "test_imap_connection",
    "validate_account_params",
]
