#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""em_flow（东财资金流数据层·P2-18）黄金用例。

字段口径实证基准（2026-09-09 收盘实测·银行Ⅱ BK0475 / 半导体 BK1036）：
  f62=-265560816 → 主力 main_net
  f66=-250720496 → 超大 super_large_net；f72=-14840320 → 大 large_net
  主=超大+大：-250720496 + -14840320 = -265560816 ✓ 精确成立
  f78=-394034688 → 中 mid_net；f84=+659595520 → 小 small_net
  守恒：main + mid + small = -265560816 - 394034688 + 659595520 ≈ 0 ✓
分钟 klines 行序：时间,主力,小单,中单,大单,超大单（2026-09-09 14:59 实测行）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.em_flow import (
    net_to_yi,
    parse_kline_flows,
    parse_snapshot_item,
    secid_of_sector,
    secid_of_stock,
    sector_flow_structure,
)

# ── 实测快照样例（BK0475 银行Ⅱ 收盘）──
REAL_ITEM = {
    "f12": "BK0475", "f14": "银行Ⅱ",
    "f62": -265560816.0, "f66": -250720496.0, "f72": -14840320.0,
    "f78": -394034688.0, "f84": 659595520.0,
    "f124": 1788939572,
}
# ── 实测分钟 klines 行（14:59）──
REAL_KLINE = "2026-09-09 14:59,-265677611.0,659578932.0,-393901348.0,-14957105.0,-250720506.0"


# ── 实测概念板块守恒破缺样例（BK1650·2026-09-09 21:35 实测）──
# main=super+large 精确成立；但 mid+small 与 -main 偏差约 1.1%（成分重叠/分档舍入正常态）
CONCEPT_BREACH_ITEM = {
    "f12": "BK1650", "f14": "概念样例",
    "f62": 12353864960.0, "f66": 11883013376.0, "f72": 470851584.0,
    "f78": -6857756672.0, "f84": -5635534848.0,
    "f124": 1788939572,
}


def test_parse_snapshot_守恒成立():
    """真实样例通过校验且字段映射正确（主=超大+大 精确成立）。"""
    it = parse_snapshot_item(REAL_ITEM)
    assert it is not None
    assert it["code"] == "BK0475"
    assert it["name"] == "银行Ⅱ"
    assert it["main_net"] == -265560816.0
    assert it["super_large_net"] == -250720496.0
    assert it["large_net"] == -14840320.0
    assert it["mid_net"] == -394034688.0
    assert it["small_net"] == 659595520.0
    assert it["source_ts"] == 1788939572
    assert it["flow_consistent"] is True
    # 恒等式复核
    assert it["main_net"] == it["super_large_net"] + it["large_net"]
    assert abs(it["main_net"] + it["mid_net"] + it["small_net"]) < 100.0  # 元级舍入噪声(实测16元)


def test_parse_snapshot_主力定义违反丢弃():
    """main ≠ 超大+大（定义性恒等）→ 上游数据异常 → 丢弃（强校验）。"""
    bad = dict(REAL_ITEM, f62=-999999999.0)  # 主力改错
    assert parse_snapshot_item(bad) is None


def test_parse_snapshot_概念守恒破缺软打标():
    """概念板块 1~5% 守恒破缺 → 保留数据 + flow_consistent=False（不误杀正常态）。"""
    it = parse_snapshot_item(CONCEPT_BREACH_ITEM)
    assert it is not None
    assert it["code"] == "BK1650"
    assert it["flow_consistent"] is False
    # main = 超大+大 在破缺样例上仍精确成立（恒等 1 不受影响）
    assert it["main_net"] == it["super_large_net"] + it["large_net"]


def test_parse_snapshot_缺f124丢弃():
    """f124 缺失/为 0 → 无源时间戳 → 丢弃（新鲜度门禁前置）。"""
    bad = dict(REAL_ITEM, f124=0)
    assert parse_snapshot_item(bad) is None


def test_secid_规则():
    assert secid_of_sector("BK0475") == "90.BK0475"
    assert secid_of_sector("90.BK1036") == "90.BK1036"  # 幂等
    assert secid_of_stock("600519") == "1.600519"
    assert secid_of_stock("000001") == "0.000001"
    assert secid_of_stock("300750") == "0.300750"
    assert secid_of_stock("688981") == "1.688981"  # 科创板=沪市 1 前缀


def test_parse_kline_行序():
    """行序=时间,主力,小单,中单,大单,超大单（实测列序·勿对错）。"""
    flows = parse_kline_flows([REAL_KLINE])
    assert len(flows) == 1
    f = flows[0]
    assert f["time"] == "2026-09-09 14:59"
    assert f["main_net"] == -265677611.0       # parts[1] 主力
    assert f["small_net"] == 659578932.0       # parts[2] 小单（第三位！）
    assert f["mid_net"] == -393901348.0        # parts[3] 中单
    assert f["large_net"] == -14957105.0       # parts[4] 大单
    assert f["super_large_net"] == -250720506.0  # parts[5] 超大单
    # 行内恒等式复核
    assert f["main_net"] == f["super_large_net"] + f["large_net"]
    assert abs(f["main_net"] + f["mid_net"] + f["small_net"]) < 100.0  # 元级舍入噪声(实测27元)


def test_net_to_yi_与结构():
    assert net_to_yi(-265560816.0) == -2.66
    assert net_to_yi(0) == 0.0
    st = sector_flow_structure(parse_snapshot_item(REAL_ITEM))
    assert st["super_large_yi"] == -2.51
    assert st["large_yi"] == -0.15
    assert st["mid_yi"] == -3.94
    assert st["small_yi"] == 6.6
    assert st["source_ts"] == 1788939572
