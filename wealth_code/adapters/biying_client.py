#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
必盈（biyingapi）HTTP 客户端 —— 忠实移植 quant-review/daily_review/http.py。

安全铁律（与 quant-review 一致）：
- token 仅来自环境变量 BIYING_TOKEN，**绝不 hardcode**。
- 未设置 BIYING_TOKEN 时 api() 直接抛错，由上层的 load_zt_pool 捕获并降级选股宝。

接口形式（spec §3）：
  {BIYING_BASE_URL}/{path}/{token}
默认 base_url = https://api.biyingapi.com（可用 BIYING_BASE_URL 覆盖）。
"""

from __future__ import annotations

from typing import Any, Optional

# 架构重构（2026-08-17 P0-4·复用优先）：底层网络统一走 common.http_client，
# 消除本文件自实现 urllib/重试/超时（原 4 套 HTTP 实现之一）。
# 保留 HttpClient.api/get_json 接口与 404/5xx 语义不变（biying_pools 依赖）。
from common.http_client import http_get, http_post  # noqa: E402


class HttpClient:
    def __init__(self, base_url: str, token: str, timeout: int = 30, retries: int = 2):
        self.base_url = (base_url or "https://api.biyingapi.com").rstrip("/")
        self.token = token or ""
        self.timeout = timeout
        self.retries = retries

    def _open_json(self, url: str) -> Any:
        """GET 并解析 JSON。网络层走 common.http_client（重试/超时统一）；
        业务语义保留：404 由 api() 处理（本方法直接返回 None），5xx 靠 http_client 重试。"""
        return http_get(url, t=self.timeout, retries=self.retries)

    def api(self, path: str, *, exit_on_404: bool = True, quiet_404: bool = False) -> Optional[Any]:
        if not self.token:
            raise RuntimeError("未设置 BIYING_TOKEN：请先 export BIYING_TOKEN=... 再运行")
        url = f"{self.base_url}/{path}/{self.token}"
        res = self._open_json(url)
        if res is None:
            # http_client 网络失败返回 None：若请求本身是 404，api() 按 404 语义处理
            if not quiet_404:
                if not exit_on_404:
                    return None
                raise RuntimeError(f"biyingapi 请求失败(网络错误或 404): {path}")
            return None
        return res

    def get_json(self, url: str) -> Any:
        return self._open_json(url)


def build_client() -> HttpClient:
    """从环境变量构建客户端（不读 .env 文件，token 必须已注入环境）。"""
    import os
    base = os.getenv("BIYING_BASE_URL", "https://api.biyingapi.com")
    token = os.getenv("BIYING_TOKEN", "")
    return HttpClient(base_url=base, token=token)
