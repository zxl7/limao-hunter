# -*- coding: utf-8 -*-
"""盘面事件判据回归锁（2026-10-14 用户概念纠正 · 权威=宪法§245 时间线模型）。

## 用户两条论断（原话）
①「**情绪变化就是因为没有整体联动**」
②「**龙头切换就是错误的概念，那只是短期的爆发个股而已！**」

## 修法（`scripts/memory_lib.py::detect_events`）
① 情绪变化 ⇒ 必须有 **≥`LINKAGE_MIN_LAYERS` 层同向联动证据**（四层：指数 / 广度 / 炸板 / 涨停），
   **且口径形态一致**（排除 `market_state.sentiment` 双写造成的假事件）。
   两者不满足 ⇒ **不记** —— 单层抖动只是「状态」，不是「情绪变化」。
② **不再产出「龙头切换」**；仅当该票**真达各自涨停线附近**时记「个股爆发」（severity=**mid**）。
   ⛔ 真龙头是**身份**（应稳定），某票今天涨得多只是**状态**（短期爆发）。

⛔ 两个判据都有可逆开关（`ENABLE_SENTIMENT_LINKAGE_GATE` / `ENABLE_LEADER_SWITCH_EVENT`）。
"""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("memory_lib", SCRIPTS / "memory_lib.py")
ml = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ml)


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """⛔ **必须隔离**：`record_push` 末尾会 `save_memory`（`:502`）。

    首版本测试文件漏了这一步，用 `empty_mem("2026-10-14")` 调到 `record_push`
    → 把**生产** `cache/market_memory_2026-10-14.json` 覆盖成空模板，
    **当日 24 条 event_log 全丢**（2026-10-14 20:59 事故）。
    契约明令：「注入临时输出目录，禁止测试写生产」。
    """
    monkeypatch.setattr(ml, "CACHE", tmp_path)


# ────────────────────────── 构造小工具 ──────────────────────────

def _latest(**over):
    """最小 latest（只含判据用到的字段）。"""
    d = {"sentiment": "分歧", "total_zt": 36,
         "index": [{"name": "上证指数", "pct": -0.86},
                   {"name": "深证成指", "pct": -1.91},
                   {"name": "创业板指", "pct": -3.01}],
         "breadth": {"up": 973, "down": 4504},
         "broken": {"count": 26},
         "top5": []}
    d.update(over)
    return d


def _mem_with(**state):
    m = ml.empty_mem("2026-10-14")
    m["market_state"]["sentiment"] = "修复"
    m["market_state"].update(state)
    return m


def _leader_top5(name, code, pct, zt=2):
    return {"top5": [{"name": "题材X", "zt_count": zt,
                      "leader": {"name": name, "code": code, "pct": pct}}]}


def _mem_with_prev_leader(name, code):
    m = ml.empty_mem("2026-10-14")
    m["prev_top5"] = [{"name": "题材X", "zt_count": 2,
                       "leader": {"name": name, "code": code, "pct": 10.0}}]
    return m


def _sent_events(evs):
    return [e for e in evs if e.get("topic") == "情绪"]


# ────────────────── ① 情绪变化必须有整体联动 ──────────────────

def test_sentiment_change_without_linkage_is_not_recorded():
    """⛔ 核心：只有标签变、四层均无变化 → **不记事件**（那是状态抖动）。"""
    m = _mem_with(index_avg=-1.9, breadth_up=980, breadth_down=4500, broken=26)
    latest = _latest(sentiment="退潮", broken={"count": 26},
                     index=[{"name": "a", "pct": -1.93}],       # Δ=-0.03 未达 0.3
                     breadth={"up": 975, "down": 4510})         # 比值几乎不变
    assert not _sent_events(ml.detect_events(latest, mem=m))


def test_sentiment_change_with_two_layer_linkage_is_recorded():
    """≥2 层同向 → 记事件，且**文案带证据**。"""
    m = _mem_with(index_avg=-0.5, breadth_up=2000, breadth_down=3000, broken=10)
    latest = _latest(sentiment="退潮", broken={"count": 26},
                     index=[{"name": "a", "pct": -1.5}],        # 指数 -0.5→-1.5 ✅
                     breadth={"up": 900, "down": 4500})         # 涨跌比 0.67→0.20 ✅
    ev = _sent_events(ml.detect_events(latest, mem=m))
    assert len(ev) == 1
    assert "转差" in ev[0]["event"]
    assert "指数" in ev[0]["event"] and "涨跌比" in ev[0]["event"]


def test_event_depends_on_linkage_not_label_string():
    """⛔ 核心：判据**只看四层联动**，不看 `sentiment` 字符串。

    标签**没变**（都是"分歧"）但联动 ≥2 层同向 → **应当记**（盘面确在变化，只是标签没追上）。
    """
    m = _mem_with(sentiment="分歧", index_avg=-0.5,
                  breadth_up=2000, breadth_down=3000, broken=10)
    latest = _latest(sentiment="分歧", broken={"count": 26},
                     index=[{"name": "a", "pct": -1.5}],
                     breadth={"up": 900, "down": 4500})
    ev = _sent_events(ml.detect_events(latest, mem=m))
    assert len(ev) == 1
    assert "转差" in ev[0]["event"]


