#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日市场快照 · **数据源单一聚合层**（2026-11-04 用户定架构）。

## 用户原话

> 「我其实对于能看到多少并不重要，重要的是更提升准确性。但是我们现在数据分散，
>  我觉得是整体架构的问题。我们能不能改善一下架构，让数据能输出到一个地方去？
>  这样的话，虽然各个地方引用时都是从这里拿数据，但整体数据源是在一个地方的。
>  当我们进行综合判断时，就能对数据源一目了然。
>  数据的展现是可以分散的，但数据源一定是聚合的。」

## 要解决的真问题（实证）

不是「文件多」本身，而是**同一事实多处各存一份，且互相不知道对方存在**：

1. 11-04 实测：大晟文化一只票散在 **12 个产物文件**里，
   想回答「它今天怎么样」要读 12 处。
2. 11-04 实证的严重后果：传媒线风向标在 `load_line_cores` 里按「申万行业」算，
   候选在 `pipeline_eod` 里按「炒作题材」算 —— **同一件事两套口径**，
   两套键对不上 → 锚点永不生效 → 整条最强线被自己的风向标误杀。
3. 同一只票的「封单」字段在三处叫三个名字（`seal` / `seal_amt` / `seal_ratio_pct`）。

⛔ 本质：**没有单一事实源，就没有一致性**；没有一致性，判得越多错得越多。

## 设计原则（用户「展现可以分散，源必须聚合」）

| 原则 | 落地 |
|---|---|
| **一日报一个聚合文件** | `cache/snapshot/<date>.json`，全部维度分 section 落在同一文件 |
| **分区写入，互不覆盖** | 每条链路只 `put_section("自己的名字", ...)`，别人的 section 原样保留 |
| **只放结论层，不放原始层** | 每维只存「已算好的判据结论」，原始明细留在原地（2.7MB 不进快照） |
| **单票聚合视图** | `stocks[code]` 把散在各处的该票结论合成一条，一眼看全 |
| **可追溯** | 每个 section 带 `source` + `as_of` + `version`，能回答「源数据来自何时、何时搬运、由谁写入」 |
| **诚实降级** | 缺就是缺（`None`/不出现），⛔ 绝不填默认值冒充有数据 |
| **读写分离（CQRS）** | 写/判定侧各链路自治（权威＝原始产物）；读/展现侧统一从快照拿。⛔ **判定链不读快照** —— 否则「自己算完再读自己」＝循环依赖＋自我印证 |
| **Adapter 渐进迁移** | `read_with_fallback()` 缺档回退原文件、行为逐字不变，一个消费者一个接，不用一刀切 |
| **守门留痕** | `write_health()` 把一致性校验结论落 `health` section，打开快照即知今日数据健不健康 |

## 设计模式：灵活搭配，不套单一范式（2026-11-04 用户「灵活搭配」）

- **Repository**（`put_section`/`read_section`）：聚合文件是唯一读侧仓库，分区 upsert 保证多写者互不覆盖。
- **CQRS**（写原始产物 / 读聚合快照）：判定与展现解耦，⛔ 不把判定输入换成自己的输出。
- **Adapter**（`read_with_fallback`）：旧消费者零成本接入，缺档回退。
- **依赖注入保持**：`build_cards` 是纯渲染函数，只读传入的内存对象；⛔ 不改成自己去读文件
  （那样反而把展现层绑死到存储层）。单一源由 pipeline 在组装时注入。
- **Observer 式守门**（`write_health` + capture_daily 第 14 步）：每日自动校验并留痕，异常只告警不阻断。

## ⛔ 边界（不改现有判定）

本模块**只做聚合与分发，不做裁决**：
- 不重算任何阈值/分数/闸门结论，只搬运已算好的结果。
- 不删除任何既有产物文件（原始层继续存在，只是不再被到处直读）。
- 下游迁移是渐进的：未迁移的消费者照旧读原文件，行为逐字不变。
- **判定链（`pool_call.call_pool` / `pipeline_eod` / `pipeline_verify`）永不读快照**：
  它们写原始产物、也从原始产物读，快照只是它们的**输出副本**。⛔ 反向读会形成循环依赖。
