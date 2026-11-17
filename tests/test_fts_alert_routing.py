#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价裁决卡必须走异动群（2026-11-13 用户）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def test_alert_webhook_default_is_intraday():
    import first_to_second_alert as A
    assert A.ALERT_WEBHOOK == "feishu_webhook_intraday"


def test_settings_has_intraday_webhook():
    import json
    s = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    assert s.get("feishu_webhook_intraday", "").startswith("https://open.feishu.cn/")
