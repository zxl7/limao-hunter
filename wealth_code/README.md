# 财富密码 · 狸猫猎手接入模块（wealth_code）

把 quant-review 的"龙头接力 → 竞价验证 → 净值闭环"精华，移植进狸猫猎手盯盘系统，
用狸猫猎手已有的 **涨停池(`yesterday_zt.json`) + 隔日情绪(`feedback_matrix.json`)** 作输入，
补全盯盘系统**一直缺失的"昨日推荐到底赚没赚"的闭环验证与绩效记账**。

## 一、层级结构（做好层级维护）

```
wealth_code/
├── README.md                      # 本文件
├── config/
│   └── wealth_code_rules.md       # 规则中枢：阈值/口径/判定（改一处全局生效）
├── core/                          # 纯算法层（零业务依赖，可单测复用）
│   ├── leader.py                  # ✅龙头三因子（点火+爆发+先发+题材）【忠实移植】
│   ├── dragon_tactics.py          # ✅龙头角色/接力门控库（可选增强）【忠实移植】
│   ├── signal_eval.py             # ✅次日竞价验证 super/expected/pending/reject【抽取移植】
│   ├── nav_ledger.py              # ✅净值核算（40%曲线真源码）【移植·去biying依赖】
│   └── strategy_metrics.py        # ✅策略指标汇总（胜率/收益/按开盘拆分）【移植·去biying依赖】
├── adapters/                      # 输入适配层（对接狸猫猎手现有数据）
│   ├── input_zt.py                # yesterday_zt.json + feedback_matrix.json → 标准涨停池
│   └── input_auction.py           # 次日9:25竞价快照接入 + 测试合成器
├── pipelines/                     # 编排层（任务 SOP）
│   ├── pipeline_eod.py            # 盘后：涨停池→龙头分→接力/观察池（轻量门控·干净重写）
│   └── pipeline_verify.py         # 次日：竞价验证→payload→净值+指标记账
├── scripts/
│   └── run_wealth_code.py         # CLI 入口
└── cache/                         # 产物（候选/净值/指标）
```

设计原则呼应狸猫猎手三层架构：**规则中枢(config) / 任务编排(pipelines) / 脚本能力(core)**。

## 二、移植边界（诚实说明）

| 来源 | 处理方式 | 说明 |
|---|---|---|
| `leader.py` 龙头三因子 | ✅ **逐字忠实移植** | 纯 stdlib，零依赖，直接可用 |
| `dragon_tactics_core.py` | ✅ **忠实移植为库** | 纯算法；其接力门控依赖 zt_analysis 预计算的富字段，默认走轻量版，富字段补齐后可启用 |
| `build_account_nav_ledger.py` | ✅ **移植** | 净值真源码；**去除了对 biying 实时数据的懒加载**，改为显式传入 payload |
| `build_account_strategy_metrics.py` | ✅ **移植** | 指标汇总；同上去除懒加载 |
| `_evaluate_realtime_signal` | ✅ **抽取移植** | 竞价验证从 2770 行文件抽成干净纯函数 |
| `build_stock_research_backtest.py` 全文 | 🚫 **不搬** | 2770 行、耦合 biying/http/config；其"后验回测展示面板"非核心闭环 |
| `zt_analysis.py` 接力池(2619行) | 🔧 **干净重写** | 用 leader 龙头分 + feedback_matrix 情绪代理 + 主板倾斜，替代写死潮汐魔法数字 |
| 正则抽 AI 文本预期 | 🔧 **改 heuristic** | 改由连板数推导预期区间，待 AI 研判层覆盖 |

**核心判断**：quant-review 的"财富密码"精华 = 龙头算法 + 竞价验证 + 净值闭环；
其"死板"重灾区（写死潮汐数字、正则抽文本、静态题材表）正是我们用轻量/AI 逻辑替换的地方。

## 三、数据流（闭环）

```
狸猫猎手盘后:
  record_zt_snapshot → yesterday_zt.json (涨停池)
  feedback_matrix    → feedback_matrix.json (情绪)
        │
        ▼  pipeline_eod（每日盘后/盘前）
  龙头分(leader) + 情绪代理 + 主板倾斜 → 接力池/观察池候选
        │  cache/candidates-YYYY-MM-DD.json
        ▼  次日 09:25
  input_auction 读竞价快照 → signal_eval → open_check(super/expected/pending/reject)
        │  + 隔日收盘价 → next_day 收益
        ▼  pipeline_verify
  backtest_payload → nav_ledger(净值曲线) + strategy_metrics(胜率/收益)
        │
        ▼  反哺
  "每月一票"选股纪律 + 策略验证卡推送
```

## 四、使用

```bash
# 盘后：生成候选
python scripts/run_wealth_code.py eod

# 次日验证（真实竞价快照）
python scripts/run_wealth_code.py verify --quotes /path/auction_quotes-YYYYMMDD.json

# 次日验证（dry-run，无实时数据，仅验证算法与口径）
python scripts/run_wealth_code.py verify --synthesize

# 查看最新净值与指标
python scripts/run_wealth_code.py status
```

> `verify --synthesize` 用候选的 price/pct 反推昨收并确定性合成竞价/收盘价，
> **仅用于验证算法与口径，不表示任何收益预期**。
>
> ⚠️ **成绩数据永久维护**：`cache/` 下的 `account_nav_history.jsonl`（净值账本）、
> `account_strategy_metrics.json`（胜率/收益）、`candidates-*.json`（候选）、`auction_quotes-*.json`
> （竞价快照）是**长期统计的唯一依据，禁止清空/删除**（与用户"每月一票、长期统计"纪律一致）。

## 五、接入盯盘系统的下一步

1. **真实竞价快照**：让 live_signal/intraday_pulse 在 09:25 落 `auction_quotes-YYYYMMDD.json`
   （{code:{prev_close,auction_price,auction_amount_yi}}），`verify` 直接读取。
2. **富字段补齐**（可选）：若要计算 dragon_tactics 严格门控，需补 leaderFactorScore/breakRisk/tide 等
   ——可复用狸猫猎手现有 ma5/feedback 数据近似，不必搬 zt_analysis 全套。
3. **AI 研判层覆盖预期**：用盯盘 AI 直接产出每票 expectation（替代 heuristic 默认值）。
4. **策略验证卡推送**：把 nav_ledger + strategy_metrics 结果包装成飞书卡片（复用 push_card.py）。
5. **自动化**：盘后(eod) + 次日09:30(verify) 两个 automation，复用狸猫猎手 14 任务框架。
