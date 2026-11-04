#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一 HTTP 客户端（2026-08-17 架构重构 P0-1 · 复用优先：合并 4 套独立 urllib 实现）

背景：intraday_pulse/feedback_matrix/ma5_check/push_card 各自实现 urllib 请求，
超时/UA/重试口径漂移（曾因 UA 不一致导致东财接口偶发失败）。本模块为唯一实现。

设计：
- http_get / http_post：与旧同名函数签名兼容（url, t=10 → timeout），接入零改调用方
- 默认 UA 与 intraday_pulse 一致（东财/选股宝实测可用）
- 重试：网络异常（URLError/超时）指数退避重试 2 次；业务失败不重试（调用方自己判）
- 统一日志走 common.logging_setup（P0-3 统一日志）；失败返回 None 由调用方防御
- JSON 响应统一解析；非 JSON 返回原始文本（resp.text() 辅助）

铁律：任何脚本需要发 HTTP 请求，一律 import 本模块；禁止再自写 urllib。
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

# 默认 UA：兼容东财/选股宝/腾讯（与 intraday_pulse 原值一致，实测可用）
DEFAULT_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)

# ⚠️ SSL 证书过期应急开关（2026-09-08 数据源 api.biyingapi.com 证书 09-07 过期事故）
# HTTP_SSL_VERIFY_NONE=1 时全局跳过 TLS 证书校验（安全降级，仅应急用）；
# 上游证书续期后必须 unset 恢复（默认关闭=零回归）。只影响本进程。
_SSL_VERIFY_NONE = os.getenv("HTTP_SSL_VERIFY_NONE", "") == "1"


def _ssl_context() -> Optional[ssl.SSLContext]:
    """返回应急 SSL context（跳过证书校验）；默认校验时返回 None（urllib 默认行为）。"""
    if not _SSL_VERIFY_NONE:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

# 默认重试：网络异常重试次数 + 重试间隔（秒，指数退避基）
DEFAULT_RETRIES = 2
RETRY_BACKOFF = 1.5


def _log(msg: str) -> None:
    """统一日志：优先 common.logging_setup，失败退化 print（不阻塞请求）。"""
    try:
        from common.logging_setup import get_logger

        get_logger("http_client").warning(msg)
    except Exception:
        print(f"[http_client] {msg}")


# ── 日志降噪（2026-09-02 · 中枢体检 P1-1）──────────────────────────────
# 背景：http_client.log 曾累积 1.2 万+ 条同源 WARNING（DNS 失败/HTTP 501/
# 非交易时段接口不可用），淹没真正异常。策略：同「主机+错误类型」窗口内合并计数，
# 窗口内只落首条，窗口结束补一条汇总（含次数/时长），其余静默丢弃。
NOISE_WINDOW_SEC = 600          # 合并窗口 10 分钟
_NOISE_STATE: dict[str, dict] = {}


def _host_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc or url[:40]
    except Exception:
        return url[:40]


def _log_sampled(key: str, msg: str) -> None:
    """同源错误窗口内合并：窗口内只记首条，窗口结束补汇总条。"""
    now_ts = time.time()
    st = _NOISE_STATE.get(key)
    if st is None or now_ts - st["first"] > NOISE_WINDOW_SEC:
        if st and st.get("count", 1) > 1:
            _log(f"{msg} ｜ 合并同类 {st['count']} 次 / {int(now_ts - st['first'])}s")
        else:
            _log(msg)
        _NOISE_STATE[key] = {"first": now_ts, "count": 1}
    else:
        st["count"] += 1


# ────────────────────────────────────────────────────────────────
# 熔断 / per-host 配额 / 告警解耦（数据基座 · 工件 A 增强）
#
# 全部「默认关闭」：当且仅当对应环境变量为 "1" 时生效，否则行为与此前
# 完全一致（_circuit_allow / _rate_allow 恒返回 True，_circuit_record 空操作）。
# 可逆：unset 环境变量即回退原始无熔断/无限流行为，不破坏任何调用方。
# ────────────────────────────────────────────────────────────────

# 告警解耦：调用方（守护进程/serve）可注入自己的告警通道（如飞书推卡），
# 默认 None = 不告警。熔断/限流触发时调用，失败不影响主流程。
_alert_sink = None


def set_alert_sink(fn) -> None:
    """注入告警回调 fn(msg:str)。传 None 关闭。"""
    global _alert_sink
    _alert_sink = fn


def _alert(msg: str) -> None:
    if _alert_sink is not None:
        try:
            _alert_sink(msg)
        except Exception:
            pass


# 熔断器（HTTP_CIRCUIT_BREAKER=1 启用）
_CIRCUIT_ENABLED = os.getenv("HTTP_CIRCUIT_BREAKER", "") == "1"
_CB_STATE: dict[str, dict] = {}
CB_FAILURE_THRESHOLD = 5
CB_OPEN_SEC = 60


def _circuit_allow(host: str) -> bool:
    if not _CIRCUIT_ENABLED:
        return True
    st = _CB_STATE.get(host)
    if not st or not st.get("opened_at"):
        return True
    now = time.time()
    if now - st["opened_at"] < CB_OPEN_SEC:
        return False
    # 熔断窗口已过 → 半开试探一次
    st["half_open_at"] = now
    return True


