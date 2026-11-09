"""上一波连板高度（股性·单一源）—— 龙一认定的第二序。

## 为什么需要它（2026-11-06 用户裁定）

用户原话：「今天这个东方海洋皇台啊，其实东方海洋比皇台要强啊，不应该是皇台——
上次皇台只有三连板，东方海洋上次是四连板，明显的是东方海洋要强的一批！」

当日两只都是 1 板（11-05 首板），盘后 `sector_leader.leader_key` 的第一序「板数」打平，
落到第二序「封板时段档」：皇台 09:35 早于东方海洋 09:58 → 皇台判龙一、东方海洋判第 3 身位，
09:25 被 ⑩d 龙一闸以「只做龙一，跟风不追」拦下。结果当日 东方海洋 **涨停 +8.17%**、
皇台 **−10.66%** —— 判反了。

用户给的判据是**上一波（最近一次完整）连板的最高板数**，不是历史最高板：
两只 `max_boards_hist` 都是 4，历史最高分不出强弱；分得出的是「最近那一波走到几板」。

## 口径

```
prev_streak_boards(code) = 最近一次「已结束」连板段的最高板数
                         = 当前段起点前一个交易日在板时的 boards 值
```

`zt_series` 的 `boards` 字段本身就是段内连板数（1,2,3…），故「上一个元素」的 boards
就是上一波的高度。缺段（首段题材新票）→ `None`，⛔ 不填 0、不猜。

⛔ 纯函数、只读入参序列，不读盘（读盘由调用方决定，测试可完全隔离）。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence


def _as_int(v: Any) -> int | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def norm_series(raw: Any) -> list[tuple[str, int]]:
    """把 `{code: [(date, boards), ...]}` 里的一条序列规范成升序 [(date, boards)]。"""
    if not raw:
        return []
    out: list[tuple[str, int]] = []
    for item in raw:
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            d, b = item[0], _as_int(item[1])
        elif isinstance(item, Mapping):
            d, b = item.get("date"), _as_int(item.get("boards"))
        else:
            continue
        if d is None or b is None or b <= 0:
            continue
        out.append((str(d)[:10], b))
    out.sort(key=lambda x: x[0])
    return out


def prev_streak_boards(raw_series: Any) -> int | None:
    """最近一次**已结束**连板段的最高板数。缺数据 → None（不裁决，不猜）。"""
    seq = norm_series(raw_series)
    if not seq:
        return None
    cur = seq[-1][1]                      # 当前段的长度（段内 boards 即连板数）
    idx = len(seq) - cur                  # 当前段起点下标
    if idx - 1 < 0:
        return None                       # 只有当前段，没有「上一波」
    return seq[idx - 1][1]


def prev_streak_of(series_map: Mapping[str, Any] | None, code: str) -> int | None:
    """按代码取上一波高度。`series_map` = `defense_gate.load_zt_series` 的返回值。"""
    if not isinstance(series_map, Mapping):
        return None
    return prev_streak_boards(series_map.get(str(code)))


def prev_streak_for(series_map: Mapping[str, Any] | None, code: str,
                    today: str, today_boards: Any) -> int | None:
    """把「今天这一板」补进序列后再算上一波高度。

    盘后 `zt_history` 可能还没落当天文件 —— 直接用最后的元素会把**当前段**当成
    「上一波」。故先剔除 `date >= today` 的项，再 append `(today, today_boards)`，
    保证 `seq[-1].boards` 恒等于「截至今天的连板数」。
    """
    if not isinstance(series_map, Mapping):
        return None
    b = _as_int(today_boards)
    if b is None or b <= 0:
        return None
    d = str(today)[:10]
    seq = [(x, y) for x, y in norm_series(series_map.get(str(code))) if x < d]
    seq.append((d, b))
    return prev_streak_boards(seq)


def prev_streak_text(v: int | None) -> str:
    """人话：`上一波 4 连板` / `上一波没有连板` / `上一波未知`。"""
    if v is None:
        return "上一波未知"
    if v <= 1:
        return "上一波没有连板"
    return f"上一波 {v} 连板"


def gene_stronger(mine: Any, theirs: Any) -> bool:
    """我的上一波是否**严格**强于对方。缺任一侧 → False（不裁决，避免拿缺数据当优势）。"""
    a, b = _as_int(mine), _as_int(theirs)
    if a is None or b is None:
        return False
    return a > b


def series_from_days(days: Iterable[str], rows_by_day: Mapping[str, Sequence[Mapping[str, Any]]]
                     ) -> dict[str, list[tuple[str, int]]]:
    """测试/离线回放用：把逐日涨停池拼成 `{code: [(date, boards)]}`。"""
    out: dict[str, list[tuple[str, int]]] = {}
    for d in sorted(days):
        for r in rows_by_day.get(d) or []:
            code = str(r.get("code") or r.get("dm") or "")
            b = _as_int(r.get("boards") if r.get("boards") is not None else r.get("lbc"))
            if not code or b is None:
                continue
            out.setdefault(code, []).append((str(d)[:10], b))
    return out
