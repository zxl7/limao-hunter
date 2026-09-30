#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回流前兆 题材名↔资金榜板块名 命中判定守卫（2026-09-15 立·治「电子布」刷屏事故）。

事故：2026-09-15 异动群从 09:32 到 13:30 收到 6 张「🔔 回流前兆」卡，内容恒为同一条
「▸ 电子布｜资金📈｜跟踪池 超声电子/宏昌电子/永冠新材/大豪科技」。

双层根因：
  ① **假命中**：`live_signal.py` 原判定为裸双向子串 `nm in n or n in nm`（无长度门）→
     资金榜里的泛行业板块「电子」（2 字）是候选题材「电子布」的子串 → 被判「电子布在
     资金净流入榜」。实测当日 latest 榜 10 个板块精确包含"电子布"者 **为 0**。
  ② **无变化重推**：冷却为纯 30min 时间闸（`_reflow_cd=1800`），到期即重推，
     没有"指纹是否变化"这一层 → 只要「电子」常驻榜就每半小时弹一张。

本测试锁死命中判定（②由 live_signal 的 reflow_sig_map 指纹机制保障，另由实跑验证）：
  · 「榜内泛名 ⊂ 具体题材名」必须**挡住**（电子 ⊄ 电子布、电网 ⊄ 智能电网）；
  · 「题材名 ⊂ 榜内名」必须**放行**（芯片 ⊂ 国产芯片、电子布 ⊂ 电子布概念）；
  · 「榜内 ≥3 字名 ⊂ 题材名」保留（PCB ⊂ PCB板）；
  · 精确相等恒命中；空值不命中。
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("lsmod", ROOT / "scripts" / "live_signal.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _load()
hit = M._reflow_name_hit

# ── ① 事故本体：榜内泛名（1-2 字）不得吞掉具体题材名 ──────────────────────
def test_generic_short_board_name_does_not_swallow_theme():
    """⛔ 原事故：榜内「电子」被当成「电子布」在榜 → 每 30min 重弹一张回流卡。"""
    assert hit("电子布", "电子") is False
    assert hit("智能电网", "电网") is False
    assert hit("新能源汽车", "汽车") is False
    assert hit("中芯概念", "芯片") is False


# ── ② 真命中三条通道必须保留（防过度收紧导致回流前兆重新零触发）──────────
def test_exact_match_passes():
    assert hit("MLCC", "MLCC") is True          # 09-15 真实命中项
    assert hit("半导体", "半导体") is True


def test_theme_inside_board_name_passes():
    """题材名 ⊂ 榜内名：细分为主的榜内名要能承接宽一点的题材名。"""
    assert hit("芯片", "国产芯片") is True
    assert hit("电子布", "电子布概念") is True
    assert hit("PCB", "PCB板") is True


def test_board_name_inside_theme_passes_when_long_enough():
    """榜内名 ⊂ 题材名：榜内名 ≥3 字才成立（保留 "印制电路板" ⊂ "印制电路板概念" 类）。"""
    assert hit("PCB板", "PCB") is True          # 榜内名恰 3 字 → 放行
    assert hit("印制电路板概念", "印制电路板") is True


def test_unrelated_names_never_match():
    assert hit("固态电池", "电池技术") is False
    assert hit("培育钻石", "医药生物") is False


def test_empty_inputs_are_safe():
    assert hit("", "电子") is False
    assert hit("电子布", "") is False
    assert hit("", "") is False


# ── ③ 指纹机制存在性（无变化不重推的落地保障）────────────────────────────
def test_reflow_signature_state_is_persisted():
    """`reflow_sig_map` 必须与 `reflow_pushed` 同写入 sig，否则指纹丢失=去重失效。"""
    src = (ROOT / "scripts" / "live_signal.py").read_text(encoding="utf-8")
    assert 'sig["reflow_sig_map"] = reflow_sig_map' in src
    assert 'sig["reflow_pushed"] = reflow_pushed' in src
    # 指纹必须参与判定（冷却到期 ∧ 指纹**变化** 才允许重推）。
    # ⚠️ 2026-09-29 修断言：原断言搜 `== _sig_now`，与代码/自身注释相反。
    #    `reflow_sig_map[nm]` 存的是「上次推送时的指纹」，要重推必须 `!=`（指纹变了）——
    #    代码 2026-09-15「变化驱动」修复后即用 `!=`（见 live_signal 注释：
    #    反例 09-15 电子布指纹恒同却每轮重弹，全天刷屏 6 次），是本断言过时而非代码错。
    assert 'reflow_sig_map.get(nm, "") != _sig_now' in src
