#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单一飞书推送入口（解耦 webhook 硬编码）。

用法:
  python3 push_card.py <card.json> [--webhook KEY] [--dry-run]

- card.json 两种结构都接受：
  1) 完整飞书消息：{"msg_type":"interactive","card":{...}}
  2) 简写：{"title":..,"color":..,"elements":[..],"note":..}  → 自动包装为 interactive
- webhook 选群逻辑（按交易日自动切换）：
  默认按「交易日自动判断」：周一至周五推正式群(feishu_webhook_prod)，
  周末(非交易日)推测试群(feishu_webhook_test)。
  可用环境变量 FEISHU_PUSH_ENV=prod|test 强制覆盖；或 --webhook KEY 显式指定（最高优先级）。
- 任务 prompt 已改为「写入 /tmp/limao_card.json 后调用本脚本」，不再生成 push_deep_*.py。
- 客户群镜像（2026-09-10）：主群推送成功后按 settings.json 的 client_mirror 规则镜像到客户群
  （异动群高频全量 + 正式群复盘卡），开关 enabled=false 一键关；详见下方 mirror_* 函数。
"""
from __future__ import annotations
import argparse, glob, json, os, sys
from datetime import datetime
from pathlib import Path
from typing import Optional  # 2026-09-30：load_webhook 注解用到，此前靠 __future__ 延迟求值兜住

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SETTINGS = SCRIPT_DIR.parent / "config" / "settings.json"


def load_webhook(key: str = "feishu_webhook") -> Optional[str]:
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get(key) or cfg.get("feishu_webhook")
    except Exception as e:
        print(f"[warn] 读取 {SETTINGS} 失败: {e}", file=sys.stderr)
        return None


def resolve_webhook_key() -> str:
    """按交易日自动选群，环境变量 FEISHU_PUSH_ENV 可强制覆盖。

    规则：
      FEISHU_PUSH_ENV=prod -> 正式群 (feishu_webhook_prod)
      FEISHU_PUSH_ENV=test -> 测试群 (feishu_webhook_test)
      未设 / auto        -> **A股交易日**用 prod，非交易日（周末 + 法定休市日）用 test
    说明：交易日判定走唯一真源 `common.trading_calendar`（2026-10-09 起）——
    此前只判星期，法定休市日（如中秋 09-25、国庆 10-05）会被误推正式群。
    """
    env = os.environ.get("FEISHU_PUSH_ENV", "auto").lower()
    if env == "prod":
        return "feishu_webhook_prod"
    if env == "test":
        return "feishu_webhook_test"
    now = datetime.now()
    try:
        from common.trading_calendar import is_a_share_trading_day
        is_td = is_a_share_trading_day(now)
    except Exception:  # pragma: no cover - 极端环境兜底
        is_td = now.weekday() < 5  # trading-day-fallback: 唯一真源不可用时的周末兜底
    return "feishu_webhook_prod" if is_td else "feishu_webhook_test"


# 飞书 header template 合法枚举（非法值 → 兜底 red，防无色 header·2026-08-18 复盘卡 lime 踩坑）
VALID_TEMPLATES = {"blue", "wathet", "turquoise", "green", "yellow", "orange", "red",
                   "carmine", "violet", "purple", "indigo", "grey"}


def _safe_color(color) -> str:
    if isinstance(color, str) and color.lower() in VALID_TEMPLATES:
        return color.lower()
    if color:
        print(f"[warn] 卡片 color「{color}」非法（合法: {'/'.join(sorted(VALID_TEMPLATES))}），已兜底 red", file=sys.stderr)
    return "red"


def normalize(card: dict) -> dict:
    """统一成飞书 interactive 消息体。"""
    if "msg_type" in card:
        return card
    title = card.get("title")
    if not title:
        # 兜底：杜绝裸「通知」，并提示任务 prompt 缺 title（见 shared_rules【标题规范】）
        title = "📊 盘面播报"
        print("[warn] 卡片 JSON 未提供 title 字段，已用兜底标题「📊 盘面播报」；"
              "请任务 prompt 显式写 title（见 shared_rules【标题规范】）", file=sys.stderr)
    elements = card.get("elements", [])
    if "note" in card:
        elements = elements + [{"tag": "note",
                                 "elements": [{"tag": "plain_text", "content": card["note"]}]}]
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": _safe_color(card.get("color", "red")),
            },
            "elements": elements,
        },
    }


def _fix_content_array(obj):
    """兜底修复 content 数组（2026-08-25 事故：13:00 猎手卡 content 写成数组 → 飞书 200621）。
    飞书 lark_md/plain_text 的 content 必须是字符串；AI 拼卡偶发把多行内容放数组，
    这里递归把 content 数组 join 成 '\n' 字符串（lint 会拦，本函数是 --skip-lint 时的最后防线）。"""
    if isinstance(obj, dict):
        if "content" in obj and isinstance(obj["content"], list):
            obj["content"] = "\n".join(str(x) for x in obj["content"])
        for v in obj.values():
            _fix_content_array(v)
    elif isinstance(obj, list):
        for v in obj:
            _fix_content_array(v)
    return obj


def fix_newlines(obj):
    """把模型误写的字面 \\n（反斜杠+n 两字符）还原为真实换行符。

    飞书 lark_md / plain_text 只渲染真实换行 \\n；若内容里是字面反斜杠+n
    （如字符串 '...\\\\n\\\\n...'），客户端原样显示两字符而不换行，导致卡片
    正文全部挤成一行（2026-08-05 盘前预测卡实测：大量 '\\\\n' 未解析）。
    本函数在推送前递归修复所有字符串字段；已是真实换行的内容不受影响。"""
    if isinstance(obj, dict):
        return {k: fix_newlines(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fix_newlines(v) for v in obj]
    if isinstance(obj, str):
        return obj.replace("\\n", "\n")
    return obj


PUSH_DEDUP_FILE = None  # 延迟初始化（相对项目根）


def _dedup_path() -> str:
    global PUSH_DEDUP_FILE
    if PUSH_DEDUP_FILE is None:
        PUSH_DEDUP_FILE = str(SCRIPT_DIR.parent / "cache" / "push_dedup.json")
    return PUSH_DEDUP_FILE


def _content_hash(card: dict) -> str:
    """规范化内容 hash（忽略时间戳等易变字段），用于同内容幂等去重。"""
    import hashlib, copy
    c = copy.deepcopy(card)
    for e in c.get("elements", []):
        if isinstance(e, dict):
            e.pop("timestamp", None)
            e.pop("ts", None)
    return hashlib.md5(
        json.dumps(c, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def dedup_check(card: dict, window_min: int = 10) -> tuple[bool, str]:
    """【只读检查】同内容卡片在 window_min 分钟内是否已推过 → (True, 说明)。

    ⛔ 本函数**不再写盘**（2026-09-17 修复）：原实现在**发送前**就登记 hash，
    导致发送失败时同内容在窗口内无法重试（被自己的去重误拦）。
    登记改由「发送成功后」的 `dedup_mark()` 负责。
    文件损坏/读取失败不影响主流程（幂等降级为不拦截）。
    """
    import time
    try:
        path = _dedup_path()
        db = {}
        try:
            db = json.load(open(path, encoding="utf-8"))
        except Exception:
            db = {}
        h = _content_hash(card)
        now = time.time()
        if h in db and now - db[h] < window_min * 60:
            return True, f"同内容卡片 {window_min} 分钟内已推送（hash {h[:8]}），跳过"
        return False, ""
    except Exception as e:
        print(f"[dedup] 去重降级（不拦截）: {e}", file=sys.stderr)
        return False, ""


def dedup_mark(card: dict) -> None:
    """【成功后登记】把内容 hash 写入去重库（顺带清理 >2h 过期记录）。

    ⛔ 只在**实际推送成功后**调用；失败**不得**登记（否则失败卡在窗口内无法重试）。
    写盘失败不影响主流程。
    """
    import time
    try:
        path = _dedup_path()
        db = {}
        try:
            db = json.load(open(path, encoding="utf-8"))
        except Exception:
            db = {}
        now = time.time()
        db = {k: v for k, v in db.items() if now - v < 7200}
        db[_content_hash(card)] = now
        json.dump(db, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception as e:
        print(f"[dedup] 登记降级（不影响推送）: {e}", file=sys.stderr)


def push(card: dict, webhook: str, retries: int = 2):
    """推送飞书卡片。网络异常/业务失败时指数退避重试 retries 次（默认 2 次重试）。

    架构重构（2026-08-17 P0-3）：底层网络请求统一走 common.http_client
    （复用优先·唯一 HTTP 实现）；本函数保留业务级重试（错误码 19001/19021/9499 不重试）。
    http_client 传 retries=0 避免网络层+业务层双重叠加重试。
    """
    import time

    from common.http_client import http_post

    last_res = None
    for attempt in range(retries + 1):
        try:
            res = http_post(webhook, body=card, t=10, retries=0)
            if res is None:
                raise RuntimeError("网络请求失败（http_client 返回 None）")
            if res.get("code") == 0:
                return True, res
            last_res = res
            # 业务失败（如 19001 群不存在）不重试
            if res.get("code") in (19001, 19021, 9499):
                return False, res
        except Exception as e:
            last_res = {"code": -1, "msg": f"网络异常: {e}"}
        if attempt < retries:
            time.sleep(2 * (attempt + 1))  # 2s / 5s 指数退避
    return False, last_res


def purge_stray_scripts():
    """兜底清理：删除 scripts/ 下非本脚本的 push_*.py 与 /tmp 下的 push_*.py。

    防止任务模型绕过「Write /tmp/limao_card.json → push_card.py」约定自创推送脚本，
    导致项目目录 / /tmp 文件无限堆叠（scripts/ 下的脚本会进 git，最危险）。
    本函数仅删 push_ 前缀的脚本，绝不触碰 push_card.py 本身或卡片 JSON。"""
    patterns = [str(SCRIPT_DIR / "push_*.py"), "/tmp/push_*.py"]
    removed = []
    for pat in patterns:
        for fp in glob.glob(pat):
            if os.path.basename(fp) == "push_card.py":
                continue
            try:
                os.remove(fp)
                removed.append(os.path.basename(fp))
            except OSError:
                pass
    if removed:
        print(f"[purge] 已清理 {len(removed)} 个意外推送脚本: {', '.join(removed)}", file=sys.stderr)


# ── 客户群镜像广播（2026-09-10 建立 · 2026-09-11 收窄为「成品卡白名单」）──────
# 设计：主群推送成功后，按 settings.json 的 client_mirror 规则额外镜像一份到客户群。
# 只改本文件一处即覆盖全系统（short_spirit / pulse_aggregator 均 subprocess 调本脚本）。
# 2026-09-11 用户「只要短线精灵+盘中快照+复盘卡」→ 由「异动通道整通道全量」改为
#   精准白名单匹配（卡名 + 卡标题双维度），内部高频卡（要闻快递/板块拉升/心跳）不再外发。
# ⚠️ 2026-10-14 用户「快照合并进猎手」→ 白名单②「盘中快照」标题已不再产生（原快照时点改出
#   猎手卡·标题「盘中猎手·增量」）⇒ settings 里 title_patterns:"盘中快照" 永不命中、客户群少一张卡；
#   是否改为转发特定时点猎手卡待用户拍板（详见 shared_rules【客户群镜像广播】章节）。
MIRROR_SETTINGS_KEY = "client_mirror"
MIRROR_TEST_SOURCE = "feishu_webhook_test"   # ⛔ 测试群来源一律不镜像（硬门·防外泄）


def card_title(card) -> str:
    """取卡片标题，兼容两种结构：内部简版 {"title": str} 与飞书版 {"card":{"header":...}}。

    两侧都试的原因：main() 里 normalize() 之后才调 mirror_targets，但 --dry-run/测试
    可能传入原始简版卡；取标题只用于镜像判定，故做双结构兼容（缺失返回空串）。
    """
    if not isinstance(card, dict):
        return ""
    c = card.get("card")
    if isinstance(c, dict):
        h = c.get("header")
        if isinstance(h, dict):
            t = h.get("title")
            if isinstance(t, dict):
                return str(t.get("content") or t.get("text") or "")
            if isinstance(t, str):
                return t
    t = card.get("title")
    return t if isinstance(t, str) else ""


def load_mirror_config() -> dict:
    """读取 settings.json 的 client_mirror 配置；缺失/异常返回 {}（降级=不镜像）。"""
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            cfg = json.load(f)
        m = cfg.get(MIRROR_SETTINGS_KEY)
        return m if isinstance(m, dict) else {}
    except Exception as e:
        print(f"[mirror] 读取 client_mirror 配置失败: {e}", file=sys.stderr)
        return {}


def mirror_targets(card_name: str, source_key: str, card: dict | None = None) -> list:
    """判定本次推送是否需要镜像到客户群，返回命中目标列表。

    命中条件（任一即命中，exclude_patterns 优先级最高）：
      1) channels —— source_key 在规则 channels 中（整通道全量镜像·能力保留，当前未启用）
      2) card_patterns —— 卡文件名(小写)含任一子串（窄口径，如 short_spirit＝短线精灵）
      3) title_patterns —— 卡标题含任一子串（跨 AI 拼卡最稳：所有 AI 拼盘中卡共用
         /tmp/limao_card.json 文件名，唯有标题可区分；猎手＝「盘中猎手·增量」。
         ⚠️ 2026-10-14 快照并入猎手 → settings 里 title_patterns 的「盘中快照」已失效）
      4) prod_card_patterns —— source_key==feishu_webhook_prod 且文件名含任一子串
         （⛔ 限定 prod，防止测试群的 review_* 测试卡被误镜像到客户群）

    ⛔ 硬门：来源为测试群 feishu_webhook_test → 一律不镜像（防短线精灵等测试卡外泄）。
    ⛔ client_key==source_key 跳过（防自指循环）。
    """
    cfg = load_mirror_config()
    if not cfg.get("enabled"):
        return []
    if source_key == MIRROR_TEST_SOURCE:
        return []
    name = (card_name or "").lower()
    title = card_title(card)
    out = []
    for rule in cfg.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        ckey = rule.get("client_key")
        if not ckey or ckey == source_key:
            continue
        ex = [p.lower() for p in (rule.get("exclude_patterns") or [])]
        if any(p in name for p in ex) or any(p in title.lower() for p in ex):
            continue
        hit = source_key in (rule.get("channels") or [])
        if not hit:
            hit = any(p.lower() in name for p in (rule.get("card_patterns") or []))
        if not hit:
            hit = any(p in title for p in (rule.get("title_patterns") or []))
        if not hit and source_key == "feishu_webhook_prod":
            hit = any(p.lower() in name for p in (rule.get("prod_card_patterns") or []))
        if not hit:
            continue
        wh = load_webhook(ckey)
        if wh:
            out.append({"key": ckey, "name": rule.get("client_name") or ckey, "webhook": wh})
        else:
            print(f"[mirror] ⚠️ 客户群 key「{ckey}」在 settings.json 无有效地址，跳过", file=sys.stderr)
    return out


def mirror_push(card: dict, targets: list, card_path: str) -> None:
    """把同一张卡镜像推送到客户群。

    - 绕过同内容幂等去重（主群推送时已写入 dedup，镜像必然命中 → 必须直推）
    - 逐目标独立审计（webhook=客户群 key + detail=mirror:客户名），便于区分主推与镜像
    - 任一目标失败只记 audit + stderr，绝不阻断主流程、不触发测试群告警（防客户群配置问题刷屏）
    """
    for t in targets:
        try:
            ok, res = push(card, t["webhook"])
            if ok:
                print(f"MIRROR_OK -> {t['name']}")
                _audit("push", card=Path(card_path).name, webhook=t["key"],
                       result="ok", detail=f"mirror:{t['name']}"[:200])
            else:
                print(f"[mirror] ⚠️ 镜像到「{t['name']}」失败: {res}", file=sys.stderr)
                _audit("push", card=Path(card_path).name, webhook=t["key"],
                       result="fail", detail=f"mirror:{t['name']} {str(res)[:150]}"[:200])
        except Exception as e:
            print(f"[mirror] ⚠️ 镜像到「{t['name']}」异常: {e}", file=sys.stderr)
            _audit("push", card=Path(card_path).name, webhook=t["key"],
                   result="exception", detail=f"mirror:{t['name']} {e}"[:200])


def _mask_url(url: str) -> str:
    """⛔ 脱敏 webhook URL（2026-09-23）：完整 token 不得进日志、终端回显或对话。

    只保留「路径前缀 + token 前后各 4 位」，足以核对“是不是这条 webhook”，
    又不足以被滥用。dry-run 与任何报错输出都必须过这里。
    """
    s = str(url or "")
    if not s:
        return "(空)"
    head, _, tail = s.rpartition("/")
    if not head:                     # 无路径分隔符 → 整体当 token 处理
        return (s[:4] + "…" + s[-4:]) if len(s) > 8 else "***"
    tok = tail
    masked = (tok[:4] + "…" + tok[-4:]) if len(tok) > 8 else "***"
    return f"{head}/{masked}"


def main():
    purge_stray_scripts()
    ap = argparse.ArgumentParser()
    ap.add_argument("card", help="卡片 JSON 文件路径")
    ap.add_argument("--webhook", default=None,
                    help="显式指定 settings.json 中的 webhook 键名（最高优先级，覆盖交易日自动选择）")
    ap.add_argument("--dry-run", action="store_true", help="只打印目标与卡片结构，不真正发送")
    ap.add_argument("--skip-lint", action="store_true",
                    help="跳过推送前 lint/schema 校验（紧急兜底用，慎用）")
    ap.add_argument("--force", action="store_true",
                    help="绕过同内容幂等去重，强制推送（测试/重推用）")
    args = ap.parse_args()

    # 选群：显式 --webhook > 环境变量/交易日自动判断
    env = os.environ.get("FEISHU_PUSH_ENV", "auto").lower()
    resolved = args.webhook or resolve_webhook_key()

    try:
        card = json.load(open(args.card, encoding="utf-8"))
    except Exception as e:
        print(f"ERROR 读取卡片失败: {e}", file=sys.stderr)
        sys.exit(2)

    # ── 推送前质量门（P0-1 · 2026-08-17）：lint + schema 校验，不过阻断 ──
    if not args.skip_lint:
        try:
            import importlib.util
            def _load_mod(path, name):
                spec = importlib.util.spec_from_file_location(name, path)
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                return m
            card_lint = _load_mod(str(SCRIPT_DIR / "card_lint.py"), "card_lint")
            ok_lint, errs, warns = card_lint.lint_card(card)
            for w in warns:
                print(f"[lint] ⚠️ {w['msg']}", file=sys.stderr)
            for e in errs:
                print(f"[lint] ❌ {e['msg']}", file=sys.stderr)
            if not ok_lint:
                print("ERROR 推送阻断：卡片 lint 未通过（可用 --skip-lint 紧急跳过）", file=sys.stderr)
                _audit("push", card=Path(args.card).name, webhook=resolved, result="blocked",
                       detail="; ".join(e["msg"] for e in errs)[:300])
                sys.exit(3)
        except SystemExit:
            raise
        except Exception as e:
            print(f"[lint] ⚠️ lint 执行异常（不阻断，仅警告）: {e}", file=sys.stderr)

    card = _fix_content_array(fix_newlines(normalize(card)))  # 还原字面 \n + content 数组兜底（飞书才解析）
    wh = load_webhook(resolved)
    if not wh:
        print("ERROR: 未能从 config/settings.json 获取 webhook", file=sys.stderr)
        sys.exit(2)

    if args.dry_run:
        if args.webhook is not None:
            mode, tag = "显式指定", f"--webhook={args.webhook}"
        elif env != "auto":
            mode, tag = "环境变量指定", f"FEISHU_PUSH_ENV={env}"
        else:
            mode, tag = "交易日自动(按星期)", "未设置"
        print(f"[dry-run] 选群策略: {mode} ({tag})")
        print(f"[dry-run] 选用 key: {resolved} -> {_mask_url(wh)}")
        print(f"[dry-run] msg_type: {card.get('msg_type')} | card keys: {list(card.get('card', {}).keys())}")
        _mt = mirror_targets(Path(args.card).name, resolved, card)
        print(f"[dry-run] 卡标题: {card_title(card) or '(空)'}")
        print("[dry-run] 镜像目标: " + (", ".join(f"{t['name']}({t['key']})" for t in _mt) if _mt else "无（未命中 client_mirror 规则）"))
        sys.exit(0)

    # ── 幂等去重（P1-⑧ · 2026-08-17）：同内容 10 分钟内已推则跳过，--force 绕过 ──
    if not args.force:
        dup, dup_msg = dedup_check(card)
        if dup:
            print(f"SKIP_DEDUP {dup_msg}")
            _audit("push", card=Path(args.card).name, webhook=resolved, result="dedup", detail=dup_msg[:200])
            sys.exit(0)

    try:
        ok, res = push(card, wh)
    except Exception as e:
        print(f"ERROR 推送异常: {e}", file=sys.stderr)
        _notify_failure(args.card, f"推送异常: {e}")
        _audit("push", card=Path(args.card).name, webhook=resolved, result="exception", detail=str(e)[:200])
        sys.exit(1)
    if ok:
        print("OK")
        # ⛔ 2026-09-17 修复：内容 hash 只在**实际推送成功后**登记（失败不登记 → 可重试）
        dedup_mark(card)
        _audit("push", card=Path(args.card).name, webhook=resolved, result="ok")
        mirror_push(card, mirror_targets(Path(args.card).name, resolved, card), args.card)
        sys.exit(0)
    else:
        print(f"FAIL {res}", file=sys.stderr)
        _notify_failure(args.card, f"推送失败: {res}")
        _audit("push", card=Path(args.card).name, webhook=resolved, result="fail", detail=str(res)[:200])
        sys.exit(1)


def _audit(event: str, **fields):
    """审计记录（P1-⑪ · append-only cache/audit.jsonl）。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from common.audit_log import audit
        audit(event, **fields)
    except Exception as e:
        print(f"[audit] 审计写入异常: {e}", file=sys.stderr)


def _notify_failure(card_path: str, detail: str):
    """推送失败时自动发「⚠️系统异常」蓝卡到测试群（P0-3，去重防刷屏）。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from common.logging_setup import notify_alert, get_logger
        logger = get_logger("push_card")
        logger.error(f"{card_path} {detail}")
        notify_alert("飞书推送失败", f"{card_path}\n{detail}")
    except Exception as e:
        print(f"[alert] 告警发送异常: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
