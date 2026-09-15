# -*- coding: utf-8 -*-
"""股性基因单测（2026-09-09 用户「股性很重要·连板/涨停溢价/竞价全做」）。

锁定 zt_genes 核心逻辑（纯函数·不触网）：
  1) _norm 东财涨停池字段规范化
  2) build_gene 涨停基因/晋级率/炸板率统计
  3) gene_score 权重合成合理（连板多/晋级高→分高）
  4) with_kline=False 溢价基因为 None（批量防限流路径）
"""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("zt_genes", SCRIPTS / "zt_genes.py")
zg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zg)


def _x(code, boards, broken=0, seal=92500, days=None, ct=None):
    days = days or boards
    ct = ct or boards
    return {"code": code, "name": f"票{code}", "zt_boards": boards, "zt_days": days,
            "zt_times": ct, "seal_min": zg._hhmm_to_min(seal), "last_seal_min": None,
            "broken": broken, "seal_fund_yi": 1.0, "pct": 10.0, "hs": 8.0, "industry": "测试"}


def test_norm_and_hhmm():
    assert zg._hhmm_to_min(92500) == 9 * 60 + 25
    assert zg._hhmm_to_min(130530) == 13 * 60 + 5
    assert zg._hhmm_to_min(None) is None
    n = zg._norm({"c": "002702", "n": "海欣食品", "lbc": 3, "fbt": 92500, "lbt": 93639,
                  "zbc": 2, "fund": 68849242, "hybk": "食品加工",
                  "zttj": {"days": 3, "ct": 3}, "zdp": 10.05, "hs": 19.4})
    assert n["code"] == "002702" and n["zt_boards"] == 3 and n["broken"] == 2
    assert n["zt_days"] == 3 and n["zt_times"] == 3 and n["seal_fund_yi"] == 0.69
    assert n["seal_min"] == 9 * 60 + 25


def test_build_gene_pure_pool_no_kline():
    # 三连板票：D1 首板 → D2 连板 → D3 连板（晋级 2/2）
    pools = {
        "20260904": [_x("000001", 1), _x("000002", 2)],
        "20260907": [_x("000001", 2, seal=93000), _x("000003", 1)],
        "20260908": [_x("000001", 3, seal=94500), _x("000003", 2)],
    }
    g = zg.build_gene("000001", pools, with_kline=False)
    assert g["zt_n"] == 3 and g["max_lbc"] == 3
    assert g["promo_rate"] == 100.0        # D1→D2、D2→D3 全晋级
    assert g["prem_close_avg"] is None     # with_kline=False → 溢价无
    assert g["broken_rate"] == 0.0
    assert g["gene_score"] > 50            # 高连板高晋级 → 高分


def test_build_gene_promo_miss():
    # 首板票次日断板 → 晋级 0；炸板 1 次
    pools = {
        "20260907": [_x("000005", 1, broken=1)],
        "20260908": [],
    }
    # 空日不参与（_load_pools 过滤空）——再造 D2 有他人涨停=交易日
    pools["20260908"] = [_x("000009", 1)]
    g = zg.build_gene("000005", pools, with_kline=False)
    assert g["zt_n"] == 1 and g["promo_rate"] == 0.0
    assert g["broken_rate"] == 1.0
    assert g["gene_score"] < 50  # 无晋级+高炸板 → 明显低于高连板票（小窗口频率分偏高属正常）


def test_build_gene_no_record_returns_none():
    pools = {"20260908": [_x("000009", 1)]}
    assert zg.build_gene("999999", pools, with_kline=False) is None


def test_gene_score_ordering():
    # 同窗口：4板高晋级 > 1板无晋级
    pools = {
        "20260904": [_x("000010", 1), _x("000020", 1)],
        "20260907": [_x("000010", 2), _x("000020", 1)],
        "20260908": [_x("000010", 3), _x("000020", 1)],
    }
    g1 = zg.build_gene("000010", pools, with_kline=False)
    g2 = zg.build_gene("000020", pools, with_kline=False)
    assert g1["gene_score"] > g2["gene_score"]
    assert g1["max_lbc"] == 3 and g2["max_lbc"] == 1


# ── 历史涨停基因（日K 自算）──

def _krows(closes: list[float], code="600000") -> list[dict]:
    """由收盘序列构造日K行（open=前收·开盘价未知场景用昨收近似；涨停判定只看 close/prev）"""
    rows = []
    prev = closes[0] * 0.95
    for i, c in enumerate(closes):
        rows.append({"d": f"2026{i+1:03d}", "open": prev, "close": c,
                     "high": max(c, prev), "low": min(c, prev)})
        prev = c
    return rows


def test_zt_price_boundary():
    """涨停判定边界：10.00 封板价=round(昨收*1.1,2) 命中；9.5% 未封不命中。"""
    # 昨收 10.00 → 涨停价 11.00
    rows = [{"d": "A", "open": 10.0, "close": 10.0},
            {"d": "B", "open": 10.5, "close": 11.0},     # 涨停
            {"d": "C", "open": 11.0, "close": 10.95}]    # 未封
    data = zg._limit_rows(rows, "600000")
    assert zg._is_zt_row(data[1]) is True
    assert zg._is_zt_row(data[2]) is False


