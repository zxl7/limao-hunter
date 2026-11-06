#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""退潮预警（ebb）口径 —— 2026-11-05 用户「留意整体大盘的赚钱效应，别只看打板！！」。

## 事故（用户盘感 vs 系统结论完全相反）

2026-11-05 上午，终端打出「⚡ 情绪转弱」，但当天实际是：

| 大盘 | 数值 |
|---|---|
| 全市场涨跌 | 4843 涨 / 632 跌 = **88.5% 上涨** |
| 指数平均 | **+1.6%** |
| 成交额 | 15088 亿 |
| 昨涨停上涨率 | 92.2%，平均 **+5.8%** |
| 跌停 | 0 |

用户当场质疑「情绪感觉越来越强啊」。**用户是对的。**

## 三个根因（逐层修）

### ① 晋级率用了写死的 25%，没用自己的动态门槛
`promote_rate = 22.9%`，系统自算的 `trend.promotion_gate_10d = 21.9%`
（前 10 交易日去极值截尾均值）—— **22.9 > 21.9 属正常水平**，
却被固定 25% 判成「接力成功率低」扣 2 分。
`live_signal.py` / `build_cards.py` 早就在用动态门槛，只有 `ebb` 没跟上。

### ② 六个信号全是打板指标，大盘赚钱效应一个字没看
普涨日的特征是「资金做趋势不做连板」：涨停少、板高低、题材散 ——
**打板读数必然难看，但赚钱效应其实很好**。旧判据分不清：
- 打板冷清 **且普涨** → 赚钱效应好，不该报
- 打板冷清 **且普跌** → 真退潮，必须报

