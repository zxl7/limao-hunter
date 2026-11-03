"""地位与周期证据：只读历史，纯计算；不写交易结论，不改竞价预期。

所有事件均以完整收盘池确认；缺日/盘中快照不能推断断板。
角色只有龙头/准龙头/跟风；未知保留空，不伪装跟风。
"""
from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

from core import dragon_confirm as _dc      # 龙头资格认定（2026-10-21 P2·纯计算）

ENABLE_STATUS_PRIORITY = True
# 🐉 龙头资格认定（2026-10-21 P2）：把五维确认与两把尺合成结论写进 `dragon_confirm`。
# ⛔ False 时逐字回到旧行为（`annotate` 不产出该字段）。
ENABLE_DRAGON_CONFIRM = True
ROLE_ORDER = {"龙头": 3, "准龙头": 2, "跟风": 1}
HIGH_BOARD_MIN = 5
HISTORY_DAYS = 30


def number(value, default=0.0):
    try:
        n = float(value)
        return n if math.isfinite(n) else default
    except (ValueError, TypeError):
        return default


def code_of(row):
    return str(row.get("code") or row.get("dm") or "")


def boards_of(row):
    return int(number(row.get("boards", row.get("lbc"))))


def role_of(row):
    role = row.get("leader_role")
    if role in ROLE_ORDER:
        return role
    identity = str(((row.get("expectation") or {}).get("dragon_diagnosis") or {}).get("identity") or "")
    if identity.startswith("真龙头") or identity == "龙头":
        return "龙头"
    if identity == "准龙头":
        return "准龙头"
    if identity.startswith("跟风"):
        return "跟风"
    return None


def status_key(row):
    """高地位在前；带动→连续性→竞争身位；板数不作为跨角色排序。"""
    if role_of(row) not in ROLE_ORDER:
        return (0, 0, 0, 0)
    ev = row.get("leader_evidence") or {}
    return (ROLE_ORDER.get(role_of(row), 0),
            number(row.get("carry_cnt")), number(ev.get("continuous_days")),
            -number(row.get("line_rank", row.get("relay_rank")), 999))


def _family(row):
    if "preselect_theme" in row:
        return row["preselect_theme"]
    from core.pool_call import line_family
    return line_family(row)


def _complete(snapshot, day):
    if not isinstance(snapshot, dict) or snapshot.get("date") != day:
        return False
    rows = snapshot.get("zt_list")
    stamp = str(snapshot.get("generated_ts") or snapshot.get("generated_at_bj") or "")
    return (isinstance(rows, list) and bool(rows)
            and stamp[:10] == day and len(stamp) >= 16
            and stamp[11:16] >= "15:00"
            and int(number(snapshot.get("zt_count"), -1)) == len(rows))


def load_history(root, asof, *, before_only=False):
    """截止依据日读取；不补抓、不落盘。空壳仍保留为断链标记。"""
    root = Path(root)
    days = []
    for path in sorted((root / "cache/zt_history").glob("zt_*.json")):
        day = path.stem[3:]
        if len(day) != 10 or day > asof or (before_only and day == asof):
            continue
        try:
            snapshot = json.loads(path.read_text())
        except (ValueError, OSError):
            snapshot = {}
        rows = snapshot.get("zt_list") or []
        candidates = {}
        try:
            cand = json.loads((root / "wealth_code/cache" / f"candidates-{day}.json").read_text())
            stamp = str(cand.get("generated_at_bj") or "")
            if cand.get("date") == day and (not before_only or (stamp[:10] == day and stamp[11:16] >= "15:00")):
                candidates = {code_of(r): r for k in ("relay", "watch") for r in cand.get(k, [])}
                if before_only:
                    candidates = {c: {k: r[k] for k in ("core_theme", "leader_role", "carry_cnt") if k in r}
                                  for c, r in candidates.items()}
        except (ValueError, OSError):
            pass
        days.append({"date": day, "complete": _complete(snapshot, day),
                     "source_ts": snapshot.get("generated_ts") or snapshot.get("generated_at_bj"),
                     "rows": [{**r, **candidates.get(code_of(r), {})} for r in rows if isinstance(r, dict)]})
    return days[-HISTORY_DAYS:]