- 开关 `ENABLE_MARKET_SNAPSHOT=0` → `put_section` 变 no-op，全系统回到旧行为；
  `ENABLE_SNAPSHOT_GUARD=0` → 关闭日常守门（不影响写入）。
"""
from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent          # wealth_code/
HUNTER_ROOT = ROOT.parent                              # 狸猫猎手/
if str(HUNTER_ROOT) not in sys.path:
    sys.path.insert(0, str(HUNTER_ROOT))

SCHEMA = "market_snapshot_v1"
ENABLED_ENV = "ENABLE_MARKET_SNAPSHOT"

# 各 section 的**权威写入者**（⛔ 一个 section 只允许一个写入者，避免又出现两套口径）
OWNERS: dict[str, str] = {
    "market": "feedback_matrix / market_memory",   # 情绪·广度·涨停数
    "themes": "theme_rotation / theme_snapshot",   # 题材轮动·主线
    "ladder": "zt_history / board_track",          # 连板梯队·高度
    "lines": "pool_call.load_line_cores",          # 每条线的风向标（唯一源）
    "candidates": "pipeline_eod",                  # 候选池结论层
    "verdict": "pool_call",                        # 09:25 竞价终裁
    "quotes": "pipeline_verify",                   # 09:25 竞价报价
    "gates": "pipeline_eod",                       # 各闸拦截汇总
}

# ── 维度状态台账（2026-11-04 系统关联审计 · 只标注，不改任何判定）──────────
# 用户「数据源一定要聚合……综合判断时对数据源一目了然」：
#   「一目了然」必须包含「哪些维度已经死了 / 算了却没人用」，
#   否则一张看似满格的快照会把停更 2 月的数据当成今天在产 —— 比缺数据更危险。
# ⛔ 只标注：不删脚本、不改写入、不改 cadence 之外的任何判定；恢复把 status 改回 live 即可。
# ⛔ 状态是**审计快照**，不是运行时探测；改产物/接线后须同步本表（否则标注本身会骗人）。
DIMENSION_STATUS: dict[str, dict[str, str]] = {
    # 进聚合快照的 8 个业务 section
    "market":         {"status": "live",   "note": "情绪/广度 · feedback_matrix + market_memory"},
    "themes":         {"status": "live",   "note": "题材轮动 · theme_rotation + theme_snapshot"},
    "ladder":         {"status": "live",   "note": "连板梯队 · zt_history"},
    "lines":          {"status": "live",   "note": "线风向标 · pool_call.load_line_cores（唯一源）"},
    "candidates":     {"status": "live",   "note": "候选结论层 · pipeline_eod"},
    "verdict":        {"status": "live",   "note": "09:25 终裁 · pool_call"},
    "quotes":         {"status": "live",   "note": "竞价报价 · pipeline_verify"},
    "gates":          {"status": "live",   "note": "各闸拦截 · pipeline_eod.diagnostics"},
    # 审计出的死维度（停更）
    "lhb":            {"status": "dead",   "note": "龙虎榜三套产物分裂：lhb_*.json 停 09-16 / lhb_card_* 停 09-29 / hithink_lhb_* 停 10-19；tasks.yaml 无日常登记"},
    "board_core":     {"status": "dead",   "note": "板块核心票 全部停在 2026-09-09 15:46"},
    # 活但白算（产物在更新，决策链零引用）
    "effect_paths":   {"status": "unused", "note": "每日 08:37 仍在产（11-04 13:22 更新），quant_score/eod/pool_call/build_cards 零引用"},
    "ma5_status":     {"status": "unused", "note": "盘中刷新，决策链零引用"},
    "theme_rotation": {"status": "unused", "note": "盘中刷新；仅进快照 themes section 作展示，⛔ 不进判定（与上面 'themes' section 不是一回事）"},
    "vol_ratio":      {"status": "unused", "note": "vol_ma5 只做诊断，⛔ 不进任何闸（仅诊断，不作为主判据）"},
}

META_SECTIONS = {"health"}          # 非业务 section：不进 coverage 的 8/8 分母
GUARD_ENV = "ENABLE_SNAPSHOT_GUARD"  # 日常一致性守门总开关（capture_daily 第 14 步）


def enabled() -> bool:
    return os.environ.get(ENABLED_ENV, "1") not in ("0", "false", "False", "")


def guard_enabled() -> bool:
    """日常守门（补全 section + validate + 落 health）是否开启，默认开。"""
    return os.environ.get(GUARD_ENV, "1") not in ("0", "false", "False", "")


def snapshot_path(date10: str, root: Path | None = None) -> Path:
    d = (Path(root) if root else HUNTER_ROOT) / "cache" / "snapshot"
    return d / f"{date10}.json"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_all(date10: str, root: Path | None = None) -> dict[str, Any]:
    """读当日聚合文件；缺失/损坏 → 空骨架（⛔ 不抛错，聚合失败不能阻断主链）。"""
    p = snapshot_path(date10, root)
    try:
        d = json.loads(p.read_text(encoding="utf-8")) or {}
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}


def _atomic_write(path: Path, data: dict[str, Any]) -> bool:
    """原子写：临时文件 + replace（⛔ 不留半截文件，下游不会读到截断的 JSON）。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=str(path.parent),
                prefix=".tmp_", suffix=".json", delete=False) as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
            tmp = Path(fh.name)
        tmp.replace(path)
        return True
    except Exception:
        return False