### ③ 抵消必须分级（二修）
最初想「赚钱效应直接扣总分」，但那等于用指数掩盖个股亏损：
**核按钮 12 只 + 大盘涨 1.6%**，你去接高位票照样亏。
故：赚钱效应只抵消**结构性弱项**，真实亏钱效应（核按钮/炸板率）独立计分。
"""
import json
import sys
import tempfile
from pathlib import Path
from datetime import datetime
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import intraday_pulse as ip  # noqa: E402


def _fm(**kw):
    """默认 = 2026-11-05 上午真实值（普涨日）。"""
    base = {
        "date": "2026-11-05",
        "nuke_count": 2,
        "promote_rate": 22.9,
        "broken_rate": 19.0,
        "sentiment_score": 6.6,
        "breadth": {"up": 4843, "down": 632},
        "idx_avg": 1.6,
        "trend": {"promotion_gate_10d": 21.9},
    }
    base.update(kw)
    return base


def _hot_sectors(n=6):
    """全热点板块（避开「缺热点」这条干扰）。"""
    return {f"题材{i}": {"name": f"题材{i}", "zt": 10, "hot": 1} for i in range(n)}


def _cold_sectors(n=4):
    """缺热点板块（zt>=2 但 hot==0）→ 触发「活跃板块缺乏热点支撑」。"""
    return {f"冷门{i}": {"name": f"冷门{i}", "zt": 5, "hot": 0} for i in range(n)}


def _call(fm, sectors=None, total_zt=68, max_board=4):
    """`detect_ebb` 读的是 `CACHE_DIR.parent / "feedback_matrix.json"`，
    所以矩阵要放在 cache_dir 的**父目录**。"""
    root = Path(tempfile.mkdtemp())
    cache_dir = root / "pulse_snapshots"
    cache_dir.mkdir()
    (root / "feedback_matrix.json").write_text(json.dumps(fm), encoding="utf-8")
    stocks = {"600001": {"name": "高标", "boards": f"{max_board}天{max_board}板", "pct": 10}}
    with patch.object(ip, "CACHE_DIR", cache_dir), patch.object(ip, "now", return_value=datetime(2026, 11, 5, 10, 40)):
        return ip.detect_ebb(sectors or _hot_sectors(), stocks, total_zt)


# ══ ① 核心回归：11-05 这种普涨日不得报「情绪转弱」 ══════════════════════
def test_普涨日不报转弱_115实测():
    """用户原始质疑的那一天：88.5% 上涨 / 指数 +1.6% 却说情绪转弱 = 明显错误。"""
    assert _call(_fm()) is None


def test_晋级率高于动态门槛不加分():
    """22.9% > 门槛 21.9% → 属正常水平，不得判「接力成功率低」。"""
    res = _call(_fm(breadth={"up": 2500, "down": 2500}, idx_avg=0.0))
    assert res is None, f"不该报警：{res and res['signals']}"


def test_晋级率低于动态门槛要报():
    """门槛 21.9%，晋级率跌到 12% → 判罚 2 分，理由须写明门槛值。

    ⛔ 单独 2 分不到 3 分门槛（不报警）—— 这是有意的：单一信号不构成「情绪转弱」。
       叠加一个结构性弱项（缺热点 +1）后才够 3 分。"""
    res = _call(_fm(promote_rate=12.0, breadth={"up": 2500, "down": 2500}, idx_avg=0.0),
                sectors=_cold_sectors(4))
    assert res is not None
    assert any("接力成功率低" in s for s in res["signals"])
    assert any("21.9" in s for s in res["signals"]), "理由要写明用的动态门槛"


def test_门槛缺失回落25():
    """⛔ 拿不到动态门槛 → 回落 25（诚实降级）。22.9 < 25 会被判罚。

    对照上面同一个 22.9%：有动态门槛时不判罚，没有时才判罚 —— 正是本次修的差异。"""
    res = _call(_fm(trend={}, breadth={"up": 2500, "down": 2500}, idx_avg=0.0),
                sectors=_cold_sectors(4))
    assert res is not None
    assert any("接力成功率低" in s for s in res["signals"])


# ══ ② 反向保护：真正的暴跌必须报得出来 ══════════════════════════════════
def test_普跌日报退潮预警():
    """88% 下跌 + 指数 −2.5% + 核按钮 12 → 必须 ⚠ 退潮预警。"""
    res = _call(_fm(nuke_count=12, breadth={"up": 600, "down": 4800}, idx_avg=-2.5))
    assert res is not None
    assert res["severity"] == "⚠ 大盘风险预警"
    assert any("亏钱效应明显" in s for s in res["signals"])


def test_亏钱效应能独立拉响警报():
    """打板指标全中性、只有大盘崩 → 也要能报警（旧实现完全看不到这一维）。

    ⛔ 单独 2 分不到门槛，故意叠加一个结构性项（缺热点 +1）验证「广度确实加分了」；
       若广度维度没生效，这条只会剩 1 分，永远报不出来。"""
    res = _call(_fm(nuke_count=0, promote_rate=40.0, broken_rate=10.0,
                    breadth={"up": 500, "down": 4900}, idx_avg=-2.0),
                sectors=_cold_sectors(4))
    assert res is not None, "广度维度没生效（否则 2+1=3 该报）"
    assert any("亏钱效应明显" in s for s in res["signals"])


# ══ ③ 抵消分级：结构性可抵消，真实亏钱不可抵消 ══════════════════════════
def test_结构性弱项被赚钱效应抵消():
    """缺热点(+1) + 涨停少(+1) + 板高低(+1) = 3 分，强赚钱效应 −2 → 1 分，不报警。

    ⛔ 这正是「普涨日打板冷清」的典型形态：结构性难看，但赚钱效应好。"""
    res = _call(_fm(), sectors=_cold_sectors(4), total_zt=10)
    assert res is None, f"结构性弱项未被抵消：{res and (res['score'], res['signals'])}"


def test_核按钮不可被赚钱效应抵消():
    """核按钮 12 只(+2) 与结构性弱项同现：赚钱效应只抵结构性那部分。

    ⛔ 大盘普涨救不了你接高位票的亏损 —— 核按钮必须独立计分。"""
    res = _call(_fm(nuke_count=12), sectors=_cold_sectors(4), total_zt=10)
    assert res is not None, "核按钮 12 只被大盘赚钱效应完全抹平了"
    assert any("核按钮" in s for s in res["signals"])
    assert res["score"] == 3, f"预期 2 硬 + (3 软 −2) = 3，实得 {res['score']}"


def test_抵消额度封顶2分():
    """赚钱效应再强也只抵 2 分，不因广度 98% 就抵掉更多。"""
    res = _call(_fm(nuke_count=10, breadth={"up": 4900, "down": 50}, idx_avg=3.0),
                sectors=_cold_sectors(4), total_zt=10)
    assert res is not None
    assert res["score"] == 3, f"预期 3 分（2 硬 + 1 软），实得 {res['score']}"


# ══ ④ 数据缺失不得当成「0 家上涨」 ══════════════════════════════════════
def test_广度缺失时不加不减():
    """⛔ unknown ≠ 0：拿不到 breadth 就不该判「全市场 0% 上涨」。"""
    assert _call(_fm(breadth=None, idx_avg=None)) is None


def test_广度样本太少不采信():
    """涨跌家数合计 <500 → 不足以代表全市场，不参与判定。"""
    assert _call(_fm(breadth={"up": 30, "down": 20}, idx_avg=1.5)) is None


def test_指数缺失不误判为强赚钱效应():
    """广度够但指数缺失 → 不进强档（强档要求指数），也不凭空报警。"""
    assert _call(_fm(idx_avg=None)) is None


# ══ ⑤ 开关可回退 ═════════════════════════════════════════════════════════
def test_赚钱效应开关可关():
    """`ENABLE_EBB_BREADTH=0` → 回到旧行为：结构性弱项不再被抵消。"""
    with patch.object(ip, "EBB_BREADTH_ENABLED", False):
        res = _call(_fm(), sectors=_cold_sectors(4), total_zt=10)
    assert res is not None, "关掉开关后应回到旧行为（结构性 3 分会报警）"
    assert res["severity"] == "⚡ 接力转弱"


def test_普涨叠加打板亏损只报接力风险():
    res = _call(_fm(nuke_count=12, broken_rate=35))
    assert res is not None
    assert res["market_effect"] == "strong"
    assert res["risk_scope"] == "relay"
    assert res["severity"] == "⚡ 接力转弱"


def test_昨日广度不抵消今日接力风险():
    res = _call(_fm(date="2026-11-04"), sectors=_cold_sectors(), total_zt=10, max_board=2)
    assert res is not None
    assert res["market_effect"] == "unknown"
    assert res["score"] == 3


def test_错误矩阵不参与广度判断():
    res = _call(_fm(error="quote_failed"), sectors=_cold_sectors(), total_zt=10, max_board=2)
    assert res["market_effect"] == "unknown"
    assert res["score"] == 3


def test_带图标旧标题也能识别预警升级():
    assert ip._ebb_escalated(
        {"severity": "⚠ 接力风险预警", "signals": []},
        {"severity": "⚡ 情绪转弱", "signals": ["a", "b"]},
    )

