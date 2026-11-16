"""两池资格单一源：观察身份不授予交易资格，开关关闭保留旧链。"""
from __future__ import annotations

import os
from copy import deepcopy


def enabled():
    return os.environ.get("ENABLE_TWO_POOL_CONTRACT", "1").lower() not in {"0", "false", "off"}


def widen_enabled():
    """观察池「保留已归入观察的票」+「一字龙头入池」（2026-11-04 用户要求）。

    ⛔ `ENABLE_TWO_POOL_WIDEN=0` → 逐字回到旧行为：
       · `finalize` 只认 `watch_kind()` 两类锚点，其余一律 excluded；
       · 一字板封单不厚/缺数据 → 丢弃，不进观察池。
    """
    return os.environ.get("ENABLE_TWO_POOL_WIDEN", "1").lower() not in {"0", "false", "off"}


def height_observation(row):
    return row.get("height_observe") is True or row.get("watch_group") == "高度观察"


def observation_only(row):
    return height_observation(row) or row.get("participation_eligibility") == "observe_only"


# ── 🔁 盘后降级票的 09:25 复核类别（11-05 用户 A 方案·单一源）────────────────
# 用户原话：「观察肯定也要再通过竞价这里再判一遍，而不是直接 pass 掉」。
# ⛔ 保守白名单：只有明确属于「状态类」的降级才允许被竞价新证据解锁；
#    其余一律只复核留痕、不放行。竞价能推翻市场/状态级判断，推不翻事实权限
#    与有回测证据的个股赔率闸（10-28 冰点解冻同一铁律）。
RECHECK_HARD_GROUPS = {"最高板封单过薄", "一字龙头", "一字核心", "容量核心", "首板观察"}
RECHECK_HARD_MARKS = ("封单只占流通", "一字封死买不进", "一字封死、厚封单", "薄封")
RECHECK_STATE_GROUPS = {"过筛未过·只观察"}


def repair_recheck_enabled() -> bool:
    """「历史连板修复」组能否进 09:25 复核（2026-11-06 用户「可以放进来」）。

    ## 为什么放开（`scripts/research_open_gap_band.py`，270 票次 / 28 交易日）

    该组的盘后拒因固定是「逐日历史连板后断板再涨停；非价量N形态确认」——
    **是一条量价形态条件，不是事实权限**，而 09:25 竞价本身就是最直接的量价证据，
    恰好能推翻它。但原白名单没有这个分组，于是**整组 8 只全部 restricted**，
    连进完整裁决链的机会都没有。

    实测这 8 只：**当日 +2.98% / 胜 75%**，是所有盘后分组里最好的一组
    （对照：补充观察 −0.01%/胜47%、已获资格组 −1.67%/胜31%、容量核心 −1.13%）。
    11-05 当天涨得最好的三只——德才股份 −0.10%→**+10.11%**、万向德农 −2.30%→+6.23%、
    福莱新材 +2.81%→+6.99%——全在这个组里，全部被整组挡住。

    ⛔ 只解锁「复核资格」，不放宽任何个股闸：DEV(gap_zone) / 量能 / 身位 / 龙一 /
       薄封 / 顶部空间硬闸一律原样；身份仍是 observe_only，买不买由竞价现场判。
    ⛔ 样本仅 8 只，`POOL_CALL_RECHECK_REPAIR=0` 可逐字回退。
    """
    return os.environ.get("POOL_CALL_RECHECK_REPAIR", "1") != "0"


# 「历史连板修复」组的拒因关键词。⛔ 用整句特征而非「断板再涨停」四字——
# 后者过宽，会把别的组的文案一起捞进来。
RECHECK_REPAIR_GROUP = "历史连板修复"
RECHECK_REPAIR_MARKS = ("非价量N形态确认", "断板再涨停；")
# 🔁 11-06 修文案匹配缺口：本元组是**拒因文案的关键词**，而 `daily_screen` 实际写出的
#    门槛类拒因还有「换手板成交额 X 亿 < 5亿（少数资金对倒，易被做局）」这一种写法
#    （§三·G 换手板成交额门槛）。它属于用户定义的「门槛」类状态，却因字面不含
#    「成交额/题材门槛」而掉出白名单，被判 restricted → 09:25 永远拿不到翻案资格。
#    实测 11-06：贝特利(301697·涨 20%)、春光科技(603657·涨 10%) 均卡在此。
#    ⛔ 仍受身位闸等既有闸约束：翻案只解锁「复核资格」，不等于能买。
RECHECK_STATE_MARKS = ("同题材仅", "未成线", "成交额/题材门槛", "带动", "梯队", "情绪",
                       "换手板成交额", "题材家数", "门槛未过")