def test_label_change_alone_without_linkage_is_not_recorded():
    """标签变了（分歧→退潮）但四层无变化 → **不记**（原实现的假事件模式）。"""
    m = _mem_with(sentiment="分歧", index_avg=-1.9, breadth_up=980,
                  breadth_down=4500, broken=26)
    latest = _latest(sentiment="退潮", broken={"count": 26},
                     index=[{"name": "a", "pct": -1.93}],
                     breadth={"up": 975, "down": 4510})
    assert not _sent_events(ml.detect_events(latest, mem=m))


def test_same_direction_within_cooldown_is_suppressed(monkeypatch):
    """同方向在冷却期（30 min）内不重复（防每轮刷屏）。"""
    from datetime import datetime as _real

    class _Fake:
        @staticmethod
        def now():
            return _real(2026, 10, 14, 9, 55)

    monkeypatch.setattr(ml, "datetime", _Fake)
    m = _mem_with(index_avg=-0.5, breadth_up=2000, breadth_down=3000, broken=10)
    m["event_log"].append({"time": "09:50", "event": "市场情绪转差（指数-0.5%→-1.5%）",
                           "severity": "high", "topic": "情绪"})
    latest = _latest(broken={"count": 26}, index=[{"name": "a", "pct": -1.5}],
                     breadth={"up": 900, "down": 4500})
    assert not _sent_events(ml.detect_events(latest, mem=m))   # 仅隔 5 分钟 → 抑制


def test_direction_reversal_bypasses_cooldown(monkeypatch):
    """方向反转（转差→转好）→ 冷却不拦，立即记。"""
    from datetime import datetime as _real

    class _Fake:
        @staticmethod
        def now():
            return _real(2026, 10, 14, 9, 55)

    monkeypatch.setattr(ml, "datetime", _Fake)
    m = _mem_with(index_avg=-1.5, breadth_up=900, breadth_down=4500, broken=26)
    m["event_log"].append({"time": "09:50", "event": "市场情绪转差（指数）",
                           "severity": "high", "topic": "情绪"})
    latest = _latest(broken={"count": 10}, index=[{"name": "a", "pct": -0.5}],
                     breadth={"up": 2000, "down": 3000})
    ev = _sent_events(ml.detect_events(latest, mem=m))
    assert len(ev) == 1
    assert "转好" in ev[0]["event"]


def test_missing_linkage_baseline_does_not_fabricate_direction():
    """联动基准全缺失 → **不产生证据**（不填零、不猜方向）⇒ 证据不足不记。"""
    m = _mem_with()          # index_avg / breadth_* / broken 均为 None
    assert not _sent_events(ml.detect_events(_latest(sentiment="退潮"), mem=m))


def test_gate_off_falls_back_to_old_behavior():
    """开关关闭 → 逐字回退「字符串不等即记」（可逆）。"""
    orig = ml.ENABLE_SENTIMENT_LINKAGE_GATE
    ml.ENABLE_SENTIMENT_LINKAGE_GATE = False
    try:
        ev = _sent_events(ml.detect_events(_latest(sentiment="分歧"), mem=_mem_with(sentiment="修复")))
        assert len(ev) == 1
        assert ev[0]["event"] == "市场情绪由「修复」转为「分歧」"
    finally:
        ml.ENABLE_SENTIMENT_LINKAGE_GATE = orig


# ──────────── ② 「龙头切换」是错误概念 → 改为「个股爆发」 ────────────

def test_no_leader_switch_event_by_default():
    """⛔ 默认**不再产出「龙头切换」**（用户：错误的概念）。"""
    m = _mem_with_prev_leader("甲", "600001")
    ev = ml.detect_events(_leader_top5("乙", "600002", 10.0), mem=m)
    assert not [e for e in ev if "龙头切换" in e["event"]]


def test_burst_requires_reaching_limit_line():
    """名字变了但**未达涨停线** → 不算爆发（那只是排名变化）。"""
    m = _mem_with_prev_leader("甲", "600001")
    ev = ml.detect_events(_leader_top5("乙", "600002", 4.5), mem=m)
    assert not [e for e in ev if "个股爆发" in e["event"]]


def test_burst_recorded_as_mid_and_without_handover_meaning():
    """真达涨停线 → 记「个股爆发」，severity=**mid**、文案**不含"换人"含义**。"""
    m = _mem_with_prev_leader("甲", "600001")
    ev = [e for e in ml.detect_events(_leader_top5("乙", "600002", 9.9), mem=m)
          if "个股爆发" in e["event"]]
    assert len(ev) == 1
    assert ev[0]["severity"] == "mid"
    assert "+9.9%" in ev[0]["event"]
    assert "龙头切换" not in ev[0]["event"]


