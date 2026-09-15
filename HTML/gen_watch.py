#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC 端全屏行情看盘页（2026-08-18 · 浅色重排版·专业行情软件感；2026-08-19 升级：
①短线精灵数据源=短线接口 short_spirit_signals.json（信号带共振/资金流·点击行展开详情=动态联动）
②板块涨跌修复（push2 断连→降级 push2delay）③候选池去「预测」字样直显收盘价）—— 狸猫猎手实时看盘。

架构（与服务端预渲染一致，混合刷新）：
  - 生成时内嵌 latest.json + prev.json + 短线信号 + 候选池快照（file:// 双击即看）
  - 前端 30s fetch '../cache/pulse_snapshots/latest.json' + short_spirit_signals.json 增量刷新（HTTP 下实时）

布局（全屏 Grid 平铺，1920×1080+ 自适应）：
  ┌──────────┬───────────────────────────┬─────────────┐
  │ TOPBAR 深色顶栏（品牌+指数迷你+时间+状态）                │
  ├──────────┼───────────────────────────┼─────────────┤
  │ ①短线精灵 │ ②大盘概览（指数卡+涨跌家数） │ ⑤自选股      │
  │ 左栏固定  ├───────────────────────────┼─────────────┤
  │ 滚动+悬停 │ ③行情统计 KPI              │ ⑥板块涨跌幅   │
  │ 暂停+筛选 ├───────────────────────────┼─────────────┤
  │          │ ④个股分时·资金（ECharts）   │ ⑦涨跌停原因   │
  └──────────┴───────────────────────────┴─────────────┘
  ⑧龙虎榜 → ⑦区域下方空态预留。