def recheck_class(row):
    """返回 "state" / "hard" / "restricted"。

    state      = 盘后状态类（题材家数/梯队/带动/情绪/门槛），09:25 可用真实报价复核解锁。
    hard       = 事实权限或个股赔率硬闸（一字/688/ST/最高板薄封），竞价再强也不推翻。
    restricted = 个股级准入未过（历史修复被前置准入/题材龙拦等）或分组无依据，
                 仍逐票复核留痕，但不取得买入资格。
    ⛔ 缺字段/老候选 → 一律 restricted（诚实降级，不误放行）。
    """
    if height_observation(row):
        return "hard"
    code = str(row.get("code") or "")
    name = str(row.get("name") or "").upper()
    if code.startswith("688") or "ST" in name or row.get("is_one_word") is True:
        return "hard"
    group = str(row.get("watch_group") or "")
    reason = str(row.get("watch_reason") or "")
    if group in RECHECK_HARD_GROUPS or any(m in reason for m in RECHECK_HARD_MARKS):
        return "hard"
    # ⛔ 状态类优先于历史修复标记：分组白名单在前，历史修复票（分组「历史连板修复」）
    #    不会因此被误放行；但「过筛未过·同题材未成线」的票不该只因带修复档案就被判死。
    if group in RECHECK_STATE_GROUPS and any(m in reason for m in RECHECK_STATE_MARKS):
        return "state"
    # 🔓 2026-11-06 用户「可以放进来」：「历史连板修复」组（拒因＝量价形态条件）
    #    从整组 restricted 改为可复核。⛔ 判据是「组名 ∧ 该组固定拒因」双命中，
    #    不按「只要带修复档案就放行」——那会把别的组的修复票一起捞进来。
    if (repair_recheck_enabled() and group == RECHECK_REPAIR_GROUP
            and any(m in reason for m in RECHECK_REPAIR_MARKS)):
        return "state"
    repair = row.get("historical_repair")
    if isinstance(repair, dict) and repair.get("blocked_by"):
        return "restricted"
    return "restricted"


def recheck_unlock_enabled() -> bool:
    """竞价复核翻案能否取得入场资格。`POOL_CALL_RECHECK_UNLOCK=0` 逐字回退。"""
    return os.environ.get("POOL_CALL_RECHECK_UNLOCK", "1") != "0"


def all_tradeable_enabled() -> bool:
    """⛔ 2026-11-10 用户「这些票全部都要有可交易的权限」。

    旧口径：观察池 = `observe_only / buyable=False`，09:25 只有 `state` 类能翻案，
    `restricted`/`hard` 一律「今天只看不买」——等于**盘后身份一票否决今早竞价**。
    新口径：候选池里每一只都具备交易资格，**买不买只由 09:25 竞价现场的闸门说了算**
    （价格/量能/顶部空间/封板质量/stance 名额），不再由盘后分组预先剥夺。

    回退：`POOL_CALL_ALL_TRADEABLE=0` → 逐字回到「只有 state 类能翻案」。
    """
    # 🔴 2026-11-12 用户：「09:25 裁决完全没有参考明日裁决的东西，很不合理…要修改逻辑的」。
    #    → 默认 **0**：盘后判定为「只观察」的票，09:25 不得翻案买入；
    #      只有 `state` 类（历史连板修复）保留复核权。回退 `POOL_CALL_ALL_TRADEABLE=1`。
    return os.environ.get("POOL_CALL_ALL_TRADEABLE", "0") != "0"


def rechecked_entry(row: dict | None) -> bool:
    """09:25 竞价复核**翻案票**的唯一判据（冻结件 / 决策行通用）。

    只有「状态类观察票 + 竞价现场判为 participate」才解锁入场；身份仍是
    `observe_only`、`buyable=False`，⛔ 不改候选、不改身份字段，只让下游三处
    （验证记录 / AI 口径 / 账户账本）不再拿盘后身份把已翻案的票二次否决。
    ⛔ hard / restricted / 复核后仍 watch 的票一律返回 False。
    """
    if not isinstance(row, dict) or height_observation(row) or not recheck_unlock_enabled():
        return False
    if all_tradeable_enabled():
        # 全池可交易：不再要求 recheck_class == "state"
        return (row.get("rechecked_from_observe") is True
                and str(row.get("view") or "") == "participate")
    return (row.get("rechecked_from_observe") is True
            and str(row.get("recheck_class") or "") == "state"
            and str(row.get("view") or "") == "participate")


