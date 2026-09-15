#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置完整性校验器（P1-⑤ 基础层 · 2026-08-17）
校验 app_config.yaml 索引中登记的关键配置是否存在且键完整：
  - settings.json 的 webhook 键 / webhook_default_key
  - .env 的 BIYING_TOKEN / WEALTH_USE_BIYING（存在即可，不回显值）
  - launchd plist 文件存在
  - 关键脚本阈值文件存在
用法：python3 scripts/validate_config.py [--json]
exit=0 全过；exit=1 有缺失（仅报告）。
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "config" / "app_config.yaml"
SETTINGS = ROOT / "config" / "settings.json"
ENV = ROOT / "wealth_code" / ".env"


def _load_yaml(path: Path):
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_settings():
    import json as _j
    with open(SETTINGS, encoding="utf-8") as f:
        return _j.load(f)


def _load_env():
    env = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def validate(verbose=True) -> dict:
    try:
        cfg = _load_yaml(CFG)
    except Exception as e:
        return {"ok": False, "errors": [f"app_config.yaml 解析失败: {e}"], "warnings": []}

    errors, warnings = [], []
    try:
        settings = _load_settings()
    except Exception as e:
        return {"ok": False, "errors": [f"settings.json 读取失败: {e}"], "warnings": []}
    env = _load_env()

    # ① settings.json webhook 键
    for name, meta in (cfg.get("feishu") or {}).items():
        k = meta.get("key")
        if k not in settings:
            errors.append(f"[settings.json] 缺键: {k}（{name}）")

    # ② .env 敏感键
    for name, meta in (cfg.get("env") or {}).items():
        k = meta.get("key")
        if not ENV.exists():
            errors.append("[.env] wealth_code/.env 文件缺失")
            break
        if k not in env:
            errors.append(f"[.env] 缺键: {k}（{name}）")

    # ③ launchd plist
    for name, meta in (cfg.get("launchd") or {}).items():
        p = os.path.expanduser(meta.get("file", ""))
        if not os.path.exists(p):
            warnings.append(f"[launchd] 未找到: {meta.get('file')}（{name}·可能未装）")

    # ④ 阈值文件存在
    for name, meta in (cfg.get("thresholds") or {}).items():
        f = ROOT / meta.get("file", "")
        if not f.exists():
            warnings.append(f"[thresholds] 文件缺失: {meta.get('file')}（{name}）")

    ok = not errors
    if verbose:
        print(f"settings.json 键 {len(settings)} 个 | .env 键 {len(env)} 个 | 登记配置项 {(cfg.get('feishu') or {}).__len__() + (cfg.get('env') or {}).__len__()} 类")
        print(f"结果: {'✅ 配置完整性通过' if ok else '❌ 存在缺失'}")
        for w in warnings:
            print(f"  ⚠️ {w}")
        for e in errors:
            print(f"  ❌ {e}")
    return {"ok": ok, "errors": errors, "warnings": warnings}


if __name__ == "__main__":
    res = validate(verbose="--json" not in sys.argv)
    if "--json" in sys.argv:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res["ok"] else 1)