def _circuit_record(host: str, success: bool) -> None:
    if not _CIRCUIT_ENABLED:
        return
    st = _CB_STATE.setdefault(host, {"failures": 0, "opened_at": None})
    if success:
        st["failures"] = 0
        st["opened_at"] = None
    else:
        st["failures"] += 1
        if st["failures"] >= CB_FAILURE_THRESHOLD:
            st["opened_at"] = time.time()
            _alert(f"[熔断开启] host={host} 连续失败 {st['failures']} 次，{CB_OPEN_SEC}s 内跳过请求")


# per-host 令牌桶配额（HTTP_RATE_LIMIT=1 启用）
_RATE_ENABLED = os.getenv("HTTP_RATE_LIMIT", "") == "1"
_RATE: dict[str, list[float]] = {}
RATE_MAX = int(os.getenv("HTTP_RATE_MAX", "20") or "20")
RATE_WINDOW = float(os.getenv("HTTP_RATE_WINDOW", "1.0") or "1.0")


def _rate_allow(host: str) -> bool:
    if not _RATE_ENABLED:
        return True
    now = time.time()
    ring = _RATE.setdefault(host, [])
    while ring and now - ring[0] > RATE_WINDOW:
        ring.pop(0)
    if len(ring) >= RATE_MAX:
        return False
    ring.append(now)
    return True


def http_get(
    url: str,
    t: int = 10,
    *,
    timeout: Optional[float] = None,
    headers: Optional[dict[str, str]] = None,
    retries: int = DEFAULT_RETRIES,
    parse_json: bool = True,
) -> Any:
    """GET 请求。默认返回 JSON 解析结果；parse_json=False 返回文本 str。
    失败（网络异常且重试耗尽）返回 None（调用方防御，不抛异常）。"""
    return _request(
        url=url,
        method="GET",
        timeout=float(timeout if timeout is not None else t),
        headers=headers,
        retries=retries,
        parse_json=parse_json,
    )


def http_post(
    url: str,
    body: Any = None,
    t: int = 10,
    *,
    timeout: Optional[float] = None,
    headers: Optional[dict[str, str]] = None,
    retries: int = DEFAULT_RETRIES,
    parse_json: bool = True,
) -> Any:
    """POST 请求。body 为 dict/list 时自动 JSON 序列化（ensure_ascii=False）。
    默认返回 JSON 解析结果；失败返回 None。"""
    return _request(
        url=url,
        method="POST",
        body=body,
        timeout=float(timeout if timeout is not None else t),
        headers=headers,
        retries=retries,
        parse_json=parse_json,
    )


def _request(
    *,
    url: str,
    method: str,
    timeout: float,
    body: Any = None,
    headers: Optional[dict[str, str]] = None,
    retries: int,
    parse_json: bool,
) -> Any:
    req_headers = {"User-Agent": DEFAULT_UA}
    if headers:
        req_headers.update(headers)
    host = _host_of(url)
    # 熔断 / per-host 配额（默认关闭时均为放行，行为不变）
    if not _circuit_allow(host):
        _alert(f"[熔断] {host} 已开启熔断，本次请求跳过")
        return None
    if not _rate_allow(host):
        _alert(f"[限流] {host} 触发 per-host 配额，本次请求跳过")
        return None
    data = None
    if method == "POST" and body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        else:
            data = body.encode("utf-8") if isinstance(body, str) else body

    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
                raw = resp.read().decode("utf-8")
                if not parse_json:
                    _circuit_record(host, True)
                    return raw
                try:
                    obj = json.loads(raw)
                    _circuit_record(host, True)
                    return obj
                except json.JSONDecodeError as e:
                    _circuit_record(host, False)
                    _log_sampled(f"json|{host}",
                                 f"JSON 解析失败 {url[:80]}: {e} (前200字={raw[:200]})")
                    return None
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            last_err = e
            _circuit_record(host, False)
            if attempt < retries:
                time.sleep(RETRY_BACKOFF * (2**attempt))
    # 降噪：同主机 + 同异常类型合并（真实异常仍会落首条，不会被静默吞掉）
    _log_sampled(f"req|{host}|{type(last_err).__name__}",
                 f"请求失败(重试{retries}次后) {method} {url[:100]} | {last_err}")
    return None


def fetch_all_parallel(
    tasks: dict[str, Callable[[], Any]],
    max_workers: int = 4,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """并发执行多个取数任务（各返回 None 视为失败）。tasks={label: callable}。
    用于守护循环批量抓取；超时由 caller 传入的 http_get 的 t 控制。
    返回 {label: result}，单任务异常不阻断其余。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        fut_map = {ex.submit(fn): label for label, fn in tasks.items()}
        for fut in as_completed(fut_map, timeout=timeout):
            label = fut_map[fut]
            try:
                results[label] = fut.result()
            except Exception as e:  # noqa: BLE001
                _log(f"并发任务 {label} 异常: {e}")
                results[label] = None
    return results