def watch_kind(row):
    from core.seal_gate import seal_profile, THICK_SEAL_EXEMPT_PCT
    code = str(row.get("code") or "")
    if not code or code.startswith("688") or "ST" in str(row.get("name") or "").upper():
        return None
    theme = str(row.get("core_theme") or "").strip()
    if theme in {"", "其他", "—", "未知", "-"}:
        return None
    dragon = row.get("is_sector_dragon") is True and row.get("dragon_rank", 1) == 1
    role = row.get("leader_role")
    core = dragon or role in {"龙头", "准龙头"}
    if not core:
        return None
    if row.get("is_one_word"):
        ratio = seal_profile(row).get("seal_ratio_pct")
        if ratio is not None and ratio >= THICK_SEAL_EXEMPT_PCT:
            return "一字核心"
        # 🔓 2026-11-04 用户「观察池把一字的龙头也放进来一下，方便观察」。
        # ⛔ 原实现：一字板封单不厚（或**数据缺失**）→ 直接 `return None` 丢弃。
        #    实证 11-04：全天 4 只一字板（广聚能源/大连友谊/高争民爆3板/大晟文化3板）
        #    **全部因封单数据缺失被丢弃**，观察池里一个一字龙头都看不到。
        # ⛔ 一字板的结构特点就是「封死、量小」，封单/流通比值在数据源里经常拿不到——
        #    拿不到 ≠ 封得不厚。用「缺数据」当「不合格」是把 unknown 当成 false。
        # ✅ 现口径：一字 ∧ 是龙头（题材龙一 / 龙头·准龙头角色）→ 一律入观察池，
        #    分组「一字龙头」，仍然 `buyable=False`（只看不买）。
        #    封单厚的那批走「一字核心」，措辞不同而已，**是否可见不再由封单决定**。
        return "一字龙头" if widen_enabled() else None
    if row.get("is_capacity_core") is True:
        return "容量核心"
    return None


def mark_watch(row, kind):
    _why = {
        "一字核心": "一字封死、厚封单核心，仅作板块情绪参照，不参与",
        "一字龙头": "一字封死买不进，仅作这条线的强弱参照；不参与",
        "容量核心": "容量核心代表板块资金态度，不是连板接力对象；仅参照，不参与",
    }
    row.update(bucket="watch", bucket_label="观察池", watch_group=kind,
               participation_eligibility="observe_only", watch_only=True, buyable=False,
               watch_reason=_why.get(kind, "仅作观察，不参与"))
    return row


