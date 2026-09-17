# -*- coding: utf-8 -*-
"""龙头锁定（2026-09-17 用户拍板）回归锁。

## 用户口径（原话）
「龙头基本不会出现日内切换，龙头讲究的是**连续性、领涨性和带领性**。
  一般确定了之后，哪怕是前赴后继变强而已，也不会改变龙头的核心。」
→ **换人的唯一条件 = 原龙头退位**（不再封板 = 炸板 / 跌停 / 断板）。

## 病根（实证，必须被这些用例钉住）
原实现 `max(up, key=(主板, len(boards), hot, pct))`：
  ① 每轮从「当轮上涨的票」重选 → 是**排名**不是**身份**；
  ② 末档键是**当轮涨幅**；
  ③ `len(boards)` 拿**字符串长度**当连板高度 —— `'4天2板'` 与 `'8天5板'` 长度同为 4 → **并列**
     → 直接落到 `pct` 决胜负。
后果：09-17 PCB板一天"换"6 次龙头，只有 3 个名字来回转
（中材科技→中岩大地→中材科技→澳弘电子→中材科技→中岩大地→澳弘电子）；这类抖动占全天事件 51.4%。

⛔ 这些是**静默错值**：判错不报错，只是把「排序抖动」当成「市场事件」推给用户。
"""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("intraday_pulse", SCRIPTS / "intraday_pulse.py")
ip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ip)

spec2 = importlib.util.spec_from_file_location("memory_lib", SCRIPTS / "memory_lib.py")
ml = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(ml)


def S(code, name, boards, pct, up=True, et=0, hot=False):
    """造一只板块内个股（字段与 `parse_stocks` 产出对齐）"""
    return {"code": code, "name": name, "boards": boards, "pct": pct, "up": up,
            "enter_ts": et, "turn": 5.0, "hot_plates": ["热门"] if hot else []}


def _old_key(s):
    """⛔ **旧算法**（已废弃，仅用于对照证明病根）：主板 > len(boards) > hot > pct"""
    c = s.get("code", "")
    main = (c[:1] in ("6", "0")) and not c.startswith(("688", "300", "301"))
    return (main, len(s.get("boards", "")), bool(s.get("hot_plates")), s["pct"])


# ── ① 板数解析：连板数 ≠ 字符串长度 ─────────────────────────────
def test_parse_board_num_reads_the_board_count_not_string_length():
    assert ip.parse_board_num("4天2板") == 2
    assert ip.parse_board_num("8天5板") == 5
    assert ip.parse_board_num("18天9板") == 9
    assert ip.parse_board_num("2天2板") == 2
    assert ip.parse_board_num("5板") == 5
    assert ip.parse_board_num("1天1板") == 1
    assert ip.parse_board_num("") == 0
    assert ip.parse_board_num(None) == 0
    assert ip.parse_board_num("3") == 3          # 无「N板」形态 → 退回首个数字
    # ⛔ 反例：字符串长度会把 2 板和 5 板判成「一样高」——这正是旧 bug 的根
    assert len("4天2板") == len("8天5板") == 4
    assert ip.parse_board_num("4天2板") != ip.parse_board_num("8天5板")


# ── ② 病根对照：旧键抖动，新键恒定 ─────────────────────────────
def test_old_pct_key_flaps_while_new_key_is_stable():
    """同一批票、只有当轮涨幅在变 → 旧算法**来回换**，新算法**纹丝不动**。"""
    rounds = ({"a": 10.02, "b": 9.99},   # 甲涨得多
              {"a": 9.99, "b": 10.02},   # 乙涨得多
              {"a": 10.01, "b": 10.00})  # 甲又涨得多
    old_names, new_names = [], []
    for p in rounds:
        a = S("600001", "甲", "4天2板", p["a"], et=34200)
        b = S("600002", "乙", "8天5板", p["b"], et=34800)
        pair = [a, b]
        old_names.append(max(pair, key=_old_key)["name"])                 # ⛔ 旧算法
        new_names.append(ip.diagnose_dragon(pair, 30)["candidate"]["name"])
    assert len(set(old_names)) > 1, "旧算法本应抖动（否则这组数据没复现病根）"
    assert set(new_names) == {"乙"}, "新算法必须以连板数定龙（5板 > 2板），与涨幅无关"


