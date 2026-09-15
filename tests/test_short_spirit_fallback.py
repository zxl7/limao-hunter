# -*- coding: utf-8 -*-
"""短线精灵连板龙头兜底（2026-09-07 C 方案 + _boards_map 修复）回归锁。

C=东财涨幅榜抓取失败(下午沙箱常 RemoteDisconnected→池0)时, 用本地面板快照
   latest.json 的连板龙头(boards>=2)兜底, 确保连板龙头盘中任何时段可推。
⛔ 同时锁死 _boards_map 对 boards="3天3板" 中文格式的解析(之前 int(b) 抛异常→A豁免失效):
  1. _boards_map("3天3板") 必须 = 3
  2. _leaders_as_pool 只返回 boards>=2
  3. 降级项 market_cap_yi=9999(触发A豁免) + prev_close 反推(涨停标正确)
"""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("short_spirit", SCRIPTS / "short_spirit.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


def test_boards_map_parses_chinese_format():
    d = {"leaders": [{"code": "600865", "name": "百大", "boards": "3天3板"}]}
    assert ss._boards_map(d).get("600865") == 3


def test_leaders_as_pool_only_boards_ge2():
    d = {"leaders": [
        {"code": "600865", "name": "百大", "boards": "3天3板", "pct": 10.04,
         "price": 12.49, "sector": "大消费"},
        {"code": "123456", "name": "一板", "boards": "1天1板", "pct": 10.0,
         "price": 5.0, "sector": "X"},
    ]}
    pool = ss._leaders_as_pool(d)
    assert [p["code"] for p in pool] == ["600865"]   # 只连板>=2
    b = pool[0]
    assert b["market_cap_yi"] == 9999.0               # 触发 A 豁免
    assert abs(b["prev_close"] - 12.49 / 1.1004) < 0.01  # 涨停价反推正确