def _is_trading(d: date) -> bool:
    """A股交易日 —— **唯一真源 = common.trading_calendar**（2026-10-09 唯一化）。

    ⛔ `_adjacent` 曾用 `weekday() < 5` 判中间日，把「工作日休市」（如中秋 2026-09-25）
       错当交易日 → 隔着假期的两次涨停会被误判成「不连续」。
    """
    try:
        import sys
        from pathlib import Path as _Path
        _root = str(_Path(__file__).resolve().parents[2])
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from common.trading_calendar import is_a_share_trading_day
        return is_a_share_trading_day(d)
    except Exception:  # pragma: no cover - 极端环境兜底
        return d.weekday() < 5  # trading-day-fallback: 唯一真源不可用时的周末兜底


def _adjacent(a, b):
    """`a` 与 `b` 是否**紧邻**（两者之间不含任何交易日）。

    不把缺失的交易日当休市。假期无法确认时宁可暂停推断。
    """
    d, end = date.fromisoformat(a) + timedelta(days=1), date.fromisoformat(b)
    while d < end:
        if _is_trading(d):
            return False
        d += timedelta(days=1)
    return d == end


def analyse(history, asof):
    """跨日事件留痕；包含全部出生候选和失败项，不只保存幸存者。"""
    days = sorted([d for d in history if d["date"] <= asof], key=lambda d: d["date"])
    events, previous = [], None
    for day in days:
        if not day.get("complete"):
            for event in events:
                if event["status"] == "tracking":
                    event["status"] = "data_gap"
                    event["invalid_reason"] = "收盘数据不完整，停止连续性推断"
            previous = None
            continue
        current = {code_of(r): r for r in day["rows"]}
        if previous and _adjacent(previous["date"], day["date"]):
            for event in events:
                if event["status"] == "tracking":
                    r = current.get(event["code"])
                    if r is None:
                        event["status"] = "not_limit_up"
                        event["invalid_reason"] = "后续完整收盘池未再涨停；不等同于跌停或亏损"
                    else:
                        event["progress"].append({"date": day["date"], "boards": boards_of(r)})
                    event["last_checked"] = day["date"]
            for old in previous["rows"]:
                if boards_of(old) < HIGH_BOARD_MIN or role_of(old) != "龙头" or code_of(old) in current:
                    continue
                # 旧身份来自断板前档案，不能因后来出现赢家才回填老龙。
                for r in current.values():
                    old_fam, new_fam = _family(old), _family(r)
                    known = old_fam not in ("—", "其他", "") and new_fam not in ("—", "其他", "")
                    events.append({"old_code": code_of(old), "old_name": old.get("name"),
                                   "old_boards": boards_of(old), "break_date": day["date"],
                                   "code": code_of(r), "name": r.get("name"),
                                   "birth_boards": boards_of(r),
                                   "kind": "first_board" if boards_of(r) == 1 else "promotion",
                                   "relation": ("same_theme" if old_fam == new_fam else "other_theme") if known else "unknown",
                                   "fbt": r.get("fbt"), "status": "tracking", "last_checked": day["date"],
                                   "progress": [{"date": day["date"], "boards": boards_of(r)}]})
        elif previous or events:
            for event in events:
                if event["status"] == "tracking":
                    event["status"] = "data_gap"
                    event["invalid_reason"] = "跨日数据不完整，停止连续性推断"
        previous = day
    return events


def _known_family(row):
    family = _family(row)
    return family if family not in ("—", "其他", "", None) else None


def _tail(history, asof):
    """仅使用截止日连续且完整的收盘记录，不跨缺日拼证据。"""
    out = []
    for d in reversed(sorted((d for d in history if d["date"] <= asof), key=lambda d: d["date"])):
        if not d.get("complete") or (not out and d["date"] != asof):
            break
        if out and not _adjacent(d["date"], out[-1]["date"]):
            break
        out.append(d)
    return list(reversed(out))