def test_board_count_beats_higher_pct():
    """连板数优先：低涨幅的高板票必须赢过高涨幅的低板票。"""
    low_board_hot = S("600001", "热票", "2天2板", 20.0, et=33000, hot=True)
    high_board = S("600002", "高板", "6天5板", 9.99, et=36000)
    assert ip.diagnose_dragon([low_board_hot, high_board], 30)["candidate"]["name"] == "高板"


def test_same_board_then_earliest_seal_wins():
    """领涨性：同为 3 板 → **先封板**的带队（与 `leader.score_leadership` 同口径）。"""
    late = S("600001", "晚封", "3天3板", 10.0, et=36000)
    early = S("600002", "早封", "3天3板", 10.0, et=34200)
    assert ip.diagnose_dragon([late, early], 30)["candidate"]["name"] == "早封"


def test_missing_seal_time_sorts_last():
    """缺失封板时刻 → 排最后（⛔ 不让缺失数据胜出）。"""
    no_time = S("600001", "无时刻", "3天3板", 10.0, et=0)
    has_time = S("600002", "有时刻", "3天3板", 10.0, et=36000)
    assert ip.diagnose_dragon([no_time, has_time], 30)["candidate"]["name"] == "有时刻"


def test_deterministic_regardless_of_input_order():
    """纯确定性：名单顺序不影响结论（否则同一份数据两轮可能给出不同龙头）。"""
    import itertools
    sec = [S("600001", "甲", "3天3板", 10.0, et=34200), S("000002", "乙", "3天3板", 10.0, et=34200),
           S("600003", "丙", "1天1板", 10.0, et=33000)]
    names = {ip.diagnose_dragon(list(p), 30)["candidate"]["name"] for p in itertools.permutations(sec)}
    assert len(names) == 1


def test_main_board_tilt_is_gone():
    """⛔ 「主板优先」已按用户 2026-08-13 的撤销处理：同板数时**先封板的创业板票**应当胜出。"""
    gem_early = S("300003", "创业早封", "3天3板", 20.0, et=33000)
    main_late = S("600001", "主板晚封", "3天3板", 10.02, et=36000)
    assert _old_key(main_late) > _old_key(gem_early), "旧算法本应让主板票胜出（复现撤销前的行为）"
    assert ip.diagnose_dragon([gem_early, main_late], 30)["candidate"]["name"] == "创业早封"


# ── ③ 锁定：确立后只允许加固，换人只发生在退位 ─────────────────
def test_lock_holds_even_when_a_higher_board_newcomer_arrives():
    """用户拍板的核心：**后来者变强不换龙头**。"""
    a = S("600001", "原龙头", "3天3板", 10.0, et=33000)
    newcomer = S("300009", "新贵", "5天5板", 20.0, et=34500)   # 板更高、涨幅更大
    no_lock = ip.diagnose_dragon([a, newcomer], 30)
    assert no_lock["candidate"]["name"] == "新贵", "无锁时应按连板数换人（前提成立）"
    with_lock = ip.diagnose_dragon([a, newcomer], 30, locked={"code": "600001", "name": "原龙头"})
    assert with_lock["candidate"]["name"] == "原龙头"
    assert with_lock["lock_held"] is True


def test_lock_breaks_only_when_original_retires():
    """唯一允许换人的情况：原龙头**不再封板**（炸板/跌停/断板）。"""
    retired = S("600001", "原龙头", "3天3板", 3.2, up=False)      # ⛔ 炸板 → 不在 up 名单
    successor = S("600002", "接棒", "4天4板", 10.0, et=36000)
    d = ip.diagnose_dragon([retired, successor], 30, locked={"code": "600001", "name": "原龙头"})
    assert d["candidate"]["name"] == "接棒"
    assert d["lock_held"] is False


def test_lock_of_absent_code_does_not_crash():
    """锁里的票本轮完全不在该板块名单里 → 正常重选，不抛。"""
    d = ip.diagnose_dragon([S("600002", "别的票", "2天2板", 10.0, et=36000)], 30,
                           locked={"code": "600001", "name": "已消失"})
    assert d["candidate"]["name"] == "别的票"


