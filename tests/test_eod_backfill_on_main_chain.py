# -*- coding: utf-8 -*-
"""🔒 候选字段回填必须落在**主链** `run_eod` 内（2026-11-17 用户「以最新为准」）。

## 为什么写这个测试

2026-11-16 晚三项改造（题材兜底留痕 / 同花顺涨停原因 / 收盘共振分）写进了
`pipeline_eod._rank_and_split()`。那个函数**自 2026-09-29 起就是死代码、全仓零调用点**，
于是改造一条都没落到候选文件里 —— 11-17 实测 `limit_up_reason` 0/13、
`close_resonance` 0/13、`theme_source` 0/13，日志却只显示「同花顺涨停原因 36 条」
（那是抓取层，不是挂载层），看起来一切正常，实际白丢一天。

用户 2026-11-17 指令：**最新的改动覆盖原来的逻辑，哪怕冲突也以最新为准。**
→ 三段已搬到 `run_eod()` 的「候选行三层回填」块。

## 这个测试锁什么

只锁「代码位置」，不锁业务口径（口径由各自的专项测试负责）：

1. 三个字段的回填代码必须出现在 `run_eod` 函数体内；
2. 不允许再出现在 `_rank_and_split` 里（那个函数零调用点，写进去等于没写）。

⛔ 反例：将来想把回填搬到新函数，**可以**，但必须同时更新本测试并确认新函数
   在 `run_eod` 的调用链上 —— 不要为了让测试变绿而把断言删掉。
"""

import inspect
import sys
from pathlib import Path

import pytest

WC = Path(__file__).resolve().parents[1] / "wealth_code"
if str(WC) not in sys.path:
    sys.path.insert(0, str(WC))

pipeline_eod = pytest.importorskip("pipelines.pipeline_eod")


_FIELDS = ("limit_up_reason", "close_resonance", "theme_source")


@pytest.mark.parametrize("field", _FIELDS)
def test_backfill_lives_in_run_eod(field):
    """字段回填必须在 `run_eod` 里 —— 否则生产路径上等于没写。"""
    src = inspect.getsource(pipeline_eod.run_eod)
    assert field in src, (
        f"`{field}` 的回填不在 `run_eod()` 里。"
        "只写进别的函数（尤其零调用点的 `_rank_and_split`）不会生效。"
    )


def test_dead_function_is_gone():
    """⛔ 2026-11-17：`_rank_and_split` 已**整段删除**（零调用点 133 行）。

    删除理由：留着它只会让人（包括我们自己）把改动写进去然后以为生效了 ——
    11-16 的三项改造就是这么丢的。若将来要恢复排序入口，请改 `select_relay_watch`。
    """
    assert not hasattr(pipeline_eod, "_rank_and_split"), (
        "`_rank_and_split` 又被加回来了 —— 它零调用点，写进去等于没写；"
        "排序入口是 `core.quant_score.select_relay_watch`。"
    )


def test_candidate_card_renders_reason_when_present():
    """卡面必须真的读 `limit_up_reason`（写了不读 = 白写）。"""
    from pipelines import build_cards as bc

    src = inspect.getsource(bc)
    assert "limit_up_reason" in src, "卡面未读取 `limit_up_reason` → 字段写了没人用"