@contextmanager
def _write_lock(date10: str, root: Path | None = None):
    # 锁文件不能随 JSON replace 更换 inode，也不能在释放后删除。
    path = snapshot_path(date10, root).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _set_section(data: dict[str, Any], date10: str, section: str, payload: Any, *,
                 source: str | None = None, as_of: str | None = None,
                 ingested_at: str | None = None, source_date: str | None = None):
    now = _now()
    data.setdefault("schema", SCHEMA)
    data.setdefault("date", date10)
    if not isinstance(data.get("sections"), dict):
        data["sections"] = {}
    data["sections"][section] = {
        # 未提供源时刻时保持未知，不能把历史搬运时刻当成源时刻。
        "as_of": as_of,
        "ingested_at": ingested_at if ingested_at is not None else now,
        "source_date": source_date if source_date is not None else (
            str(as_of)[:10] if as_of else date10),
        "source": source or OWNERS.get(section, ""),
        "owner": OWNERS.get(section, ""),
        "payload": payload,
    }
    if section not in META_SECTIONS:
        data["revision"] = data.get("revision", 0) + 1
    data["updated_at"] = now


def put_section(date10: str, section: str, payload: Any, *,
                source: str | None = None, as_of: str | None = None,
                ingested_at: str | None = None, source_date: str | None = None,
                root: Path | None = None) -> bool:
    """写入（或覆盖）**一个** section，其它 section 原样保留。

    ⛔ 语义是 upsert-section，不是整文件覆盖 —— 这是各链路能并行写同一文件的前提。
    ⛔ 失败一律返回 False 且不抛错：聚合是增强，不能把主链带崩。
    as_of 是源时刻（未知则 None），ingested_at 是搬运时刻；source_date 是源业务日期。
    未指定源日期时依次取 as_of 日期、调用方指定的 date10，不从搬运时刻推导。
    """
    if not enabled():
        return False
    if not date10 or not section:
        return False
    try:
        with _write_lock(date10, root):
            data = _read_all(date10, root)
            _set_section(data, date10, section, payload, source=source, as_of=as_of,
                         ingested_at=ingested_at, source_date=source_date)
            return _atomic_write(snapshot_path(date10, root), data)
    except Exception:
        return False


def read_section(date10: str, section: str, *, root: Path | None = None) -> Any:
    """读一个 section 的 payload；缺失 → None（⛔ 不给默认值冒充有数据）。"""
    d = _read_all(date10, root)
    sec = (d.get("sections") or {}).get(section)
    if not isinstance(sec, dict):
        return None
    return _health_view(d) if section == "health" else sec.get("payload")