def test_no_limit_up_stock_returns_none():
    """板块本轮无封板票 → None（调用方**保留原锁**，不当作换人）。"""
    assert ip.diagnose_dragon([S("600001", "甲", "2天2板", 2.0, up=False)], 30,
                              locked={"code": "600001", "name": "甲"}) is None


def test_switch_off_restores_previous_selection(monkeypatch):
    """⛔ 开关可回退：关掉锁定 → 忽略 locked（回到「逐轮重选」）。"""
    monkeypatch.setattr(ip, "ENABLE_DRAGON_LOCK", False)
    a = S("600001", "原龙头", "3天3板", 10.0, et=33000)
    newcomer = S("300009", "新贵", "5天5板", 20.0, et=34500)
    d = ip.diagnose_dragon([a, newcomer], 30, locked={"code": "600001", "name": "原龙头"})
    assert d["candidate"]["name"] == "新贵"
    assert d["lock_held"] is False


# ── ④ 锁条目的更新规则 ───────────────────────────────────────
def test_update_lock_keeps_first_confirmed_time_for_same_stock():
    first = ip.update_lock(None, {"code": "600001", "name": "甲", "boards": "2天2板"}, "09:40")
    assert first["at"] == "09:40" and first["board"] == 2
    again = ip.update_lock(first, {"code": "600001", "name": "甲", "boards": "2天2板"}, "11:20")
    assert again["at"] == "09:40", "同一只票 → 保留首次确立时刻（身份延续）"


def test_update_lock_resets_time_when_handover_happens():
    first = ip.update_lock(None, {"code": "600001", "name": "甲", "boards": "2天2板"}, "09:40")
    handover = ip.update_lock(first, {"code": "600002", "name": "乙", "boards": "3天3板"}, "13:30")
    assert handover["code"] == "600002" and handover["at"] == "13:30"


# ── ⑤ 端到端：抖动不再产生「龙头切换」事件 ────────────────────
def _run_day(rounds, use_lock):
    """按 `main` 的顺序跑若干轮，返回**龙头切换事件**文案（走真实的 `memory_lib.detect_events`）。"""
    locks, mem, out = {}, ml.empty_mem("2026-09-17"), []
    for stocks in rounds:
        prev = locks.get("题材")
        d = ip.diagnose_dragon(stocks, 30, locked=prev if use_lock else None)
        if not d:
            continue
        c = d["candidate"]
        if use_lock:
            locks["题材"] = ip.update_lock(prev, c, "10:00")
        latest = {"top5": [{"name": "题材", "zt_count": 3,
                            "leader": {"name": c["name"], "code": c["code"]}}],
                  "total_zt": 30}
        out += [e["event"] for e in ml.detect_events(latest, mem=mem) if "龙头切换" in e["event"]]
    return out


A = S("600001", "甲", "2天2板", 10.0, et=34200)
B = S("600002", "乙", "1天1板", 10.0, et=34800)
C = S("300003", "丙", "3天3板", 20.0, et=35400)     # 后来者，板更高


def test_end_to_end_no_spurious_switch_when_newcomer_arrives():
    """后来者（更高板、20cm）入场 → **不应产生任何龙头切换事件**。"""
    rounds = [[A, B], [A, B], [A, B, C], [A, B, C], [A, B, C]]
    assert _run_day(rounds, use_lock=True) == []
    # 对照：无锁时同一个 newcomer 会（按连板数）接任 → 产生 1 次事件
    assert len(_run_day(rounds, use_lock=False)) == 1


def test_end_to_end_retirement_produces_exactly_one_event():
    """原龙头退位 → **恰好 1 次**换人事件，此后不再重复。"""
    rounds = [[A, B], [A, B], [A, B, C],      # 甲在位，丙入场但不换
              [B, C], [B, C], [B, C]]         # 甲退位 → 丙接棒
    events = _run_day(rounds, use_lock=True)
    assert len(events) == 1
    assert "龙头切换：甲 → 丙" in events[0]


