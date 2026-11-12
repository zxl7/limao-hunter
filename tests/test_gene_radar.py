# -*- coding: utf-8 -*-
"""低位连板龙雷达（`pipeline_eod.select_gene_radar`）回归。

背景（2026-10-22 用户定）：大业股份这种票「第一个板或第二个板就必须发现」，
但首板/2 板常只有 1~3 亿成交额，会被进场门槛挡在池外，于是卡上查无此票。
本名单把「低位 + 股性连板龙 + 还有量」的票单列出来，**只展示、不占名额、不进 09:25 裁决**。

这里钉死四条纪律（都是踩过坑的）：
  ① 池内已有的票不重复列（否则雷达变成池子的复印件）；
  ② 高位二波不进（冰山冷热 000530 教训：首板+25 亿+基因连板龙，但位置已 +45%~64%）；
  ③ 一字/688/ST/成交额<1 亿 不进（买不到 / 无权限 / 铁律不看 / 看了也白看）；
  ④ 「连板龙·次数少 / 历史短」不进（样本不够，不是「它一直能连」的证据）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "wealth_code"))

import pipelines.pipeline_eod as pz  # noqa: E402


def _row(code, name, boards=1, cje=2.0, **kw):
    d = {"code": code, "name": name, "boards": boards, "cje_yi": cje,
         "pct": 10.0, "fbt": "09:35:00", "zbc": 0, "theme": "机器人",
         "is_20cm": False, "is_st": False, "is_one_word": False}
    d.update(kw)
    return d


def _facts(grade="连板龙", zt=16, promo=40.0, mb=8, fpo=2.2):
    return {"grade": grade, "zt_250": zt, "promo_rate_hist": promo,
            "max_boards_hist": mb, "first_prem_open_hist": fpo, "annual_avg": 9.0}


def _patch(monkeypatch, facts_by_code, wave_by_code=None):
    monkeypatch.setattr(pz._zt_gene, "gene_facts",
                        lambda code, cache_dir=None: facts_by_code.get(str(code), {}))
    wave_by_code = wave_by_code or {}
    monkeypatch.setattr(
        pz._defense_gate, "position_wave",
        lambda code, series=None, days=None: wave_by_code.get(
            str(code), {"known": True, "is_rebound": False, "prior_max_board": 1}),
    )


def test_keeps_low_position_lianban_long(monkeypatch):
    """低位 + 连板龙 + 有量 → 进雷达，且不可买。"""
    _patch(monkeypatch, {"603933": _facts()})
    out = pz.select_gene_radar([_row("603933", "睿能科技", 1, 1.03)],
                               in_pool_codes=set(),
                               excluded_turnover_codes={"603933"})
    assert [x["code"] for x in out] == ["603933"]
    assert out[0]["buyable"] is False
    assert out[0]["gene"]["grade"] == "连板龙"
    assert "3亿" in out[0]["radar_why"]


def test_skips_stocks_already_in_pool(monkeypatch):
    """已在 relay/watch 里的票不再列一遍。"""
    _patch(monkeypatch, {"603933": _facts()})
    out = pz.select_gene_radar([_row("603933", "睿能科技", 1, 1.03)],
                               in_pool_codes={"603933"})
    assert out == []


def test_skips_high_position_second_wave(monkeypatch):
    """冰山冷热教训：首板 + 大成交 + 连板龙，但断板 3 天后重启且上一波 4 板 → 不是低位。"""
    _patch(monkeypatch,
           {"000530": _facts(promo=57.0, zt=8, mb=6)},
           {"000530": {"known": True, "is_rebound": True, "prior_max_board": 4}})
    out = pz.select_gene_radar([_row("000530", "冰山冷热", 1, 25.5)], in_pool_codes=set())
    assert out == []


def test_keeps_fast_n_shape(monkeypatch):
    """断 1~2 天快速回封是加分项 → 不能被位置过滤误杀。"""
    _patch(monkeypatch,
           {"603278": _facts()},
           {"603278": {"known": True, "is_rebound": False, "prior_max_board": 5}})
    out = pz.select_gene_radar([_row("603278", "大业股份", 2, 4.0)], in_pool_codes=set())
    assert [x["code"] for x in out] == ["603278"]


def test_skips_small_sample_grade(monkeypatch):
    """「连板龙·次数少 / 历史短」不进雷达（样本不够，不构成「一直能连」的证据）。"""
    _patch(monkeypatch, {"002076": _facts(grade="连板龙·次数少", zt=4, promo=67.0)})
    out = pz.select_gene_radar([_row("002076", "星光股份", 1, 2.0)], in_pool_codes=set())
    assert out == []


def test_skips_hard_exclusions(monkeypatch):
    """一字板 / 688 / ST / 3 板以上 一律不进。

    ⚠️ 2026-11-11 用户「5 亿限制全系统清除」后，成交额不再是否决理由；
    地量票（0.6 亿）现在可以进雷达，故本用例只锁前四类硬排除。
    """
    _patch(monkeypatch, {c: _facts() for c in
                         ("603001", "688001", "603002", "603003", "603004")})
    rows = [
        _row("603001", "一字票", 1, 2.0, is_one_word=True),
        _row("688001", "科创票", 1, 2.0),
        _row("603002", "ST票", 1, 2.0),
        _row("603003", "高位票", 3, 2.0),
    ]
    assert pz.select_gene_radar(rows, in_pool_codes=set()) == []


def test_position_thresholds_not_duplicated(monkeypatch):
    """雷达用的位置阈值必须取 defense_gate 的常量，防止两处各写一套漂移。"""
    assert pz._defense_gate.POSITION_DEV10_MIN == 45.0
    assert pz._defense_gate.POSITION_DEV30_MIN == 50.0
    assert pz._defense_gate.POSITION_PRIOR_BOARD_MIN == 3
