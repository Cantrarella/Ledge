# -*- coding: utf-8 -*-
"""共享 HTTP 请求工具：统一禁止自动重定向的 urllib opener（防止会话凭据跨域泄露）。"""
from __future__ import annotations

import urllib.request


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """禁止自动跟随 HTTP 重定向，由调用方显式校验同站重定向目标。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


NO_REDIRECT_OPENER = urllib.request.build_opener(NoRedirectHandler())


def open_no_redirect(req: urllib.request.Request, timeout_s: float, http_opener=None):
    """使用注入的测试 opener 或共享的无重定向 opener 发起请求。"""
    if http_opener is not None:
        return http_opener(req, timeout=timeout_s)
    return NO_REDIRECT_OPENER.open(req, timeout=timeout_s)
