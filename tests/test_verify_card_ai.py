# -*- coding: utf-8 -*-
"""竞价验证卡：历史股性行 + AI 判断行（2026-09-10 用户「合并进竞价验证 + 补历史股性」）。

锁定：build_verify_card 对每票渲染 📈 历史基因（读 hist_{code}.json）与
🤖 AI 判断（读 cache/ai_view_{trade_date}.json）；文件缺失 → 诚实省略不占位。
隔离：mock 掉外部依赖块（_core_reversal_block/_nav_drawdown_block）避免触网。
"""
import importlib.util
import json
from pathlib import Path

WC = Path(__file__).resolve().parents[1] / "wealth_code"
spec = importlib.util.spec_from_file_location("build_cards", WC / "pipelines" / "build_cards.py")
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)


def _rec(code, name, status, gap):
    return {"code": code, "name": name, "bucket_label": "高标/题材核心",
            "theme": "大消费", "industry_sector": "一般零售",
            "performance": {"open_check": {"decision_status": status, "can_enter": status == "buy",
                                           "gap_pct": gap, "auction_amount_yi": 0.9,
                                           "auction_amount_need_yi": 0.6, "signal_label": "符合预期"}}}


def _vr(trade_date, records):
    return {"payload": {"records": records},
            "summary": {"total_candidates": len(records), "buy_signals": 0, "hit_signals": 0,
                        "covered": 0, "nav_latest": 1.0, "trade_date10": trade_date}}


def _text(card):
    return "\n".join(e.get("text", {}).get("content", "") for e in card["elements"] if e.get("text"))


def test_verify_card_has_hist_gene(monkeypatch):
    """有 hist 档案的票 → 竞价验证卡渲染历史股性行。"""
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    # 000759 中百集团（真实 hist 档案存在）· 无 ai_view → 只测基因行
    card = bc.build_verify_card(_vr("2099-01-01", [_rec("000759", "中百集团", "buy", 0.4)]))
    s = _text(card)
    assert "📈 历史基因" in s and "近一年" in s      # 2026-09-10 白话化：近250日→近一年
    assert "🤖 AI 昨日判断" not in s          # 无 AI 视图 → 不占位（诚实）


