#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
必盈 API（biyingapi）统一客户端 —— 全项目共享，零 token 硬编码。

=====================================================================
⚠️ 安全约定（务必遵守，关系到能否安全提交到远端仓库）
---------------------------------------------------------------------
Token 绝不写死在本文件或任何项目源码里。读取优先级：

  1) 环境变量 BIYING_TOKEN（推荐）
     → 在 ~/.zshrc（或 .bashrc）中 `export BIYING_TOKEN=xxxx`，
       只在你本机 shell 中存在，永远不会进入 git 仓库，最安全、最可移植。

  2) 兜底：本机已安装的 a-stock-realtime 技能配置
     ~/.workbuddy/skills/a-stock-realtime/references/biyingapi-config.md
     → 该文件位于用户主目录「之外」的项目目录，不会被 git 提交。
     → 仅作为本机个人使用的便利兜底；跨机器请务必用第 1 种方式。

项目根目录已配 `.gitignore` 屏蔽 `.env` / `*.local` / `cache/` 等，
确保任何包含实时数据或密钥的文件都不会被 `git add` 进仓库。
=====================================================================

提供能力：
  - fetch_zt_pool(date)         涨停池 / 连板天梯数据源，端点 hslt/ztgc
  - fetch_realtime_quotes(codes) 实时行情（竞价价/昨收/成交额/时间），端点 hsrl/ssjy_more
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

BASE = "https://api.biyingapi.com"
_UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
# 技能配置路径（位于用户主目录，不在项目目录，不会被提交）
_SKILL_CFG = (
    Path.home()
    / ".workbuddy"
    / "skills"
    / "a-stock-realtime"
    / "references"
    / "biyingapi-config.md"
)


def get_token() -> str:
    """读取 biyingapi token。优先环境变量，兜底读技能配置；都不存在则报错。"""
    tok = os.environ.get("BIYING_TOKEN")
    if tok and tok.strip():
        return tok.strip()
    if _SKILL_CFG.exists():
        for line in _SKILL_CFG.read_text(encoding="utf-8").splitlines():
            m = re.match(
                r"\s*\|\s*\*\*Token\*\*\s*\|\s*`?([0-9A-Fa-f\-]+)`?", line
            )
            if m:
                v = m.group(1).strip()
                if v:
                    return v
    raise RuntimeError(
        "未找到 BIYING_TOKEN：请在本机 shell 中 `export BIYING_TOKEN=xxx`（推荐，最安全），"
        "或确保 a-stock-realtime 技能配置中存在 token。切勿把 token 写进项目文件。"
    )


def _http_get(url: str, *, timeout: int = 15) -> Any:
    req = urllib.request.Request(url, headers=_UA)
    # ⚠️ SSL 证书过期应急开关（2026-09-08 api.biyingapi.com 证书 09-07 过期）：
    # HTTP_SSL_VERIFY_NONE=1 时跳过 TLS 校验，与 common.http_client 同款开关，
    # 上游续期后必须 unset 恢复（默认关闭=零回归）。
    try:
        from common.http_client import _ssl_context

        ctx = _ssl_context()
    except Exception:
        ctx = None
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _unwrap(payload: Any) -> Any:
    """
    biyingapi 部分端点返回 {'data': [...]}（data 直接是 list），统一取出。
    若 payload 本身是 list 也直接返回；若 data 缺失则返回原 dict。
    """
    if isinstance(payload, dict):
        d = payload.get("data")
        return d if d is not None else payload
    return payload


def is_20cm(code: str) -> bool:
    """创业板/科创板/北交所 = 20cm（含 920 新代码）。"""
    return str(code).strip().startswith(("300", "301", "688", "689", "8", "4", "920"))


def fetch_zt_pool(date: str) -> list[dict[str, Any]]:
    """
    涨停池 / 连板天梯数据源（端点 hslt/ztgc/{YYYY-MM-DD}/{token}）。

    date 形如 2026-08-10。返回标准化 dict 列表，字段与原有东财快照兼容：
      code, name, pct, price, boards(连板数), fbt(首封), zbc(炸板),
      hs(换手%), sector(行业 hy), seal(封板资金 亿), is_20cm
    """
    tok = get_token()
    url = f"{BASE}/hslt/ztgc/{date}/{tok}"
    data = _unwrap(_http_get(url))
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for it in data:
        code = str(it.get("dm") or "").strip()
        if not code:
            continue
        out.append(
            {
                "code": code,
                "name": str(it.get("mc") or ""),
                "pct": round(float(it.get("zf") or 0.0), 2),
                "price": round(float(it.get("p") or 0.0), 2),
                "boards": int(it.get("lbc") or 0),
                "fbt": str(it.get("fbt") or ""),
                "zbc": int(it.get("zbc") or 0),
                "hs": round(float(it.get("hs") or 0.0), 2),
                "sector": str(it.get("hy") or ""),
                "seal": round(float(it.get("zj") or 0.0) / 1e8, 2),  # 封板资金(亿)
                # 容量因子数据源（元→亿）：成交额/总市值/流通市值，供财富密码容量评分复用 quant-review 逻辑
                "cje_yi": round(float(it.get("cje") or 0.0) / 1e8, 2),
                "zsz_yi": round(float(it.get("zsz") or 0.0) / 1e8, 2),
                "lt_yi": round(float(it.get("lt") or 0.0) / 1e8, 2),
                "is_20cm": is_20cm(code),
            }
        )
    return out


def fetch_realtime_quotes(
    codes: Iterable[str], *, retries: int = 3, timeout: int = 15
) -> dict[str, dict[str, Any]]:
    """
    实时行情（端点 hsrl/ssjy_more/{token}?stock_codes=...），批量最多 20 只/次。

    返回 {code: 原始字段字典}。关键字段：
      o  今开（=集合竞价成交价，09:25 即竞价价）
      yc 昨收
      cje 成交额（元；09:25 时≈竞价成交额）
      t  更新时间
      p  当前价 / pc 涨跌幅% / hs 换手% / lb 量比 等
    """
    tok = get_token()
    codes = [str(c).strip() for c in codes if str(c).strip()]
    out: dict[str, dict[str, Any]] = {}
    if not codes:
        return out
    for i in range(0, len(codes), 20):
        batch = codes[i : i + 20]
        url = f"{BASE}/hsrl/ssjy_more/{tok}?" + urllib.parse.urlencode(
            {"stock_codes": ",".join(batch)}
        )
        last_err: Exception | None = None
        for _ in range(retries):
            try:
                data = _unwrap(_http_get(url, timeout=timeout))
                if isinstance(data, list):
                    for it in data:
                        c = str(it.get("dm") or "").strip()
                        if c:
                            out[c] = it
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(1.0)
        else:
            if last_err:
                sys.stderr.write(f"[biying] ssjy_more 批次失败 {batch[:3]}…: {last_err}\n")
        if i + 20 < len(codes):
            time.sleep(0.2)  # 礼貌限速（免费版 300 次/分钟，远未触顶）
    return out


if __name__ == "__main__":
    # 简单自检：打印 token 是否就绪 + 拉一次涨停池行数
    try:
        t = get_token()
        print(f"[biying_client] token 就绪 (len={len(t)})")
    except RuntimeError as e:
        print(f"[biying_client] {e}")
        sys.exit(1)
    pool = fetch_zt_pool(__import__("datetime").date.today().strftime("%Y-%m-%d"))
    print(f"[biying_client] 今日涨停池: {len(pool)} 只")