def read_meta(date10: str, section: str, *, root: Path | None = None) -> dict[str, Any] | None:
    """读一个 section 的元信息（as_of / source / owner），用于卡面标注数据时点。"""
    d = _read_all(date10, root)
    sec = (d.get("sections") or {}).get(section)
    if not isinstance(sec, dict):
        return None
    return {k: sec.get(k) for k in
            ("as_of", "ingested_at", "source_date", "source", "owner")}


# ─────────────────────────────────────────────────────────────────────────────
# 读侧统一门面（**Adapter / 渐进迁移**）—— 用户「展现可分散，源必须聚合」
# ─────────────────────────────────────────────────────────────────────────────
def read_with_fallback(date10: str, section: str, legacy_loader=None, *,
                       root: Path | None = None) -> tuple[Any, str]:
    """只读消费者统一入口：优先读聚合快照，缺档回退原产物。

    ⛔ 为什么需要它（CQRS 式读写分离，设计模式灵活搭配而非一刀切）：
       · **写侧/判定侧**：各链路继续写、继续读自己的原始产物（权威源），
         ⛔ 判定链绝不读快照 —— 否则「自己算完再读自己」会形成循环依赖与自我印证。
       · **读侧/展现侧**：serve 只读 API、诊断、单票视图统一从这里拿，
         这样「综合判断时对数据源一目了然」才真正落地，而不是只有写入端聚合。
       · **Adapter 回退**：未迁移 / 快照缺档时调 `legacy_loader()` 读原文件，
         行为与旧代码**逐字不变**，所以可以一个消费者一个消费者地渐进接。

    返回 `(payload, source)`，source ∈ {"snapshot", "legacy", "missing"}，
    调用方可据此标注数据时点（⛔ 不把 legacy 数据冒充成快照数据）。
    """
    v = read_section(date10, section, root=root) if enabled() else None
    if v is not None:
        return v, "snapshot"
    if legacy_loader is not None:
        try:
            value = legacy_loader()
            return value, "legacy" if value is not None else "missing"
        except Exception:
            return None, "missing"
    return None, "missing"


def coverage(date10: str, *, root: Path | None = None) -> dict[str, Any]:
    """当日聚合覆盖情况：哪些 section 有、几点写的、谁写的。

    这是「对数据源一目了然」的落点 —— 一眼看出今天哪个维度还没接上。
    """
    return _coverage(date10, _read_all(date10, root))


def _health_view(data: dict[str, Any]) -> dict[str, Any] | None:
    payload = ((data.get("sections") or {}).get("health") or {}).get("payload")
    if not isinstance(payload, dict):
        return None
    stale = payload.get("revision") is None or payload["revision"] != data.get("revision", 0)
    return {**payload, "stale": stale, "ok": None if stale else payload.get("ok")}


def _coverage(date10: str, d: dict[str, Any]) -> dict[str, Any]:
    secs = d.get("sections") or {}
    out: dict[str, Any] = {}
    for name in OWNERS:
        s = secs.get(name)
        if not isinstance(s, dict):
            out[name] = {"present": False, "owner": OWNERS[name]}
        else:
            out[name] = {"present": True, "as_of": s.get("as_of"),
                         "source": s.get("source") or "",
                         "owner": OWNERS[name]}
    # health 是守门结果（非业务数据源），⛔ 不混进「未知 section」告警
    extra = [k for k in secs if k not in OWNERS and k not in META_SECTIONS
             and k not in {"eod_candidates", "eod_gates"}]
    h = _health_view(d)
    dead = sorted(k for k, v in DIMENSION_STATUS.items() if v["status"] == "dead")
    unused = sorted(k for k, v in DIMENSION_STATUS.items() if v["status"] == "unused")
    return {"date": date10, "updated_at": d.get("updated_at"),
            "sections": out, "extra_sections": extra,
            "present": sum(1 for v in out.values() if v["present"]),
            "total": len(OWNERS),
            "health": h if isinstance(h, dict) else None,
            # 「一目了然」必须包含死/白算维度，否则满格快照会把停更数据当今天在产
            "dimensions": {"dead": dead, "unused": unused,
                           "detail": DIMENSION_STATUS}}


