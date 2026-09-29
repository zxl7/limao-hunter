# -*- coding: utf-8 -*-
"""behavior_attribution.py 回归锁（2026-09-23 建）

锁的是三个**最容易算错的口径**——都是真实交割单上踩过的坑：
  ① 比率列量纲：Excel「0.00%」存小数、csv 存小数，必须归一到「百分数」
  ② 仓位分母：时段亏损占比的分母是「**净亏损**」而不是毛亏损（错了会把 66% 算成 27.6%）
  ③ 仓位大小：「本轮持仓内**最大占用资金峰值**」而不是「买入总额」
     （买总额会把滚动加仓累加，实测某债算出 359 万，而账户净投入只有 65 万）
"""
import csv
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("behavior_attribution", ROOT / "scripts" / "behavior_attribution.py")
ba = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ba)

CLOSED_HEADER = ["清仓日期", "代码", "名称", "总盈亏", "盈亏比", "同期大盘", "跑赢大盘",
                 "买入均价", "卖出均价", "清仓距今", "持仓天数", "交易费用", "建仓日期"]
# ⚠️ 比率列故意写「小数」（0.10 = 10%），验证 csv 无量纲信息时的量级兜底
CLOSED_ROWS = [
    ["2024-03-10", "000001", "甲", "500", "0.10", "0.01", "0.09", "10", "11", "0.05", "10", "30", "2024-02-01"],
    ["2024-03-12", "000002", "乙", "-2000", "-0.05", "0.01", "-0.06", "10", "9.5", "0.60", "3", "20", "2024-03-01"],
    ["2024-03-20", "000003", "丙", "-1000", "-0.02", "0.01", "-0.03", "10", "9.8", "-0.70", "30", "10", "2024-02-20"],
]
TRADE_HEADER = ["成交日期", "成交时间", "代码", "名称", "交易类别", "成交数量", "成交价格",
                "发生金额", "成交金额", "费用", "备注"]
TRADE_ROWS = [
    # 甲：买入 1 万 → 卖出（峰值占用 1 万）
    ["2024-02-01", "09:30:00", "000001", "甲", "买入", "1000", "10", "10000", "10000", "10", ""],
    ["2024-03-10", "14:55:00", "000001", "甲", "卖出", "1000", "11", "11000", "11000", "10", ""],
    # 乙：买入 2 万 → 卖出（峰值占用 2 万 = 最大仓）
    ["2024-03-01", "09:31:00", "000002", "乙", "买入", "2000", "10", "20000", "20000", "20", ""],
    ["2024-03-12", "10:00:00", "000002", "乙", "卖出", "2000", "9.5", "19000", "19000", "20", ""],
    # 丙：买入 1 万，**同一轮内做 T 再买 1 万**（买总额 2 万，但峰值占用仍是 1 万）
    ["2024-02-20", "09:40:00", "000003", "丙", "买入", "1000", "10", "10000", "10000", "10", ""],
    ["2024-02-21", "10:00:00", "000003", "丙", "卖出", "1000", "10.2", "10200", "10200", "10", ""],
    ["2024-02-22", "10:10:00", "000003", "丙", "买入", "1000", "10", "10000", "10000", "10", ""],
    ["2024-03-20", "14:00:00", "000003", "丙", "卖出", "1000", "9.8", "9800", "9800", "10", ""],
]


def _write(d: Path, name: str, header, rows):
    p = d / name
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return p


@pytest.fixture()
def broker_dir(tmp_path):
    _write(tmp_path, "已清仓.csv", CLOSED_HEADER, CLOSED_ROWS)
    _write(tmp_path, "交易记录.csv", TRADE_HEADER, TRADE_ROWS)
    return tmp_path


def test_csv_dir_loads_both_tables(broker_dir):
    b = ba.load_broker(broker_dir)
    assert len(b["closed"]) == 3
    assert len(b["trades"]) == 8
    assert b["meta"]["closed_sheet"] == "已清仓"
    assert b["meta"]["trade_sheet"] == "交易记录"


def test_ratio_columns_normalised_to_percent(broker_dir):
    """① 量纲：csv 里的 0.10 必须变成 10.0（百分数量纲）"""
    m = ba.compute(ba.load_broker(broker_dir))
    assert m["avg_win_pct"] == pytest.approx(10.0)
    assert m["avg_loss_pct"] == pytest.approx(-3.5)      # (-5 + -2) / 2
    assert m["win_rate_pct"] == pytest.approx(100 / 3)
    assert m["expectancy_pct"] == pytest.approx(1.0)     # 0.3333*10 + 0.6667*(-3.5)
    assert m["payoff"] == pytest.approx(10 / 3.5)


def test_position_size_is_peak_capital_not_gross_buy(broker_dir):
    """③ 仓位大小 = 窗口内 (买入−卖出) 累计的峰值。丙做 T 后买总额 2 万，但峰值仍 1 万"""
    m = ba.compute(ba.load_broker(broker_dir))
    assert m["pos_max"] == pytest.approx(20000)          # 乙
    assert m["pos_median"] == pytest.approx(10000)
    # 若误用「买入总额」，丙会计成 20000 → 与乙并列，最大仓判定就失真
    segs = {s["label"]: s for s in m["size_segments"]}
    assert segs["最大 25%"]["n"] == 1                    # 只有乙在最大档


def test_top25_loss_share_uses_net_loss_denominator(broker_dir):
    """② 分母口径：净亏损 = 500 − 2000 − 1000 = −2500；最大仓（乙）亏 −2000 → 80%"""
    m = ba.compute(ba.load_broker(broker_dir))
    assert m["realized_pnl"] == pytest.approx(-2500)
    assert m["top25_loss_share_pct"] == pytest.approx(80.0)
    # 用毛亏损（−3000）当分母会算成 66.7% —— 口径写错就退化，必须锁住
    assert m["top25_loss_share_pct"] > 66.7


def test_fly_ratio_and_gates(broker_dir):
    m = ba.compute(ba.load_broker(broker_dir))
    ae = m["after_exit"]
    assert ae["fly_n"] == 1 and ae["good_n"] == 1        # 乙 +60% 卖飞 / 丙 −70% 卖对
    assert ae["fly_ratio"] == pytest.approx(1.0)

    g = {x["key"]: x for x in ba.gates(m)}
    assert g["payoff"]["ok"] is True                     # 2.857 ≥ 2.0
    assert g["horizon"]["ok"] is True                    # 中位 10 天 ≥ 5
    assert g["size"]["ok"] is False                      # 80% > 40%
    assert g["friction"]["ok"] is True                   # 100 / 2500 = 4.0% ≤ 5%
    assert g["fly"]["ok"] is True                        # 1.0 ≤ 1.0


def test_no_persist_by_default(monkeypatch, broker_dir):
    """⛔ 生产路径守卫：测试环境下 main() 不得写生产 cache"""
    prod = ROOT / "wealth_code" / "cache" / "behavior_attribution.json"
    before = prod.exists()
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_no_persist_by_default")
    monkeypatch.setattr("sys.argv", ["behavior_attribution.py", "--path", str(broker_dir), "--persist"])
    with pytest.raises(SystemExit) as ei:
        ba.main()
    assert "拒绝写生产路径" in str(ei.value)
    assert prod.exists() == before                       # 生产目录未新增文件
