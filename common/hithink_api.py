#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同花顺金融数据（hithink-finance）统一 REST 客户端。

凭据：~/Library/Application Support/hithink-finance/credentials.env
      （0600 权限，单行 KEY=xxx；Key 只从文件读，绝不硬编码/回显/写日志）
鉴权：X-api-key 请求头
Base：https://fuyao.aicubes.cn

铁律：
- 任何脚本要调同花顺，一律 import 本模块（复用 common.http_client 网络层），禁止自写 urllib。
- Key 注入请求头，http_client 日志只记 URL 不含 header → Key 不落日志。
- 失败返回 None 由调用方防御；code != 0 视为失败。

用法：
    from common.hithink_api import hithink_get, api_key, to_thscode
    data = hithink_get("a-share/special-data/limit-up-pool", params={"size": 80})
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from common.http_client import http_get

BASE = "https://fuyao.aicubes.cn"
CRED_REL = Path("Library") / "Application Support" / "hithink-finance" / "credentials.env"


def credential_file() -> Path:
    return Path.home() / CRED_REL


def api_key() -> Optional[str]:
    """读统一凭据文件取 Key（不回显）。缺失/空返回 None。"""
    try:
        f = credential_file()
        if not f.exists():
            return None
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                return line.split("=", 1)[1].strip()
    except Exception:
        return None
    return None


def to_thscode(code: str) -> str:
    """6 位纯代码 → 带交易所后缀的 thscode（大写）。已是 thscode 则原样归一化。"""
    c = (code or "").strip().upper()
    if "." in c:
        return c
    if c[:1] in ("6", "9", "5"):
        return c + ".SH"
    if c.startswith(("8", "4", "920")):
        return c + ".BJ"
    return c + ".SZ"


def hithink_get(path: str, params: Optional[dict] = None, t: int = 15) -> Any:
    """GET 同花顺 REST 端点。path 不含前导 /api/（如 'a-share/prices/snapshot'）。
    成功且 code==0 返回 data 部分；失败/异常返回 None。"""
    key = api_key()
    if not key:
        return None
    url = f"{BASE}/api/{path}"
    if params:
        import urllib.parse

        url += "?" + urllib.parse.urlencode(params)
    raw = http_get(url, t=t, headers={"X-api-key": key}, retries=1)
    if not isinstance(raw, dict):
        return None
    if raw.get("code") != 0:
        return None
    return raw.get("data")
