# 退役脚本归档 · 2026-10

> 来源：`架构/数据血缘与行情口径审计_2026-10-14.md` §十一 P2-⑪「清理日期化死脚本」。
> 处理方式：**归档（move）而非删除** —— 这些脚本是当时的「一次性卡片生成器 / 预览稿」，
> 被若干条记忆日志按名引用（作为「可复跑模板」），硬删会让日志指针悬空；移到此处既
> 让 `scripts/` 与仓库根**不再被血缘静态扫描计为消费方**，又保留可回溯性。

## 归档清单（28 个 · 零代码消费者）

### `scripts/`（17）

| 文件 | 性质 |
|---|---|
| `_build_evening_0910.py` / `_0914` / `_0917` / `_0918` / `_0921` / `_0922` / `_0923` / `_1013` | 机构晚报速览卡的**当日一次性拼卡脚本**（09-10～10-13）。现由 20:00 任务按 prompt 内联拼卡 + `push_card.py` 推送，脚本不再被调用 |
| `build_evening_0916.py` | 同上（09-16） |
| `build_hunter_0925.py` / `_build_hunter_0925.py` | 09-25 盘中猎手卡一次性构建稿（后者仅差下划线前缀） |
| `_build_snapshot1000.py` | 10:00 盘中快照卡一次性构建稿（快照卡型已于 10-14 并入猎手骨架 B） |
| `_gen_morning_card_0910.py` | 09-10 早盘卡一次性构建稿 |
| `_preview_hunter_v4.py` / `_preview_hunter_v5.py` | 猎手卡**样式预览稿**（曾用于定位 note 嵌套降级事故） |
| `build_morning_20260921.py` / `build_pool_20260921.py` | 09-21 早盘卡 / 候选池一次性导出稿 |

### 仓库根（3）

| 文件 | 性质 |
|---|---|
| `build_1300_card.py` | 13:00 卡一次性构建稿（对应现「盘中猎手-1300」任务，卡由骨架 B 渲染） |
| `build_hunter_1400.py` | 14:00 猎手卡一次性构建稿 |
| `build_snapshot_1445.py` | 14:45 快照卡构建稿（已由记忆标注「废弃」，卡型已并入猎手） |

### `cache/`（8 · 目录职责混入）

`build_1400.py`、`build_fupan_0910.py`、`build_review_2026-08-14.py`、`_gen_0915.py`、
`export_institution_pool.py`、`export_pool_0916.py`、`gen_morning_0916.py`、`gen_morning_card.py`

> ⚠️ 同为 `cache/` 下的 `cache/backups/*.py`（`gap5_*` / `n_shape_fix_*` / `nav_*` / `reflow_fix_*`）
> **不在归档范围**：它们是 `build_cards.py` / `live_signal.py` / `ai_ledger.py` / `decision_outlook.py`
> 的**事故前快照**（刻意保留的安全垫），不是一次性脚本。

## 判定依据（可复现）

1. 全仓（含 `docs/` / `tests/` / 各 skill / `config/tasks.yaml` / launchd plist）按文件名 grep
   ⇒ **唯一命中是审计报告自身**；
2. 逐个 `automation_update` 回读相关任务 prompt（机构晚报 `ed5fd591` / 机构研报早盘卡 `36303687`）
   ⇒ prompt 自包含，只引用 `push_card.py` 与 `cache/gangtise_*_research.json`，**未引用任何归档件**；
3. 仅 `.workbuddy/memory/*.md` 的历史日志按名引用（叙事留痕，非代码消费者）。

## 复跑与回退

- 需要参考当时版式：直接 `Read` 本目录下对应文件（依赖 `scripts/`、`cache/` 的相对路径已失效，
  需手动改 `sys.path` / 数据根）。
- 需要恢复：`git mv 架构/退役脚本/2026-10/<sub>/<name> <原路径>` 即可（本目录内文件已入 git）。
