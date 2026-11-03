#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""蓝宝书晨报 → 「机构题材热度榜」飞书卡（商业版）

设计原则（2026-10-29 用户定）：
  · 资讯属性：不做个股权限拦截，原文个股全量保留（688/北交/etc 一概不剔）
    权限与风控拦截只在实操链路（pool_call / defense_gate）体现
  · 序号普通阿拉伯数字，不用圈号、不加 icon
  · 热度用星级（ceil(h/2) → 5/4/3 星）
  · 卡面不出现任何内部处理痕迹（剔除/无权限/空组说明）
  · 逻辑保留原文完整推演与加粗，不压缩、不改数字
  · 只取热度前 N 条（默认 10）

用法：
  python scripts/build_bluebook_card.py --date 2026-10-29 --batch morning --top 10
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.trading_calendar import is_a_share_trading_day as is_trading_day  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "alphapai_bluebook"
# Alpha派官方 skill 的 CLI（本地直连，非 MCP）
AP_CLI = Path.home() / ".workbuddy/skills/alphapai-research/scripts/alphapai_client.py"


def fetch(card_date: str, batch: str) -> Path:
    """调 Alpha派 CLI 拉指定批次话题，落 cache/<YYYYMMDD>_<batch>.json。

    只读列表接口（不消耗 AI 额度）。已存在则默认复用，--refresh 可强刷。
    """
    import subprocess
    import sys

    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{card_date.replace('-', '')}_{batch}.json"
    if not AP_CLI.exists():
        raise SystemExit(f"Alpha派 CLI 不存在：{AP_CLI}（技能未安装？）")
    cmd = [
        sys.executable, str(AP_CLI), "bluebook", "topic-list",
        "--batch-type", batch, "--batch-scope", "domestic",
        "--start", card_date, "--end", card_date, "--page-size", "30",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise SystemExit(f"拉取失败 rc={proc.returncode}: {proc.stderr[:300]}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise SystemExit(f"返回非 JSON：{e} / {proc.stdout[:200]}")
    if payload.get("code") != 200000:
        raise SystemExit(f"接口报错 code={payload.get('code')} msg={payload.get('message')}")
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    n = len((payload.get("data") or {}).get("data") or [])
    print(f"[fetch] {card_date} {batch} → {n} 条 → {out.name}")
    return out



def stars(h: int | None) -> int:
    """热度 1-10 → 星级：9-10→5 星，7-8→4 星，5-6→3 星，3-4→2 星，1-2→1 星。"""
    return max(1, min(5, ((h or 0) + 1) // 2))


STAR_COLOR = {5: "carmine", 4: "red", 3: "orange", 2: "grey", 1: "grey"}


def star_mark(h: int | None) -> str:
    n = stars(h)
    glyphs = "★" * n + "☆" * (5 - n)
    return f"<font color='{STAR_COLOR[n]}'>{glyphs}</font>"


# 源数据错字/占位符：非真实证券名称，静默剔除（属纠错，非权限拦截）
BAD_NAMES = {"万能"}


def full_logic(x: dict) -> str:
    """原文 sourceReason 的推演段（'关注：' 之前）即完整逻辑。

    ⛔ 清掉全部 ** 标记：源数据加粗本身不成对（多条为奇数个 **，
    会跨段错位渲染），统一排版比局部加粗更稳、更商务。
    """
    raw = str(x.get("sourceReason") or "")
    main = raw.split("<br>")[0]
    main = main.replace("标的", "个股").replace("**", "")
    main = re.sub(r"\s+", "", main).strip()
    return main


def parse_groups(x: dict) -> list[tuple[list[str], str]]:
    """解析「关注：」段的分组：**票A**/**票B**（在逻辑链中的位置）。"""
    raw = str(x.get("sourceReason") or "")
    if "关注：" not in raw:
        return []
    focus = raw.split("关注：", 1)[1].replace("<br>", "")
    pat = re.compile(r"((?:\*\*[^*]+\*\*\s*/?\s*)+)（([^）]+)）")
    groups: list[tuple[list[str], str]] = []
    covered: set[str] = set()
    for m in pat.finditer(focus):
        names = [n for n in re.findall(r"\*\*([^*]+)\*\*", m.group(1)) if n not in BAD_NAMES]
        if names:
            groups.append((names, m.group(2).strip()))
            covered.update(names)
    # 无括号归属的裸票，归入兜底组
    bare = [
        n for n in re.findall(r"\*\*([^*]+)\*\*", focus)
        if n not in covered and n not in BAD_NAMES
    ]
    if bare:
        groups.append((bare, "产业链关联"))
    return groups


def build(card_date: str, batch: str, top: int, out: Path) -> dict:
    src = CACHE / f"{card_date.replace('-', '')}_{batch}.json"
    payload = json.loads(src.read_text(encoding="utf-8"))
    rows = payload["data"]["data"]
    rows.sort(key=lambda x: -(x.get("heatLevel") or 0))
    total = len(rows)
    if total == 0:
        raise SystemExit(f"{card_date} {batch} 批次为 0 条：该批次尚未发布或无数据，不生成空卡")
    picked = rows[:top]

    els: list[dict] = []
    cnt = Counter(stars(x.get("heatLevel")) for x in picked)
    dist = "　".join(
        f"{'★' * n}{'☆' * (5 - n)} {cnt[n]} 条" for n in sorted(cnt, reverse=True)
    )
    label = {"morning": "晨报", "noon": "午报", "evening": "晚报"}.get(batch, batch)
    mmdd = card_date[5:].replace("-", " / ")

    els.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content":
                 f"**{mmdd} · 机构题材热度榜**\n"
                 f"<font color='grey'>Alpha派蓝宝书 · {label} {total} 条 · 取热度前 {len(picked)}</font>\n"
                 f"<font color='grey'>{dist}</font>"},
    })
    els.append({"tag": "hr"})

    for seq, x in enumerate(picked, 1):
        logic = full_logic(x) or str(x.get("summary") or "")
        groups = parse_groups(x)
        lines = [
            f"<font color='carmine'>{seq}.</font>　**{x['topicName']}**　{star_mark(x.get('heatLevel'))}",
            f"<font color='grey'>逻辑</font>｜{logic}",
        ]
        for names, why in groups:
            lines.append(
                f"<font color='grey'>个股</font>｜**{'·'.join(names)}**"
                f"<font color='grey'>（{why}）</font>"
            )
        els.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
        if seq < len(picked):
            els.append({"tag": "hr"})

    els.append({"tag": "hr"})
    els.append({"tag": "note", "elements": [{"tag": "plain_text", "content":
        f"资料来源：Alpha派蓝宝书 · {card_date} {label}。逻辑为原文要点，个股按原文逻辑链分组，"
        "括号注明其在该链条中的位置，非强弱排名。热度为批次内相对关注度，非资金流向。不构成买卖建议。"}]})

    card = {
        "title": f"机构题材热度榜 | {card_date[5:]}",
        "color": "carmine",
        "elements": els,
    }
    out.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    return card


# ── 实操票池导出 ────────────────────────────────────────────────────────────
# ⛔ 与卡面相反：卡面不拦个股，票池必须拦——票池供盘中实盘消费，只留可交易标的。
MAIN_PREFIX = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301")

# 底库 name_index 只有"进过系统"的票（约 2600 只），大盘股常缺；此处按需补录。
EXTRA_CODES = {
    "万华化学": "600309", "万泽股份": "000534", "上海电气": "601727",
    "东方电气": "600875", "中兵红箭": "000519", "中国海油": "600938",
    "中国神华": "601088", "中国西电": "601179", "中航光电": "002179",
    "兖矿能源": "600188", "四方达": "300179", "宁波银行": "002142",
    "平高电气": "600312", "德福科技": "301511", "招商银行": "600036",
    "新奥股份": "600803", "新宙邦": "300037", "星源材质": "300568",
    "特变电工": "600089", "科华数据": "002335", "精测电子": "300567",
    "紫金矿业": "601899", "陕西煤业": "601225", "高澜股份": "300499",
    "鼎胜新材": "603876",
}


def _name_to_code_map() -> dict[str, str]:
    reg = ROOT / "cache/stock_registry/stock_registry.json"
    out: dict[str, str] = dict(EXTRA_CODES)
    try:
        idx = json.loads(reg.read_text(encoding="utf-8")).get("name_index") or {}
        for name, hit in idx.items():
            code = hit[0] if isinstance(hit, (list, tuple)) and hit else hit
            if isinstance(code, str) and code[:1].isdigit() and name not in out:
                out[name] = code
    except Exception as e:  # noqa: BLE001
        print(f"[pool] 底库读取失败，仅用补录表：{e}")
    return out


def export_pool(card_date: str, batch: str, top: int) -> dict:
    """导出 institution_pool_<date>.json 供 inst_direction / live_signal 消费。

    ⛔ 来源已从 Gangtise 机构点名换为蓝宝书产业链清单，pool 内 `source` 字段如实标注，
    下游据此可知名单性质变化（不是机构真点名，是题材产业链关联）。
    """
    src = CACHE / f"{card_date.replace('-', '')}_{batch}.json"
    rows = json.loads(src.read_text(encoding="utf-8"))["data"]["data"]
    rows.sort(key=lambda x: -(x.get("heatLevel") or 0))
    picked = rows[:top]

    code_map = _name_to_code_map()
    items: list[dict] = []
    seen: set[str] = set()
    unknown: list[str] = []
    dropped: list[str] = []

    for x in picked:
        theme = str(x.get("topicName") or "").strip()
        key = re.sub(r"[：:（(].*$", "", theme)[:20] or theme[:20]
        for names, _why in parse_groups(x):
            for nm in names:
                code = code_map.get(nm)
                if not code:
                    unknown.append(nm)
                    continue
                code = str(code)[:6]
                if not code.startswith(MAIN_PREFIX):
                    dropped.append(nm)
                    continue
                if code in seen:
                    continue
                seen.add(code)
                items.append({
                    "code": code,
                    "name": nm,
                    "theme": theme,
                    "direction_key": key,
                    "market_tags": [],   # 不臆造题材名，让 inst_direction 走词面兜底
                })

    pool = {
        "date": card_date,
        "count": len(items),
        "source": "bluebook",
        "source_note": "蓝宝书题材热度榜产业链关联清单，非机构点名；仅留主板/创业板可交易标的",
        "batch": batch,
        "items": items,
    }
    out = ROOT / "cache" / f"institution_pool_{card_date}.json"
    out.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "items": len(items),
        "dropped_non_main": len(dropped),
        "unknown_code": len(unknown),
        "unknown_names": sorted(set(unknown)),
    }