def test_hist_zt_count_and_seq():
    # 10 天：3 连板 + 隔 1 天 2 连板 = 涨停5次 最长3板 序列[3,2]
    prices = [10, 10, 10, 11.0, 12.10, 13.31, 12.5, 12.5, 13.75, 15.13, 14.0]
    rows = _krows(prices, "600000")
    g = zg.build_hist_gene("600000", rows)
    assert g["zt_total"] == 5
    assert g["max_boards_hist"] == 3
    assert g["board_seq"][-2:] == [3, 2]


def test_hist_promo_and_prem():
    # 两段：首板次日再涨停(晋级) → 首板次日高开低走(不晋级)
    # 手算：d1 首板收11 → d2 再板收12.1(晋级·open 11.5=+4.5%) → d3 回落收11.5(open 12.5=+3.3%)
    prices = [10, 11, 12.1, 11.5]
    rows = [{"d": f"D{i}", "open": [10, 11.5, 12.5, 11.5][i],
             "close": p} for i, p in enumerate(prices)]
    g = zg.build_hist_gene("600000", rows)
    assert g["promo_rate_hist"] == 50.0           # 两次晋级机会(D1→D2 成 / D2→D3 败)
    assert g["prem_open_hist"] == round(((12.5/11-1) + (11.5/12.1-1)) * 50, 2)  # 两次涨停次日开盘溢价均
    assert g["first_prem_open_hist"] == round((12.5/11-1) * 100, 2)  # 首板(D1)次日高开


def test_hist_window_250_excludes_old_zt():
    """2026-09-09 窗口统一修复：晋级/溢价只统计近250交易日窗内涨停——窗口外(旧)涨停
    的晋级样本不再计入（曾全历史统计致口径混用：应流股份全历史首板高开 1.35% vs 250日窗 0.63%）。"""
    closes = [10.0] * 300
    closes[5] = 11.0      # 窗口外(idx5 < win0=50)涨停
    closes[6] = 12.1      # 窗口外涨停次日再涨停（晋级对·旧逻辑计入、新逻辑排除）
    closes[259] = 10.0
    closes[260] = 11.0    # 窗口内(idx260 >= 50)涨停
    closes[261] = 12.0    # 次日非涨停 → 窗口内晋级失败
    rows = _krows(closes, "600000")
    g = zg.build_hist_gene("600000", rows)
    assert g["zt_total"] == 3              # 全历史 3 次涨停
    assert g["zt_250"] == 1                # 250 窗内仅 idx260
    assert g["promo_rate_hist"] == 0.0     # 仅 idx260 样本(失败)——旧逻辑 1/3=33.3%
    assert g["max_boards_hist"] == 2       # 全历史最长 2 连板(idx5-6·语义保留)


def test_hist_window_250_excludes_old_zt():
    """2026-09-09 窗口统一修复：晋级/溢价只统计近250交易日窗内涨停——窗口外(旧)涨停
    的晋级样本不再计入（曾全历史统计致口径混用：应流股份全历史首板高开 1.35% vs 250日窗 0.63%）。"""
    closes = [10.0] * 300
    closes[5] = 11.0      # 窗口外(idx5 < win0=50)涨停
    closes[6] = 12.1      # 窗口外涨停次日再涨停（晋级对·旧逻辑计入、新逻辑排除）
    closes[259] = 10.0
    closes[260] = 11.0    # 窗口内(idx260 >= 50)涨停
    closes[261] = 12.0    # 次日非涨停 → 窗口内晋级失败
    rows = _krows(closes, "600000")
    g = zg.build_hist_gene("600000", rows)
    assert g["zt_total"] == 3              # 全历史 3 次涨停
    assert g["zt_250"] == 1                # 250 窗内仅 idx260
    assert g["promo_rate_hist"] == 0.0     # 仅 idx260 样本(失败)——旧逻辑 1/3=33.3%
    assert g["max_boards_hist"] == 2       # 全历史最长 2 连板(idx5-6·语义保留)


def test_hist_20cm_and_st():
    # 创业板 20cm 判定 + ST 5% 不误判
    rows = [{"d": "A", "open": 10.0, "close": 10.0},
            {"d": "B", "open": 11.5, "close": 12.0}]   # +20% → 创业板涨停价
    data = zg._limit_rows(rows, "300999")
    assert zg._is_zt_row(data[1]) is True
    # ST 主板 +5%: 昨收 10 → 收 10.5 非 11.0 → 不判涨停
    rows2 = [{"d": "A", "open": 10.0, "close": 10.0}, {"d": "B", "open": 10.3, "close": 10.5}]
    data2 = zg._limit_rows(rows2, "600001")
    assert zg._is_zt_row(data2[1]) is False


# ── 历史涨停基因（日K 自算）──

def _krows(closes: list[float], code="600000") -> list[dict]:
    """由收盘序列构造日K行（open=前收·开盘价未知场景用昨收近似；涨停判定只看 close/prev）"""
    rows = []
    prev = closes[0] * 0.95
    for i, c in enumerate(closes):
        rows.append({"d": f"2026{i+1:03d}", "open": prev, "close": c,
                     "high": max(c, prev), "low": min(c, prev)})
        prev = c
    return rows
