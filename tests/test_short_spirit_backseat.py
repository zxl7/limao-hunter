# -*- coding: utf-8 -*-
"""B 档·后排关联（2026-10-12 用户「取选股宝题材分类下的数据·含已封板」）回归锁。

规则（本测试锁死）：
  ① relay 后排池 = `limit_up_stocks` 同题材 ＋ **选股宝题材 `top5[题材].zt_stocks` 补全**（含 code）；
  ② 补全按 code 去重（已在 ladder 的不重复加）；
  ③ ⛔ 开关 ENABLE_SPIRIT_THEME_BACKSEAT=False → 回退到「仅 limit_up_stocks 名称匹配」的改动前行为；
  ④ 卡面第三段标签 `🔒 后排关联`（用户选 A：含已封板）。

隔离约定：仅调纯函数/纯渲染，不推送、不联网、不碰生产 cache。
"""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location(
    "short_spirit_backseat", SCRIPTS / "short_spirit.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

NOW = ss.datetime(2026, 10, 12, 10, 0)


def _sig(name="多伦科技", code="603528", sector="机器人", pct=9.98, typ="涨停"):
    return {"name": name, "code": code, "sector": sector, "theme": sector,
            "market_cap_yi": 50.0, "amount_yi": 8.0, "pct": pct, "type": typ, "tags": [],
            "resonance": 50, "flow_yi": 1.0, "strength": 0.9, "reason": f"共振[{sector}]"}


# top5 题材含 zt_stocks（选股宝直接标注的该题材票）；
# limit_up_stocks 只含其中 1 只 → 测「补全」的增量。
D = {
    "top5": [{"name": "机器人", "zt_count": 5, "zt_stocks": [
        {"name": "多伦科技", "code": "603528.SS", "pct": 9.98},
        {"name": "众业达", "code": "002441.SZ", "pct": 10.04},
        {"name": "德新科技", "code": "603032.SS", "pct": 10.02},
        {"name": "金自天正", "code": "600560.SS", "pct": 10.01},
    ]}],
    "limit_up_stocks": [
        {"name": "多伦科技", "code": "603528.SS", "sector": "机器人",
         "boards": "3天2板", "pct": 9.98},
    ],
    "leaders": [],
}


def _codes(ladder):
    return [x["code"] for x in ladder]


def test_backseat_fills_from_top5_zt_stocks():
    """选股宝题材 zt_stocks 全部补入后排池（含 code）。"""
    r = ss._relay_hint([_sig()], D)
    assert r is not None and r["sector"] == "机器人"
    assert {"603528", "002441", "603032", "600560"} <= set(_codes(r["ladder"]))


def test_backseat_dedup_by_code():
    """已在 limit_up_stocks 的票不重复（按 code 去重）。"""
    r = ss._relay_hint([_sig()], D)
    assert _codes(r["ladder"]).count("603528") == 1, "多伦科技不得重复"


def test_backseat_switch_off(monkeypatch):
    """关开关 → 回退到仅 limit_up_stocks 匹配（只剩多伦科技）。"""
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_THEME_BACKSEAT", False)
    r = ss._relay_hint([_sig()], D)
    codes = set(_codes(r["ladder"]))
    assert codes == {"603528"}, f"关开关应只剩 limit_up_stocks，实际 {codes}"


def test_backseat_renders_in_card():
    """卡面第三段出现「后排关联」含补全票。"""
    r = ss._relay_hint([_sig()], D)
    card = ss.build_card([_sig()], "morning", NOW, relay=r)
    body = "\n".join((el.get("text") or {}).get("content", "") for el in card["elements"])
    assert "后排关联" in body
    assert "众业达" in body