数据源全部复用现有系统：latest.json / prev.json / candidates / 东财 push2 板块行情。
"""
import json
import os
import pathlib
import datetime
import html as html_mod
import tempfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SNAP = ROOT / "cache" / "pulse_snapshots" / "latest.json"
PREV = ROOT / "cache" / "pulse_snapshots" / "prev.json"
OUT = pathlib.Path(__file__).resolve().parent / "watch.html"
WC_CACHE = ROOT / "wealth_code" / "cache"


def esc(s):
    return html_mod.escape(str(s)) if s is not None else ""


def _http_get(url, t=8):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=t) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _sector_boards():
    """东财行业板块涨幅/跌幅榜（2026-08-19 修复：push2 直连被远端断连致空数据→
    push2 优先 + 失败自动降级 push2delay 延迟节点；均带 Referer 防断连）。失败返回空。"""
    out = {"up": [], "down": []}
    for key, po in (("up", 1), ("down", 0)):
        for host in ("https://push2.eastmoney.com", "https://push2delay.eastmoney.com"):
            try:
                url = ("%s/api/qt/clist/get?pn=1&pz=15&po=%d&np=1"
                       "&fltt=2&invt=2&fid=f3&fs=m:90+t:2+f:!50&fields=f2,f3,f12,f14" % (host, po))
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                           "Referer": "https://quote.eastmoney.com/"})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    diff = ((json.loads(resp.read().decode("utf-8")) or {}).get("data") or {}).get("diff") or []
                out[key] = [{"name": it.get("f14"), "pct": it.get("f3")} for it in diff]
                break
            except Exception:
                continue
    return out


def _pulse(d):
    """短线精灵数据：leaders + rising 合并去重，带类型分类。"""
    seen, rows = set(), []
    for l in (d.get("leaders") or []):
        c = str(l.get("code") or "")
        if not c or c in seen:
            continue
        seen.add(c)
        pct = float(l.get("pct") or 0)
        is20 = bool(l.get("is_20cm"))
        if pct >= (19.9 if is20 else 9.9):
            t = "涨停"
        elif is20 and pct >= 10:
            t = "20cm强势"
        elif pct >= (15.0 if is20 else 7.5):
            t = "冲板"
        else:
            t = "拉升"
        rows.append({"code": c, "name": l.get("name") or c, "pct": pct, "price": l.get("price"),
                     "type": t, "sector": l.get("sector") or "", "is20": is20,
                     "buyable": bool(l.get("buyable")), "boards": l.get("boards") or ""})
    for r in (d.get("rising") or []):
        c = str(r.get("code") or "")
        if not c or c in seen:
            continue
        seen.add(c)
        pct = float(r.get("pct") or 0)
        is20 = c[:3] in ("300", "301")
        if pct >= (19.9 if is20 else 9.9):
            t = "涨停"
        elif is20 and pct >= 10:
            t = "20cm强势"
        elif pct >= (15.0 if is20 else 7.5):
            t = "冲板"
        else:
            t = "拉升"
        rows.append({"code": c, "name": r.get("name") or c, "pct": pct, "price": r.get("price"),
                     "type": t, "sector": r.get("industry") or "", "is20": is20,
                     "buyable": not bool(r.get("up")), "boards": ""})
    rows.sort(key=lambda x: -x["pct"])
    return rows


def _breadth_delta(d, prev):
    """涨跌家数变化：对比 prev breadth（红/绿各变化多少家）。"""
    cur = d.get("breadth") or {}
    pv = (prev or {}).get("breadth") or {}
    try:
        up_d = (cur.get("up") or 0) - (pv.get("up") or 0)
        down_d = (cur.get("down") or 0) - (pv.get("down") or 0)
    except Exception:
        up_d = down_d = 0
    return {"up": cur.get("up"), "down": cur.get("down"), "flat": cur.get("flat"),
            "up_delta": up_d, "down_delta": down_d}


def _stats(d):
    ld = d.get("limit_down") or {}
    br = d.get("broken") or {}
    sd = d.get("sentiment_detail") or {}
    return {
        "zt": d.get("total_zt") or 0,
        "ld": ld.get("count") or 0,
        "br": br.get("count") or 0,
        "mkt": d.get("market"),
        "seal_rate": sd.get("seal_rate"),
        "sentiment": d.get("sentiment"),
        "sentiment_score": sd.get("score"),
    }


def _cands():
    files = sorted(WC_CACHE.glob("candidates-*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        return []
    try:
        c = json.loads(files[-1].read_text(encoding="utf-8"))
        out = []
        for it in (c.get("relay") or [])[:10] + (c.get("watch") or [])[:6]:
            out.append({"code": it.get("code"), "name": it.get("name"),
                        "pct": it.get("pct"), "sector": it.get("core_theme") or it.get("theme"),
                        "bucket": it.get("bucket_label"), "score": it.get("score")})
        return out
    except Exception:
        return []


def _spirit():
    """短线接口信号（scripts/short_spirit.py 引擎输出·cache/short_spirit_signals.json，
    2026-08-19 用户「用短线接口把右侧整列拉满」：watch 短线精灵数据源由 latest.json
    leaders/rising 升级为短线信号，信号字段更全：共振/资金流/市值/成交额）。"""
    s = _load_json(ROOT / "cache" / "short_spirit_signals.json")
    out = []
    for it in (s.get("signals") or []):
        c = str(it.get("code") or "")
        if not c:
            continue
        out.append({"code": c, "name": it.get("name") or c, "pct": it.get("pct"),
                    "price": it.get("price"), "sector": it.get("sector") or "",
                    "type": it.get("type") or "拉升", "reason": it.get("reason") or "",
                    "resonance": it.get("resonance"), "amount_yi": it.get("amount_yi"),
                    "flow_yi": it.get("flow_yi"), "cap_yi": it.get("market_cap_yi"),
                    "strength": it.get("strength")})
    return {"ts": s.get("ts") or "", "session": s.get("session") or "", "signals": out}


def _em_live_quotes(codes):
    """东财实时行情（2026-08-18：返回 {code: {"pct","price"}}，price=最新价(f2 收盘后即收盘价)）：
    批量拉竞价可买票当前涨幅/价格。优先 push2 实时，失败降级 push2delay（系统涨幅榜同源，
    约几十秒延迟）。均失败容错返回 {}，不影响页面生成。
    secid：6/9 开头沪→1.、其余(0/3/8)深/北→0.；腾讯自选股 skill 无个股实时行情命令
    （kline 有延迟），故用系统已在用的东财通道。"""
    codes = [c for c in codes if c]
    if not codes:
        return {}
    secids = ",".join(("1." + c) if c[:1] in ("6", "9") else ("0." + c) for c in codes)
    fields = "f2,f3,f12&fltt=2"
    for host in ("https://push2.eastmoney.com", "https://push2delay.eastmoney.com"):
        try:
            url = f"{host}/api/qt/ulist.np/get?secids={secids}&fields={fields}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                diff = ((json.loads(resp.read().decode()) or {}).get("data") or {}).get("diff") or []
            out = {}
            for it in diff:
                if not it.get("f12") or it.get("f3") is None:
                    continue
                try:
                    pr = float(it["f2"]) if it.get("f2") is not None else None
                except (TypeError, ValueError):
                    pr = None
                out[str(it["f12"])] = {"pct": float(it["f3"]), "price": pr}
            if out:
                return out
        except Exception:
            continue
    return {}


def _nav_today(today):
    """净值账本今日行（eod 结算后写入·含当日命中池 codes/names）——用户"我们有账本的"。
    权威源 wealth_code/cache/account_nav_history.jsonl。"""
    try:
        p = ROOT / "wealth_code" / "cache" / "account_nav_history.jsonl"
        for ln in p.read_text(encoding="utf-8").strip().splitlines():
            r = json.loads(ln)
            if str(r.get("trade_date") or "") == today and r.get("codes"):
                return r
    except Exception:
        return None
    return None


def _panqian_hits(pulse, cands):
    """watch「🤖 AI预测 + 📊 今日收盘」数据（2026-08-18 用户：竞价可买→AI预测·验证命中→今日收盘·显示收盘价）：
    pred  = AI 预测候选池（cands·relay/watch，带收盘价/涨幅）；
    close = 今日验证命中（盘中=holdings buy 信号 / 盘后=净值账本今日行），带收盘价。
    返回 (pred, close, note)。不再用盘中强势股。"""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    # ── AI 预测候选池（恒展示） ──
    lq = _em_live_quotes([str(x.get("code") or "") for x in cands])
    pred = [{"code": it.get("code"), "name": it.get("name"),
             "pct": (lq.get(str(it.get("code") or "")) or {}).get("pct", it.get("pct")),
             "price": (lq.get(str(it.get("code") or "")) or {}).get("price"),
             "sector": it.get("sector") or "", "bucket": "候选",
             "boards": "", "auction_pct": None, "status": ""} for it in cands[:16]]
    # ── 今日验证命中：盘中 holdings / 盘后净值账本 ──
    close = []
    h = _load_json(ROOT / "cache" / "holdings.json")
    if str(h.get("updated") or "") == today and (h.get("holdings") or []):
        hs = h.get("holdings") or []
        lq2 = _em_live_quotes([str(x.get("code") or "") for x in hs])
        try:
            (ROOT / "cache" / "pulse_snapshots" / "auction_live.json").write_text(
                json.dumps({"ts": datetime.datetime.now().strftime("%H:%M:%S"),
                            "quotes": {str(x.get("code") or ""): (lq2.get(str(x.get("code") or "")) or {}).get("pct")
                                       for x in hs}},
                           ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        close = [{"code": str(x.get("code") or ""), "name": x.get("name"),
                  "pct": (lq2.get(str(x.get("code") or "")) or {}).get("pct"),
                  "price": (lq2.get(str(x.get("code") or "")) or {}).get("price"),
                  "sector": x.get("theme") or "", "bucket": "今日收盘",
                  "boards": x.get("boards"), "auction_pct": x.get("auction_pct"),
                  "status": x.get("open_status")} for x in hs[:16]]
    else:
        nav = _nav_today(today)
        if nav:
            lq2 = _em_live_quotes([str(c) for c in (nav.get("codes") or [])])
            pulse_map = {str(r.get("code")): r.get("sector") for r in pulse}
            cand_theme = {}
            try:
                for _f in sorted((ROOT / "wealth_code" / "cache").glob("candidates-*.json"))[-2:]:
                    _c = json.loads(_f.read_text(encoding="utf-8"))
                    for _it in (_c.get("relay") or []) + (_c.get("watch") or []):
                        cand_theme.setdefault(str(_it.get("code") or ""),
                                              _it.get("core_theme") or _it.get("theme") or "")
            except Exception:
                pass
            close = [{"code": str(c), "name": str(n),
                      "pct": (lq2.get(str(c)) or {}).get("pct"),
                      "price": (lq2.get(str(c)) or {}).get("price"),
                      "sector": cand_theme.get(str(c)) or pulse_map.get(str(c)) or "",
                      "bucket": "今日收盘", "boards": "", "auction_pct": None, "status": ""}
                     for c, n in zip(nav.get("codes") or [], nav.get("names") or [])][:16]
    note = "今日收盘" if close else "今日未验证"
    return pred, close, note


def _prev_promote_rate():
    """昨日晋级率（前日涨停→昨日涨停 占比，2026-08-18 用户「昨/今对比」）：
    今日晋级率 feedback_matrix 已带，此为对比基准；读 zt_history 按日期取昨日/前日两池。
    例：08-14 涨停63→08-17 涨停15 = 23.8%（昨日晋级率），今日 16% 作对比。"""
    files = sorted((ROOT / "cache" / "zt_history").glob("zt_*.json"))
    if len(files) < 3:
        return None
    def _codes(p):
        try:
            z = json.loads(p.read_text(encoding="utf-8"))
            return {x.get("code") for x in (z.get("zt_list") or []) if x.get("code")}
        except Exception:
            return set()
    prev_s, pprev_s = _codes(files[-2]), _codes(files[-3])
    if not pprev_s:
        return None
    return round(len(prev_s & pprev_s) / len(pprev_s) * 100, 1)


def _reasons(d):
    """涨跌停/异动原因：top5 板块 desc + newsflash。"""
    out = []
    for s in (d.get("top5") or [])[:6]:
        out.append({"name": s.get("name"), "zt": s.get("zt_count"),
                    "desc": s.get("desc") or ""})
    return out


def _sector_map(d):
    """选股宝题材成分映射（2026-08-18 用户「点题材显示命中的成分股」）：
    top5 板块的 leader/followers 即选股宝 plates 收录的成分。"""
    out = {}
    for s in (d.get("top5") or [])[:8]:
        L = s.get("leader") or {}
        out[s.get("name")] = {
            "desc": s.get("desc") or "",
            "zt_count": s.get("zt_count") or 0,
            "leader": {"name": L.get("name"), "code": str(L.get("code") or "")[:6], "pct": L.get("pct")},
            "followers": [{"name": f.get("name"), "pct": f.get("pct")}
                          for f in (s.get("followers") or [])],
        }
    return out


def _load_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _feedback(d):
    """昨日涨停反馈 + 核按钮/风险（feedback_matrix + weak_sectors 真实）。"""
    fb = _load_json(ROOT / "cache" / "feedback_matrix.json")
    ws = d.get("weak_sectors") or {}
    out = {
        "promote_rate": fb.get("promote_rate"),
        "nuke_count": fb.get("nuke_count") or 0,
        "broken_rate": fb.get("broken_rate"),
        "avg_pct": fb.get("avg_pct"),
        "up_rate": fb.get("up_rate"),
        "zt_expand": fb.get("zt_expand"),
        "sentiment_score": fb.get("sentiment_score"),
        "now_zt": fb.get("now_zt_count") or d.get("total_zt") or 0,
        "prev_zt": fb.get("prev_zt_count"),
        "prev_promote_rate": _prev_promote_rate(),
        "max_boards": fb.get("now_max_boards"),
        "prev_max_boards": fb.get("prev_max_boards"),
        "ld": ws.get("ld_count") or 0,
        "br": ws.get("broken_count") or 0,
        "nuke_list": (fb.get("nuke_list") or [])[:6],
        "weak_names": (ws.get("ld_names") or [])[:4],
    }
    return out


def _rotation():
    """题材轮动/回流候选（theme_rotation 真实·2026-08-18 全量展示不再截前6，
    携带 flow_vals/zt_series 供前端做资金流预测排序）。"""
    tr = _load_json(ROOT / "cache" / "theme_rotation.json")
    cands = []
    for c in (tr.get("candidates") or []):
        cands.append({"theme": c.get("theme"), "phase": c.get("phase"),
                      "confidence": c.get("confidence"), "boom_day": str(c.get("boom_day") or "")[-5:],
                      "cooled_days": c.get("cooled_days"),
                      "flow_trend": c.get("flow_trend"), "flow_vals": c.get("flow_vals"),
                      "zt_series": c.get("zt_series")})
    return {"date": tr.get("date"), "candidates": cands,
            "inflow": ((tr.get("market_context") or {}).get("inflow_top") or [])[:3],
            "outflow": ((tr.get("market_context") or {}).get("outflow_top") or [])[:3]}


def _senti_5d():
    """五日情绪周期（summary_5d 真实）。"""
    s5 = _load_json(ROOT / "cache" / "short_term" / "summary_5d.json")
    days = []
    for day in (s5.get("days") or [])[-5:]:
        days.append({"date": str(day.get("date") or "")[-5:], "score": day.get("sentiment_score"),
                     "label": day.get("sentiment_label"), "zt": day.get("zt_count")})
    return {"window": s5.get("window_days"), "days": days}


SNAP_MARKER = "window.SNAP = "


def write_html_atomic(out: pathlib.Path, html_out: str) -> None:
    """原子写入看盘页（2026-09-02 · P0-1）。

    背景：守护循环每轮重写 watch.html，进程被中断/并发读取时会留下半截文件，
    下一轮解析内嵌 JSON 即报 `JSONDecodeError: Extra data`。

    做法：临时文件落盘 → fsync → 回读校验内嵌 SNAP 是否为合法 JSON → os.replace 原子替换。
    校验失败不替换正式文件（保留上一份可用页面），并清理临时文件。
    """
    if SNAP_MARKER not in html_out:
        raise ValueError("模板缺少 SNAP 占位标记，拒绝写入（防止生成不可用页面）")
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=str(out.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html_out)
            f.flush()
            os.fsync(f.fileno())
        written = pathlib.Path(tmp_name).read_text(encoding="utf-8")
        start = written.index(SNAP_MARKER) + len(SNAP_MARKER)
        end = written.index("\nconst $", start)
        json.loads(written[start:end].rstrip().rstrip(";"))
        os.replace(tmp_name, out)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _load_snapshot(path: pathlib.Path):
    """容错读取快照（2026-09-02）：损坏/半截文件返回 None 并留痕，不抛异常打断守护循环。"""
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        print(f"⚠️ 快照损坏，跳过本轮渲染 {path.name}: {e}")
        return None


def main():
    if not SNAP.exists():
        print("❌ 未找到 latest.json")
        return
    d = _load_snapshot(SNAP)
    if d is None:
        # 不覆盖上一份可用页面：宁可页面时间戳旧，也不产出损坏 HTML
        print("⚠️ latest.json 不可解析，保留上一版 watch.html")
        return
    prev = _load_snapshot(PREV)

    ts_raw = str(d.get("ts") or "")
    try:
        ts = datetime.datetime.fromisoformat(ts_raw).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        ts = ts_raw

    pulse = _pulse(d)
    cands = _cands()
    wpred, wclose, wnote = _panqian_hits(pulse, cands)   # AI预测 + 今日收盘（只算一次）
    snap = {
        "ts": ts,
        "index": d.get("index") or [],
        "breadth": _breadth_delta(d, prev),
        "stats": _stats(d),
        "pulse": pulse,
        "spirit": _spirit(),
        "flow": {"inflow": ((d.get("sector_risk") or {}).get("inflow_top") or [])[:8],
                 "outflow": ((d.get("sector_risk") or {}).get("outflow_top") or [])[:8]},
        "boards": _sector_boards(),
        "watch_pred": wpred,
        "watch_close": wclose,
        "watch_note": wnote,
        "reasons": _reasons(d),
        "sector_map": _sector_map(d),
        "fb": _feedback(d),
        "rotation": _rotation(),
        "senti5d": _senti_5d(),
        "newsflash": [n.get("title", "") for n in (d.get("newsflash") or [])[:6]],
        "ladder": d.get("ladder") or [],
    }

    html_out = TPL.replace("{{SNAP}}", json.dumps(snap, ensure_ascii=False))
    write_html_atomic(OUT, html_out)
    print("✅ 全屏看盘页已生成:", OUT, "| 快照:", ts)


TPL = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>狸猫猎手 · 实时行情看盘</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
:root{
  /* 系统规范 token（shared_rules 视觉管控·与 dashboard 同源，2026-08-18 对齐） */
  --up:#ee2500;--up-soft:#ffe9e5;           /* 涨红（A股） */
  --down:#0a8f3c;--down-soft:#e3f4ea;       /* 跌绿（A股） */
  --rec:#8b5cf6;--rec-soft:#f1ecfe;         /* 可买/机会：紫 */
  --lead:#e8940a;--lead-soft:#fdf3e0;       /* 标杆龙：金 */
  --warn:#0d9488;                            /* 退潮/预警：青绿 */
  --bg:#f2f3f6;--card:#ffffff;              /* tinted neutral 底/卡 */
  --line:#e7e8ee;--line-strong:#d3d5dd;     /* 细线/强线 */
  --txt:#16161a;--sub:#666a76;              /* 非纯黑正文/次级 */
  --head-bg:#1c2130;--head-txt:#eef1f7;     /* 深色顶栏（watch 专用） */
  --gold:#e8940a; --purple:#8b5cf6; --blue:#2f6fd0;  /* 功能别名：gold=lead/purple=rec */
  --num:tabular-nums,'SF Mono',Menlo,ui-monospace,monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden}
body{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;
  background:var(--bg);color:var(--txt);font-size:13px;line-height:1.5;
  font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased}
.up{color:var(--up)}.down{color:var(--down)}.sub{color:var(--sub)}

#app{height:100vh;display:grid;grid-template-rows:44px 1fr;gap:6px;padding:6px 8px 8px}

/* ── 顶栏 ── */
.topbar{display:flex;align-items:center;gap:18px;padding:0 16px;border-radius:8px;
  background:linear-gradient(90deg,#1c2130,#283049);color:var(--head-txt);font-size:13px}
.topbar .brand{display:flex;align-items:center;gap:8px;font-weight:700;font-size:15px;letter-spacing:.02em;flex:none}
.topbar .brand .lg{width:24px;height:24px;border-radius:6px;background:linear-gradient(135deg,#ee2500,#ff7a3d);
  display:inline-flex;align-items:center;justify-content:center;color:#fff;font-size:12px;font-weight:800}
.topbar .nav-link{flex:none;color:#9aa4b8;font-size:12px;font-weight:600;text-decoration:none;padding:4px 10px;border-radius:6px;border:1px solid rgba(255,255,255,.16);white-space:nowrap}
.topbar .nav-link:hover{color:#fff;border-color:rgba(255,255,255,.4);background:rgba(255,255,255,.08)}
.mini-idx{display:flex;gap:14px;flex:1}
.mini-idx .mi{display:flex;align-items:center;gap:6px;white-space:nowrap}
.mini-idx .mi b{font-size:15px;font-weight:700}
.mini-idx .mi .nm{color:#9aa4b8}
.top-bd{display:flex;align-items:center;gap:12px;font-size:12px;color:#b6bfd0;white-space:nowrap;padding:0 14px;border-left:1px solid rgba(255,255,255,.14);border-right:1px solid rgba(255,255,255,.14)}
.top-bd .bd-item{display:flex;align-items:center;gap:4px}
.top-bd .bd-item b{font-size:14px;font-weight:700}
.top-bd .bd-delta{font-size:11px;color:#8b94a8}
.topbar .meta{text-align:right;white-space:nowrap;color:#b6bfd0}
.topbar .live{display:inline-block;width:8px;height:8px;border-radius:50%;background:#34d399;margin-right:5px;
  box-shadow:0 0 6px #34d399;animation:blink 1.6s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.25}}

/* ── 主体网格 ── */
.main{display:grid;grid-template-columns:300px 1fr 264px;grid-template-rows:1.05fr 1fr 1fr;gap:6px;min-height:0}
.pane{background:var(--card);border:1px solid var(--line);border-radius:10px;box-shadow:0 1px 2px rgba(22,22,26,.05);display:flex;flex-direction:column;min-height:0;overflow:hidden}
.pane:hover{border-color:var(--line-strong)}
.pane>h3{font-size:13px;font-weight:700;padding:8px 12px;border-bottom:1px solid var(--line);
  display:flex;align-items:center;justify-content:space-between;flex:none;background:#fafbfc}
.pane>h3 .x{font-size:11px;color:var(--sub);font-weight:500}

/* ① 短线精灵（左栏·跨两行） */
.ticker{grid-row:1/4;grid-column:3}
.filters{display:flex;gap:4px;padding:6px 10px;border-bottom:1px solid var(--line);flex-wrap:wrap;flex:none}
.fbtn{border:1px solid var(--line-strong);background:#fff;color:var(--sub);padding:2px 9px;border-radius:11px;
  font-size:11px;cursor:pointer;font-weight:600}
.fbtn.active{background:var(--up);border-color:var(--up);color:#fff}
.tick-scroll{flex:1;min-height:0;overflow-y:auto;position:relative}
.tick-inner{will-change:transform}
/* 拉升类折叠区（2026-08-18 用户「异动频率高」A 方案：拉升降级收底） */
.minor-bar{cursor:pointer;font-size:11px;color:var(--sub);padding:6px 10px;border-top:1px solid var(--line);background:#fafbfc;font-weight:700;position:sticky;bottom:0}
.minor-bar:hover{color:var(--rec)}
.minor-list{border-top:1px solid var(--line);max-height:35vh;overflow-y:auto;background:#fafbfc}
.minor-list .mrow{padding:3px 10px;font-size:11px;color:var(--sub);display:flex;align-items:center;gap:6px;border-bottom:1px dashed #eef0f4}
.minor-list .mrow .nm{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.minor-list .mrow .pct{font-weight:700;font-family:var(--num);flex:none}
.trow{padding:6px 10px;border-bottom:1px solid var(--line);font-size:12px;cursor:pointer}
.trow:hover{background:#f5f6f9}
.tr-top{display:flex;align-items:center;gap:6px}
.tr-top .tm{color:var(--sub);font-size:10px;font-family:var(--num);flex:none}
.tr-top .nm{font-weight:600;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tr-top .nm .cd{color:var(--sub);font-weight:400;font-size:10px;margin-left:3px}
.tr-bot{display:flex;align-items:center;gap:8px;margin-top:2px;color:var(--sub);font-size:11px}
.tr-bot .sec{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tr-bot .px{font-family:var(--num)}
.tr-bot .pct{font-weight:700}
/* 短线信号共振标签 + 点击展开详情（2026-08-19 用户「短线接口拉满右列+动态联动」） */
.trow .res{font-size:9px;padding:1px 5px;border-radius:8px;background:var(--rec-soft);color:var(--rec);font-weight:700;flex:none;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tr-det{display:none;font-size:10px;color:var(--sub);margin-top:3px;font-family:var(--num);border-top:1px dashed #eef0f4;padding-top:3px;line-height:1.6}
@keyframes freshin{0%{background:var(--up-soft);transform:translateX(-4px)}100%{background:transparent;transform:translateX(0)}}
.trow.fresh{animation:freshin 2s ease-out}
.trow .nm{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.trow .nm .cd{color:var(--sub);font-weight:400;font-size:10px;margin-left:3px}
.trow .pct{font-weight:700;font-family:var(--num)}
.trow .pct.charge{color:var(--purple)}
.trow .tag{font-size:9px;padding:1px 5px;border-radius:8px;font-weight:700;flex:none}
.tag.涨停{background:var(--up-soft);color:var(--up)}
.tag.冲板{background:#f0edfe;color:var(--purple)}
.tag.20cm强势{background:#fdf3e2;color:var(--gold)}
.tag.拉升{background:#e9f1fc;color:var(--blue)}
.buy{color:var(--purple);font-size:10px;font-weight:700;flex:none}

/* ② 板块资金流向（原大盘概览区改造·2026-08-18 用户「顶部已有指数·这里放资金流向」） */
.flow-in{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:10px;flex:1;min-height:0}
.flow-col{display:flex;flex-direction:column;min-height:0}
.flow-col h4{font-size:11px;color:var(--sub);margin-bottom:6px;font-weight:700;flex:none}
.flow-rows{flex:1;overflow-y:auto;font-size:12px}
.flow-row{display:flex;align-items:center;gap:6px;padding:4px 0}
.flow-row .lab{width:76px;flex:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.flow-row .track{flex:1;background:#f0f1f4;border-radius:5px;height:12px;overflow:hidden}
.flow-row .fill{height:100%;border-radius:5px}
.fill.in{background:linear-gradient(90deg,#f6b8b8,#ee2500)}
.fill.out{background:linear-gradient(90deg,#b7e8d4,#0a8f3c)}
.flow-row .val{width:52px;flex:none;text-align:right;font-weight:700;font-family:var(--num)}

/* ② 板块资金流向（2026-08-18 用户「行情统计/个股分时先删」：资金流向占满中区） */
.overview{grid-row:2;grid-column:2}

/* 中区第二行：昨日反馈 + 核按钮/风险 */
.fb{grid-row:3;grid-column:2}
.fb-in{display:grid;grid-template-columns:1fr 1fr;gap:0;flex:1;min-height:0}
.fb-col{padding:6px 10px;overflow-y:auto}
.fb-col h4{font-size:11px;color:var(--sub);margin-bottom:4px;font-weight:700}
.fb-kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:6px}
.fb-kpi{background:#fafbfc;border:1px solid var(--line);border-radius:6px;padding:5px 8px}
.fb-kpi .lb{font-size:10px;color:var(--sub)}
.fb-kpi .vv{font-size:15px;font-weight:800;font-family:var(--num)}
.fb-kpi .vs{font-size:10px;color:var(--sub);font-family:var(--num);margin-top:1px;font-weight:600}
.fb-row{display:flex;align-items:center;gap:6px;font-size:12px;padding:3px 0;border-bottom:1px dashed #eef0f4}
.fb-row .nm{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fb-row .nm .cd{color:var(--sub);font-weight:400;font-size:10px}
.fb-row .tag{font-size:9px;padding:0 4px;border-radius:6px;background:var(--down-soft);color:var(--down);font-weight:700;flex:none}
.fb-row .pct{font-weight:700;font-family:var(--num);flex:none}

/* 中区第一行：板块涨跌 + 题材轮动 + 五日情绪（2026-08-18 用户「放中间顶部」） */
.bottom{grid-row:1;grid-column:2}
.bot-in{display:grid;grid-template-columns:1fr 1fr 1fr;gap:0;flex:1;min-height:0}
.bot-col{display:flex;flex-direction:column;padding:6px 10px;min-width:0;min-height:0}
.bot-col h4{font-size:11px;color:var(--sub);margin-bottom:4px;font-weight:700;flex:none}
.sec-in{display:grid;grid-template-columns:1fr 1fr;gap:4px;flex:1;min-height:0}
.sec-col{overflow-y:auto;font-size:11px}
#rot-list{flex:1;min-height:0;overflow-y:auto}
.sec-col{overflow-y:auto;font-size:11px}
.sec-row{display:flex;justify-content:space-between;font-size:11px;padding:2px 0;border-bottom:1px solid #f0f1f4}
.sec-row .pct{font-weight:700;font-family:var(--num)}
.rt-row{display:flex;align-items:center;gap:6px;font-size:12px;padding:3px 0;border-bottom:1px solid #f0f1f4}
.rt-row .nm{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rt-row .ph{font-size:9px;padding:0 5px;border-radius:6px;font-weight:700;flex:none}
.rt-row .ph.boom{background:var(--up-soft);color:var(--up)}
.rt-row .ph.fenge{background:#fdf3e2;color:var(--gold)}
.rt-row .ph.other{background:#e9f1fc;color:var(--blue)}
.rt-row .conf{color:var(--sub);font-size:11px;font-family:var(--num);flex:none}
.s5d-chart{flex:1;min-height:0;width:100%}

/* ⑤ 自选股 / ⑦ 异动原因（右栏） */
.watch{grid-row:1;grid-column:1}
.wrow{padding:6px 12px;border-bottom:1px solid var(--line);font-size:12px}
.wrow .wr-top{display:flex;align-items:center;gap:6px;min-width:0}
.wrow .nm{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}
.wrow .nm .cd{color:var(--sub);font-weight:400;font-size:10px;margin-left:3px}
.wrow .buy{color:var(--rec);font-size:9px;font-weight:700;flex:none;border:1px solid var(--rec-soft);background:var(--rec-soft);border-radius:8px;padding:0 5px}
.wrow .buy.cand{color:var(--blue);border-color:#dbe7f7;background:#e9f1fc}
.wrow .buy.hit{color:var(--lead);border-color:#f8e6c8;background:#fdf3e0}
.wrow .price{font-size:11px;font-weight:700;font-family:var(--num);flex:none;color:var(--txt)}
.watch h4{font-size:11px;color:var(--sub);margin:8px 0 2px;padding-top:6px;border-top:1px solid #e4e6eb;font-weight:700}
.wrow .wr-bot{display:flex;align-items:center;gap:8px;margin-top:2px;font-size:11px;min-width:0}
.wrow .sec{color:var(--sub);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}
.wrow .bd{color:var(--up);background:var(--up-soft);border-radius:4px;padding:0 4px;font-size:10px;font-weight:700;flex:none}
.wrow .sc{color:var(--sub);font-size:10px;font-family:var(--num);flex:none}
.wrow .pct{font-weight:700;font-family:var(--num);flex:none}
.reasons{grid-row:2/4;grid-column:1;overflow-y:auto}
.rz{font-size:12px;padding:6px 12px;border-bottom:1px solid var(--line)}
.rz .rt{font-weight:700;display:flex;align-items:center}
.rz .rt .z{border-radius:4px;padding:0 5px;font-size:10px;color:var(--up);background:var(--up-soft);font-weight:700;margin-left:6px}
.rz .rd{color:var(--sub);font-size:11px;margin-top:2px;line-height:1.5}
.rz.lhb{color:var(--sub);font-style:italic}
.sec-btn{cursor:pointer;color:var(--txt)}
.sec-btn:hover{color:var(--up)}
.sec-detail{margin-top:4px;border-left:2px solid var(--line);padding-left:8px}
.sec-hit{display:flex;align-items:center;gap:8px;font-size:12px;padding:3px 0;border-bottom:1px dashed var(--line)}
.sec-hit .nm{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sec-hit .nm .cd{color:var(--sub);font-weight:400;font-size:10px;margin-left:3px}
.sec-hit .tg{font-size:10px;color:var(--purple);font-weight:700;flex:none}
.sec-hit .pct{font-weight:700;font-family:var(--num);flex:none}

.empty{display:flex;align-items:center;justify-content:center;height:100%;color:var(--sub);font-size:12px;border:1px dashed var(--line);border-radius:6px;margin:6px}
/* 雪球跳转链接：继承原色，hover 变机会紫 */
.xq{color:inherit;text-decoration:none;border-bottom:1px dashed rgba(22,22,26,.16)}
.xq:hover{color:var(--rec);border-bottom-color:var(--rec)}
</style>
</head>
<body>
<div id="app">
  <div class="topbar">
    <div class="brand"><span class="lg">狸</span>狸猫猎手 · 实时行情看盘</div>
    <a class="nav-link" href="dashboard.html">📋 聚合看板</a>
    <div class="mini-idx" id="mini-idx"></div>
    <div class="top-bd" id="top-bd"></div>
    <div class="meta" id="meta">加载中…</div>
  </div>
  <div class="main">
    <div class="pane ticker"><h3>⚡ 短线精灵 <span class="x" id="pulse-n">—</span></h3>
      <div class="filters" id="filters"></div>
      <div class="tick-scroll" id="tick-scroll"><div class="tick-inner" id="tick-inner"></div>
        <div class="minor-bar" id="minor-bar" hidden>📈 拉升类 <span id="minor-n">0</span> 条 ▸</div>
        <div class="minor-list" id="minor-list" hidden></div></div>
    </div>
    <div class="pane overview"><h3>💸 板块资金流向 <span class="x" id="flow-net">主力净额</span></h3>
      <div class="flow-in" id="flow-in"></div>
    </div>
    <div class="pane fb"><h3>📈 昨日涨停反馈 · 核按钮 <span class="x" id="fb-note"></span></h3>
      <div class="fb-in">
        <div class="fb-col" id="fb-yst"></div>
        <div class="fb-col" id="fb-nuke"></div>
      </div>
    </div>
    <div class="pane bottom"><h3>🗂 板块涨跌 · 题材轮动 · 五日情绪 <span class="x">真实数据</span></h3>
      <div class="bot-in">
        <div class="bot-col"><h4>🗂 板块涨跌</h4><div class="sec-in"><div class="sec-col" id="sec-up"></div><div class="sec-col" id="sec-down"></div></div></div>
        <div class="bot-col"><h4>🔁 题材轮动/回流</h4><div id="rot-list"></div></div>
        <div class="bot-col"><h4>📊 五日情绪 <span class="x" id="s5d-sum"></span></h4><div id="s5d-chart" class="s5d-chart"></div></div>
      </div>
    </div>
    <div class="pane watch"><h3>🎯 候选池 <span class="x" id="watch-note">今日收盘</span></h3>
      <div id="watch-pred"></div>
      <h4>📊 今日收盘</h4><div id="watch-close"></div>
    </div>
    <div class="pane reasons"><h3>💡 异动/涨停原因 <span class="x">点板块看成分</span></h3>
      <div id="reasons-list"></div>
    </div>
  </div>
</div>

<script>
window.SNAP = {{SNAP}};
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct = v => (v==null||isNaN(v))?'—':((v>0?'+':'')+Number(v).toFixed(2)+'%');
const cls = v => v>0?'up':(v<0?'down':'');
/* 短线精灵涨跌幅着色（2026-08-18 用户）：跌→绿 / 涨停→红 / 冲板·20cm强势(冲刺高点)→紫 */
const pctCls = r => {
  if(r.pct<0) return 'down';
  if(r.type==='涨停') return 'up';
  if(r.type==='冲板'||r.type==='20cm强势') return 'charge';
  return 'up';
};
/* 雪球跳转（2026-08-18 用户「拼接雪球链接」）：沪 SH600367 / 深 SZ300360 / 北交所 BJ */
const xqUrl = code => {
  const c = String(code||'').replace(/\D/g,'').slice(-6);
  if(!c) return '';
  if(/^(60|68|90|51|56|58)/.test(c)) return 'https://xueqiu.com/S/SH'+c;
  if(/^(00|30|20|15|16|18)/.test(c)) return 'https://xueqiu.com/S/SZ'+c;
  if(/^(83|87|43|92|82|88)/.test(c)) return 'https://xueqiu.com/S/BJ'+c;
  return 'https://xueqiu.com/S/'+c;
};
const xq = (nm, code) => `<a class="xq" href="${xqUrl(code)}" target="_blank" rel="noopener">${nm}</a>`;
let DATA = window.SNAP, filter='全部', timer=null;

/* ── 顶栏迷你指数 + 涨跌家数 ── */
function renderTop(d){
  const idx = d.index||[];
  $('mini-idx').innerHTML = idx.map(x=>`<div class="mi"><span class="nm">${esc(x.name)}</span><b class="${cls(x.pct)}">${pct(x.pct)}</b></div>`).join('');
  const b=d.breadth||{};
  $('top-bd').innerHTML = `
    <span class="bd-item">涨跌家数</span>
    <span class="bd-item"><b class="up">${b.up!=null?b.up:'—'}</b> 红</span>
    <span class="bd-item"><b class="down">${b.down!=null?b.down:'—'}</b> 绿</span>
    <span class="bd-item"><b>${b.flat!=null?b.flat:'—'}</b> 平</span>
    <span class="bd-delta">较上轮 <b class="up">+${b.up_delta>0?b.up_delta:0}红</b> · <b class="down">+${b.down_delta>0?b.down_delta:0}绿</b></span>`;
  const live = !!d._live;
  $('meta').innerHTML = `<span class="${live?'live':''}" style="${live?'':'background:#c77d0a;box-shadow:none'}"></span>${live?'实时':'快照'} · ${esc(d.ts||'—')}`;
}

/* ── ① 短线精灵（消息流·2026-08-19 升级：数据源=短线接口 short_spirit_signals
   （信号带共振/资金流），latest.json leaders/rising 兜底；点击行展开详情=动态联动） ── */
let MSG_LOG = [];       // 主消息流：涨停/冲板/20cm强势（短线信号 + pulse 兜底合并）
let MINOR_LOG = [];     // 拉升类折叠区
let SPIRIT = [];        // 短线接口信号（短线精灵引擎·优先数据源）
let IMPO_PENDING = [];  // 重要异动排队（单轮限频溢出的等下轮）
let minorOpen = false;
const MAX_LOG = 60, MAX_MINOR = 30, MAX_INSERT = 3;   // 单轮重要异动最多插入 3 条
/* 短线信号 type 映射：接近涨停→冲板 / 快速拉升→拉升 / 涨停→涨停（filter 体系复用） */
const spiritType = t => t==='涨停'?'涨停' : (t==='接近涨停'?'冲板':'拉升');
const isMinor = r => r.type==='拉升';
const mergeOne = (arr, r, cap) => {
  const m = arr.find(x=>x.code===r.code);
  if(m){ // 去抖：pct 变化 ≥1% 才更新，防止涨幅榜抖动重排
    if(m.pct==null || r.pct==null || Math.abs(r.pct-m.pct)>=1){m.pct=r.pct;m.sector=r.sector||m.sector;m.buyable=r.buyable;m.reason=r.reason||m.reason;}
    return 0;
  }
  arr.unshift({...r, ts:new Date().toTimeString().slice(0,8), fresh:true});
  if(arr.length>cap) arr.length=cap;
  return 1;
};
/* 短线接口信号 → 主消息流（去重 pulse 已存在 code） */
function mergeSpirit(rows){
  let added = 0;
  (rows||[]).forEach(r=>{
    if(MSG_LOG.some(m=>m.code===r.code)) return;
    added += mergeOne(SPIRIT, r, MAX_LOG);
  });
  return added;
}
function mergePulse(rows){
  let added = 0;
  const impo = rows.filter(r=>!isMinor(r)), minor = rows.filter(isMinor);
  // 重要异动：排队 + 本轮，单轮最多插 MAX_INSERT，溢出排队下轮
  let inserted = 0;
  IMPO_PENDING.concat(impo).forEach(r=>{
    if(inserted < MAX_INSERT){
      const k = mergeOne(MSG_LOG, r, MAX_LOG);
      if(k){added += k; inserted++;}
    } else if(!MSG_LOG.some(m=>m.code===r.code)){
      IMPO_PENDING.push(r);
    }
  });
  if(IMPO_PENDING.length>MAX_LOG) IMPO_PENDING=IMPO_PENDING.slice(-MAX_LOG);
  // 拉升类：进折叠区（去抖同规则）
  minor.forEach(r=>{ added += mergeOne(MINOR_LOG, r, MAX_MINOR); });
  return added;
}
function renderTicker(){
  // 主区：短线信号 SPIRIT + 重要异动 MSG_LOG 合并去重；filter=拉升 时主区清空改由折叠区全展
  const all = SPIRIT.concat(MSG_LOG).filter((r,i,a)=>a.findIndex(x=>x.code===r.code)===i);
  const rows = filter==='拉升' ? [] : all.filter(r=>filter==='全部'||r.type===filter);
  $('pulse-n').textContent = (all.length+MINOR_LOG.length)+' 条 · 最新 '+ (all[0]?all[0].ts:'—');
  $('tick-inner').innerHTML = rows.map(r=>`
    <div class="trow ${r.fresh?'fresh':''}" data-code="${esc(r.code)}">
      <div class="tr-top">
        <span class="tm">${r.ts?r.ts.slice(0,5):'--:--'}</span>
        <span class="nm">${xq(esc(r.name), r.code)}<span class="cd">${xq(esc(r.code), r.code)}</span></span>
        <span class="tag ${r.type}">${r.type}</span>${r.buyable?'<span class="buy">可买</span>':''}${r.reason?'<span class="res" title="${esc(r.reason)}">⚡${esc(r.reason)}</span>':''}
      </div>
      <div class="tr-bot">
        <span class="sec">${esc(r.sector||'—')}</span>
        <span class="px">${r.price!=null?Number(r.price).toFixed(2):'—'}</span>
        <span class="pct ${pctCls(r)}">${pct(r.pct)}</span>
      </div>
      ${(r.cap_yi!=null||r.amount_yi!=null||r.flow_yi!=null)?`<div class="tr-det">市值 ${r.cap_yi!=null?r.cap_yi+' 亿':'—'} · 成交 ${r.amount_yi!=null?r.amount_yi+' 亿':'—'} · 主力 ${r.flow_yi!=null?((r.flow_yi>0?'+':'')+r.flow_yi+' 亿'):'—'} · 共振 ${r.resonance??'—'}</div>`:''}
    </div>`).join('');
  // 动态联动：点击行展开/收起详情
  $('tick-scroll').querySelectorAll('.trow').forEach(el=>{
    el.addEventListener('click', ()=>{ const det=el.querySelector('.tr-det'); if(det) det.style.display = det.style.display==='none'?'block':'none'; });
  });
  (all.concat(MINOR_LOG)).forEach(m=>delete m.fresh);
  // 拉升折叠区：默认收起；filter=拉升 时全展
  const bar=$('minor-bar'), list=$('minor-list');
  bar.hidden = MINOR_LOG.length===0;
  $('minor-n').textContent = MINOR_LOG.length;
  const show = filter==='拉升' || minorOpen;
  bar.textContent = show? `📈 拉升类 ${MINOR_LOG.length} 条 ▾` : `📈 拉升类 ${MINOR_LOG.length} 条 ▸`;
  list.hidden = !show;
  if(show){
    list.innerHTML = MINOR_LOG.map(r=>`<div class="mrow"><span class="nm">${xq(esc(r.name), r.code)}</span><span class="pct ${pctCls(r)}">${pct(r.pct)}</span></div>`).join('');
  }
}
function renderFilters(){
  $('filters').innerHTML = ['全部','涨停','冲板','20cm强势','拉升'].map(t=>
    `<button class="fbtn ${filter===t?'active':''}" data-f="${t}">${t}</button>`).join('');
  $('filters').querySelectorAll('.fbtn').forEach(b=>b.addEventListener('click',()=>{filter=b.dataset.f;renderTicker();renderFilters();}));
  $('minor-bar').addEventListener('click',()=>{minorOpen=!minorOpen;renderTicker();});
}

/* ── ② 板块资金流向 ── */
function renderFlow(d){
  const f=d.flow||{};
  const net=(f.inflow||[]).reduce((a,x)=>a+Number(x.flow_yi||0),0)+(f.outflow||[]).reduce((a,x)=>a+Number(x.flow_yi||0),0);
  $('flow-net').innerHTML = `主力净额 <b class="${net>0?'up':'down'}">${net>0?'+':''}${net.toFixed(1)} 亿</b>`;
  // 全局统一满值：流入+流出取最大绝对值，两栏共用同一把尺子，量级差距一目了然
  const all=(f.inflow||[]).concat(f.outflow||[]);
  const gmx = all.reduce((a,x)=>Math.max(a,Math.abs(Number(x.flow_yi||0))),1);
  const col=(items,c,fillc,title)=>{
    const arr=items||[];
    const rows=arr.map(x=>{
      const fy=Number(x.flow_yi||0);
      const pw=Math.round(Math.abs(fy)/gmx*100);
      return `<div class="flow-row"><span class="lab">${esc(x.name)}</span>
        <div class="track"><div class="fill ${fillc}" style="width:${Math.max(2,pw)}%"></div></div>
        <span class="val ${c}">${fy>0?'+':''}${fy}</span></div>`;
    }).join('')||'<div class="empty">暂无数据</div>';
    return `<div class="flow-col"><h4>${title}</h4><div class="flow-rows">${rows}</div></div>`;
  };
  $('flow-in').innerHTML =
    col(f.inflow,'up','in','🔴 主力净流入 Top（亿）') +
    col(f.outflow,'down','out','🟢 主力净流出 Top（亿）');
}

/* ── ⑤ 候选池 + 今日收盘（2026-08-19 用户「去预测字样·直接显示收盘价」） ── */
function wrow(r, tag, tagCls){
  return `<div class="wrow"><div class="wr-top">
    <span class="nm">${xq(esc(r.name), r.code)}<span class="cd">${esc(r.code)}</span></span>
    <span class="buy ${tagCls}">${tag}</span></div>
    <div class="wr-bot">
      <span class="sec">${esc(r.sector||'—')}</span>
      <span class="price">收 ${r.price!=null?r.price.toFixed(2):'—'}</span>
      <span class="pct ${cls(r.pct)}">${pct(r.pct)}</span></div></div>`;
}
function renderWatch(d){
  const pred=d.watch_pred||[], close=d.watch_close||[];
  const note=d.watch_note||'今日收盘';
  $('watch-note').textContent = note;
  $('watch-pred').innerHTML = pred.length? pred.map(r=>wrow(r,'候选','cand')).join('')
    : '<div class="empty">暂无候选</div>';
  $('watch-close').innerHTML = close.length? close.map(r=>wrow(r,'今日','hit')).join('')
    : '<div class="empty">今日未验证 / 无命中</div>';
}

/* ── ⑥ 板块涨跌幅（红绿双列） ── */
function renderSectors(d){
  const b=d.boards||{up:[],down:[]};
  const col=arr=>arr.map(x=>`<div class="sec-row"><span>${esc(x.name)}</span><span class="pct ${cls(x.pct)}">${pct(x.pct)}</span></div>`).join('')||'<div class="empty">—</div>';
  $('sec-up').innerHTML = col(b.up);
  $('sec-down').innerHTML = col(b.down);
}

/* ── ⑥.5 昨日涨停反馈 + 核按钮/风险 ── */
function renderFeedback(d){
  const f=d.fb||{};
  // 对比卡：今值主 + 昨值副（2026-08-18 用户「涨停100/50·晋级率20%/30% 昨日vs今日」）
  const kpi=(lb,vv,c,vs)=>`<div class="fb-kpi"><div class="lb">${lb}</div><div class="vv ${c||''}">${vv}</div>${vs?`<div class="vs">${vs}</div>`:''}</div>`;
  const pr=f.promote_rate, ppr=f.prev_promote_rate;
  const prC = pr!=null && ppr!=null ? (pr>=ppr+3?'up':(pr<=ppr-3?'down':'')) : (pr!=null&&pr>=25?'up':(pr!=null&&pr<20?'down':''));
  $('fb-yst').innerHTML =
    `<h4>昨日涨停反馈（今日晋级）</h4>` +
    `<div class="fb-kpis">${kpi('晋级率',pr!=null?pr.toFixed(0)+'%':'—',prC,ppr!=null?'昨 '+ppr.toFixed(1)+'%':'')}${kpi('涨停家数',f.now_zt+' / '+(f.prev_zt!=null?f.prev_zt:'—'),f.prev_zt!=null?(f.now_zt>=f.prev_zt?'up':'down'):'','今 / 昨')}${kpi('核按钮',f.nuke_count,'down','昨涨停今亏')}</div>` +
    `<div class="fb-kpis">${kpi('平均涨幅',(f.avg_pct!=null?f.avg_pct.toFixed(2)+'%':'—'),cls(f.avg_pct))}${kpi('最高板',f.max_boards!=null?f.max_boards+' 板':'—',(f.max_boards!=null&&f.prev_max_boards!=null)?(f.max_boards>f.prev_max_boards?'up':(f.max_boards<f.prev_max_boards?'down':'')):'',f.prev_max_boards!=null?'昨 '+f.prev_max_boards+' 板':'')}${kpi('炸板率',(f.broken_rate!=null?f.broken_rate.toFixed(0)+'%':'—'),'down')}</div>`;
  const nk=(f.nuke_list||[]).map(x=>`<div class="fb-row"><span class="nm">${xq(esc(x.name), x.code)}<span class="cd">${xq(esc(x.code||''), x.code)}</span></span><span class="tag">${esc(x.tag||'核按钮')}</span><span class="pct ${cls(x.pct)}">${pct(x.pct)}</span></div>`).join('');
  $('fb-nuke').innerHTML = `<h4>⚠️ 核按钮/昨涨停今亏（前${(f.nuke_list||[]).length}）</h4>` + (nk||'<div class="empty">今日无核按钮</div>');
  $('fb-note').textContent = f.sentiment_score!=null?`情绪 ${f.sentiment_score}/10`:'';
}

/* ── ⑥.6 题材轮动/回流 ── */
function renderRotation(d){
  const r=d.rotation||{};
  /* 2026-08-18 用户：phase 英文看不懂 → 中文映射（回流候选/今日爆发/退潮中/资金未回流/观察…） */
  const PH_CN={'re-entry-candidate':'回流候选','re-entry':'回流','boom':'今日爆发','cooling':'退潮中','no-reentry':'资金未回流','dead':'已死','watch':'观察','分歧':'分歧'};
  const phCls=p=>['boom','re-entry-candidate','re-entry'].includes(p)?'boom':(p==='分歧'?'fenge':'other');
  /* 预测排序（2026-08-18 用户：结合资金流最好预测）：
     资金趋势优先（流入递增 > 流出收敛 > 中性 > 流出扩大），
     同档按相位活跃度（回流候选/今日爆发优先），再按置信度降序。
     布局保持原样单行（用户 22:06：看不懂两段式）。 */
  const fwRank={'inflow_rising':0,'outflow_shrinking':1,'neutral':2,'outflow_widening':3};
  const phRank={'re-entry-candidate':0,'boom':1,'re-entry':2,'分歧':3,'watch':4,'cooling':5,'dead':6,'no-reentry':7};
  const list=(r.candidates||[]).slice().sort((a,b)=>{
    const fa=fwRank[a.flow_trend]!==undefined?fwRank[a.flow_trend]:2;
    const fb=fwRank[b.flow_trend]!==undefined?fwRank[b.flow_trend]:2;
    if(fa!==fb) return fa-fb;
    const pa=phRank[a.phase]!==undefined?phRank[a.phase]:9;
    const pb=phRank[b.phase]!==undefined?phRank[b.phase]:9;
    if(pa!==pb) return pa-pb;
    return (b.confidence??0)-(a.confidence??0);
  });
  const rows=list.map(c=>`<div class="rt-row">
    <span class="nm">${esc(c.theme||'')}</span>
    <span class="ph ${phCls(c.phase)}">${esc(PH_CN[c.phase]||c.phase||'')}</span>
    <span class="conf">${c.confidence!=null?(c.confidence*100).toFixed(0)+'%':'—'}</span>
  </div>`).join('');
  $('rot-list').innerHTML = rows||'<div class="empty">暂无轮动数据</div>';
}

/* ── ⑥.7 五日情绪周期（ECharts 柱状图·2026-08-18 重构） ── */
let s5Chart=null;
function renderSenti5d(d){
  const s=d.senti5d||{};
  const days=(s.days||[]);
  const el=$('s5d-chart');
  if(!days.length){s5Chart=null;el.innerHTML='<div class="empty">暂无情绪数据</div>';return;}
  if(!s5Chart){el.innerHTML='';s5Chart=echarts.init(el);}
  const last=days[days.length-1];
  $('s5d-sum').textContent = last? `${esc(last.label||'')} ${last.score}/10 · 涨停${last.zt!=null?last.zt:'—'}` : '';
  const colors=days.map(x=>{const sc=x.score||0;return sc>=7?'#ee2500':(sc<=2?'#0a8f3c':'#2f6fd0');});
  s5Chart.setOption({
    tooltip:{trigger:'axis',formatter:ps=>{const i=ps[0].dataIndex;const x=days[i];return `${esc(x.date)}<br/>情绪 <b>${x.score}/10</b> ${esc(x.label||'')}<br/>涨停 ${x.zt!=null?x.zt:'—'} 家`;}},
    grid:{left:6,right:10,top:14,bottom:2,containLabel:true},
    xAxis:{type:'category',data:days.map(x=>x.date),axisLabel:{color:'#9aa0ac',fontSize:10},axisLine:{lineStyle:{color:'#e4e6eb'}}},
    yAxis:{type:'value',min:0,max:10,interval:2,axisLabel:{color:'#9aa0ac',fontSize:10},splitLine:{lineStyle:{color:'#f0f1f4'}}},
    series:[{type:'bar',data:days.map(x=>x.score),barWidth:'52%',
      itemStyle:{borderRadius:[4,4,0,0]},
      label:{show:true,position:'top',fontSize:10,fontWeight:700,color:'#333',formatter:p=>p.value},
      markLine:{symbol:'none',lineStyle:{type:'dashed',width:1},
        label:{fontSize:9,color:'#9aa0ac'},
        data:[{yAxis:7,label:{formatter:'偏强7'}},{yAxis:2,label:{formatter:'冰点2'}}]}}]
  },true);
  // 单独着色：ECharts 柱状按分数组内颜色
  s5Chart.setOption({series:[{data:days.map((x,i)=>({value:x.score,itemStyle:{color:colors[i]}}))}]});
  s5Chart.resize();
}
window.addEventListener('resize',()=>{if(s5Chart)s5Chart.resize();});

/* ── ⑦ 异动/涨停原因（板块可点击·展开选股宝题材成分·2026-08-18） ── */
function renderReasons(d){
  const r=(d.reasons||[]).map(x=>`<div class="rz">
    <div class="rt sec-btn" data-sec="${esc(x.name)}">${esc(x.name)} <span class="z">${x.zt||0} 涨停</span><span class="x" style="margin-left:6px">▸ 成分</span></div>
    <div class="rd">${esc(x.desc||'—')}</div>
    <div class="sec-detail" id="sec-${esc(x.name)}"></div>
  </div>`).join('');
  const nf=(d.newsflash||[]).map(t=>`<div class="rz"><div class="rd">📰 ${esc(t)}</div></div>`).join('');
  $('reasons-list').innerHTML = r+nf+`<div class="rz lhb">⑧ 龙虎榜 · 待接入数据源（预留）</div>`;
}
document.addEventListener('click', function(e){
  const btn = e.target.closest('.sec-btn');
  if(!btn) return;
  const sec = btn.dataset.sec, box = document.getElementById('sec-'+sec);
  if(!box) return;
  if(box.innerHTML){ box.innerHTML=''; btn.querySelector('.x').textContent='▸ 成分'; return; }
  const info = (DATA.sector_map||{})[sec]||{};
  const rows=[];
  if(info.leader && info.leader.name) rows.push({name:info.leader.name,code:info.leader.code,pct:info.leader.pct,tag:'龙头'});
  (info.followers||[]).forEach(f=>rows.push({name:f.name,pct:f.pct,tag:'跟风'}));
  (DATA.pulse||[]).filter(p=>p.sector===sec).forEach(p=>{
    if(!rows.find(r=>r.name===p.name)) rows.push({name:p.name,code:p.code,pct:p.pct,tag:p.type});
  });
  box.innerHTML = rows.map(r=>`<div class="sec-hit"><span class="nm">${xq(esc(r.name), r.code)}<span class="cd">${xq(esc(r.code||''), r.code)}</span></span><span class="tg">${esc(r.tag)}</span><span class="pct ${cls(r.pct)}">${pct(r.pct)}</span></div>`).join('')||'<div class="empty">暂无成分</div>';
  btn.querySelector('.x').textContent='▾ 收起';
});

/* ── 渲染总入口 ── */
function renderAll(d,live){
  d._live=live;
  renderTop(d);renderFlow(d);renderFeedback(d);renderSectors(d);renderRotation(d);renderSenti5d(d);renderWatch(d);renderReasons(d);
  if(!live){renderFilters();renderTicker();}
}

/* ── 前端增量刷新：短线信号 + 重组 pulse + 更新可刷新字段 ── */
function buildSpirit(rows){
  return (rows||[]).map(r=>({
    code:String(r.code||''), name:r.name||r.code, pct:r.pct, price:r.price,
    sector:r.sector||'', type:spiritType(r.type||'拉升'), reason:r.reason||'',
    cap_yi:r.market_cap_yi, amount_yi:r.amount_yi, flow_yi:r.flow_yi,
    resonance:r.resonance, strength:r.strength, buyable:false
  }));
}
function buildPulse(leaders, rising){
  const seen={},rows=[];
  const push=(c,nm,p,pr,sec,is20,bu,bds)=>{
    if(!c||seen[c])return; seen[c]=1;
    const t = p>=(is20?19.9:9.9)?'涨停' : (is20&&p>=10)?'20cm强势' : (p>=(is20?15:7.5))?'冲板':'拉升';
    rows.push({code:c,name:nm,pct:Math.round(p*100)/100,price:pr,type:t,sector:sec,is20:is20,buyable:bu,boards:bds});
  };
  (leaders||[]).forEach(l=>push(String(l.code||''),l.name,l.pct,l.price,l.sector,!!l.is_20cm,!!l.buyable,l.boards||''));
  (rising||[]).forEach(r=>{const c=String(r.code||'');push(c,r.name,r.pct,r.price,r.industry,c.slice(0,3)in{'300':1,'301':1},!r.up,'');});
  return rows.sort((a,b)=>b.pct-a.pct);
}
function buildStats(d){
  return {zt:d.total_zt||0,ld:(d.limit_down||{}).count||0,br:(d.broken||{}).count||0,
    mkt:d.market,seal_rate:(d.sentiment_detail||{}).seal_rate,
    sentiment:d.sentiment,sentiment_score:(d.sentiment_detail||{}).score};
}
function buildFlow(d){
  const sr=d.sector_risk||{};
  return {inflow:(sr.inflow_top||[]).slice(0,8),outflow:(sr.outflow_top||[]).slice(0,8)};
}

/* ── 刷新 ── */
function start(){
  mergeSpirit(buildSpirit((DATA.spirit&&DATA.spirit.signals)||[]));
  mergePulse(DATA.pulse||[]);
  renderAll(DATA,false);
  if(timer)clearInterval(timer);
  timer=setInterval(async()=>{
    try{
      const [rl,rp,ral,rs] = await Promise.all([
        fetch('../cache/pulse_snapshots/latest.json',{cache:'no-store'}),
        fetch('../cache/pulse_snapshots/prev.json',{cache:'no-store'}).catch(()=>null),
        fetch('../cache/pulse_snapshots/auction_live.json',{cache:'no-store'}).catch(()=>null),
        fetch('../cache/short_spirit_signals.json',{cache:'no-store'}).catch(()=>null)
      ]);
      if(!rl.ok)throw new Error(rl.status);
      const nd=await rl.json();
      const prevB = (rp&&rp.ok) ? (await rp.json()).breadth||{} : {};
      if(ral&&ral.ok){ // 候选池/今日收盘实时涨幅（守护链 gen_watch 30s 写入 auction_live）
        const al=await ral.json(); const qm=al.quotes||{};
        DATA.watch_pred=(DATA.watch_pred||[]).map(w=>(w.code in qm)?{...w,pct:qm[w.code]}:w);
        DATA.watch_close=(DATA.watch_close||[]).map(w=>(w.code in qm)?{...w,pct:qm[w.code]}:w);
      }
      if(rs&&rs.ok){ // 短线接口增量（动态联动：新信号入流 fresh 动画）
        const sd=await rs.json();
        if(sd.signals){
          DATA.spirit=DATA.spirit||{}; DATA.spirit.signals=sd.signals;
          const addedS=mergeSpirit(buildSpirit(sd.signals));
          if(addedS>0) renderTicker();
        }
      }
      DATA.index=nd.index||DATA.index;
      DATA.breadth={up:nd.breadth&&nd.breadth.up,down:nd.breadth&&nd.breadth.down,flat:nd.breadth&&nd.breadth.flat,
        up_delta:(nd.breadth&&nd.breadth.up||0)-(prevB.up||0),down_delta:(nd.breadth&&nd.breadth.down||0)-(prevB.down||0)};
      DATA.stats=buildStats(nd);
      DATA.pulse=buildPulse(nd.leaders,nd.rising);
      DATA.flow=buildFlow(nd);
      DATA.reasons=(nd.top5||[]).slice(0,6).map(s=>({name:s.name,zt:s.zt_count,desc:s.desc||''}));
      DATA.newsflash=(nd.newsflash||[]).slice(0,6).map(n=>n.title||'');
      DATA.ts=(nd.ts||'').replace('T',' ').slice(0,19);
      const added = mergePulse(DATA.pulse);
      renderAll(DATA,true);
      if(added>0) console.log('[watch] 新增异动消息', added);
    }catch(e){console.warn('[watch] 本地打开无 fetch，使用内嵌快照',e);}
  },30000);
}
document.addEventListener('DOMContentLoaded',start);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
