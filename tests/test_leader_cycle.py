from core import leader_cycle as lc
from core import pool_call


def stock(code, boards=1, role="跟风", carry=0, theme="医药"):
    return {"code": code, "name": code, "boards": boards, "leader_role": role,
            "carry_cnt": carry, "theme_name": theme, "sector": theme}


def day(d, rows, complete=True):
    return {"date": d, "rows": rows, "complete": complete}


def test_role_before_height_and_low_position():
    a, b, c = stock("000001", 2, "龙头"), stock("000002", 6, "准龙头"), stock("000003", 1)
    assert [r["code"] for r in sorted([c, b, a], key=lc.status_key, reverse=True)] == ["000001", "000002", "000003"]


def test_break_birth_followup_and_failures_no_future():
    old = stock("000001", 6, "龙头", 4)
    first, promoted = stock("000002"), stock("000003", 2, theme="机器人")
    hist = [day("2026-09-21", [old]), day("2026-09-22", [first, promoted]),
            day("2026-09-23", [stock("000002", 2)])]
    early = lc.analyse(hist, "2026-09-22")
    assert len(early) == 2 and all(e["status"] == "tracking" for e in early)
    assert {e["kind"] for e in early} == {"first_board", "promotion"}
    late = lc.analyse(hist, "2026-09-23")
    assert late[0]["progress"][-1]["boards"] == 2
    assert late[1]["status"] == "not_limit_up"


def test_height_alone_cannot_create_old_leader_event():
    hist = [day("2026-09-21", [stock("000001", 6, "准龙头")]), day("2026-09-22", [stock("000002")])]
    assert lc.analyse(hist, "2026-09-22") == []


def test_incomplete_or_missing_workday_cannot_imply_break():
    old = stock("000001", 6, "龙头")
    assert lc.analyse([day("2026-09-21", [old]), day("2026-09-23", [stock("000002")])], "2026-09-23") == []
    assert lc.analyse([day("2026-09-21", [old]), day("2026-09-22", [], False), day("2026-09-23", [stock("000002")])], "2026-09-23") == []


def test_same_day_not_successor_confirmation_and_no_price_mutation():
    r = stock("000002", 1)
    r["expectation"] = {"expected_range": [0, 2]}
    hist = [day("2026-09-21", [stock("000001", 6, "龙头")]), day("2026-09-22", [r])]
    lc.annotate([r], hist, "2026-09-22")
    assert r["leader_role"] == "跟风"
    assert r["leader_evidence"]["succession"]
    assert r["expectation"] == {"expected_range": [0, 2]}


def test_two_to_three_requires_carry_and_continuity():
    r = stock("000001", 2, carry=3)
    lc.annotate([r], [day("2026-09-21", [stock("000001")]), day("2026-09-22", [r])], "2026-09-22")
    assert r["leader_role"] == "准龙头"
    assert r["leader_evidence"]["two_to_three"]


def test_previous_two_board_cohort_keeps_failed_competitors():
    r = stock("000001", 3, carry=2)
    hist = [day("2026-09-21", [stock("000001", 2), stock("000002", 2)]),
            day("2026-09-22", [r])]
    lc.annotate([r], hist, "2026-09-22")
    assert r["leader_evidence"]["previous_two_board_cohort"] == [
        {"code": "000001", "same_theme": True, "promoted": True},
        {"code": "000002", "same_theme": True, "promoted": False},
    ]


def test_successor_needs_followup_carry_and_stops_on_gap():
    r = stock("000002", 3, carry=2)
    hist = [day("2026-09-21", [stock("000001", 6, "龙头")]),
            day("2026-09-22", [stock("000002", 2)]), day("2026-09-23", [r])]
    lc.annotate([r], hist, "2026-09-23")
    assert r["leader_role"] == "准龙头"
    no_carry = stock("000002", 3)
    lc.annotate([no_carry], hist, "2026-09-23")
    assert no_carry["leader_role"] == "跟风"
    events = lc.analyse(hist + [day("2026-09-24", [], False)], "2026-09-24")
    assert events[0]["status"] == "data_gap"