# ── 推送幂等守卫（2026-10-29 用户「怎么会推送4次？只要一次」）──────────────
# 同一 (交易日, 批次) 只允许推送一次。任务重跑、手工误操作、内容微调都不会二次发出。
# 想强推必须显式 --force，且会打印覆盖提示。
PUSH_LEDGER = ROOT / "cache" / "bluebook_pushed"


def _ledger_path(card_date: str, batch: str) -> Path:
    return PUSH_LEDGER / f"{card_date}_{batch}.json"


def already_pushed(card_date: str, batch: str) -> dict | None:
    p = _ledger_path(card_date, batch)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"broken": True}


def push(card_path: Path, card_date: str, batch: str, force: bool = False,
         webhook: str | None = None) -> bool:
    """推送到飞书，同日同批次幂等。返回 True=本次真的发出，False=已推过跳过。

    webhook=None 时交给 push_card 按交易日自动选群（任务用）；
    显式传 feishu_webhook_test 可只推测试群（调试用）。
    """
    import hashlib
    import subprocess

    digest = hashlib.sha1(card_path.read_bytes()).hexdigest()[:12]
    prev = already_pushed(card_date, batch)
    if prev and not force:
        same = prev.get("digest") == digest
        print(
            f"[push] 跳过：{card_date} {batch} 已于 {prev.get('pushed_at', '?')} 推送过"
            f"（digest={prev.get('digest', '?')}{'，内容相同' if same else '，本次内容有改动但按幂等规则不重推'}）。"
            f"确需重推请加 --force。"
        )
        return False

    cmd = [sys.executable, str(ROOT / "scripts/push_card.py"), str(card_path)]
    if webhook:
        cmd += ["--webhook", webhook]
    rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
    if rc != 0:
        raise SystemExit(f"推送失败 rc={rc}")

    PUSH_LEDGER.mkdir(parents=True, exist_ok=True)
    _ledger_path(card_date, batch).write_text(
        json.dumps({
            "date": card_date,
            "batch": batch,
            "digest": digest,
            "pushed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "webhook": webhook or "auto",
            "forced": bool(force and prev),
        }, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"[push] 已推送 {card_date} {batch}（digest={digest}）")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="默认今天；非交易日直接跳过")
    ap.add_argument("--batch", default="morning", choices=["morning", "noon", "evening"])
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--out", default="/tmp/bb_card.json")
    ap.add_argument("--fetch", action="store_true", help="先调 Alpha派 CLI 拉取当日批次")
    ap.add_argument("--refresh", action="store_true", help="配合 --fetch，强制重拉覆盖缓存")
    ap.add_argument("--pool", action="store_true", help="同时导出实操票池 institution_pool_<date>.json")
    ap.add_argument("--push", action="store_true", help="生成后直接推送（同日同批次幂等，只推一次）")
    ap.add_argument("--force", action="store_true", help="配合 --push，强制重推（默认幂等跳过）")
    ap.add_argument("--webhook", default=None,
                    help="配合 --push：显式指定群（feishu_webhook_test / feishu_webhook_prod）；"
                         "不填则按交易日自动选群")
    args = ap.parse_args()

    card_date = args.date or date.today().isoformat()
    if not is_trading_day(card_date):
        raise SystemExit(f"{card_date} 非A股交易日，跳过（不生成卡、不写池）")

    cache_file = CACHE / f"{card_date.replace('-', '')}_{args.batch}.json"
    if args.fetch and (args.refresh or not cache_file.exists()):
        fetch(card_date, args.batch)
    if not cache_file.exists():
        raise SystemExit(f"缺数据文件：{cache_file}（加 --fetch 拉取）")

    card = build(card_date, args.batch, args.top, Path(args.out))
    size = len(json.dumps(card, ensure_ascii=False).encode("utf-8"))
    print(f"date={card_date} elements={len(card['elements'])} topics={args.top} bytes={size}")
    print(f"out={args.out}  (飞书上限约 30KB)")

    if args.pool:
        stats = export_pool(card_date, args.batch, args.top)
        print(f"pool={ROOT / 'cache' / f'institution_pool_{card_date}.json'} {stats}")

    if args.push:
        push(Path(args.out), card_date, args.batch, force=args.force, webhook=args.webhook)


if __name__ == "__main__":
    main()