def test_burst_threshold_is_per_limit_line():
    """门槛按各自涨停线：主板 9.0 / 20cm 19.0。"""
    assert ml._burst_min_pct("600001") == 9.0
    assert ml._burst_min_pct("000001") == 9.0
    assert ml._burst_min_pct("300003") == 19.0
    assert ml._burst_min_pct("301234") == 19.0
    assert ml._burst_min_pct("688137") == 19.0
    assert ml._burst_min_pct("300002.SZ") == 19.0     # 带后缀也要认
    # 20cm 只涨 9.9% ⇒ 不算爆发
    m = _mem_with_prev_leader("甲", "300001")
    ev = ml.detect_events(_leader_top5("乙", "300002", 9.9), mem=m)
    assert not [e for e in ev if "个股爆发" in e["event"]]


def test_burst_skipped_when_pct_missing():
    """涨幅缺失 → **不记**（缺失不猜·契约 §1）。"""
    m = _mem_with_prev_leader("甲", "600001")
    latest = {"top5": [{"name": "题材X", "zt_count": 2,
                        "leader": {"name": "乙", "code": "600002"}}]}
    ev = ml.detect_events(latest, mem=m)
    assert not [e for e in ev if "个股爆发" in e["event"]]


def test_legacy_leader_switch_event_can_be_reenabled():
    """旧开关打开 → 恢复「龙头切换」（可逆回退）。"""
    orig = ml.ENABLE_LEADER_SWITCH_EVENT
    ml.ENABLE_LEADER_SWITCH_EVENT = True
    try:
        m = _mem_with_prev_leader("甲", "600001")
        ev = [e for e in ml.detect_events(_leader_top5("乙", "600002", 3.0), mem=m)
              if "龙头切换" in e["event"]]
        assert len(ev) == 1
        assert ev[0]["severity"] == "high"
    finally:
        ml.ENABLE_LEADER_SWITCH_EVENT = orig


# ──────────────── ③ 开关默认值即"新概念生效" ────────────────

def test_defaults_match_new_concept():
    """锁默认值：联动门禁开、旧「龙头切换」关（防日后被误改回）。"""
    assert ml.ENABLE_SENTIMENT_LINKAGE_GATE is True
    assert ml.ENABLE_LEADER_SWITCH_EVENT is False
    assert ml.LINKAGE_MIN_LAYERS == 2
    assert ml.SENTIMENT_COOLDOWN_MIN == 30


# ──────────────── ④ 根治双写：sentiment 唯一写入方 ────────────────

def test_detect_events_does_not_write_sentiment():
    """⛔ `detect_events` **不再写** `market_state.sentiment`（根治双写）。

    写入方唯一 = `record_push`（feedback 口径）。两处交替写会让
    pulse 裸标签与 feedback `x/10·标签` 互相覆盖 → 产生口径切换假事件。
    """
    m = _mem_with(sentiment="2.0/10·退潮")
    ml.detect_events(_latest(sentiment="修复"), mem=m)
    assert m["market_state"]["sentiment"] == "2.0/10·退潮"   # 未被 detect_events 覆盖


def test_record_push_is_the_only_sentiment_writer():
    """`record_push --sentiment` 写入并带时点（下一卡联动对比的数据源）。"""
    m = ml.empty_mem("2026-10-14")
    ml.record_push(m, "盘中猎手-1030", [], sentiment="3.5/10·分歧")
    assert m["market_state"]["sentiment"] == "3.5/10·分歧"
    assert m["market_state"]["sentiment_change_at"]


# ──────────────── ⑤ 存储安全（2026-10-14 写盘事故后立） ────────────────

def test_save_memory_is_atomic(tmp_path, monkeypatch):
    """⛔ `save_memory` 必须**原子写**（tmp + `os.replace`）：防并发读到半写文件。"""
    monkeypatch.setattr(ml, "CACHE", tmp_path)
    m = ml.empty_mem("2026-10-14")
    m["event_log"] = [{"time": "09:00", "event": "x", "severity": "high", "topic": "t"}]
    ml.save_memory(m)
    p = tmp_path / "market_memory_2026-10-14.json"
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8"))["event_log"][0]["event"] == "x"
    assert not list(tmp_path.glob("*.tmp")), "临时文件不应残留"


def test_load_memory_does_not_silently_overwrite_corrupt(tmp_path, monkeypatch):
    """⛔ 读到坏文件时**不得静默覆盖**（原实现 `empty_mem + save` 会把 event_log 整份清掉）。"""
    monkeypatch.setattr(ml, "CACHE", tmp_path)
    p = tmp_path / "market_memory_2026-10-14.json"
    p.write_text("{ broken", encoding="utf-8")
    m = ml.load_memory("2026-10-14")
    assert m["date"] == "2026-10-14"            # 返回空模板（不崩）
    assert not p.exists(), "坏文件不应被原地覆盖为空白"
    assert list(tmp_path.glob("*.corrupt-*")), "坏文件应改名留证"
