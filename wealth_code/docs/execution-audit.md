# 财富密码执行审计

执行审计把“选中了什么”和“实际形成了什么收益”分开。它不改候选评分、AI 预案、账户净值或终端现有接口，只读取既有冻结件并生成独立报告。

## 数据血缘

```text
盘中采集 / 涨停池 / 题材与资金
        │
        ├─ feedback_matrix.json ──> input_zt.load_environment
        │                                  │
        │                                  ├─ pipeline_eod / quant_score / select_relay_watch
        │                                  │       └─ wealth_code/cache/candidates-<推荐日>.json
        │                                  │
        │                                  └─ pipeline_verify 的机械竞价参考
        │
AI 盘后预案 ──> ai_decisions.jsonl ──> 9:25 pool_call-<执行日>.json
竞价冻结     ──> auction_quotes-<执行日>.json
        │
        └─ pipeline_verify / ai_caliber ──> account_nav_history.jsonl
                                             └─ account_strategy_metrics.json
```

终端只读猎手产物：`src/shared/api/routes.ts` 定义文件路由，TanStack Query 拉取后交给页面渲染。终端没有下单接口，不能把盘中候选或卡片分数显示成真实成交。

## 评分与裁决边界

| 层 | 真实输入 | 是否决定收益账本 |
| --- | --- | --- |
| `feedback_matrix` | 晋级、溢价、核按钮、断板、指数、广度等 | 间接：提供 `regime` 与环境因子 |
| `gen_sentiment_summary` | 日级情绪序列、MA3/MA5/MA10 | 否，周期归纳与展示 |
| `leader` / `quant_score` | 龙头、容量、梯队、题材、环境、风险、历史晋级 | 决定候选排序与接力/观察池 |
| `dimension_score` / `relay_score` | 现有字段派生的评分面板 | 当前不是 `pipeline_eod` 的生产入池裁决 |
| `signal_eval` | 竞价涨幅、量能、孤军承接、冰点 | 机械参考；AI 口径开启时不直接决定账户收益 |
| `ai_caliber` / `pool_call` | 盘后预案、池内 9:25 裁决、预期区间、5%硬线、量能 | 是，当前 AI 口径下决定实际入场 |
| `nav_ledger` | `pipeline_verify` 的已入场且收益已覆盖记录 | 是，唯一实际成交与收益来源 |

冰点期机械买点会被标为 `hit`；AI 若仍放行，审计报告会逐票标记，不能被普通候选命中统计掩盖。

## 运行

```bash
# 只读核验，不落盘
python3 wealth_code/scripts/execution_audit.py --dry-run

# 生成独立审计产物
python3 wealth_code/scripts/execution_audit.py
```

产物位于 `wealth_code/cache/execution_audit.json` 与 `execution_audit.md`。每个批次含候选冻结时间、执行日、AI/机械结果、池内裁决、竞价价格与时间、账户入场、毛收益及缺失原因。

执行日只从已落盘的竞价、账户和完整涨停归档组成的交易日历推导；没有证据时保留为空，绝不以“跳过周末”猜测法定节假日后的交易日。

收益标签固定为“竞价至当日收盘毛收益”。现有账本未冻结收盘价格，审计不会根据收益反推价格；手续费、滑点和未成交冲击也不计入该指标。
