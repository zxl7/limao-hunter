#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据基座单一登记处（数据基座 · 工件 E · 单一参考文档）

这是全系统「数据从哪来、落到哪、叫什么、schema 是什么、是否过期/停更」的
**唯一权威登记处**。serve.py 的 PUBLIC_ROUTES 仍保留为运行源（含审计注释），
本模块与之单向同步：

  - ROUTES          : 文件代理类路由（与 serve.py PUBLIC_ROUTES 的 key 集合一一对应）
  - QUERY_CATALOG   : 结构化查询路由（common.data_gateway 的 3 个查询）
  - meta_for(route) : 返回该路由的治理元数据 + 计算 stale_sec
  - stale_for(route, mtime, now) : 计算是否过期（None=不检查）
  - verify_against(serve_routes) : 与 serve.py 运行源做一致性自检，返回差异列表

设计铁律：
  - 本模块**只读登记**，不发起任何请求、不落盘；是「文档」不是「引擎」。
  - serve.py 启动时调用 verify_against(PUBLIC_ROUTES)；有差异只告警不阻断
    （保留可逆开关，不破坏现有 83+ 路由运行）。
  - 新增/下线数据产物，先在**本模块**登记，再同步 serve.py，避免「源分散」。

stale_sec 由 cadence 推导（retired 强制为 None，已知停更不告警）：
  intraday → 1800s(30min) | daily → 172800s(2d，跨周末容错) | manual/on-demand → None
