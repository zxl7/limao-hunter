#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价裁决/一进二卡必须走财富密码群（2026-11-17 用户「撤离异动群，只留财富密码群」）。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def test_alert_webhook_default_is_prod():
    import first_to_second_alert as A
    assert A.ALERT_WEBHOOK == "feishu_webhook_prod"


def test_settings_has_prod_webhook():
    s = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    assert s.get("feishu_webhook_prod", "").startswith("https://open.feishu.cn/")


def test_alert_webhook_env_override_rolls_back():
    """回退开关：FTS_ALERT_WEBHOOK 必须能逐字改回异动群。"""
    import importlib

    import first_to_second_alert as A
    old = os.environ.get("FTS_ALERT_WEBHOOK")
    os.environ["FTS_ALERT_WEBHOOK"] = "feishu_webhook_intraday"
    try:
        reloaded = importlib.reload(A)
        assert reloaded.ALERT_WEBHOOK == "feishu_webhook_intraday"
    finally:
        if old is None:
            os.environ.pop("FTS_ALERT_WEBHOOK", None)
        else:
            os.environ["FTS_ALERT_WEBHOOK"] = old
        importlib.reload(A)