# ─────────────────────────────────────────────────────────────────────────────
# 单票聚合视图（用户「对数据源一目了然」的核心）
# ─────────────────────────────────────────────────────────────────────────────
def stock_view(date10: str, code: str, *, root: Path | None = None) -> dict[str, Any] | None:
    """把散在各 section 里**同一只票**的结论合成一条。

    这是本模块最有价值的部分：11-04 实测大晟文化散在 12 个文件，
    这里一次读全，且每块都带来源 section，能回答「这条信息谁给的」。

    返回 None 表示当日快照里没有任何关于该票的信息（⛔ 不编造空壳）。
    """
    d = _read_all(date10, root)
    secs = d.get("sections") or {}
    code = str(code or "").strip()
    if not code:
        return None
    view: dict[str, Any] = {"code": code, "date": date10, "parts": {}}

    # ① 候选结论层（盘后）
    cand = (secs.get("candidates") or {}).get("payload") or {}
    row = ((cand.get("stocks") or {}).get(code)) if isinstance(cand, dict) else None
    if isinstance(row, dict):
        view["parts"]["candidate"] = {"as_of": (secs.get("candidates") or {}).get("as_of"),
                                      **row}

    # ② 那条线的风向标（若该票正是本线风向标，标出来）
    lines = (secs.get("lines") or {}).get("payload") or {}
    if isinstance(lines, dict):
        for fam, info in lines.items():
            if isinstance(info, dict) and str(info.get("code")) == code:
                view["parts"]["beacon_of"] = {"theme": fam, **info}
                break

    # ③ 09:25 终裁
    verd = (secs.get("verdict") or {}).get("payload") or {}
    vrow = ((verd.get("views") or {}).get(code)) if isinstance(verd, dict) else None
    if isinstance(vrow, dict):
        view["parts"]["verdict"] = {"as_of": (secs.get("verdict") or {}).get("as_of"), **vrow}

    # ④ 竞价报价
    q = ((secs.get("quotes") or {}).get("payload") or {}).get(code)
    if isinstance(q, dict):
        view["parts"]["auction"] = q

    if not view["parts"]:
        return None
    view["sources"] = sorted(view["parts"])
    return view


def build_candidate_section(candidates: dict[str, Any]) -> dict[str, Any]:
    """把 `candidates-<D>.json` 压成**结论层**（⛔ 不搬原始明细，119 字段砍到判断所需）。

    只保留回答「这票为什么进/不进、值不值得接」必需的字段；
    打分中间量、指纹、证据链等留在原始文件（要查再回原文件）。
    """
    keep = (
        "name", "boards", "theme", "core_theme", "price", "pct", "cje_yi", "hs", "zbc",
        "fbt", "seal_amt", "lt_mktcap", "seal_ratio_pct", "is_one_word",
        "dragon_rank", "dragon_leader_name", "dragon_leader_boards", "dragon_leader_block",
        "dragon_gate", "next_to_leader", "leader_role", "sector_has_mates",
        "theme_mates_cnt", "ladder_grade", "ladder_health", "ladder_has_complete",
        "watch_only", "frozen", "unfreeze_on", "watch_group", "watch_reason",
        "high_win_gate", "seal_gate", "turnover_floor_used", "score", "bucket",
    )
    out: dict[str, Any] = {"date": candidates.get("date"),
                           "regime": candidates.get("regime"),
                           "market_max_boards": candidates.get("market_max_boards"),
                           "stocks": {}}
    for pool in ("relay", "watch"):
        for r in candidates.get(pool) or []:
            if not isinstance(r, dict):
                continue
            code = str(r.get("code") or "").strip()
            if len(code) != 6:
                continue
            row = {k: r.get(k) for k in keep if k in r}
            row["pool"] = pool
            out["stocks"][code] = row
    out["pool_size"] = {"relay": len(candidates.get("relay") or []),
                        "watch": len(candidates.get("watch") or [])}
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 一致性校验（用户「重要的是更提升准确性」的落点）
# ─────────────────────────────────────────────────────────────────────────────
# 为什么必须做：2026-11-04 传媒线被误杀的根因就是「同一事实两套口径」
#   （风向标按申万行业算、候选按炒作题材算），而当时**没有任何地方会报错**。
#   聚合层把数据放到一处之后，就有了**唯一可以交叉比对的地方** ——
#   这是「数据源聚合」相对「各存一份」的真正收益：不是省 IO，是能发现矛盾。

