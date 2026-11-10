"""板块打分（theme score）—— 给「这只票所在的板块」打一个 0~100 的数。

## 为什么需要它（2026-11-10 用户：「短线接力的时候，每一个个股其实也是有板块的，这个板块进行一个打分挺好」）

系统里跟板块有关的信号已经很多，但**散在四个地方、四种口径**：

| 位置 | 说的是什么 | 输出形态 |
|---|---|---|
| `front_core.row_tier` | 这只票在这条线排第几 | `front / second / back` |
| `sector_support.compute_support` | 今早同板块有几家红开 | `red_open / mates_total` |
| `theme_linkage` | 这条线有没有联动、能不能碰 | `prefer / avoid` |
| `theme_phase` | 这条线走到哪一段 | `start / ferment / fade` |

每处都对，但**没人能一眼回答「这个板块几分」**。做接力时真正要判断的是同一句话：
*这个板块值不值得在里面买票* —— 需要把这些事合成一个数。

## 打分对象是「板块」，不是「个股」

这是本模块最重要的设计决定。板块分只描述**线本身**好不好，
⛔ 不掺「这只票排第几」——身位是个股属性，已经有 `line_tier` / ⑪b 在管。

    反例（第一版踩过）：把「卡位」放进四维，结果分数 ≈ 板数排名 + 同线家数，
    等于把 `line_rank` 换个写法再排一遍 —— 零新信息，还让人以为系统算了什么。

同一线的票得分相同，这是**正确**的：它问的是「这条线值不值得在里面买票」。

## 四个维度，等权 25 分

| 维度 | 量 | 为什么是它 |
|---|---|---|
| **规模** scale | 本线在池内的票数 | 1 只叫孤军，3 只才叫板块 |
| **高度** height | 本线最高板 | 有高度才有接力空间；全是首板的线，接力就是博傻 |
| **厚度** depth | 本线 ≥2 板的票数 | 只有一只高板 + 一堆首板 = 独角戏，不是梯队 |
| **承接** support | 今早同板块红开率 | 接力买的是「明天还有人接」，这条只有盘中有 |

⛔ **刻意等权**：任何「规模 30%、高度 25%」的差异化权重都是我拍脑袋，
没有样本能支撑那些比例。等权是唯一不需要解释的权重。
哪天要改权重，必须拿分档单调性做证据，⛔ 不许凭手感。

## 口径：池内，不是全市场

`theme_open_rows`（昨日完整涨停池）只有生产链路才传，回放拿不到。
若规模用全市场、回放用池内，就会出现「生产 80 分 / 回放 40 分」永远对不上。
⇒ **统一用池内口径**（接力池 + 观察池），两边都有，可比。

代价：池子已经筛过一轮，家数天然偏小（1~6）。阈值按这个量级定，⛔ 不是全市场阈值。

## 缺维度怎么办

盘后选股时**没有竞价数据**（承接维度不可得）。规则：

    总分 = 已给分项之和 ÷ 已给分项满分 × 100

即缺一个维度就按其余几个折算，`n_dims` 记下实际算了几维。
⛔ 不把缺失维度按 0 分算（那会让盘后分系统性偏低），也⛔ 不按满分算（那是虚高）。
⛔ **只有 1 维可评时不给总分** —— 单维折算必然得出 100 或 0，那是假高分。

⚠️ 因此「3 维的 80 分」与「4 维的 80 分」不是同一件事。跨日比较时**必须按 `n_dims` 分组**。

## 边界（第一版：只留痕，不改判据）

- 写进 `views[code].theme_score`，**不参与** view / position_cap / 名额 / 任何闸。
- 开关 `POOL_CALL_THEME_SCORE`（默认开，只写字段）。
- 要进权重必须先用 `scripts/research_theme_score_ab.py` 证明**分档单调**
  （分数越高、收益越高），否则就是个好看的摆设。
"""

from __future__ import annotations

from typing import Any

SCHEMA = "theme_score_v1"

# ── 维度满分：等权 ────────────────────────────────────────────────────────
DIM_MAX = 25.0

# ── 规模：本线在池内的票数（池内口径，量级 1~6）────────────────────────────
# ⚠️ 不是线性：1→2 只的差别（孤军 vs 有伴）比 4→5 只大得多，所以阶梯递减。
SCALE_STEPS = ((4, 25.0), (3, 20.0), (2, 13.0), (1, 6.0))

# ── 高度：本线最高板 ──────────────────────────────────────────────────────
# 2 板是接力的最低门槛（1 板是首板，不在本策略射程内），所以 2 板起才有体面分。
HEIGHT_STEPS = ((5, 25.0), (4, 21.0), (3, 16.0), (2, 10.0), (1, 4.0))