# ── ⑥ 锁文件：按日隔离 + 容错 ────────────────────────────────
def test_lock_file_is_per_day_and_tolerant(tmp_path, monkeypatch):
    monkeypatch.setattr(ip, "CACHE_DIR", tmp_path)
    locks = {"题材": {"code": "600001", "name": "甲", "board": 2, "at": "09:40"}}
    assert ip.save_dragon_locks(locks, day="2026-09-17") is True
    # 按日隔离：换一天读不到昨天的锁（次日自动重新确立）
    assert ip.load_dragon_locks(day="2026-09-17") == locks
    assert ip.load_dragon_locks(day="2026-09-18") == {}
    # 坏文件 → 空 dict，不抛
    (tmp_path / "dragon_locks_2026-09-19.json").write_text("{ 坏 json", encoding="utf-8")
    assert ip.load_dragon_locks(day="2026-09-19") == {}
    # 缺文件也安全
    assert ip.load_dragon_locks(day="2000-01-01") == {}


def test_pct_never_influences_the_winner():
    """⛔ **涨幅不得参与结论**：把板块内每只票的当轮涨幅任意互换，龙头必须完全一致。

    这是病根的直接不变量 —— 旧实现把 `pct` 放在末档，一旦高优先级的键并列（`len(boards)`
    同为 4 的 `'4天2板'` 与 `'8天5板'` 就是并列），结论就由涨幅决定 → 每轮抖动。
    """
    import itertools
    a0 = S("600001", "甲", "3天3板", 10.0, et=34200)
    b0 = S("600002", "乙", "2天2板", 10.0, et=34800)
    c0 = S("300003", "丙", "3天3板", 20.0, et=34500)
    names = set()
    for pa, pb, pc in itertools.product([3.0, 9.9, 10.0, 10.5, 20.0], repeat=3):
        sec = [{**a0, "pct": pa}, {**b0, "pct": pb}, {**c0, "pct": pc}]
        names.add(ip.diagnose_dragon(sec, 30)["candidate"]["name"])
    assert names == {"甲"}, f"涨幅影响了龙头结论：{sorted(names)}"


def test_seal_time_is_in_the_key_otherwise_ties_go_arbitrary():
    """⛔ 首封时刻必须在键里：否则同板数只能靠 code 排序定龙 —— 那不是「领涨性」。"""
    early = S("900001", "早封", "3天3板", 10.0, et=33000)   # code 更大（排序不利）
    late = S("100002", "晚封", "3天3板", 10.0, et=36000)
    assert ip.diagnose_dragon([early, late], 30)["candidate"]["name"] == "早封"


def test_pct_never_influences_the_winner():
    """⛔ **涨幅不得参与结论**：把板块内每只票的当轮涨幅任意互换，龙头必须完全一致。

    这是病根的直接不变量 —— 旧实现把 `pct` 放在末档，一旦高优先级的键并列（`len(boards)`
    同为 4 的 `'4天2板'` 与 `'8天5板'` 就是并列），结论就由涨幅决定 → 每轮抖动。
    """
    import itertools
    a0 = S("600001", "甲", "3天3板", 10.0, et=34200)
    b0 = S("600002", "乙", "2天2板", 10.0, et=34800)
    c0 = S("300003", "丙", "3天3板", 20.0, et=34500)
    names = set()
    for pa, pb, pc in itertools.product([3.0, 9.9, 10.0, 10.5, 20.0], repeat=3):
        sec = [{**a0, "pct": pa}, {**b0, "pct": pb}, {**c0, "pct": pc}]
        names.add(ip.diagnose_dragon(sec, 30)["candidate"]["name"])
    assert names == {"甲"}, f"涨幅影响了龙头结论：{sorted(names)}"


def test_seal_time_is_in_the_key_otherwise_ties_go_arbitrary():
    """⛔ 首封时刻必须在键里：否则同板数只能靠 code 排序定龙 —— 那不是「领涨性」。"""
    early = S("900001", "早封", "3天3板", 10.0, et=33000)   # code 更大（排序不利）
    late = S("100002", "晚封", "3天3板", 10.0, et=36000)
    assert ip.diagnose_dragon([early, late], 30)["candidate"]["name"] == "早封"