def load_catalysts(root, asof):
    """只读已归档新闻；不把缺少新闻当成没有催化。"""
    result = []
    paths = sorted((Path(root) / "cache/newsflash_history").glob("daily_*.json"))
    for path in [p for p in paths if p.stem[6:] <= asof][-HISTORY_DAYS:]:
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict) or data.get("date") != path.stem[6:]:
                continue
            for item in data.get("newsflash", []):
                if isinstance(item, dict):
                    result.append({**item, "date": data["date"], "archive": str(path)})
        except (ValueError, OSError):
            continue
    return result


def five_dimensions(row, history, asof, catalysts=()):
    """五维可审计证据。涨停家数仅代理板块强弱，新闻出现不等于利好兑现。"""
    days = _tail(history, asof)
    code, family = code_of(row), _known_family(row)
    unknown = {"status": "unknown"}
    out = {k: dict(unknown) for k in ("competition", "sector_follow", "divergence", "launch", "catalyst")}
    if not days:
        return out
    today = {code_of(r): r for r in days[-1]["rows"]}
    if len(days) >= 2:
        cohort = [r for r in days[-2]["rows"] if boards_of(r) == 2]
        winners = [code_of(r) for r in cohort if boards_of(today.get(code_of(r), {})) == 3]
        peers = [code_of(r) for r in cohort if family and _known_family(r) == family]
        same_winners = [c for c in winners if c in peers]
        status = "not_in_cohort"
        if code in [code_of(r) for r in cohort]:
            status = ("market_only" if len(winners) == 1 and len(cohort) > 1 else
                      "theme_winner" if code in same_winners and len(same_winners) == 1 and len(peers) > 1 else
                      "promoted" if code in winners else "not_promoted")
        out["competition"] = {"status": status, "cohort": [code_of(r) for r in cohort],
                              "winners": winners, "same_theme_cohort": peers}
    if family:
        series = []
        for d in days:
            peers = [r for r in d["rows"] if _known_family(r) == family and code_of(r) != code]
            own = next((r for r in d["rows"] if code_of(r) == code), None)
            series.append({"date": d["date"], "others": len(peers),
                           "first": sum(boards_of(r) == 1 for r in peers),
                           "second": sum(boards_of(r) == 2 for r in peers),
                           "self_limit_up": own is not None,
                           "carry": number((own or {}).get("carry_cnt")),
                           "codes": [code_of(r) for r in peers]})
        recent = series[-3:]
        follow = ("sustained" if len(recent) >= 2 and all(d["others"] > 0 for d in recent)
                  and recent[-1]["others"] >= recent[-2]["others"] else
                  "alone" if recent[-1]["others"] == 0 else "present")
        out["sector_follow"] = {"status": follow, "series": series,
                                "basis": "完整涨停池同方向其他股票，非板块全体涨跌"}
        episodes = []
        for i in range(1, len(series)):
            prev, weak = series[i-1], series[i]
            if weak["others"] < prev["others"] and weak["self_limit_up"]:
                recover = next((s for s in series[i+1:] if s["others"] > weak["others"]
                                and s["self_limit_up"] and s["carry"] > 0), None)
                # 自身中途未再涨停不能把后来的回封说成持续抗分歧。
                if recover and not all(s["self_limit_up"] for s in series[i:series.index(recover)+1]):
                    recover = None
                episodes.append({"weak_date": weak["date"], "recover_date": recover["date"] if recover else None})
        out["divergence"] = {"status": "recovery_with_carry" if any(e["recover_date"] for e in episodes)
                             else "held_during_contraction" if episodes else "not_observed",
                             "episodes": episodes, "proxy": True}
        # 启动身份在本轮首次涨停附近判断，不把今日板块热度倒灌成启动背景。
        start = len(series)-1
        while start > 0 and series[start-1]["self_limit_up"]:
            start -= 1
        before = series[max(0, start-3):start]
        if len(before) >= 3:
            peak = max(s["others"] for s in before)
            if peak == 0:
                launch = "new_direction_candidate"
            elif peak >= 3 and series[start]["others"] < peak:
                launch = "late_in_existing_direction"
            else:
                launch = "existing_direction"
            out["launch"] = {"status": launch, "start_date": series[start]["date"],
                             "prior_peak": peak, "proxy": True}
        matched, seen = [], set()
        for item in catalysts:
            if not (days[0]["date"] <= str(item.get("date", "")) <= asof):
                continue
            # 只认明确题材标签或点名，禁止用宽泛正文关键词猜产业关联。
            plates = item.get("plates") or []
            names = item.get("stocks") or []
            hit = row.get("name") in names or code in names or any(
                isinstance(p, str) and _known_family({"theme_name": p, "sector": p}) == family for p in plates)
            title = str(item.get("title") or "").strip()
            if hit and title and title not in seen:
                seen.add(title)
                matched.append({k: item.get(k) for k in ("date", "title", "archive", "source", "url")})
        # 行情播报不是外部催化；没有结构化类型、时间与来源不参加确认。
        hard = [i for i in catalysts if i.get("kind") in ("policy", "order", "industry_event", "technology")
                and i.get("verified") is True and (i.get("url") or i.get("source"))
                and any(m["date"] == i.get("date") and m["title"] == i.get("title") for m in matched)]
        hard_days = sorted({i["date"] for i in hard})
        out["catalyst"] = {"status": "sustained_verified" if len(hard_days) >= 2 else
                           "single_verified" if hard_days else "unverified_news" if matched else "unknown",
                           "verified_days": hard_days, "items": matched,
                           "note": "重复标题去重；行情播报不认硬催化；缺新闻不推断消息已兑现"}
    return out