def validate(date10: str, *, root: Path | None = None) -> dict[str, Any]:
    """交叉校验当日快照内部是否自相矛盾。

    硬矛盾（`issues` → ok=False，⛔ 只报问题不自动改数）：
      ① `boards`/`name` 在 candidates 与 verdict 两处是否一致
      ② 风向标 `beacon` 指针是否断裂 / 线核心与风向标是否同票
      ③ 竞价相关 section 的 `source_date` 是否不是当日（旧格式回退 as_of）
      ④ 09:25 终裁买入的票是否不在候选池（凭空开仓 / 串日串池）
    软提示（`warnings` → 不打 ok=False，需人工确认）：
      ⑤ 候选题材键与线风向标键零交集（11-04 传媒血案的「两套口径」）

    返回 {"ok": bool, "issues": [...], "warnings": [...], "checked": {...}}。
    """
    return _validate(date10, _read_all(date10, root))


def _validate(date10: str, d: dict[str, Any]) -> dict[str, Any]:
    secs = d.get("sections") or {}
    issues: list[dict[str, Any]] = []

    cand = (secs.get("candidates") or {}).get("payload") or {}
    verd = (secs.get("verdict") or {}).get("payload") or {}
    cstocks = cand.get("stocks") if isinstance(cand, dict) else None
    cstocks = cstocks if isinstance(cstocks, dict) else {}
    vviews = verd.get("views") if isinstance(verd, dict) else None
    vviews = vviews if isinstance(vviews, dict) else {}

    checked = {"candidates": len(cstocks), "verdict": len(vviews),
               "lines": len(((secs.get("lines") or {}).get("payload") or {}))}

    # ① 两处事实一致性
    for code, row in cstocks.items():
        v = vviews.get(code)
        if not isinstance(v, dict):
            continue
        for f in ("boards", "name"):
            a, b = (row or {}).get(f), v.get(f)
            if a is not None and b is not None and str(a) != str(b):
                issues.append({
                    "kind": "fact_mismatch", "code": code, "field": f,
                    "candidates": a, "verdict": b,
                    "hint": "同一事实现两处不同值，须以唯一源为准",
                })

    # ② 风向标必须在当日池内（in_pool 与 beacon 存在性一致）
    lines = (secs.get("lines") or {}).get("payload") or {}
    if isinstance(lines, dict):
        for fam, info in lines.items():
            if not isinstance(info, dict):
                continue
            if info.get("in_pool") and not info.get("beacon"):
                issues.append({"kind": "beacon_missing", "theme": fam,
                               "hint": "标了 in_pool=True 却没有 beacon，风向标指针断裂"})
            if info.get("beacon") and info.get("beacon").get("code") != info.get("code"):
                issues.append({"kind": "beacon_pointer", "theme": fam,
                               "line_core": info.get("code"),
                               "beacon": info["beacon"].get("code"),
                               "hint": "线核心与风向标不是同一只，须确认是否有意为之"})

    # ③ 明确源日期优先；旧快照没有 source_date 时兼容 as_of 日期。
    for name in ("quotes", "verdict"):
        meta = (secs.get(name) or {})
        as_of = str(meta.get("as_of") or "")
        source_date = meta.get("source_date")
        if source_date is None:
            source_date = as_of[:10]
        if source_date and source_date != date10:
            issues.append({"kind": "stale_section", "section": name, "as_of": as_of,
                           "source_date": source_date,
                           "hint": f"该 section 的源数据不是 {date10} 当日的"})

    # ④ 终裁买入的票必须来自候选池（⛔ 终裁不能凭空开仓 / 串日串池）
    part = verd.get("participate") if isinstance(verd, dict) else None
    if isinstance(part, list) and part:
        for code in part:
            if str(code) not in cstocks:
                issues.append({
                    "kind": "participate_not_in_candidates", "code": str(code),
                    "hint": "09:25 终裁买入的票不在盘后候选池里，须查是否串日/串池",
                })

    # ⑤ 题材键体系错配（11-04 传媒血案根因：候选按炒作题材、风向标按申万行业）
    #    两边都非空且**零交集**才提示；候选/lines 常跨交易日，部分对不上是正常的，
    #    故只作 warning 不打 ok=False（⛔ 诚实、不误伤、不自动改数）。
    warnings: list[dict[str, Any]] = []
    lines_all = (secs.get("lines") or {}).get("payload") or {}
    if isinstance(lines_all, dict) and lines_all and cstocks:
        line_keys = {str(k) for k in lines_all}
        cand_themes: set[str] = set()
        for row in cstocks.values():
            if not isinstance(row, dict):
                continue
            th = str(row.get("core_theme") or row.get("theme") or "").strip()
            if th:
                cand_themes.add(th)
        if cand_themes and not (cand_themes & line_keys):
            warnings.append({
                "kind": "theme_key_mismatch",
                "candidates": sorted(cand_themes)[:6], "lines": sorted(line_keys)[:6],
                "hint": "候选题材键与线风向标键零交集：同一件事可能又用了两套口径",
            })

    checked["sections_present"] = sum(1 for n in OWNERS if isinstance(secs.get(n), dict))
    return {"ok": not issues, "date": date10, "issues": issues,
            "warnings": warnings, "checked": checked}


