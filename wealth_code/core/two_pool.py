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


def observation_only(row):
    return row.get("participation_eligibility") == "observe_only"


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
        if kind:
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
        else:
            excluded.append({"code": row.get("code"), "name": row.get("name"), "stage": stage,
                             "reason": "不符合两类观察资格且未获有效接力资格", "high_win_gate": row.get("high_win_gate"),
                             "dragon_gate": row.get("dragon_gate"), "watch_reason": row.get("watch_reason")})
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