"""
from __future__ import annotations

from typing import Any, Optional

# cadence → 默认过期阈值（秒）。None = 不检查。
STALE_SEC: dict[str, Optional[int]] = {
    "intraday": 1800,
    "daily": 172800,
    "manual": None,
    "on-demand": None,
}

# ────────────────────────────────────────────────────────────────
# 文件代理路由（与 serve.py PUBLIC_ROUTES 的 key 集合一一对应）
# 每条字段：
#   file / dir(+pattern / dirPattern+file)  定位（与 serve.py 对齐，verify 比对）
#   mode / limit / format / cadence / retired         运行语义（对齐 serve.py）
#   owner / producer / source / schema / validation / degrade / note   治理元数据
# ────────────────────────────────────────────────────────────────
ROUTES: dict[str, dict[str, Any]] = {
    # 一、行情主链路
    "/api/market/latest": {
        "file": "cache/pulse_snapshots/latest.json", "cadence": "intraday",
        "owner": "盘中脉冲", "producer": "scripts/intraday_pulse.py", "source": ["东财快照"],
        "schema": "pulse.snapshot", "validation": "strict", "degrade": "空壳降级渲染",
        "note": "主快照，盘中每 N 秒刷新",
    },
    "/api/market/prev": {
        "file": "cache/pulse_snapshots/prev.json", "cadence": "intraday",
        "owner": "盘中脉冲", "producer": "scripts/intraday_pulse.py", "source": ["东财快照"],
        "schema": "pulse.snapshot", "validation": "strict", "degrade": "空壳降级渲染",
        "note": "上一版快照",
    },
    "/api/market/auction": {
        "file": "cache/pulse_snapshots/auction_live.json", "cadence": "manual", "retired": True,
        "owner": "竞价", "producer": "(no-writer)", "source": ["旧看板遗留"],
        "schema": "auction.quote", "validation": "warn", "degrade": "明确标注已停更·转 /api/decision/auction-history",
        "note": "2026-11-04 审计：无写入方，停在 2026-09-25，真实竞价见 auction-history",
    },
    "/api/market/signals": {
        "file": "cache/short_spirit_signals.json", "cadence": "intraday",
        "owner": "短线精灵", "producer": "scripts/short_spirit.py", "source": ["东财/必盈"],
        "schema": "short_spirit.signals", "validation": "warn", "degrade": "板块共振预警降级",
        "note": "已退化为板块级共振预警",
    },
    "/api/market/relay-effect": {
        "file": "cache/relay_effect.json", "cadence": "intraday",
        "owner": "接力效应", "producer": "scripts/relay_effect.py(inferred)", "source": ["东财"],
        "schema": "relay.effect", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/market/feedback": {
        "file": "cache/feedback_matrix.json", "cadence": "intraday",
        "owner": "涨停反馈", "producer": "scripts/feedback_matrix.py", "source": ["东财"],
        "schema": "feedback.matrix", "validation": "strict", "degrade": "空壳降级",
    },
    "/api/market/candidates": {
        "file": "cache/pulse_snapshots/candidates.json", "cadence": "intraday",
        "owner": "盘中脉冲", "producer": "scripts/intraday_pulse.py", "source": ["东财快照"],
        "schema": "pulse.candidates", "validation": "strict", "degrade": "空壳降级",
    },
    "/api/market/selection-effect": {
        "file": "cache/selection_effect.json", "cadence": "intraday",
        "owner": "候选次日反馈", "producer": "scripts/selection_effect.py(inferred)", "source": ["东财"],
        "schema": "selection.effect", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/flow/live": {
        "file": "cache/pulse_snapshots/sector_fund_pool.json", "cadence": "intraday",
        "owner": "板块资金", "producer": "scripts/intraday_pulse.py", "source": ["东财板块资金"],
        "schema": "flow.sector_pool", "validation": "strict", "degrade": "空壳降级",
    },
    # 二、题材 / 资金
    # ── 2026-11-16 补登记（自检 `check_data_foundation` 报「serve 有、catalog 无」）──
    "/api/review/panel": {
        "file": "cache/review_panel.json", "cadence": "daily",
        "owner": "复盘面板", "producer": "scripts/review_panel.py", "source": ["本地派生"],
        "schema": "review.panel", "validation": "lenient", "degrade": "空块降级",
        "desc": "复盘三块面板（风险/赚钱效应/主线+LLM点评）",
    },
    "/api/review/series": {
        "file": "cache/review_series.json", "cadence": "daily",
        "owner": "复盘面板", "producer": "scripts/review_series.py", "source": ["本地派生"],
        "schema": "review.series", "validation": "lenient", "degrade": "空序列降级",
        "desc": "复盘多日序列（情绪分/涨停/跌停/最高板）",
    },
    "/api/ai/tilt": {
        "file": "config/ai_tilt_state.json", "cadence": "daily",
        "owner": "智能层", "producer": "scripts/train_ai_tilt.py", "source": ["本地派生"],
        "schema": "ai.tilt_state", "validation": "lenient", "degrade": "缺失即不展示",
        "desc": "智能层状态（模型版本/特征维度/验证AUC/迭代记录·只读）",
    },
    "/api/dragon/gene": {
        "file": "cache/dragon_gene.json", "cadence": "daily",
        "owner": "龙头基因", "producer": "scripts/dragon_gene.py", "source": ["本地派生"],
        "schema": "dragon.gene", "validation": "lenient", "degrade": "空池降级",
        "desc": "龙头基因沉淀池（≥4板入池）",
    },
    "/api/rules": {
        "file": "config/rules_registry.json", "cadence": "daily",
        "owner": "判据登记", "producer": "人工维护", "source": ["配置"],
        "schema": "rules.registry", "validation": "strict", "degrade": "缺失即报错",
        "desc": "判据登记表（含开关与回退）",
    },
    "/api/rules/fired": {
        "dir": "cache", "pattern": r"^rules_fired_\d{4}-\d{2}-\d{2}\.json$", "mode": "latest",
        "cadence": "daily", "owner": "判据体检", "producer": "scripts/rules_fired.py",
        "source": ["本地派生"], "schema": "rules.fired", "validation": "lenient",
        "degrade": "缺失即不展示", "desc": "判据命中日报",
    },
    "/api/decision/pool-ledger": {
        "dir": "cache/pool_ledger", "pattern": r"^\d{4}-\d{2}-\d{2}\.json$", "mode": "all",
        "limit": 20, "cadence": "daily", "owner": "池内台账",
        "producer": "wealth_code/core/pool_ledger.py", "source": ["本地派生"],
        "schema": "pool.ledger", "validation": "lenient", "degrade": "空台账降级",
        "desc": "池内台账（收盘评估）",
    },
    "/api/research/backtest": {
        "file": "cache/research/backtest_summary.json", "cadence": "manual",
        "owner": "研究回测", "producer": "scripts/research_backtest.py", "source": ["本地派生"],
        "schema": "research.backtest", "validation": "lenient", "degrade": "缺失即不展示",
        "desc": "回测战绩（三口径研究快照，只读）",
    },
    "/api/ai/nav-t1": {
        "file": "wealth_code/cache/ai_nav_history_t1.jsonl", "format": "jsonl", "cadence": "daily",
        "owner": "AI 净值", "producer": "scripts/build_account_nav_ledger.py", "source": ["本地派生"],
        "schema": "ai.nav_t1", "validation": "lenient", "degrade": "缺失即不展示",
        "desc": "AI 轨 T+1 口径净值",
    },
    "/api/theme/rotation": {
        "file": "cache/theme_rotation.json", "cadence": "intraday",
        "owner": "题材轮动", "producer": "scripts/theme_rotation.py", "source": ["东财题材"],
        "schema": "theme.rotation", "validation": "strict", "degrade": "空壳降级",
    },
    "/api/theme/series": {
        "file": "cache/theme_series.json", "cadence": "daily",
        "owner": "题材序列", "producer": "common/theme_series.py(inferred)", "source": ["东财题材"],
        "schema": "theme.series", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/theme/sequence": {
        "file": "cache/theme_sequence.json", "cadence": "manual", "retired": True,
        "owner": "题材序列", "producer": "common/theme_series.py(inferred)", "source": ["东财题材"],
        "schema": "theme.sequence", "validation": "warn", "degrade": "明确标注已停更·仅留档",
        "note": "与 serve.py 对齐：已 retired（留档）",
    },
    "/api/flow/history": {
        "dir": "cache/sector_flow_history", "pattern": r"^sector_flow_\d{4}-\d{2}-\d{2}\.json$",
        "mode": "all", "cadence": "daily",
        "owner": "板块资金", "producer": "scripts/sector_flow.py(inferred)", "source": ["东财板块资金"],
        "schema": "flow.history", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/theme/weekly-map": {
        "file": "cache/weekly_zt_map.json", "cadence": "daily",
        "owner": "周涨停地图", "producer": "scripts/weekly_zt_map.py(inferred)", "source": ["东财"],
        "schema": "theme.weekly_map", "validation": "warn", "degrade": "空壳降级",
    },
    # 三、板块
    "/api/board/core": {
        "dir": "cache/board_core", "pattern": r"^board_core_.+\.json$", "mode": "all",
        "cadence": "manual", "retired": True,
        "owner": "板块核心票", "producer": "(no-writer)", "source": ["旧看板遗留"],
        "schema": "board.core", "validation": "warn", "degrade": "明确标注已停更·仅留档",
        "note": "2026-11-04 审计：全部停在 2026-09-09，恢复产出后改回 daily",
    },
    "/api/board/leader-daily": {
        "dir": "cache/board_leader_daily", "pattern": r"^leader_\d{4}-\d{2}-\d{2}\.json$",
        "mode": "all", "limit": 30, "cadence": "daily",
        "owner": "连板日观察", "producer": "scripts/board_leader_daily.py", "source": ["东财/必盈"],
        "schema": "board.leader_daily", "validation": "strict", "degrade": "空壳降级",
    },
    # 四、连板晋级量化
    "/api/promotion/summary": {
        "file": "cache/promo/promo_summary.json", "cadence": "daily",
        "owner": "晋级率", "producer": "wealth_code/core/daily_screen.py(inferred)", "source": ["东财"],
        "schema": "promo.summary", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/promotion/daily": {
        "dir": "cache/promo", "pattern": r"^promo_\d{8}\.json$", "mode": "all", "limit": 10,
        "cadence": "daily",
        "owner": "晋级事件", "producer": "wealth_code/core/daily_screen.py(inferred)", "source": ["东财"],
        "schema": "promo.daily", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/promo/backtest": {
        "dir": "cache/promo", "pattern": r"^backtest_.+[.]json$", "mode": "all", "cadence": "daily",
        "owner": "晋级回测", "producer": "wealth_code/core/backtest.py(inferred)", "source": ["历史回测"],
        "schema": "promo.backtest", "validation": "warn", "degrade": "空壳降级",
    },
    # 五、股性基因
    "/api/gene/summary": {
        "file": "cache/zt_genes/summary.json", "cadence": "daily",
        "owner": "涨停股性", "producer": "scripts/zt_gene*.py(inferred)", "source": ["东财"],
        "schema": "gene.summary", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/gene/detail": {
        "dir": "cache/zt_genes", "pattern": r"^gene_\d{6}\.json$", "mode": "all", "cadence": "daily",
        "owner": "个股基因", "producer": "scripts/zt_gene*.py(inferred)", "source": ["东财"],
        "schema": "gene.detail", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/gene/hist": {
        "dir": "cache/zt_genes", "pattern": r"^hist_\d{6}\.json$", "mode": "all", "cadence": "daily",
        "owner": "个股历史基因", "producer": "scripts/zt_gene*.py(inferred)", "source": ["东财"],
        "schema": "gene.hist", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/gene/pool": {
        "dir": "cache/zt_genes", "pattern": r"^pool_\d{8}\.json$", "mode": "latest", "cadence": "daily",
        "owner": "涨停基因池", "producer": "scripts/zt_gene*.py(inferred)", "source": ["东财"],
        "schema": "gene.pool", "validation": "warn", "degrade": "空壳降级",
    },
    # 六、情绪周期
    "/api/emotion/summary5d": {
        "file": "cache/short_term/summary_5d.json", "cadence": "daily",
        "owner": "5日情绪", "producer": "scripts/gen_sentiment_summary.py", "source": ["东财"],
        "schema": "emotion.summary5d", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/emotion/zt-history": {
        "dir": "cache/zt_history", "pattern": r"^zt_\d{4}-\d{2}-\d{2}\.json$", "mode": "all", "cadence": "daily",
        "owner": "涨停时序", "producer": "scripts/zt_history.py(inferred)", "source": ["东财"],
        "schema": "emotion.zt_history", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/emotion/memory": {
        "dir": "cache", "pattern": r"^market_memory_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "all",
        "limit": 20, "cadence": "daily",
        "owner": "情绪记忆", "producer": "scripts/emotion_memory.py(inferred)", "source": ["东财"],
        "schema": "emotion.memory", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/emotion/daily": {
        "dir": "cache/short_term/daily", "pattern": r"^emotion_\d{4}-\d{2}-\d{2}\.json$", "mode": "all",
        "cadence": "daily",
        "owner": "情绪日档", "producer": "scripts/gen_sentiment_summary.py", "source": ["东财"],
        "schema": "emotion.daily", "validation": "warn", "degrade": "空壳降级",
        "note": "周期温度历史",
    },
    "/api/zt/yesterday": {
        "file": "cache/yesterday_zt.json", "cadence": "daily",
        "owner": "昨日涨停", "producer": "scripts/yesterday_zt.py(inferred)", "source": ["东财"],
        "schema": "zt.yesterday", "validation": "warn", "degrade": "空壳降级",
    },
    # 七、数据底座 / 聚合
    "/api/data/exit-peaks": {
        "dir": "cache", "pattern": r"^intraday_exit_peaks_\d{8}\.json$", "mode": "latest", "cadence": "intraday",
        "owner": "日内峰值", "producer": "scripts/intraday_exit_peaks.py", "source": ["东财"],
        "schema": "data.exit_peaks", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/data/theme-snapshot": {
        "dir": "cache/theme_snapshot", "pattern": r"^theme_\d{4}-\d{2}-\d{2}\.json$", "mode": "all",
        "limit": 20, "cadence": "daily",
        "owner": "题材快照", "producer": "scripts/theme_snapshot.py(inferred)", "source": ["东财"],
        "schema": "data.theme_snapshot", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/data/market-snapshot": {
        "dir": "cache/snapshot", "pattern": r"^\d{4}-\d{2}-\d{2}\.json$", "mode": "latest", "cadence": "daily",
        "owner": "聚合数据源", "producer": "scripts/market_snapshot.py(inferred)", "source": ["全源聚合"],
        "schema": "data.market_snapshot", "validation": "strict", "degrade": "空壳降级",
        "note": "2026-11-04 新增：当日 8 section + health 守门 + 维度状态单一事实源（私有）",
    },
    "/api/data/top5-history": {
        "dir": "cache/top5_history", "pattern": r"^top5_\d{4}-\d{2}-\d{2}\.json$", "mode": "all",
        "limit": 20, "cadence": "daily",
        "owner": "Top5题材", "producer": "scripts/top5_archive.py(inferred)", "source": ["选股宝"],
        "schema": "data.top5_history", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/intraday/timeline": {
        "dir": "cache", "pattern": r"^intraday_timeline_\d{8}\.jsonl$", "mode": "all", "limit": 4,
        "lines": 1200, "format": "jsonl", "cadence": "intraday",
        "owner": "盘中时间轴", "producer": "scripts/intraday_timeline.py(inferred)", "source": ["东财"],
        "schema": "intraday.timeline", "validation": "warn", "degrade": "空壳降级",
    },
    # 八、龙虎榜
    "/api/lhb/tencent": {
        "dir": "cache", "pattern": r"^lhb_\d{8}\.json$", "mode": "all", "limit": 10,
        "cadence": "manual", "retired": True,
        "owner": "龙虎榜", "producer": "(no-writer)", "source": ["腾讯"],
        "schema": "lhb.tencent", "validation": "warn", "degrade": "明确标注已停更·仅留档",
        "note": "2026-11-04 审计：停在 2026-09-16",
    },
    "/api/lhb/hithink": {
        "dir": "cache", "pattern": r"^hithink_lhb_\d{8}\.json$", "mode": "all", "limit": 10,
        "cadence": "manual", "retired": True,
        "owner": "龙虎榜", "producer": "(no-writer)", "source": ["同花顺"],
        "schema": "lhb.hithink", "validation": "warn", "degrade": "明确标注已停更·仅留档",
        "note": "2026-11-04 审计：停在 2026-10-19",
    },
    # 九、同花顺专业数据
    "/api/hithink/ladder": {
        "file": "cache/hithink_ladder.json", "cadence": "daily",
        "owner": "连板天梯", "producer": "common/hithink_api.py(inferred)", "source": ["同花顺"],
        "schema": "hithink.ladder", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/hithink/zt": {
        "dir": "cache", "pattern": r"^hithink_zt_\d{8}\.json$", "mode": "all", "limit": 10, "cadence": "daily",
        "owner": "同花顺涨停池", "producer": "common/hithink_api.py(inferred)", "source": ["同花顺"],
        "schema": "hithink.zt", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/hithink/zt-diff": {
        "dir": "cache", "pattern": r"^hithink_zt_diff_\d{8}\.json$", "mode": "all", "limit": 10, "cadence": "daily",
        "owner": "涨停池双源校验", "producer": "common/hithink_api.py(inferred)", "source": ["同花顺/东财"],
        "schema": "hithink.zt_diff", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/hithink/auction": {
        "dir": "cache", "pattern": r"^hithink_auction_\d{8}\.json$", "mode": "all", "limit": 10, "cadence": "daily",
        "owner": "竞价风向标", "producer": "common/hithink_api.py(inferred)", "source": ["同花顺"],
        "schema": "hithink.auction", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/hithink/anomaly": {
        "dir": "cache", "pattern": r"^hithink_anomaly_\d{8}\.json$", "mode": "latest", "cadence": "daily",
        "owner": "异动原因", "producer": "common/hithink_api.py(inferred)", "source": ["同花顺"],
        "schema": "hithink.anomaly", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/hithink/valuation": {
        "dir": "cache", "pattern": r"^hithink_valuation_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "latest", "cadence": "daily",
        "owner": "同花顺估值", "producer": "common/hithink_api.py(inferred)", "source": ["同花顺"],
        "schema": "hithink.valuation", "validation": "warn", "degrade": "空壳降级",
    },
    # 十、机构投研
    "/api/institution/pool": {
        "dir": "cache", "pattern": r"^institution_pool_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "all",
        "limit": 10, "cadence": "daily",
        "owner": "机构池", "producer": "scripts/institution_pool.py(inferred)", "source": ["选股宝/ gangtise"],
        "schema": "institution.pool", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/institution/morning": {
        "file": "cache/gangtise_morning_research.json", "cadence": "daily",
        "owner": "机构早盘", "producer": "scripts/gangtise_morning.py", "source": ["gangtise"],
        "schema": "institution.morning", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/institution/evening": {
        "file": "cache/gangtise_evening_research.json", "cadence": "daily",
        "owner": "机构晚报", "producer": "scripts/gangtise_evening.py", "source": ["gangtise"],
        "schema": "institution.evening", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/institution/weekend": {
        "file": "cache/gangtise_weekend_0913.json", "cadence": "manual",
        "owner": "机构周末", "producer": "scripts/gangtise_weekend.py", "source": ["gangtise"],
        "schema": "institution.weekend", "validation": "warn", "degrade": "空壳降级",
    },
    # 十一、题材与消息史
    "/api/eruption/history": {
        "dir": "cache/eruption_history", "pattern": r"^eruption_.+\.json$", "mode": "all", "cadence": "daily",
        "owner": "题材爆发史", "producer": "scripts/eruption_history.py(inferred)", "source": ["东财"],
        "schema": "eruption.history", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/newsflash/history": {
        "dir": "cache/newsflash_history", "pattern": r"^daily_.+\.json$", "mode": "all", "cadence": "daily",
        "owner": "消息史", "producer": "scripts/newsflash_history.py(inferred)", "source": ["财联社"],
        "schema": "newsflash.history", "validation": "warn", "degrade": "空壳降级",
    },
    # 十二、尾盘潜伏
    "/api/ambush/pullback/weighted-pnl": {
        "file": "cache/ambush_pullback_daily_pnl.jsonl", "format": "jsonl", "cadence": "daily",
        "owner": "尾盘潜伏", "producer": "wealth_code/core/ambush_pullback.py", "source": ["东财"],
        "schema": "ambush.weighted_pnl", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/ambush/pullback/signals": {
        "file": "cache/ambush_pullback_signal_log.jsonl", "format": "jsonl", "cadence": "daily",
        "owner": "尾盘潜伏", "producer": "wealth_code/core/ambush_pullback.py", "source": ["东财"],
        "schema": "ambush.signals", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/ambush/pullback/settlements": {
        "file": "cache/ambush_pullback_settlement_log.jsonl", "format": "jsonl", "cadence": "daily",
        "owner": "尾盘潜伏", "producer": "wealth_code/core/ambush_pullback.py", "source": ["东财"],
        "schema": "ambush.settlements", "validation": "warn", "degrade": "空壳降级",
    },
    # 十三、决策（私有）
    "/api/decision/candidates": {
        "dir": "wealth_code/cache", "pattern": r"^candidates-\d{4}-\d{2}-\d{2}\.json$", "mode": "all",
        "limit": 3, "cadence": "daily",
        "owner": "决策候选", "producer": "wealth_code/core/decision_outlook.py(inferred)", "source": ["东财"],
        "schema": "decision.candidates", "validation": "strict", "degrade": "需鉴权·空壳降级",
    },
    "/api/decision/latest": {
        "dir": "wealth_code/cache", "pattern": r"^candidates-\d{4}-\d{2}-\d{2}\.json$", "mode": "latest", "cadence": "daily",
        "owner": "决策候选", "producer": "wealth_code/core/decision_outlook.py(inferred)", "source": ["东财"],
        "schema": "decision.candidates", "validation": "strict", "degrade": "需鉴权·空壳降级",
    },
    "/api/decision/execution-audit": {
        "file": "wealth_code/cache/execution_audit.json", "cadence": "daily",
        "owner": "执行审计", "producer": "wealth_code/core/execution_audit.py(inferred)", "source": ["内部"],
        "schema": "decision.execution_audit", "validation": "strict", "degrade": "需鉴权·空壳降级",
    },
    "/api/decision/auction-history": {
        "dir": "wealth_code/cache", "pattern": r"^auction_quotes-\d{4}-\d{2}-\d{2}\.json$", "mode": "all",
        "limit": 10, "cadence": "daily",
        "owner": "竞价报价", "producer": "scripts/intraday_pulse.py(inferred)", "source": ["必盈"],
        "schema": "decision.auction_history", "validation": "strict", "degrade": "需鉴权·空壳降级",
    },
    "/api/decision/archive": {
        "dir": "cache/decision_log", "dirPattern": r"^\d{4}-\d{2}-\d{2}$", "file": "candidates.json",
        "mode": "all", "limit": 3, "cadence": "daily",
        "owner": "决策黑匣子", "producer": "scripts/decision_log.py(inferred)", "source": ["内部"],
        "schema": "decision.archive", "validation": "strict", "degrade": "需鉴权·空壳降级",
    },
    "/api/decision/zt-pool": {
        "dir": "cache/decision_log", "dirPattern": r"^\d{4}-\d{2}-\d{2}$", "file": "zt_pool.json",
        "mode": "latest", "cadence": "daily",
        "owner": "决策涨停池", "producer": "scripts/decision_log.py(inferred)", "source": ["内部"],
        "schema": "decision.zt_pool", "validation": "strict", "degrade": "需鉴权·空壳降级",
    },
    "/api/decision/manifest": {
        "dir": "cache/decision_log", "dirPattern": r"^\d{4}-\d{2}-\d{2}$", "file": "manifest.json",
        "mode": "latest", "cadence": "daily",
        "owner": "存档清单", "producer": "scripts/decision_log.py(inferred)", "source": ["内部"],
        "schema": "decision.manifest", "validation": "strict", "degrade": "需鉴权·空壳降级",
    },
    "/api/decision/pool-call": {
        "dir": "wealth_code/cache", "pattern": r"^pool_call-\d{4}-\d{2}-\d{2}\.json$", "mode": "all",
        "limit": 3, "cadence": "daily",
        "owner": "池内裁决", "producer": "wealth_code/core/pool_call.py(inferred)", "source": ["内部"],
        "schema": "decision.pool_call", "validation": "strict", "degrade": "需鉴权·空壳降级",
    },
    "/api/decision/screen": {
        "dir": "cache/daily_screen", "pattern": r"^screen_\d{4}-\d{2}-\d{2}\.json$", "mode": "all",
        "limit": 5, "cadence": "daily",
        "owner": "全量过筛", "producer": "scripts/daily_screen.py(inferred)", "source": ["东财"],
        "schema": "decision.screen", "validation": "strict", "degrade": "需鉴权·空壳降级",
    },
    "/api/decision/sector-gap-trace": {
        "file": "cache/sector_gap_trace/daily_trace.json", "cadence": "daily",
        "owner": "竞价三环影子", "producer": "scripts/sector_gap_daily_trace.py",
        "source": ["09:25竞价快照", "选股宝题材快照", "前复权日K"],
        "schema": "sector_gap_daily_trace_v1", "validation": "loose", "degrade": "需鉴权·空壳降级",
        "note": "只读研究留痕：linked_would_cut 恒不改裁决；两套收益分列，不并入账户战绩",
    },
    # 十四、AI / 账户（私有）
    "/api/ai/view": {
        "dir": "cache", "pattern": r"^ai_view_\d{4}-\d{2}-\d{2}\.json$", "mode": "all", "limit": 20, "cadence": "daily",
        "owner": "AI判断", "producer": "wealth_code/core/expectation_ai.py(inferred)", "source": ["内部AI"],
        "schema": "ai.view", "validation": "strict", "degrade": "需鉴权·空壳降级",
    },
    "/api/ai/decisions": {
        "file": "wealth_code/cache/ai_decisions.jsonl", "format": "jsonl", "cadence": "daily",
        "owner": "AI判断台账", "producer": "wealth_code/core/expectation_ai.py(inferred)", "source": ["内部AI"],
        "schema": "ai.decisions", "validation": "strict", "degrade": "需鉴权·空壳降级",
    },
    "/api/ai/nav": {
        "file": "wealth_code/cache/ai_nav_history.jsonl", "format": "jsonl", "cadence": "daily",
        "owner": "AI轨净值", "producer": "wealth_code/core/nav_ledger.py", "source": ["内部"],
        "schema": "ai.nav", "validation": "strict", "degrade": "需鉴权·空壳降级",
    },
    "/api/account/nav": {
        "file": "wealth_code/cache/account_nav_history.jsonl", "format": "jsonl", "cadence": "daily",
        "owner": "净值账本", "producer": "wealth_code/core/nav_ledger.py", "source": ["内部"],
        "schema": "account.nav", "validation": "strict", "degrade": "需鉴权·空壳降级",
    },
    "/api/account/metrics": {
        "file": "wealth_code/cache/account_strategy_metrics.json", "cadence": "daily",
        "owner": "策略指标", "producer": "wealth_code/core/nav_ledger.py", "source": ["内部"],
        "schema": "account.metrics", "validation": "strict", "degrade": "需鉴权·空壳降级",
    },
    # 十五、盘面补充
    "/api/carrier/state": {
        "file": "cache/carrier_state.json", "cadence": "daily",
        "owner": "载体证伪", "producer": "scripts/carrier_state.py(inferred)", "source": ["东财"],
        "schema": "carrier.state", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/ma5/status": {
        "file": "cache/ma5_status.json", "cadence": "daily",
        "owner": "五日线", "producer": "scripts/ma5_status.py(inferred)", "source": ["东财"],
        "schema": "ma5.status", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/shadow/relay": {
        "dir": "cache/shadow", "pattern": r"^relay_shadow_.+\.json$", "mode": "latest", "cadence": "daily",
        "owner": "接力影子", "producer": "wealth_code/core/relay_score.py(inferred)", "source": ["东财"],
        "schema": "shadow.relay", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/risk/watch": {
        "dir": "cache", "pattern": r"^risk_watch_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "all",
        "limit": 10, "cadence": "daily",
        "owner": "风险监控", "producer": "scripts/risk_watch.py(inferred)", "source": ["内部"],
        "schema": "risk.watch", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/backtest/report": {
        "dir": "cache", "pattern": r"^backtest_report_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "all",
        "limit": 10, "cadence": "daily",
        "owner": "推测回测", "producer": "scripts/backtest_report.py(inferred)", "source": ["历史"],
        "schema": "backtest.report", "validation": "warn", "degrade": "空壳降级",
    },
    # 十六、卡片存档
    "/api/card/fupan": {
        "dir": "cache", "pattern": r"^fupan_card_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "all",
        "limit": 10, "cadence": "daily",
        "owner": "复盘卡", "producer": "scripts/fupan_card.py(inferred)", "source": ["内部"],
        "schema": "card.fupan", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/card/panqian": {
        "dir": "wealth_code/cache", "pattern": r"^panqian_card_\d+\.json$", "mode": "all",
        "limit": 10, "cadence": "daily",
        "owner": "盘前卡", "producer": "wealth_code/core/front_core.py(inferred)", "source": ["内部"],
        "schema": "card.panqian", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/card/ambush": {
        "dir": "wealth_code/cache", "pattern": r"^ambush_card_\d+\.json$", "mode": "all",
        "limit": 10, "cadence": "daily",
        "owner": "埋伏卡", "producer": "wealth_code/core/ambush_pullback.py(inferred)", "source": ["内部"],
        "schema": "card.ambush", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/card/lhb": {
        "dir": "cache", "pattern": r"^lhb_card_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "all",
        "limit": 10, "cadence": "daily",
        "owner": "龙虎榜卡", "producer": "scripts/lhb_card.py(inferred)", "source": ["内部"],
        "schema": "card.lhb", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/card/mixed": {
        "dir": "wealth_code/cache", "pattern": r"^card_\d{8}_\d{6}\.json$", "mode": "all",
        "limit": 12, "cadence": "daily",
        "owner": "混合卡", "producer": "wealth_code/core/front_core.py(inferred)", "source": ["内部"],
        "schema": "card.mixed", "validation": "warn", "degrade": "空壳降级",
    },
    # 十七、推送 / 审计 / 系统
    "/api/pulse/windows": {
        "dir": "cache/pulse_windows", "pattern": r"^window_\d{8}_\d{4}\.json$", "mode": "all",
        "limit": 40, "cadence": "manual", "retired": True,
        "owner": "脉冲窗口", "producer": "(no-writer)", "source": ["旧看板遗留"],
        "schema": "pulse.windows", "validation": "warn", "degrade": "明确标注已停更·仅留档",
        "note": "2026-11-04 审计：已停更",
    },
    "/api/pulse/pushed-signals": {
        "dir": "cache", "pattern": r"^pushed_signals_\d+\.json$", "mode": "all", "limit": 5, "cadence": "daily",
        "owner": "推送信号", "producer": "scripts/push_card.py(inferred)", "source": ["内部"],
        "schema": "pulse.pushed_signals", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/audit/log": {
        "file": "cache/audit.jsonl", "format": "jsonl", "cadence": "intraday",
        "owner": "推送审计", "producer": "scripts/push_card.py", "source": ["内部"],
        "schema": "audit.log", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/push/dedup": {
        "file": "cache/push_dedup.json", "cadence": "daily",
        "owner": "推送去重", "producer": "scripts/push_card.py", "source": ["内部"],
        "schema": "push.dedup", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/system/tasks": {
        "file": "config/tasks.yaml", "format": "text", "cadence": "daily",
        "owner": "自动化台账", "producer": "config/tasks.yaml", "source": ["WorkBuddy自动化"],
        "schema": "system.tasks", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/system/learning": {
        "file": "config/learning_config.json", "cadence": "daily",
        "owner": "自学习配置", "producer": "config/learning_config.json", "source": ["内部"],
        "schema": "system.learning", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/system/short-spirit": {
        "file": "config/short_spirit.json", "cadence": "daily",
        "owner": "短线精灵配置", "producer": "config/short_spirit.json", "source": ["内部"],
        "schema": "system.short_spirit", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/system/rules": {
        "dir": "wealth_code/config", "pattern": r"^.+[.]json$", "mode": "all", "cadence": "daily",
        "owner": "策略规则", "producer": "wealth_code/config/*.json", "source": ["内部"],
        "schema": "system.rules", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/system/engine": {
        "dir": "cache/pulse_snapshots",
        "pattern": r"^(boost_state|pulse_pending|sector_risk_cache|sector_fund_pool)[.]json$",
        "mode": "all", "cadence": "intraday",
        "owner": "盘中引擎", "producer": "scripts/intraday_pulse.py", "source": ["东财"],
        "schema": "system.engine", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/system/backups": {
        "dir": "cache/backups",
        "pattern": r"^(nav_|ai_decisions_|learning_rollback_)[^/]*[.](json|jsonl)$",
        "mode": "all", "limit": 20, "cadence": "daily",
        "owner": "系统备份", "producer": "(whitelist)", "source": ["内部"],
        "schema": "system.backups", "validation": "warn", "degrade": "空壳降级",
    },
    "/api/system/archive-index": {
        "file": "cache/decision_log/index.jsonl", "format": "jsonl", "cadence": "daily",
        "owner": "决策索引", "producer": "scripts/decision_log.py(inferred)", "source": ["内部"],
        "schema": "system.archive_index", "validation": "strict", "degrade": "空壳降级",
    },
}

# ────────────────────────────────────────────────────────────────
# 结构化查询路由（common.data_gateway，不走 PUBLIC_ROUTES 文件代理）
# ────────────────────────────────────────────────────────────────
QUERY_CATALOG: dict[str, dict[str, Any]] = {
    "/api/data/stock-registry": {
        "cadence": "on-demand", "owner": "数据底座", "producer": "common/stock_registry.py",
        "source": ["底库"], "schema": "query.stock_registry", "validation": "strict",
        "degrade": "found=false 降级", "note": "单票事实底库投影，仅接受 code",
    },
    "/api/data/market-daily": {
        "cadence": "on-demand", "owner": "数据底座", "producer": "common/market_daily_store.py",
        "source": ["底库日K"], "schema": "query.market_daily", "validation": "strict",
        "degrade": "空序列降级", "note": "单票近月日K，limit≤20",
    },
    "/api/data/stock-profile": {
        "cadence": "on-demand", "owner": "数据底座", "producer": "common/stock_profile.py",
        "source": ["跨源聚合"], "schema": "query.stock_profile", "validation": "strict",
        "degrade": "跨维 status 标注", "note": "跨源聚合画像，每维带 status/asof",
    },
}

_ALL = {**ROUTES, **QUERY_CATALOG}


def meta_for(route: str) -> Optional[dict[str, Any]]:
    """返回某路由的治理元数据（含计算后的 stale_sec）。未知路由返回 None。"""
    rec = _ALL.get(route)
    if rec is None:
        return None
    cadence = rec.get("cadence", "manual")
    # retired / manual / on-demand 不检查过期
    stale_sec = None if rec.get("retired") else STALE_SEC.get(cadence)
    out = dict(rec)
    out["route"] = route
    out["stale_sec"] = stale_sec
    return out


def stale_for(route: str, mtime_ts: float, now: Optional[float] = None) -> tuple[Optional[bool], Optional[int]]:
    """判断路由产物是否过期。

    返回 (is_stale, age_sec)：
      - is_stale=None 表示不检查（retired / manual / on-demand）
      - is_stale=True  表示 age_sec 超过 stale_sec 阈值
    """
    import time

    meta = meta_for(route)
    if meta is None or meta["stale_sec"] is None:
        return None, None
    now_ts = time.time() if now is None else now
    age = int(now_ts - mtime_ts)
    return age > meta["stale_sec"], age


def verify_against(serve_routes: dict[str, dict[str, Any]]) -> list[str]:
    """与 serve.py 运行源做一致性自检，返回差异字符串列表（空=一致）。

    比对维度：route 集合 + cadence + retired + 定位（file 或 dir+pattern）。
    mode/limit/format 等运行细节以 serve.py 为准，不强制比对，避免脆弱。
    """
    diffs: list[str] = []
    cat_keys = set(ROUTES.keys())
    serve_keys = set(serve_routes.keys())

    for r in serve_keys - cat_keys:
        diffs.append(f"[缺失登记] serve 有但 data_catalog 无: {r}")
    for r in cat_keys - serve_keys:
        diffs.append(f"[多余登记] data_catalog 有但 serve 无: {r}")

    for r in serve_keys & cat_keys:
        s = serve_routes[r]
        c = ROUTES[r]
        if s.get("cadence") != c.get("cadence"):
            diffs.append(f"[cadence 不一致] {r}: serve={s.get('cadence')} catalog={c.get('cadence')}")
        if bool(s.get("retired")) != bool(c.get("retired")):
            diffs.append(f"[retired 不一致] {r}: serve={s.get('retired')} catalog={c.get('retired')}")
        s_loc = s.get("file") or s.get("dir")
        c_loc = c.get("file") or c.get("dir")
        if s_loc != c_loc:
            diffs.append(f"[定位不一致] {r}: serve={s_loc} catalog={c_loc}")
    return diffs


def routes_by_cadence(cadence: str) -> list[str]:
    """返回某 cadence 下的全部路由（用于调度编排/告警）。"""
    return [r for r, m in ROUTES.items() if m.get("cadence") == cadence]


def retired_routes() -> list[str]:
    """返回全部已停更（retired）路由。"""
    return [r for r, m in ROUTES.items() if m.get("retired")]
