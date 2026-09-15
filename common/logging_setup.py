#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一日志 + 异常告警（P0-3 · 2026-08-17）
========================================
全系统统一日志入口：标准库 logging，按日轮转写 cache/logs/，控制台同时输出。
异常/推送失败时自动推「⚠️系统异常」蓝卡到测试群（同异常 30min 内去重防刷屏）。

用法:
  from common.logging_setup import get_logger
  logger = get_logger("pulse_fetcher")
  logger.info("..."); logger.error("...")

  # 推送失败时：
  from common.logging_setup import notify_alert
  notify_alert("推送失败", "card_xxx.json 推送飞书失败: timeout")
"""
from __future__ import annotations
import json, logging, os, sys, time, urllib.request
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "cache" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 告警去重：{ (标题, 摘要前40字): 上次告警时间戳 }
_ALERT_DEDUP: dict[tuple, float] = {}
_ALERT_COOLDOWN = 1800  # 30 分钟冷却，防刷屏

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _settings() -> dict:
    try:
        return json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_logger(name: str = "limao") -> logging.Logger:
    """获取统一 logger：按日轮转写 cache/logs/{name}.log + 控制台。"""
    logger = logging.getLogger(f"limao.{name}")
    if logger.handlers:  # 已初始化
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(_FORMAT)
    fh = TimedRotatingFileHandler(LOG_DIR / f"{name}.log", when="midnight",
                                  backupCount=14, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.propagate = False
    return logger


def notify_alert(title: str, detail: str, force: bool = False) -> bool:
    """推送「⚠️系统异常」蓝卡到测试群。同 (title, 摘要) 30min 内去重。

    force=True 跳过冷却（人工触发/关键故障）。返回是否真正发送。
    """
    key = (title, detail[:40])
    now = time.time()
    if not force and key in _ALERT_DEDUP and now - _ALERT_DEDUP[key] < _ALERT_COOLDOWN:
        get_logger("alert").warning(f"[去重跳过] {title}: {detail[:60]}")
        return False
    _ALERT_DEDUP[key] = now

    cfg = _settings()
    wh = cfg.get("feishu_webhook_test") or cfg.get("feishu_webhook")
    if not wh:
        get_logger("alert").error(f"[无webhook] {title}: {detail[:80]}")
        return False

    ts = datetime.now().strftime("%m-%d %H:%M:%S")
    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": f"⚠️ 系统异常 | {ts}"},
                       "template": "blue"},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md",
                                        "content": f"**{title}**\n{detail}"}},
                {"tag": "note", "elements": [{"tag": "plain_text",
                                              "content": "📡 系统自动告警 | 详见 cache/logs/"}]},
            ],
        },
    }
    try:
        data = json.dumps(card, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(wh, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        ok = res.get("code") == 0
        get_logger("alert").info(f"[{'OK' if ok else 'FAIL'}] {title}: {res}")
        return ok
    except Exception as e:
        get_logger("alert").error(f"[告警推送异常] {title}: {e}")
        return False