def write_health(date10: str, *, root: Path | None = None) -> dict[str, Any]:
    """跑一次一致性校验，把结论落进 `health` section（守门人留痕）。

    ⛔ 只写结论**不自动改数**：用户「提升准确性」指的是能发现矛盾，不是让脚本擅自纠错。
    ⛔ 写盘失败不影响返回（聚合是增强，不带崩主链）。
    """
    if not enabled() or not date10:
        return _build_health(date10, _read_all(date10, root))
    try:
        with _write_lock(date10, root):
            data = _read_all(date10, root)
            payload = _build_health(date10, data)
            _set_section(data, date10, "health", payload, source="snapshot_guard",
                         as_of=payload["checked_at"])
            _atomic_write(snapshot_path(date10, root), data)
            return payload
    except Exception:
        return _build_health(date10, _read_all(date10, root))


def _build_health(date10: str, data: dict[str, Any]) -> dict[str, Any]:
    cov = _coverage(date10, data)
    val = _validate(date10, data)
    payload: dict[str, Any] = {
        "revision": data.get("revision", 0),
        "stale": False,
        "checked_at": _now(),
        "ok": val["ok"],
        "issues": val["issues"],
        "warnings": val["warnings"],
        "checked": val["checked"],
        "coverage": {
            "present": cov["present"], "total": cov["total"],
            "missing": [n for n, v in cov["sections"].items() if not v["present"]],
        },
        "dimensions": {"dead": cov["dimensions"]["dead"],
                       "unused": cov["dimensions"]["unused"]},
        "schema": SCHEMA,
    }
    return payload


def health(date10: str, *, root: Path | None = None) -> dict[str, Any] | None:
    """读当日守门结论；没跑过 → None（诚实降级，⛔ 不编造「已校验」）。"""
    return read_section(date10, "health", root=root)