def test_verify_card_has_ai_view(monkeypatch, tmp_path):
    """有 ai_view → 渲染 🤖 AI 判断行（参与红/放弃绿/观察紫 + 理由）。"""
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    d = tmp_path / "cache"
    d.mkdir()
    (d / "ai_view_2099-01-02.json").write_text(json.dumps({
        "date": "2099-01-02", "views": {
            "000759": {"view": "skip", "reason": "冰点中位断板风险"},
            "000930": {"view": "participate", "reason": "题材最强+身位首选"},
        }}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    card = bc.build_verify_card(_vr("2099-01-02", [_rec("000759", "中百集团", "buy", 0.4),
                                                   _rec("000930", "中粮科技", "pending", 2.2)]))
    s = _text(card)
    assert "🤖 AI 昨日判断：放弃" in s and "冰点中位断板风险" in s
    assert "🤖 AI 昨日判断：参与" in s


def test_verify_card_no_data_graceful(monkeypatch, tmp_path):
    """无 hist 档案、无 ai_view → 两行均省略、卡片正常生成（不报错）。"""
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    card = bc.build_verify_card(_vr("2099-01-03", [_rec("888888", "不存在", "reject", -1.0)]))
    s = _text(card)
    assert "📈 历史基因" not in s and "🤖 AI 昨日判断" not in s
    assert card["elements"]


def test_verify_card_reject_section_has_gene_and_ai(monkeypatch, tmp_path):
    """2026-09-10 用户「竞价验证这里没有历史股性也要加上」：
    ⚪ 其余未达标（reject）段的每票也必须渲染 📈 历史基因 + 🤖 AI 判断
    （此前只加在 buy/hit/pending 三段 → reject 票看不到股性/AI）。"""
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    d = tmp_path / "cache"
    (d / "zt_genes").mkdir(parents=True)      # 隔离后 hist 档案也须就位（ROOT 被替换）
    (d / "zt_genes" / "hist_603308.json").write_text(json.dumps({
        "code": "603308", "zt_250": 7, "max_boards_hist": 1,
        "promo_rate_hist": 0.0, "first_prem_open_hist": 0.63}, ensure_ascii=False),
        encoding="utf-8")
    (d / "ai_view_2099-01-04.json").write_text(json.dumps({
        "date": "2099-01-04",
        "views": {"603308": {"view": "skip", "reason": "首板魔咒·7次全单板"}},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    # 603308 应流股份：真实 hist 档案存在 + reject 状态
    card = bc.build_verify_card(_vr("2099-01-04", [_rec("603308", "应流股份", "reject", -0.65)]))
    s = _text(card)
    assert "其余没达标" in s
    assert "📈 历史基因" in s and "近一年" in s      # 2026-09-10 白话化：近250日→近一年
    assert "🤖 AI 昨日判断：放弃" in s and "首板魔咒" in s


def test_boards_txt_and_stock_label():
    """2026-09-10 用户「竞价要显示几板·方便确认接力」：
    竞价卡个股标签追加连板数；首板标「首板」；无板数据诚实省略。"""
    assert bc._boards_txt({"boards": 3}) == " 3板"
    assert bc._boards_txt({"boards": 5}) == " 5板"
    assert bc._boards_txt({"boards": 1}) == " 首板"
    assert bc._boards_txt({}) == ""            # 无数据不标
    assert bc._boards_txt({"boards": 0}) == ""
    lbl = bc._stock_hl_boards({"name": "中百集团", "code": "000759", "score": 82.5, "boards": 3})
    assert "中百集团(000759)" in lbl and "82.5" in lbl and "3板" in lbl
    lbl1 = bc._stock_hl_boards({"name": "海通发展", "code": "603162", "boards": 1})
    assert "首板" in lbl1


def test_verify_card_rows_show_boards(monkeypatch):
    """竞价验证卡各段主行（观察/未达标）必须带板数。"""
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    monkeypatch.setattr(bc, "_load_ai_view", lambda td: {})
    r1 = _rec("000759", "中百集团", "pending", 5.67)
    r1["boards"] = 3
    r2 = _rec("603308", "应流股份", "reject", -0.65)
    r2["boards"] = 1
    s = _text(bc.build_verify_card(_vr("2099-01-05", [r1, r2])))
    assert "中百集团(000759)" in s and "3板" in s
    assert "应流股份" in s and "首板" in s


def test_card_copy_no_jargon(monkeypatch):
    """2026-09-10 用户「话术上删除专业术语，不要推专业术语！」：
    竞价卡/盘后候选卡文案禁出现机器黑话（英文档位/字段名/内部指标/自造术语）。"""
    BAD = ["expected", "regime=", "门控=", "pp）", "[放量", "可信度", "题材板块效应", "孤军",
           "弱转强", "分歧/死亡", "冰点禁买", "已结算", "样本", "容量核心", "承接",
           "spec §", "health ", "情绪升温", "情绪退潮", "情绪中性", "严格主升", "冰点档"]
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    r = _rec("000759", "中百集团", "pending", 5.67)
    r["boards"] = 3
    s = _text(bc.build_verify_card(_vr("2099-01-06", [r])))
    hit = [b for b in BAD if b in s]
    assert hit == [], f"竞价卡残留术语: {hit}"


def test_boards_and_seal_form_plain():
    """形态标签白话化：`[放量烂板×1]` → `（放量、封板不稳·砸开1次）`。"""
    it = {"expectation": {"seal_form": "放量烂板", "seal_zbc": 1}}
    txt = bc._seal_form_tag(it)
    assert txt == "（放量、封板不稳·砸开1次）"
    assert bc._seal_form_tag({"expectation": {"seal_form": "快速板"}}) == "（封板很快）"
    assert bc._seal_form_tag({}) == ""
    # 英文状态档位 → 中文
    assert bc._status_plain("expected") == "符合预期"
    assert bc._status_plain("super") == "超预期"
    assert bc._gate_plain("ice") == "市场很冷"
    assert bc._gate_plain("neutral") == "市场平稳"


# ── 2026-09-10 补强：eod 卡「🎯 明日执行判定」段 + 大盘环境块纳入禁项扫描 ──
# 背景：test_card_copy_no_jargon 只扫竞价卡，eod 卡的决策段（core/decision_outlook.py）
# 与 gate 块（透传 feedback_matrix.sentiment_label）漏网，09-10 盘后卡实测残留
# 「情绪 1.0/10 冰点」「·退潮」被用户铁律判为禁项。此处补扫防复发。

_EOD_BAD = ["expected", "regime=", "defense=", "pp）", "[放量", "可信度", "题材板块效应", "孤军",
            "弱转强", "冰点", "退潮", "亢奋", "修复档", "容量核心", "spec §", "health ",
            "情绪升温", "情绪退潮", "情绪中性", "严格主升", "冰点档", "身位", "杂毛",
            "buy/", "pending", "reject", "MA5", "feedback_matrix", "反馈矩阵", "已结算", "样本"]


def _do():
    import importlib.util as _u
    sp = _u.spec_from_file_location("decision_outlook", WC / "core" / "decision_outlook.py")
    m = _u.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


def test_decision_outlook_copy_no_jargon():
    """明日执行判定五档文案全白话（阈值/verdict 键不变·仅显示层）。"""
    do = _do()
    cases = [
        dict(regime="ice", sentiment=1.0, promote_rate=18.8, nuke_count=9, avg_pct=0.1),
        dict(regime="ice", sentiment=1.5, promote_rate=12.0, nuke_count=7, promote_downtrend=True),
        dict(regime="risk_off", sentiment=3.5, promote_rate=18.0, nuke_count=8,
             avg_pct=0.4, defense_level=1),
        dict(regime="risk_on", sentiment=8.0, promote_rate=45.0, nuke_count=1),
        dict(regime="neutral", sentiment=5.0, promote_rate=32.0, nuke_count=3),
        dict(regime="neutral", sentiment=5.0, nav_drawdown_pct=12.0),
        dict(sentiment=None),
    ]
    for c in cases:
        d = do.build_decision(**c)
        shown = {k: v for k, v in d.items() if k != "basis"}   # basis 是内部字段名·不上卡
        txt = json.dumps(shown, ensure_ascii=False)
        hit = [b for b in _EOD_BAD if b in txt]
        assert hit == [], f"{c} 残留术语: {hit}"
        assert d["verdict"] in do.VERDICT_LABEL


def test_eod_decision_block_no_jargon(monkeypatch):
    """eod 卡「🎯 明日执行判定」依据/纪律行零字段名。

    2026-09-14 漏网：`test_decision_outlook_copy_no_jargon` 显式排除 `basis`
    （注释写「basis 是内部字段名·不上卡」），但 `build_eod_card` 恰恰把 basis/rules
    以 `k=v` 渲染上卡 → 正式群卡面出现 `sentiment=8.0｜promote_rate=27.5｜nuke_count=3｜
    avg_pct=3.17｜defense_level=1` 与 `position=/count=/entry=/stop=`。
    ⛔ 教训：**测试排除项 == 实际渲染项** 是同类漏网根因；扫描必须覆盖整卡渲染结果。
    """
    import core.decision_outlook as _do_mod
    monkeypatch.setattr(_do_mod, "decision_from_files", lambda root: {
        "verdict": "wait", "tone": "👀 观望·等信号再动",
        "conclusion": "今天做多气氛 8.0 分（满分10），环境偏防守——观望为主",
        "basis": {"sentiment": 8.0, "promote_rate": 27.5, "nuke_count": 3,
                  "avg_pct": 3.17, "regime": None, "defense_level": 1},
        "triggers": ["做多气氛 ≥4.5 分"],
        "rules": {"position": "≤30% 单票", "count": "最多 1 只·只做成交活跃的龙头",
                  "entry": "开盘涨幅 ≤3% 才参与，超 5% 放弃",
                  "stop": "跌 3% 或跌破当天均价就走", "note": "一次买够"},
        "risks": ["市场只转好一天就会错过"],
    })
    card = bc.build_eod_card({"date": "2026-09-14", "regime": "neutral",
                              "environment": {}, "relay": [], "watch": [],
                              "diagnostics": {}})
    blob = json.dumps(card, ensure_ascii=False)
    field_leaks = ["sentiment=", "promote_rate=", "nuke_count=", "avg_pct=",
                   "defense_level=", "position=", "count=", "entry=", "stop=",
                   "regime=", "verdict=", "basis"]
    hit = [b for b in field_leaks if b in blob]
    assert hit == [], f"决策段残留内部字段名 {hit}"
    assert "做多气氛8.0/10" in blob and "晋级率27.5%" in blob and "核按钮3只" in blob
    assert "仓位：≤30% 单票" in blob and "止损：跌 3% 或跌破当天均价就走" in blob


def test_gate_block_no_jargon():
    """大盘环境块：feedback_matrix 的 sentiment_label 不得原样透传（退潮/冰点 → 白话）。"""
    blocks = bc._gate_block(None, {"regime": "ice", "sentiment_score": 1.0,
                                   "sentiment_label": "退潮", "promote_rate": 18.8,
                                   "nuke_count": 9, "break_count": 0})
    s = "\n".join(b.get("text", {}).get("content", "") for b in blocks if b.get("text"))
    hit = [b for b in ["退潮", "冰点", "亢奋", "修复档"] if b in s]
    assert hit == [], f"大盘环境块残留定调术语: {hit}"
    assert "市场很冷" in s and "做多气氛1.0/10" in s      # 白话映射生效
    # 未知标签诚实透传（不编造）
    assert bc._sentiment_label_plain("某个新标签") == "某个新标签"


# ── 2026-09-11 补强：候选胶囊 tag 云纳入禁项扫描 ──
# 背景：09-11 推送前整卡扫描实测残留「板块孤军」「换手30.8%·高承接」两处圈内造词
# （均在 `_candidate_tags`，eod 卡与竞价卡共用）。已白话化为「同方向只它1家」「·换手大」。
# 此处锁定：tag 云全量文案零禁项（阈值/字段名不动·仅显示层）。

_TAG_BAD = ["孤军", "承接", "容量核心", "弱转强", "梯队", "身位", "杂毛", "溢价率",
            "维度", "研判", "标的", "卡位", "兑现", "埋伏", "打板", "buy", "pending", "reject"]


def test_candidate_tags_no_jargon():
    """候选胶囊 tag 云全白话：高换手 + 同方向仅1家 + 断板风险等极端分支全覆盖。"""
    cases = [
        # 高换手(≥15%) + 真孤军（行业/题材均无板块效应）
        {"code": "002585", "name": "双星新材", "boards": 1, "hs": 30.75, "cje_yi": 10.4,
         "seal_amt": 0.93, "fbt": "093318", "zbc": 2, "sector_is_lone": True,
         "theme_has_sector_effect": False, "carry_cnt": 1, "is_20cm": False},
        # 早封 + 题材有板块效应 + 带动 + 20cm
        {"code": "300563", "name": "神宇股份", "boards": 1, "hs": 18.05, "cje_yi": 6.12,
         "seal_amt": 0.56, "fbt": "100624", "zbc": 3, "sector_is_lone": False,
         "theme_has_sector_effect": True, "theme_name": "通信设备", "theme_mates_cnt": 3,
         "carry_cnt": 3, "is_20cm": True},
        # 空字段兜底（诚实降级不编造）
        {},
        # 2026-09-14 补：断板后回封 + 同方向各档都有 + 板块大票 + 题材龙一 + 断板风险高
        # （旧 fixture 未覆盖 weak2strong / ladder_has_complete 分支 → 这两处曾残留
        #  「弱转强·N板后回封」「梯队完整」圈内造词而测试不报错）
        {"code": "000978", "name": "桂林旅游", "boards": 4, "hs": 33.72, "cje_yi": 16.9,
         "seal_amt": 1.2, "fbt": "093009", "zbc": 8, "sector_is_lone": False,
         "theme_has_sector_effect": True, "theme_name": "旅游", "theme_mates_cnt": 3,
         "carry_cnt": 1, "is_20cm": False, "weak2strong": True, "w2s_prev_boards": 4,
         "ladder_has_complete": True, "is_sector_dragon": True, "is_capacity_core": True,
         "dim_panel": {"风险": {"label": "高"}, "_band": "强候选"},
         "lt_mktcap": 50.8, "price": 14.74, "main_flow_yi": 2.17, "chip_profit_rate": 91},
        # 断板后回封（前次仅 1 板）+ 无梯队
        {"code": "600488", "name": "津药药业", "boards": 1, "hs": 10.9, "cje_yi": 6.7,
         "fbt": "101920", "zbc": 0, "weak2strong": True, "w2s_prev_boards": 1},
    ]
    for it in cases:
        txt = "".join(t for t, _ in bc._candidate_tags(it))
        hit = [b for b in _TAG_BAD if b in txt]
        assert hit == [], f"tag 云残留术语 {hit}: {txt}"


def test_eod_mid_carry_line_no_jargon():
    """eod 卡「中间位置没人跟」提示零禁项（2026-09-14：原句含「梯队」圈内造词）。"""
    import json as _json
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(bc.__file__).resolve().parent.parent))
    cand = {
        "date": "2026-09-14", "regime": "neutral",
        "environment": {"sentiment_score": 8.0, "sentiment_label": "偏强",
                        "promote_rate": 27.5, "nuke_count": 3, "broken_rate": 38.9,
                        "break_count": 0, "now_zt_count": 55},
        "relay": [{"code": "000823", "name": "超声电子", "boards": 3, "score": 99.3,
                   "cje_yi": 24.9, "dim_panel": {"_mid_carry": "不足"}}],
        "watch": [],
        "diagnostics": {}, "global_narrative": "账户：测试\n盘面：测试\n操作：测试",
    }
    card = bc.build_eod_card(cand)
    blob = _json.dumps(card, ensure_ascii=False)
    hit = [b for b in _TAG_BAD + ["中位没人接"] if b in blob]
    assert hit == [], f"eod 卡残留术语 {hit}"


# ── 2026-09-11：竞价卡「🤖 AI 昨日买入计划」段 ──
# 用户需求原话：「AI 在每天 9:25 竞价的时候，在那张卡片里面要告诉我今天买什么，
# 然后我们再来统计它的收益。不要因为我的买动，而是要 AI 做出它自己的买入和操作方向。」
# 机制：AI 昨日盘后写 ai_view（stance/direction + views）→ 次日卡上按竞价 gap 判成交 + 展示 AI 独立轨成绩。

_PLAN_BAD = ["已结算", "样本", "承接", "孤军", "弱转强", "容量核心", "标的", "研判", "维度",
             "regime=", "命门", "冰点", "退潮", "亢奋", "buy", "pending", "reject", "MA5"]


def test_verify_card_ai_plan_block(monkeypatch, tmp_path):
    """有 ai_view（含攻守定调）→ 渲染「🤖 AI 昨日买入计划」：买到/没买到 + 其余不买 + 战绩。"""
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    d = tmp_path / "cache"
    d.mkdir()
    (d / "ai_view_2099-02-01.json").write_text(json.dumps({
        "date": "2099-02-01", "decision_date": "2099-01-30",
        "stance": "selective", "stance_label": "只做最强的一只",
        "direction": "电力/绿电", "note": "市场不冷不热，只做最强的一只",
        "entry_max_gap": 3.0,
        "views": {
            "000993": {"view": "participate", "reason": "3板·带动3家", "entry_max_gap": 3.0},
            "000823": {"view": "participate", "reason": "电子布涨价链", "entry_max_gap": 3.0},
            "603421": {"view": "skip", "reason": "封板不稳"},
        }}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    r1 = _rec("000993", "闽东电力", "pending", 2.1)
    r1["boards"] = 3
    r2 = _rec("000823", "超声电子", "reject", 6.8)
    r2["boards"] = 2
    r3 = _rec("603421", "鼎信通讯", "reject", 1.2)
    r3["boards"] = 3
    s = _text(bc.build_verify_card(_vr("2099-02-01", [r1, r2, r3])))
    assert "🤖 AI 昨日买入计划" in s and "01-30" in s       # 决策日（execution-1）显式标注
    assert "只做最强的一只" in s and "电力/绿电" in s and "市场不冷不热" in s
    assert "买入" in s and "高开5%以内" in s               # 000993 竞价+2.10% ≤ 5% → 买到
    assert "没买到" in s and "高开超5%" in s               # 000823 竞价+6.80% > 5% → 没买到
    assert "其余 1 只昨日没有安排买" in s and "鼎信通讯" in s
    assert "还没开始记账" in s                            # 无 AI 轨账本 → 诚实不编造
    hit = [b for b in _PLAN_BAD if b in s]
    assert hit == [], f"AI 买入计划段残留术语: {hit}"


def test_verify_card_ai_stats_rendered(monkeypatch, tmp_path):
    """有 AI 独立轨账本 → 卡上展示 AI 自己的成绩（天数/胜率/日均/净值·与真人持仓无关）。"""
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    d = tmp_path / "cache"
    d.mkdir()
    (tmp_path / "wealth_code" / "cache").mkdir(parents=True)
    (d / "ai_view_2099-02-02.json").write_text(json.dumps({
        "date": "2099-02-02", "decision_date": "2099-02-01",
        "stance_label": "只做最强的一只", "direction": "电力/绿电",
        "entry_max_gap": 3.0,
        "views": {"000993": {"view": "participate", "reason": "3板·带动3家"}},
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "wealth_code" / "cache" / "ai_nav_history.jsonl").write_text(
        json.dumps({"trade_date": "2099-01-31", "avg_return_pct": 1.5, "nav": 1.015}) + "\n" +
        json.dumps({"trade_date": "2099-02-01", "avg_return_pct": -0.5, "nav": 1.00993}) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    s = _text(bc.build_verify_card(_vr("2099-02-02", [_rec("000993", "闽东电力", "pending", 1.0)])))
    assert "AI 自己的成绩" in s
    assert "2" in s and "50.0%" in s and "+0.50%" in s and "1.00993" in s


# ── 2026-09-14：买入口径统一为 AI（用户原话「我想统一为AI口径」） ──
# 背景：09-14 卡头写「买入信号 5」（机械 can_enter），而 AI 计划只判 1 只「参与」，
# 5 只里有 3 只 AI 明确判放弃 —— 同一张卡两个买入口径打架。现统一为 AI 口径：
# 卡头计数 + 定调 + ✅ 段标注全部由 AI 判「参与」的只数驱动，机械达标降为灰字括注参考。

def _vr_buys(trade_date, records, buy_signals):
    return {"payload": {"records": records},
            "summary": {"total_candidates": len(records), "buy_signals": buy_signals,
                        "hit_signals": 0, "covered": 0, "nav_latest": 1.0,
                        "trade_date10": trade_date}}


def _ai_view_file(d, trade_date, views, **extra):
    payload = {"date": trade_date, "decision_date": "2099-03-01",
               "stance_label": "只观察·最多试一只", "entry_max_gap": 3.0, "views": views}
    payload.update(extra)
    (d / f"ai_view_{trade_date}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_verify_card_buy_signal_uses_ai_caliber(monkeypatch, tmp_path):
    """AI 有判断 → 卡头「AI 买入信号」= AI 判「参与」只数；机械达标降为灰字参考；✅ 段标机械。"""
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    d = tmp_path / "cache"
    d.mkdir()
    _ai_view_file(d, "2099-03-02", {
        "000993": {"view": "participate", "reason": "3板·带动3家", "entry_max_gap": 2.0},
        "000823": {"view": "skip", "reason": "先看不动"},
        "600184": {"view": "skip", "reason": "股性没验证过"},
    })
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    # 机械 3 只达标（buy），AI 只认 1 只
    recs = [_rec("000993", "闽东电力", "buy", 1.0),
            _rec("000823", "超声电子", "buy", 0.5),
            _rec("600184", "光电股份", "buy", 0.8)]
    card = bc.build_verify_card(_vr_buys("2099-03-02", recs, 3))
    s = _text(card)
    assert "AI 买入信号" in s and ">1<" in s                     # AI 口径 = 1
    assert "（机械达标 3 只）" in s                                # 机械口径降为灰字参考
    assert "✅ 竞价达标·机械参考" in s                              # ✅ 段明确标注口径
    assert "达标买入信号" not in s                                 # 旧标题不再出现
    assert "AI 只买1只·精选" in card["title"]                       # 定调同口径


def test_verify_card_buy_signal_falls_back_without_ai_view(monkeypatch, tmp_path):
    """AI 无判断（ai_view 缺失）→ 回退机械口径（诚实降级·不编造 AI 数字）。"""
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    recs = [_rec("000993", "闽东电力", "buy", 1.0), _rec("000823", "超声电子", "buy", 0.5)]
    card = bc.build_verify_card(_vr_buys("2099-03-03", recs, 2))
    s = _text(card)
    assert "AI 买入信号" not in s and "机械达标" not in s
    assert "买入信号" in s and ">2<" in s
    assert "达标买入信号" in s and "机械参考" not in s               # 旧行为原样
    assert "个别达标·精选" in card["title"]                         # 定调仍按机械只数(2)


def test_verify_card_ai_caliber_empty_participate(monkeypatch, tmp_path):
    """AI 判 0 只参与（全观察/放弃）→ 卡头 0 只 + 定调空仓观望，不误显机械只数当买入。"""
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    d = tmp_path / "cache"
    d.mkdir()
    _ai_view_file(d, "2099-03-04", {"000993": {"view": "watch", "reason": "再等一等"}})
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    card = bc.build_verify_card(_vr_buys("2099-03-04", [_rec("000993", "闽东电力", "buy", 1.0)], 1))
    s = _text(card)
    assert "AI 买入信号" in s and ">0<" in s and "（机械达标 1 只）" in s
    assert "AI 空仓·观望" in card["title"]
    hit = [b for b in _PLAN_BAD if b in s]
    assert hit == [], f"AI 口径卡残留术语: {hit}"


def test_ai_caliber_switch_off_restores_old(monkeypatch, tmp_path):
    """开关 AI_CALIBER_BUY_SIGNAL=False → 一键回退旧行为（机械口径），AI 视图照常渲染。"""
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    d = tmp_path / "cache"
    d.mkdir()
    _ai_view_file(d, "2099-03-05", {"000993": {"view": "participate", "reason": "3板"}})
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    monkeypatch.setattr(bc, "AI_CALIBER_BUY_SIGNAL", False)
    card = bc.build_verify_card(_vr_buys("2099-03-05", [_rec("000993", "闽东电力", "buy", 1.0),
                                                       _rec("000823", "超声电子", "buy", 0.5)], 2))
    s = _text(card)
    assert "AI 买入信号" not in s and "（机械达标" not in s
    assert "买入信号" in s and ">2<" in s
    assert "达标买入信号" in s
    assert "个别达标·精选" in card["title"]                         # 回退后定调按机械只数(2)


def test_ai_participate_count_unit():
    """计数口径：只认 view=="participate"；watch/skip/空/非 dict 一律不计。"""
    assert bc._ai_participate_count({}) == 0
    assert bc._ai_participate_count({"views": {}}) == 0
    assert bc._ai_participate_count({"views": {"a": {"view": "participate"},
                                               "b": {"view": "watch"},
                                               "c": {"view": "skip"},
                                               "d": {}, "e": None}}) == 1


# ── 2026-09-15 用户「个股下的话术重复了」：同源 reason 禁两处渲染 ──
# 背景：`views[code].reason` 被「🤖 AI 昨日买入计划」块（买入票）+ 个股段
# `_ai_view_line`（🤖 AI 判断行）各渲染一次 → 同一段逐字重复。
# 09-15 超声电子(000823) 实证：整卡扫描该段 4 句全部 x2（唯一逐字重复项）。
# 修复：计划块仅在**下方不会渲染该票 AI 判断行**时才补出理由（信息不丢）。

def test_ai_reason_rendered_once(monkeypatch, tmp_path):
    """买入票的 AI 理由只在个股段渲染一次（计划块不重复整段）。"""
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    d = tmp_path / "cache"
    d.mkdir()
    _ai_view_file(d, "2099-04-01", {
        "000823": {"view": "participate", "reason": "电子布涨价链最强", "entry_max_gap": 5.0}})
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    r = _rec("000823", "超声电子", "buy", 1.63)      # decision_status=buy → 个股段渲染 AI 判断行
    s = _text(bc.build_verify_card(_vr_buys("2099-04-01", [r], 1)))
    assert "🤖 AI 昨日买入计划" in s and "电子布涨价链最强" in s
    assert s.count("电子布涨价链最强") == 1, "同源 AI 理由被重复渲染（计划块 + 个股段）"


def test_ai_reason_kept_when_no_per_stock_line(monkeypatch, tmp_path):
    """报价缺失（unavailable）段只出行、不渲染 AI 判断行 → 计划块须补出理由（信息不丢）。"""
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    d = tmp_path / "cache"
    d.mkdir()
    _ai_view_file(d, "2099-04-02", {
        "000823": {"view": "participate", "reason": "电子布涨价链最强", "entry_max_gap": 5.0}})
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    r = _rec("000823", "超声电子", "unavailable", None)   # 无报价 → 个股段不出 AI 判断行
    s = _text(bc.build_verify_card(_vr_buys("2099-04-02", [r], 0)))
    assert "报价缺失" in s
    assert s.count("电子布涨价链最强") == 1, "报价缺失时理由应保留一次（不得丢失）"