def finalize(relay, watch, *, stage):
    """仅保留已获接力资格且未被降级者；绝不从观察反提。

    ## ⛔ 2026-11-04 修「降为观察的票凭空消失」

    原实现只认两类观察锚点（`watch_kind()` → 一字核心 / 容量核心），
    **其余一律进 `excluded` 丢弃**。而 `pipeline_eod.demote_thin_seal_top`
    把「全市场最高板 + 封单极薄」的票从接力池降到观察池时，
    打的是 `watch_only + watch_reason + watch_group="最高板封单过薄"`，
    `watch_kind()` 对它返回 `None` → **被判「不符合两类观察资格」直接删掉**。

    实证 11-04：紫竹高科(3板/0.63%)、浙文影业(3板/0.14%)、闽发铝业(3板/1.51%)
    被 `thin_seal_top` 明确降为观察，日志也照实打了「降为观察（只看不买）」
    → 最终 `watch=0`，三只票在页面上完全消失。
    用户看到的是「系统说降为观察，但观察池是空的」。

    ✅ 现口径：**已被闸门显式降级到观察的票必须留下**——
       判据是 `watch_only is True` **且** 有 `watch_reason`（证明是某个闸主动降的，
       不是没资格）。它们只在观察池展示、扔不给买入资格（`buyable=False`）。
    ⛔ 这**不违反**「绝不从观察反提」——本分支只写 watch，永不进 relay。
    ⛔ `excluded` 仍保留真正没资格的票（无题材/ST/688/一字未获锚点资格等）。
    """
    if not enabled():
        return relay, watch, {"enabled": False}
    kept, anchors, excluded = [], [], []
    relay_codes = {str(r.get("code")) for r in relay}
    for row in relay + watch:
        kind = watch_kind(row)
        if height_observation(row):
            r = deepcopy(row)
            r.update(bucket="watch", bucket_label="高度观察", watch_group="高度观察",
                     height_observe=True, participation_eligibility="observe_only",
                     watch_only=True, buyable=False)
            anchors.append(r)
        elif kind:
            anchors.append(mark_watch(deepcopy(row), kind))
        elif (str(row.get("code")) in relay_codes and not row.get("watch_only")
              and not observation_only(row) and not row.get("is_one_word")
              and not str(row.get("code") or "").startswith("688")
              and "ST" not in str(row.get("name") or "").upper()):
            r = deepcopy(row)
            r.update(bucket="relay", bucket_label="接力池", participation_eligibility="relay_candidate")
            kept.append(r)
        elif row.get("watch_only") and row.get("watch_reason"):
            # 被某个闸显式降级 → 留在观察池（⛔ 只写 watch，永不提回 relay）
            r = deepcopy(row)
            r.update(bucket="watch", bucket_label="观察池",
                     participation_eligibility="observe_only", buyable=False)
            r.setdefault("watch_group", "已降级")
            anchors.append(r)
        elif widen_enabled() and row.get("bucket") == "watch" and row.get("watch_group"):
            # 🔓 2026-11-04：上游（`select_relay_watch`）已明确把它归入观察池
            #    （`高标/题材核心` / `补充观察` / `容量核心` 三档之一），
            #    但 `watch_kind()` 只认「一字/容量」两类 → 这里会把
            #    **高标题材核心、补充观察两类整批按「不符合两类观察资格」删掉**。
            #    实证 11-04：龙版传媒（3板·短剧6家·昨日已在池）因此消失。
            # ⛔ 判据是「上游已经定了它是观察票」，不是「我认为它可以观察」——
            #    没有 `watch_group` 的票仍然落到下面 excluded（不给蒙混空间）。
            r = deepcopy(row)
            r.update(bucket="watch", bucket_label="观察池",
                     participation_eligibility="observe_only", buyable=False)
            anchors.append(r)
        elif recheck_class(row) == "state":
            # 🔁 2026-11-11：状态类的「历史连板修复 / 过筛未过」票，**先留在观察池**，
            #    09:25 由竞价复核决定要不要翻案（11-06 用户「可以放进来」的落地位）。
            #    ⛔ 身份仍是 observe_only + buyable=False：进池 ≠ 能买。
            #    ⛔ 只放 state 类；hard（一字/688/ST/薄封）与 restricted 照旧排除。
            #    回退：`POOL_KEEP_RECHECK_STATE=0`。
            if os.environ.get("POOL_KEEP_RECHECK_STATE", "1") == "0":
                excluded.append({"code": row.get("code"), "name": row.get("name"),
                                 "stage": stage,
                                 "reason": "不符合两类观察资格且未获有效接力资格",
                                 "watch_reason": row.get("watch_reason")})
            else:
                r = deepcopy(row)
                r.update(bucket="watch", bucket_label="待复核",
                         participation_eligibility="observe_only", buyable=False,
                         watch_only=True, watch_group=row.get("watch_group") or "待复核",
                         watch_reason=row.get("watch_reason") or "状态类降级，09:25 竞价复核")
                anchors.append(r)
        elif row.get("is_one_word") and os.environ.get("POOL_KEEP_ONEWORD_RECHECK", "1") != "0":
            # 🔓 2026-11-16 用户「为什么选不中北纬」：一字板被判 is_one_word 后**直接删掉**，
            #    于是盘后池里彻底看不见它，第二天它一字晋级也与我们无关。
            #    改为「留在观察池·9:25 看是否开板」：身份 observe_only，不进买入池，
            #    但页面能看到它、也能给它一句「什么情况下可以买」。
            r = deepcopy(row)
            r.update(bucket="watch", bucket_label="待复核", participation_eligibility="observe_only",
                     buyable=False, watch_only=True, watch_group=row.get("watch_group") or "一字待开板",
                     watch_reason=row.get("watch_reason") or "一字板买不到，明早看是否开板")
            anchors.append(r)
        else:
            excluded.append({"code": row.get("code"), "name": row.get("name"), "stage": stage,
                             "reason": "不符合两类观察资格且未获有效接力资格", "high_win_gate": row.get("high_win_gate"),
                             "dragon_gate": row.get("dragon_gate"), "watch_reason": row.get("watch_reason")})
    # 🔁 09:25 复核类别随候选一次定稿（⛔ 只标注，不改身份：
    #    observe_only / buyable=False 原样保留，是否放行由竞价现场判）。
    for r in anchors:
        r["recheck_class"] = recheck_class(r)
    for bucket, rows in (("relay", kept), ("watch", anchors)):
        for i, r in enumerate(rows, 1):
            r[bucket + "_rank"] = i
            r.pop("watch_rank" if bucket == "relay" else "relay_rank", None)
    return kept, anchors, {"enabled": True, "stage": stage, "excluded": excluded}


def promotion_rate(row):
    evidence = row.get("conditional_promotion") or {}
    if evidence.get("quality") != "ok":
        return -1.0
    rate = evidence.get("rate")
    return float(rate) if isinstance(rate, (int, float)) else -1.0