# ─────────────────────────────────────────────────────────────────────────────
# CLI（人读·回答「今天数据源齐了吗」）
# ─────────────────────────────────────────────────────────────────────────────
def _cli(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="每日市场快照 · 数据源聚合层")
    ap.add_argument("action", choices=["coverage", "show", "stock", "validate",
                                       "dimensions", "guard", "backfill"])
    ap.add_argument("--date", default="")
    ap.add_argument("--code", default="")
    a = ap.parse_args(argv)

    date10 = a.date or datetime.now().strftime("%Y-%m-%d")

    if a.action == "coverage":
        c = coverage(date10)
        print(f"📅 {c['date']}  数据源覆盖 {c['present']}/{c['total']}")
        if c.get("updated_at"):
            print(f"   最后更新 {c['updated_at']}")
        for name, v in c["sections"].items():
            mark = "✅" if v["present"] else "⬜"
            extra = f" {v.get('as_of')}" if v.get("as_of") else ""
            print(f"   {mark} {name:12s} {v['owner']}{extra}")
        if c["extra_sections"]:
            print(f"   ➕ 额外 section: {', '.join(c['extra_sections'])}")
        h = c.get("health")
        if isinstance(h, dict):
            mark = ("已过期，需重新校验" if h.get("stale") else
                    "一致" if h.get("ok") else f"{len(h.get('issues') or [])} 处矛盾")
            print(f"   🩺 守门 {mark} · 校验于 {h.get('checked_at')}"
                  + (f" · 提示 {len(h.get('warnings') or [])}" if h.get("warnings") else ""))
        else:
            print("   🩺 守门 未跑（今日还没做一致性校验）")
        dim = c.get("dimensions") or {}
        if dim.get("dead"):
            print(f"   💀 停更维度: {', '.join(dim['dead'])}")
        if dim.get("unused"):
            print(f"   ⚪ 算了没用: {', '.join(dim['unused'])}")
        return 0

    if a.action == "show":
        p = snapshot_path(date10)
        if not p.exists():
            print(f"无快照：{p}")
            return 1
        d = _read_all(date10)
        for name, sec in (d.get("sections") or {}).items():
            print(f"\n=== {name} · {sec.get('as_of')} · {sec.get('source')} ===")
            print(json.dumps(sec.get("payload"), ensure_ascii=False, indent=2)[:1200])
        return 0

    if a.action == "stock":
        v = stock_view(date10, a.code)
        if v is None:
            print(f"{date10} 快照里没有 {a.code} 的任何信息")
            return 1
        print(json.dumps(v, ensure_ascii=False, indent=2))
        return 0

    if a.action == "validate":
        r = validate(date10)
        mark = "✅ 一致" if r["ok"] else f"⚠️ 发现 {len(r['issues'])} 处矛盾"
        print(f"📅 {r['date']}  {mark}")
        print(f"   核对：候选 {r['checked']['candidates']} 只 · "
              f"裁决 {r['checked']['verdict']} 只 · 线 {r['checked']['lines']} 条")
        for it in r["issues"]:
            print(f"   ⛔ [{it['kind']}] {it.get('hint')}")
            for k, v in it.items():
                if k not in ("kind", "hint"):
                    print(f"        {k} = {v}")
        for it in r.get("warnings") or []:
            print(f"   ⚠️ [{it['kind']}] {it.get('hint')}（软提示，不判失败）")
        return 0 if r["ok"] else 2

    if a.action == "dimensions":
        print("📊 维度状态台账（2026-11-04 系统关联审计 · 只标注不改判定）")
        for name, v in DIMENSION_STATUS.items():
            tag = {"live": "🟢", "dead": "💀", "unused": "⚪"}.get(v["status"], "?")
            print(f"   {tag} {name:16s} {v['status']:7s} {v['note']}")
        return 0

    if a.action == "guard":
        # 只校验 + 落 health；⛔ 有矛盾也返回 0（守门只告警，不阻断日常链）
        h = write_health(date10)
        mark = "✅ 一致" if h["ok"] else f"⚠️ {len(h['issues'])} 处矛盾"
        print(f"[snapshot_guard] 📅 {date10} {mark} · 覆盖 "
              f"{h['coverage']['present']}/{h['coverage']['total']}"
              + (f" · 提示 {len(h['warnings'])}" if h["warnings"] else ""))
        for it in h["issues"]:
            print(f"[snapshot_guard] ⛔ [{it['kind']}] {it.get('hint')}")
        for it in h["warnings"]:
            print(f"[snapshot_guard] ⚠️ [{it['kind']}] {it.get('hint')}")
        return 0

    print("backfill 未实现（按需再加，避免无凭证重算历史）")
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