def annotate(rows, history, asof, catalysts=(), *, continuity_by_boards=False):
    """原位补事实字段，但不改 expectation 数值、预案或准入。返回全量事件。"""
    days = sorted([d for d in history if d["date"] <= asof], key=lambda d: d["date"])
    events = analyse(days, asof)
    for row in rows:
        code = code_of(row)
        continuous, last = 0, None
        for day in reversed(days):
            if not day.get("complete") or (last and not _adjacent(day["date"], last)):
                break
            if continuous == 0 and day["date"] != asof:
                break
            if code not in {code_of(r) for r in day["rows"]}:
                break
            continuous += 1
            last = day["date"]
        b, carry = boards_of(row), number(row.get("carry_cnt"))
        if continuity_by_boards:
            continuous = min(continuous, b)
        original = role_of(row)
        # 三板/五板不能直接封龙；已有三要素结论须有带动与跨日延续印证。
        role = original
        if original == "龙头" and not (carry > 0 and continuous >= 2):
            role = "准龙头"
        elif b == 2 and carry > 0 and continuous >= 2 and original != "龙头":
            role = "准龙头"
        links = [e for e in events if e["code"] == code]
        active = [e for e in links if e["status"] == "tracking" and e["last_checked"] == asof]
        if active and carry > 0 and continuous >= 2 and role != "龙头":
            role = "准龙头"
        notes = []
        if b == 2:
            notes.append("二进三观察，须验证竞争胜出与同方向带动")
        if b >= HIGH_BOARD_MIN:
            notes.append(f"{b}板高位候选；不凭板数认定市场总龙头")
        for e in active:
            relation = {"same_theme": "同方向", "other_theme": "不同方向", "unknown": "题材关系待核实"}[e["relation"]]
            kind = "首板" if e["kind"] == "first_board" else "继续晋级"
            notes.append(f"{e['break_date']} {e.get('old_name') or e['old_code']}断板当日{kind}，{relation}；仍需后续带动确认")
        dimensions = five_dimensions(row, days, asof, catalysts)
        comp = dimensions["competition"]["status"]
        follow = dimensions["sector_follow"]["status"]
        divergence = dimensions["divergence"]["status"]
        launch = dimensions["launch"]["status"]
        catalyst = dimensions["catalyst"]["status"]
        competitive = comp in ("market_only", "theme_winner")
        # 竞争胜出必须同时有带动和连续性，不能仅凭唯一晋级封龙。
        if competitive and carry > 0 and continuous >= 2 and role != "龙头":
            role = "准龙头"
        confirmed = (carry > 0 and continuous >= 3 and follow == "sustained"
                     and divergence == "recovery_with_carry"
                     and launch in ("new_direction_candidate", "existing_direction")
                     and catalyst == "sustained_verified")
        if confirmed and (competitive or original in ("龙头", "准龙头") or active):
            role = "龙头"
        elif role == "龙头":
            role = "准龙头"
        labels = {
            "competition": {"market_only": "昨日二板中仅它晋级三板", "theme_winner": "同方向昨日二板竞争胜出", "promoted": "昨日二板已晋级，仍有竞争者", "not_promoted": "昨日二板未晋级"},
            "sector_follow": {"sustained": "同方向其他涨停票持续跟上", "alone": "同方向没有其他涨停票", "present": "同方向有涨停票，持续性仍待确认"},
            "divergence": {"recovery_with_carry": "同方向涨停减少时仍涨停，之后扩张且有带动记录（涨停数量口径）", "held_during_contraction": "同方向涨停减少时仍涨停，尚未确认带动回升"},
            "launch": {"new_direction_candidate": "启动前三个记录日同方向无其他涨停，新方向待确认", "late_in_existing_direction": "启动前同方向已集中涨停，留意后排补涨", "existing_direction": "在已有活跃方向内启动"},
            "catalyst": {"sustained_verified": "至少两日有已核实的不同催化事件", "single_verified": "仅有单日已核实催化，延续待确认", "unverified_news": "有关联新闻，但不足以确认硬催化延续"},
        }
        for key, mapping in labels.items():
            text = mapping.get(dimensions[key]["status"])
            if text:
                notes.append(text)
        missing = [label for key, label in (("competition", "同期竞争"), ("sector_follow", "板块后续"),
                   ("divergence", "分歧表现"), ("launch", "启动背景"), ("catalyst", "催化"))
                   if dimensions[key]["status"] == "unknown"]
        if missing:
            notes.append("待补证据：" + "、".join(missing))
        row["leader_role"] = role
        prev = days[-2] if len(days) >= 2 and days[-1]["date"] == asof else None
        today = days[-1] if days and days[-1]["date"] == asof else None
        cohort = [r for r in prev["rows"] if boards_of(r) == 2] if (prev and today and prev["complete"] and today["complete"] and _adjacent(prev["date"], asof)) else []
        today_map = {code_of(r): r for r in today["rows"]} if today else {}
        competitors = [{"code": code_of(r), "same_theme": _family(r) == _family(row),
                        "promoted": boards_of(today_map.get(code_of(r), {})) == 3} for r in cohort]
        row["leader_evidence"] = {"asof": asof, "continuous_days": continuous,
                                  "previous_two_board_cohort": competitors,
                                  "two_to_three": b == 2, "high_candidate": b >= HIGH_BOARD_MIN,
                                  "succession": links, "dimensions": dimensions,
                                  "confirmation": "confirmed" if confirmed else "pending",
                                  "note": "；".join(notes)}
        # 🐉 龙头资格认定（2026-10-21 P2）：把五维确认与 92科比 两把尺（时间越走越早 /
        #   爆量检验）合成一个结论 + 逐项证据 + 一句人话。
        #   ⛔ 只写 `dragon_confirm` 字段与叙述，**不改上面的 role 判定**——
        #      用户 2026-09-24 已拍板「地位优先 + 五维确认」是资格唯一判据，
        #      两把尺只作叙述层（详情见 dragon_confirm 模块注释）。
        if ENABLE_DRAGON_CONFIRM:
            try:
                _dc_res = _dc.confirm(row, _dc.series_of(days, code, asof))
                row["dragon_confirm"] = _dc_res
                # ⛔ 只升不降（2026-10-21）：认定结论为「龙头」但上面 role 仍是准龙头时，
                #    必须同步升级——否则会出现「卡面写龙头、排序按准龙头」的双标，
                #    读者和系统各说一套（海洋王 10-20 实测即此：五维只差催化，认定龙头，
                #    但 role 停在准龙头）。
                #    反向**绝不降级**：认定说准龙头而 role 是龙头时保持原样，
                #    因为「五维已确认过就受用户 2026-09-24 拍板保护」。
                if _dc_res.get("identity_role") == "龙头" and role != "龙头":
                    row["leader_role"] = "龙头"
            except Exception:
                row["dragon_confirm"] = {"enabled": False, "level": "", "label": "",
                                         "checks": [], "narrative": ""}
    return events