def test_five_dimensions_competition_follow_and_news_boundary():
    r = stock("000001", 3, carry=2)
    hist = [day("2026-09-21", [stock("000001", 2), stock("000002", 2), stock("000003")]),
            day("2026-09-22", [r, stock("000003", 2), stock("000004")])]
    news = [{"date": "2026-09-21", "title": "政策落地", "stocks": ["000001"]},
            {"date": "2026-09-22", "title": "订单签订", "stocks": ["000001"]}]
    ev = lc.five_dimensions(r, hist, "2026-09-22", news)
    assert ev["competition"]["status"] == "market_only"
    assert ev["sector_follow"]["status"] == "sustained"
    assert ev["catalyst"]["status"] == "unverified_news"
    for item in news:
        item.update(kind="policy", verified=True, source="test_fixture")
    assert lc.five_dimensions(r, hist, "2026-09-22", news)["catalyst"]["status"] == "sustained_verified"
    news[1]["title"] = news[0]["title"]
    assert lc.five_dimensions(r, hist, "2026-09-22", news)["catalyst"]["status"] != "sustained_verified"


def test_five_dimensions_recovery_and_launch_no_future(monkeypatch):
    from core import dragon_confirm as _dc
    r = stock("000001", 4, "准龙头", 3)
    hist = [day("2026-09-16", [stock("000099", theme="机器人")]),
            day("2026-09-17", [stock("000099", theme="机器人")]),
            day("2026-09-18", [stock("000099", theme="机器人")]),
            day("2026-09-21", [stock("000001"), stock("000002"), stock("000003")]),
            day("2026-09-22", [stock("000001", 2), stock("000002", 2)]),
            day("2026-09-23", [r, stock("000002", 3), stock("000004")])]
    ev = lc.five_dimensions(r, hist, "2026-09-23")
    assert ev["launch"]["status"] == "new_direction_candidate"
    assert ev["divergence"]["status"] == "recovery_with_carry"
    early = lc.five_dimensions(r, hist, "2026-09-22")
    assert early["divergence"]["status"] == "held_during_contraction"
    # ⛔ 替代路径关闭时保持原文语义：缺催化不能确认（这是本条测试的本意）
    monkeypatch.setenv(_dc.ALLOW_NO_CATALYST_ENV, "0")
    lc.annotate([r], hist, "2026-09-23")
    assert r["leader_role"] == "准龙头"  # 缺催化不能确认
    # 2026-10-21 用户拍板 A：替代路径开启后，只差催化也能确认（数据缺口补偿）
    monkeypatch.delenv(_dc.ALLOW_NO_CATALYST_ENV, raising=False)
    r.pop("dragon_confirm", None)
    r["leader_role"] = "准龙头"
    lc.annotate([r], hist, "2026-09-23")
    assert r["leader_role"] == "龙头"          # A 方案：只差催化 → 也算确认
    assert "催化这一项目前拿不到证据" in (r["dragon_confirm"]["narrative"] or "")
    news = [{"date": d, "title": title, "stocks": ["000001"], "kind": "policy",
             "verified": True, "source": "test_fixture"} for d, title in
            [("2026-09-22", "事件一"), ("2026-09-23", "事件二")]]
    lc.annotate([r], hist, "2026-09-23", news)
    assert r["leader_role"] == "龙头"
    assert r["leader_evidence"]["confirmation"] == "confirmed"
    assert "至少两日" in r["leader_evidence"]["note"]


def test_complete_guard():
    s = {"date": "2026-09-22", "zt_list": [stock("000001")], "zt_count": 1, "generated_ts": "2026-09-22 14:00:00"}
    assert not lc._complete(s, "2026-09-22")
    s["generated_ts"] = "2026-09-22 15:30:00"
    assert lc._complete(s, "2026-09-22")
    s["zt_count"] = 2
    assert not lc._complete(s, "2026-09-22")


def test_pool_status_priority_keeps_entry_gates():
    a, b = stock("000001", 3, "龙头", 4), stock("000002", 6, "跟风")
    for r in (a, b):
        r["expectation"] = {"expected_range": [0, 5], "auction_amount_min_yi": 0.1}
    quotes = {r["code"]: {"prev_close": 10, "auction_price": 10.2, "auction_amount_yi": 1} for r in (a, b)}
    result = pool_call.call_pool({"relay": [b, a]}, quotes, stance="defend", line_cores={})
    assert result["views"]["000001"]["view"] == "participate"
    assert result["views"]["000001"]["status_rank"] == 1
    quotes["000001"]["auction_price"] = 10.6
    result = pool_call.call_pool({"relay": [b, a]}, quotes, stance="defend", line_cores={})
    assert result["views"]["000001"]["view"] == "skip"
