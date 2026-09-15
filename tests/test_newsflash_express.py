# -*- coding: utf-8 -*-
"""快讯时效门回归锁（2026-09-07 用户核心铁律「快讯也要实时·禁推过时」）。

事故背景：baoer 返回 30 条是 3h 消息池且顺序≠时间序（55min 旧快讯排前），
express 曾按返回序取前 20 + stocks 命中就推 → 推了 55min 前的「传媒涨停」旧快讯。
锁死：
  1. 旧快讯(>10min) → _is_fresh=False 永不推（即使当天首次见也不推）
  2. 新快讯(≤10min) → _is_fresh=True 可推
  3. fetch_messages 按 created_at 降序（第 1 条必须最新）
  4. created_at 兼容 秒/毫秒/ISO
  5. 无 created_at → 不推（宁缺毋滥，无法证明当下）
"""
import importlib.util
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("newsflash_express_tl", SCRIPTS / "newsflash_express.py")
ne = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ne)


def _msg(age_s, title="测试涨停", with_stock=True):
    m = {"created_at": int(time.time()) - age_s, "title": title,
         "stocks": [{"name": "X", "symbol": "000001"}] if with_stock else []}
    return m


def test_stale_news_never_fresh():
    # 1h 前快讯（事故样本 55min）→ 不推
    assert ne._is_fresh(_msg(3600)) is False
    assert ne._is_fresh(_msg(3300)) is False   # 55min 事故场景
    assert ne._is_fresh(_msg(601)) is False    # 刚过 10min 边界


def test_fresh_news_ok():
    assert ne._is_fresh(_msg(30)) is True
    assert ne._is_fresh(_msg(300)) is True
    assert ne._is_fresh(_msg(599)) is True


def test_fetch_sorted_newest_first():
    msgs = ne.fetch_messages()
    if not msgs:
        return  # 接口不可用时跳过（不误报）
    # 校验整体按 created_at 降序
    ts = [ne._nf_ts(m) for m in msgs]
    assert ts == sorted(ts, reverse=True), "fetch 必须按时间降序（最新在前），防旧快讯排前被优先取到"


def test_nf_ts_compat_units():
    now = time.time()
    assert abs(ne._nf_ts({"created_at": int(now)}) - now) < 2          # 秒
    assert abs(ne._nf_ts({"created_at": int(now * 1000)}) - now) < 2   # 毫秒
    # ISO
    import datetime
    iso = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    assert abs(ne._nf_ts({"created_at": iso}) - now) < 2


def test_no_created_at_not_fresh():
    assert ne._is_fresh({"title": "无时间"}) is False  # 无法证明当下 → 不推（宁缺毋滥）