ENABLE_PRESELECT_STATUS = True


def build_preselect_context(scored, pool, history, *, asof, cutoff, themes):
    """显式冻结输入的纯比较；历史题材只认当时记录，不反填今日题材。"""
    from core.expectation_ai import dragon_three_diagnosis
    if (not pool or cutoff[:10] != asof or cutoff[11:16] < "15:00"
            or not any(v.get("theme") not in (None, "", "—", "其他") for v in themes.values())):
        return None
    themes = {c: v for c, v in themes.items()
              if not v.get("source_ts") or (str(v["source_ts"])[:10] == asof and str(v["source_ts"]) <= cutoff)}
    if not themes:
        return None
    rows = deepcopy(scored)
    today = deepcopy(pool)
    codes = {code_of(r) for r in today}
    if not rows or any(code_of(r) not in codes for r in rows):
        return None

    def family(r):
        theme = str(r.get("core_theme") or "").strip()
        # 线族用于检索，精确题材才允许合并；例如创新药与中药不因同属医药强并。
        return theme if theme not in ("", "—", "其他") else None

    clean, seen, quality = [], set(), []
    for day in sorted(history, key=lambda d: d["date"]):
        if day["date"] >= asof:
            continue
        d = deepcopy(day)
        stamp = str(d.get("source_ts") or "")
        valid_time = stamp[:10] == d["date"] and "15:00" <= stamp[11:16] and stamp <= cutoff
        # 不以候选补充字段掩盖原始行情重复；两份相同池均不用于连续性。
        signature = json.dumps(sorted((code_of(r), boards_of(r), str(r.get("fbt") or ""),
                                       number(r.get("cje", r.get("cje_yi"))), number(r.get("amount")))
                                      for r in d["rows"]), ensure_ascii=False)
        duplicate = signature in seen
        if duplicate:
            for prior in clean:
                if prior.get("signature") == signature:
                    prior["complete"] = False
        seen.add(signature)
        d["signature"] = signature
        d["complete"] = bool(d.get("complete") and valid_time and not duplicate)
        if not d["complete"]:
            quality.append({"date": d["date"], "reason": "重复或不完整/来源时间无效"})
        if duplicate:
            quality.extend({"date": p["date"], "reason": "与后日快照重复，原日一并降级"}
                           for p in clean if p.get("signature") == signature)
        for r in d["rows"]:
            r["core_theme"] = r.get("core_theme") or ""
            r["preselect_theme"] = family(r) or ""
            # 不让宽行业兜底参与关系与连续带动确认。
            for key in ("theme_name", "sector", "theme", "hy"):
                r.pop(key, None)
        clean.append(d)
    for r in today + rows:
        r["core_theme"] = str(themes.get(code_of(r), {}).get("theme") or "")
        if not family(r):
            r["carry_cnt"] = 0
        for key in ("theme_name", "sector", "theme", "hy"):
            r.pop(key, None)
    from core.sector_support import annotate_carry
    # 仅在冻结的精确题材桶中重新算带动，禁止继承宽行业兜底带动。
    for r in today + rows:
        r["preselect_theme"] = family(r) or ""
    for fam in {family(r) for r in rows} - {None}:
        annotate_carry([r for r in rows if family(r) == fam],
                       pool=[r for r in today if family(r) == fam])
    current = {code_of(r): r for r in rows}
    today = [{**r, **current.get(code_of(r), {})} for r in today]
    clean.append({"date": asof, "complete": True, "rows": today})
    for r in rows:
        r.pop("leader_role", None)
        r.pop("leader_evidence", None)
        r["expectation"] = {"dragon_diagnosis": dragon_three_diagnosis(r, use_relay_rank=False)}
    events = annotate(rows, clean, asof, continuity_by_boards=True)
    facts = {}
    for d in clean[:-1]:
        if not d["complete"]:
            continue
        for r in d["rows"]:
            facts.setdefault(code_of(r), []).append({"date": d["date"], "source_ts": d["source_ts"],
                "boards": boards_of(r), "role": role_of(r), "family": family(r)})
    entries = {}
    for r in rows:
        code, fam = code_of(r), family(r)
        own = facts.get(code, [])
        matching = [f for f in own if fam and f["family"] == fam]
        peers = [{"code": c, "facts": fs} for c, fs in facts.items() if c != code
                 and fam and any(f["family"] == fam for f in fs)]
        historical = max((f["boards"] for f in matching), default=0)
        own_days = {f["date"] for f in matching}
        linked_days = sorted({f["date"] for p in peers for f in p["facts"]
                              if f["family"] == fam and f["date"] in own_days and f["boards"] >= HIGH_BOARD_MIN})
        line_rank = 1 + sum(family(p) == fam and boards_of(p) > boards_of(r) for p in today) if fam else 0
        ev = r["leader_evidence"]
        # 历史只影响同角色辨识度，不恢复历史角色，不重复加 raw 分。
        key = (ROLE_ORDER.get(role_of(r), 0), number(r.get("carry_cnt")),
               number(ev["continuous_days"]), historical, len(linked_days),
               int(ev["dimensions"]["competition"]["status"] in ("market_only", "theme_winner")),
               -line_rank if line_rank else -999)
        entries[code] = {"key": key, "leader_role": r["leader_role"], "leader_evidence": ev,
                         "core_theme": r["core_theme"], "line_family": _family({"core_theme": r["core_theme"]}),
                         "history": own, "same_line": peers,
                         "current_peers": [{"code": code_of(p), "boards": boards_of(p)} for p in today
                                           if fam and family(p) == fam and code_of(p) != code],
                         "relation_window": [{"date": d["date"], "complete": d["complete"],
                                              "self_present": any(code_of(p) == code for p in d["rows"]),
                                              "peer_codes": [code_of(p) for p in d["rows"]
                                                             if fam and family(p) == fam and code_of(p) != code]}
                                             for d in clean],
                         "historical_boards": historical, "linked_days": linked_days,
                         "line_rank": line_rank, "relation_note": "同日同题材在板仅是联动代理，不证明因果带动",
                         "theme_source": {"source": themes.get(code, {}).get("source", "冻结题材输入"),
                                          "observed_at": cutoff, "source_ts": themes.get(code, {}).get("source_ts")},
                         "unknown": ([] if fam else ["可靠题材缺失"]) + ([] if own else ["有效历史缺失"])
                                    + ([] if themes.get(code, {}).get("source_ts") else ["题材源时间未提供；观察时间非源时间"])}
    return {"asof": asof, "cutoff": cutoff, "entries": entries, "quality": quality, "events": events}


def enrich_payload(payload, root):
    """仅挂接盘后已有产物；不运行抓取、不另设生产文件。"""
    asof = str(payload.get("date") or "")
    if len(asof) != 10:
        return payload
    rows = [r for k in ("relay", "watch") for r in payload.get(k, [])]
    history = load_history(root, asof)
    events = annotate(rows, history, asof, load_catalysts(root, asof))
    payload.setdefault("diagnostics", {})["leader_cycle"] = {
        "asof": asof, "events": events,
        "complete_days": sum(bool(d["complete"]) for d in history),
        "latest_complete": bool(history and history[-1]["date"] == asof and history[-1]["complete"])}
    if ENABLE_STATUS_PRIORITY:
        for rank, row in enumerate(sorted(rows, key=status_key, reverse=True), 1):
            row["status_rank"] = rank
        for bucket in ("relay", "watch"):
            payload.get(bucket, []).sort(key=status_key, reverse=True)
    return payload
