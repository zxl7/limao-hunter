# -*- coding: utf-8 -*-
"""传媒被误归「AI算力」事故（2026-10-12）的回归锁。

事故：10-12 09:25 池内裁决把**传媒整条误杀**（中文在线/掌阅被判定为「传艺科技(3板·已断板)
的跟风」→ 不买），反而买入国芳集团(大消费·当日 −8%)；而当日传媒全线涨停
（慈文/博纳/新华 +10%、果麦 +20%、中文在线 +6%）。

根因：
  ① `common/stock_theme.py` 泛规则 `("AI","AI算力")` —— desc 含「AI」就归 AI算力，
     把 中文在线/掌阅（plates=影视）吞成 AI算力；
  ② `SECTOR_THEME_MAP` 把 传媒/游戏Ⅱ/数字媒体 归「大消费」—— 传媒与一般零售混成一条线。
  叠加 → 5 只分属 5 个行业的票同归【AI算力】→ line_core=传艺科技(已断板) → 跟风闸整条误杀。

修复（本锁锁死）：
  ① 删泛「AI」规则 + 新增 影视/短剧/院线/传媒/出版/阅读/游戏 → 影视传媒；
  ② 传媒独立成线（传媒/游戏Ⅱ/数字媒体/影视院线/出版/影视/影视传媒/数字阅读 → 传媒）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WC = ROOT / "wealth_code"
for p in (str(ROOT), str(WC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from common import stock_theme as st          # noqa: E402
from core import sector_support as ss         # noqa: E402
from core import pool_call                     # noqa: E402


# ── ① 题材提炼：泛「AI」规则已删 ─────────────────────────────
def test_generic_ai_rule_removed():
    """desc 含「AI」但无具体算力词 → 不再归 AI算力（根因①）。"""
    theme, _ = st._extract_theme("公司积极布局AI相关业务与生态")
    assert theme != "AI算力", f"泛「AI」规则应已删除，实际归 {theme!r}"


def test_media_desc_maps_to_media():
    """含影视/短剧/传媒 的 desc → 影视传媒。"""
    assert st._extract_theme("AI影视内容制作")[0] == "影视传媒"
    assert st._extract_theme("公司主营AI短剧与院线发行")[0] == "影视传媒"


def test_real_compute_desc_still_ai():
    """真算力词（算力/服务器）仍归 AI算力（回归：删泛规则不得误伤真算力）。"""
    assert st._extract_theme("AI大模型算力服务器")[0] == "AI算力"
    assert st._extract_theme("数据中心液冷服务器")[0] in ("AI算力", "液冷散热")


# ── ② 线族归并：传媒独立成线 ───────────────────────────────
def test_media_line_independent_from_ai():
    """中文在线/掌阅（影视传媒/数字媒体）→ 传媒，不再归 AI算力。"""
    assert ss._theme_key({"core_theme": "影视传媒", "sector": "数字媒体"}) == "传媒"
    assert ss._theme_key({"core_theme": "影视", "sector": "数字媒体"}) == "传媒"


def test_media_sectors_map_to_media():
    """传媒/游戏/数字媒体/影视院线/出版/影视 → 传媒线。"""
    for sec in ("传媒", "游戏Ⅱ", "数字媒体", "影视院线", "出版", "影视"):
        assert ss._theme_key({"sector": sec}) == "传媒", f"{sec} 应归「传媒」"


def test_media_not_merged_with_consumer():
    """传媒与一般零售(大消费) **不同线**（根因②：不再混成一条）。"""
    assert ss._theme_key({"sector": "数字媒体"}) == "传媒"
    assert ss._theme_key({"sector": "一般零售"}) == "大消费"
    assert (pool_call.line_family({"sector": "数字媒体"})
            != pool_call.line_family({"sector": "一般零售"}))


def test_real_ai_compute_line_intact():
    """真算力票仍归 AI算力（回归：删泛规则不得误伤真算力）。

    ⚠️ 2026-11-11 改口径（双层线）：`半导体` / `PCB` **不再**桥接成 AI算力 ——
    它们是各自独立的炒作线（催化/资金/产业链环节都不同），只共用「AI算力」这个
    大方向名。见 `core/sector_support.py` 文件头「细分方向独立成线」。
    ⛔ 本条锁的是「真算力词仍归 AI算力」，不是「所有电子票都归 AI算力」。
    """
    assert ss._theme_key({"core_theme": "AI算力"}) == "AI算力"
    assert ss._theme_key({"core_theme": "算力"}) == "AI算力"          # EM_THEME_BRIDGE 保留的大词
    # 细分线各自独立，但大方向一致
    assert ss._theme_key({"sector": "半导体"}) == "半导体"
    assert ss._theme_key({"core_theme": "PCB"}) == "PCB板"
    for _k in ("半导体", "PCB板", "光通信", "液冷", "存储", "服务器"):
        assert ss.big_line({"core_theme": _k}) == "AI算力", f"{_k} 的大方向应是 AI算力"


def test_optical_comm_is_its_own_line():
    """2026-11-11 事故锁：光通信 10 家首板必须**独立成线**，不许被吞进 AI算力。

    病灶：`EM_THEME_BRIDGE` 把 光通信/CPO/PCB/半导体/液冷 全压成「AI算力」→
    系统里根本没有「光通信」这条线，龙一/身位/跟风闸全按 17 家混算。
    """
    for name in ("光通信", "CPO", "CPO光模块", "光模块", "光纤", "光纤光缆", "光芯片"):
        assert ss._theme_key({"core_theme": name}) == "光通信", f"{name} 应归「光通信」"
    # 同义叫法必须聚成一条线（否则同一条线会被拆成三四条）
    assert len({ss._theme_key({"core_theme": n})
                for n in ("光通信", "CPO", "光模块", "光纤")}) == 1
    # 光通信 ≠ PCB板 ≠ 半导体（三条独立行情，不能互相算身位）
    assert len({ss._theme_key({"core_theme": n})
                for n in ("光通信", "PCB板", "半导体")}) == 3