# ── 厚度：本线 ≥2 板的票数 ────────────────────────────────────────────────
# 只有一只高板 + 一堆首板 = 独角戏；有 3 只以上 ≥2 板才是真梯队。
DEPTH_STEPS = ((3, 25.0), (2, 18.0), (1, 11.0), (0, 4.0))

# ── 承接：今早同板块红开率 ────────────────────────────────────────────────
# 分母用「有报价的成员数」而不是「成员总数」：没报价的成员既不算红也不算绿。
SUPPORT_STEPS = ((0.60, 25.0), (0.40, 20.0), (0.20, 13.0), (0.001, 7.0), (0.0, 2.0))

GRADE_STRONG = 75.0
GRADE_MID = 50.0
GRADE_WEAK = 25.0


def _as_int(v: Any) -> int | None:
    """把入参收敛成 int 或 None。⛔ 类型不对一律当「没有这个数」，不抛。

    ⚠️ `bool` 必须排除：`True >= 1` 成立，会把 True 当成「1 只票」算分。
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() else None
    if isinstance(v, str):
        t = v.strip()
        return int(t) if t.lstrip("-").isdigit() else None
    return None


def _step(value: float | None, steps: tuple[tuple[float, float], ...]) -> float | None:
    """阶梯打分：取第一个满足 `value >= 门槛` 的档。`None` → `None`（缺数据不猜）。"""
    if value is None:
        return None
    for floor, score in steps:
        if value >= floor:
            return score
    return 0.0


def _dim(score: float | None, value: Any, note: str) -> dict[str, Any]:
    return {"v": value, "score": score, "max": DIM_MAX, "note": note}


def score(*, theme_zt_count: int | None = None,
          theme_max_boards: int | None = None,
          theme_depth: int | None = None,
          red_open: int | None = None,
          mates_quoted: int | None = None,
          theme_name: str = "") -> dict[str, Any]:
    """给一个板块打分。入参全是**事前可得**的客观量，不接受任何「感觉」。

    - `theme_zt_count`：本线在池内的票数（池内口径）
    - `theme_max_boards`：本线最高板
    - `theme_depth`：本线 ≥2 板的票数
    - `red_open` / `mates_quoted`：今早同板块红开家数 / 有报价的成员数
      ⛔ 盘后这两个为 `None` → 承接维度整体记缺失

    返回 `{"schema","total","grade","n_dims","dims","theme_name","why"}`。
    ⛔ `total` 可能为 `None`（不可评），调用方必须当作「没有这个数」，⛔ 不许当 0 分。
    """
    theme_zt_count = _as_int(theme_zt_count)
    theme_max_boards = _as_int(theme_max_boards)
    theme_depth = _as_int(theme_depth)
    red_open = _as_int(red_open)
    mates_quoted = _as_int(mates_quoted)

    dims: dict[str, dict[str, Any]] = {}

    dims["scale"] = _dim(
        _step(theme_zt_count, SCALE_STEPS), theme_zt_count,
        f"本线池内 {theme_zt_count} 只" if theme_zt_count else "本线票数未知",
    )
    dims["height"] = _dim(
        _step(theme_max_boards, HEIGHT_STEPS), theme_max_boards,
        f"本线最高 {theme_max_boards} 板" if theme_max_boards else "本线高度未知",
    )
    dims["depth"] = _dim(
        _step(theme_depth, DEPTH_STEPS), theme_depth,
        f"本线 {theme_depth} 只 ≥2 板" if theme_depth is not None else "梯队厚度未知",
    )

    # 承接：两个数任何一个缺 → 整维缺失（⛔ 不拿红开数除以猜测的分母）
    if isinstance(red_open, int) and isinstance(mates_quoted, int) and mates_quoted > 0:
        rate = round(red_open / mates_quoted, 4)
        dims["support"] = _dim(
            _step(rate, SUPPORT_STEPS), rate,
            f"同板块 {mates_quoted} 家有报价、{red_open} 家红开（{rate * 100:.0f}%）",
        )
    else:
        dims["support"] = _dim(None, None, "盘后无竞价数据，承接不可评")

    given = [d for d in dims.values() if d["score"] is not None]
    if not given:
        return {"schema": SCHEMA, "total": None, "grade": "不可评", "n_dims": 0,
                "dims": dims, "theme_name": theme_name,
                "why": "四个维度全部缺数据 → 不裁决（⛔ 不猜分）"}
    if len(given) == 1:
        # ⛔ 只有 1 维可评时**不给总分**：单维折算必然得出 100 或 0，
        #    那是个假高分（2026-11-10 实测：只剩一维时全池都是「强 100 分」）。
        #    宁可写「不可评」，也不要一个看起来很像结论的假数。
        only = next(k for k, d in dims.items() if d["score"] is not None)
        return {"schema": SCHEMA, "total": None, "grade": "不可评", "n_dims": 1,
                "dims": dims, "theme_name": theme_name,
                "why": f"只有「{only}」一维可评，单维折算会出假高分 → 不出总分"}

    raw = sum(float(d["score"]) for d in given)
    full = DIM_MAX * len(given)
    total = round(raw / full * 100.0, 1)
    grade = ("强" if total >= GRADE_STRONG else
             "中" if total >= GRADE_MID else
             "弱" if total >= GRADE_WEAK else "无")

    why = f"{len(given)}/{len(dims)} 维可评 · " + "｜".join(
        f"{k}{d['score']:.0f}" for k, d in dims.items() if d["score"] is not None)
    if len(given) < len(dims):
        missing = [k for k, d in dims.items() if d["score"] is None]
        why += f"（缺 {'/'.join(missing)}，按已给维度折算）"

    return {"schema": SCHEMA, "total": total, "grade": grade, "n_dims": len(given),
            "dims": dims, "theme_name": theme_name, "why": why}


# ─────────────────────────────────────────────────────────────────────────────
# 板块强度分（strength）—— 唯一通过分档验证的那一维
#
# 🔬 验证（`scripts/research_theme_momentum_ab.py`，38 日 · 186 票次 · **已去日期效应**）：
#
#     n1~2 家涨停   超额 −0.88%   (n=37, 胜 44.4%)
#     n3~5 家       超额 −0.65%   (n=53, 胜 47.9%)
#     n≥6 家        超额 +0.69%   (n=96, 胜 53.8%)   ← 唯一单调递增
#
#   「超额」= 该笔当日收益 − 当日全部样本均值，即删掉「大盘涨跌」这个共同项。
#   ⇒ 这是**选对板块**的净贡献，不是「那几天市场好」。
#
# ⛔ 为什么不用另外三维（同样方法验证过，全部不单调）：
#     dir（家数环比）    rising +0.56% / up −1.04% / flat +0.18% / falling −0.86%  → U 形
#     pos_ratio（÷5日峰） 中间 0.33~0.66 +1.93% / <0.33 −2.84% / ≥0.66 +0.22%      → 倒 U
#     streak（连续天数）  连≥5 +0.19% / 连2~4 −0.01% / 首日 −0.25%                  → 几乎无差别
#
# ⛔ 也不能把三维合成进这个分：合成会把单调的一维稀释掉 —— 上一版四维热度分
#    （等权混入板数、池内家数）就是这么变成「分越高反而越差」的。
#
# ⚠️ 口径必须是**全市场同题材涨停家数**（1~96），不是池内家数（1~6）。
#    池内是「筛完还剩几只」，区分度不够；全市场才是这条线真实的热度。
# ─────────────────────────────────────────────────────────────────────────────

STRENGTH_STEPS = ((10, 100.0), (6, 85.0), (3, 50.0), (1, 20.0))

STRENGTH_GRADE = ((85.0, "强"), (50.0, "中"), (20.0, "弱"))


def strength(theme_zt_count: int | None, *, theme_name: str = "") -> dict[str, Any]:
    """板块强度分（0~100）—— 只由「全市场同题材涨停家数」决定。

    ⛔ 这是本模块唯一**通过分档验证**的成分，可以拿来当结论用。
       `score()` 那套四维热度已实测**无预测力**，只作描述，⛔ 不要拿它的 total 说事。

    `theme_zt_count` 缺失 → `total=None`（不可评），⛔ 不当 0 分。
    """
    n = _as_int(theme_zt_count)
    if n is None or n < 0:
        return {"schema": "theme_strength_v1", "total": None, "grade": "不可评",
                "n": None, "theme_name": theme_name,
                "why": "缺全市场同题材涨停家数 → 不裁决（⛔ 不猜）"}
    total = 0.0
    for floor, sc in STRENGTH_STEPS:
        if n >= floor:
            total = sc
            break
    grade = next((g for floor, g in STRENGTH_GRADE if total >= floor), "无")
    return {"schema": "theme_strength_v1", "total": total, "grade": grade, "n": n,
            "theme_name": theme_name,
            "why": f"全市场同题材 {n} 只涨停 → {total:.0f} 分"
                   f"（超额参考：n≥6 +0.69% / n3~5 −0.65% / n1~2 −0.88%）"}


def brief(sc: dict[str, Any] | None) -> str:
    """卡面/日志用的一行摘要。⛔ 缺分时如实说「不可评」，⛔ 不写 0 分。"""
    if not isinstance(sc, dict):
        return "板块不可评"
    total = sc.get("total")
    if total is None:
        return "板块不可评"
    return f"板块{sc.get('grade', '')} {total:.0f}/100"
