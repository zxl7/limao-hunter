# -*- coding: utf-8 -*-
"""题材关键词「泛词」审计 —— 2026-10-12 传媒事故的结构性防错层。

事故根因：`common/stock_theme.THEME_KEYWORDS` 里一条过泛的 `("AI","AI算力")`——
desc 含「AI」就归 AI算力，把 中文在线/掌阅（plates=影视）全吞成 AI算力，
再叠加「传媒归大消费」的旧映射 → 5 只分属 5 行业的票同线 → 跟风闸整条误杀。

**单个泛词 = 系统性误杀**。本锁不针对某一票，而是**防止「泛词」再次被引入**：
  ① 禁止「纯字母且长度 ≤2」的 ASCII 关键词（`AI` 这类前缀会命中大量无关 desc）；
  ② 已撤销的泛词（`AI`/`ai`）不得回归；
  ③ 传媒关键题材词必须在表内（防误删）；
  ④ 传媒线映射必须在（`数字媒体`/`传媒` → `传媒`）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WC = ROOT / "wealth_code"
for _p in (str(ROOT), str(WC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common import stock_theme as st       # noqa: E402
from core import sector_support as ss      # noqa: E402


def test_no_short_alpha_keyword():
    """① 禁止纯字母 ≤2 的 ASCII 关键词（如 'AI'）—— 会误吞 AI影视/AI阅读/AI算力。"""
    bad = [kw for kw, _t, _l in st.THEME_KEYWORDS
           if kw.isascii() and kw.isalpha() and len(kw) <= 2]
    assert not bad, f"过泛的短英文关键词（会误吞无关 desc）：{bad}"


def test_generic_ai_not_reintroduced():
    """② 已撤销的泛词「AI」不得回归。"""
    keys = {kw for kw, _t, _l in st.THEME_KEYWORDS}
    hit = keys & {"AI", "ai", "Ai", "a I"}
    assert not hit, f"泛词「AI」回归：{hit}"


def test_media_keywords_present():
    """③ 传媒关键题材词必须在表内（防误删）。"""
    keys = {kw for kw, _t, _l in st.THEME_KEYWORDS}
    for kw in ("影视", "短剧", "传媒"):
        assert kw in keys, f"传媒关键题材词缺失：{kw}"


def test_media_line_mapping_present():
    """④ 传媒线映射必须在（数字媒体/传媒/影视院线 → 传媒）。"""
    for sec in ("数字媒体", "传媒", "影视院线", "出版"):
        assert ss.SECTOR_THEME_MAP.get(sec) == "传媒", f"{sec} 应映射到「传媒」"
