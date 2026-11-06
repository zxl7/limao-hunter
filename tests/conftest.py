#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pytest 统一配置（2026-09-02 · 中枢体检 P1-2）。

作用：把 pytest 的 basetemp 固定到项目内 .pytest_tmp，
避免依赖系统 /tmp（受限环境/沙箱下 mkdir 会被拒绝，导致 tmp_path 夹具全部 ERROR）。
这样 `pytest tests/` 在本机与受限环境下行为一致。
"""
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WC = ROOT / "wealth_code"
if str(WC) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(WC))

# 11-05 用户删除 5% 高开线与「必须红开」线，竞价判据改为逐票质量预期。
# 下列模块锁的是**旧口径契约**（5% 硬线 / 没红开 / 旧卡面文案 / 旧量能只判红开），
# 断言本身仍有效，但必须显式跑在 `AI_EXPECTED_GAP_MODE=absolute` 回退模式下；
# 新默认口径的回归锁见 `tests/test_expected_gap_zone.py`。
# ⛔ 集中白名单，避免在每个旧文件重复挂 fixture；新增旧口径模块须在此登记。
LEGACY_GAP_MODE_MODULES = frozenset({
    "test_ai_ledger", "test_observe_recheck", "test_recheck_unlock_downstream",
    "test_verify_card_ai", "test_verify_card_dynamic",
    "test_volume_bands_even_allocation", "test_leader_cycle",
})


@pytest.fixture(autouse=True)
def _legacy_gap_mode_for_old_contracts(request, monkeypatch):
    """旧口径契约测试默认回退；新判据专项自行 setenv relative 覆盖本夹具。"""
    if request.module.__name__.rsplit(".", 1)[-1] in LEGACY_GAP_MODE_MODULES:
        monkeypatch.setenv("AI_EXPECTED_GAP_MODE", "absolute")


@pytest.fixture
def legacy_pool_call_rules(monkeypatch):
    """2026-11-04 及之前的旧口径回归夹具（⛔ 非 autouse，需显式 usefixtures）。

    11-05 用户改了三条规则：量能分档区间 / 不限制只数 / 平均分配仓位。
    历史契约测试断言的是**旧口径**（名额按 stance 截断 / 量能 0.7 单线否决），
    直接跑必然红。这里显式关掉四个新开关，让旧断言逐字成立，同时也验证
    「新规则可逆」——回退后行为与 11-04 完全一致。

    新默认口径的回归锁见 `tests/test_volume_bands_even_allocation.py`。
    """
    from core import ai_caliber, pool_call

    monkeypatch.setattr(ai_caliber, "AI_VOLUME_BANDS", False)
    monkeypatch.setattr(pool_call, "POOL_CALL_UNLIMITED_SLOTS", False)
    monkeypatch.setattr(pool_call, "POOL_CALL_EVEN_ALLOCATION", False)
    # 11-05 09:21 加的「defend 名额不受 stance 压制」也是新口径的一部分
    monkeypatch.setattr(pool_call, "POOL_CALL_STOCK_FIRST_SLOTS", False)
    # 11-05 16:55 用户删除 5% 高开线与「必须红开」线 → 竞价判据改为逐票质量预期。
    # 本夹具锁的是 11-04 及之前的旧口径，显式回退到绝对线模式（同时验证开关可逆）。
    monkeypatch.setenv("AI_EXPECTED_GAP_MODE", "absolute")
    monkeypatch.setattr(pool_call, "POOL_CALL_LOW_OPEN_GUARD", False)
    # 11-06 用户 A：身位差分母改「同题材最高板」+ 硬闸改「只降仓不剔除」。
    # 本夹具锁的是 11-06 之前的旧口径，这两条显式回退：
    #   `SCOPE=market` 回全市场最高板分母，`HARD=True` 恢复「≥2 板判后排剔出」。
    monkeypatch.setenv("POOL_CALL_POSITION_GAP_SCOPE", "market")
    monkeypatch.setattr(pool_call, "POOL_CALL_POSITION_GAP_HARD", True)
    # 盘后过筛同步回退（旧行为：身位差 ≥2 直接 reject）。
    try:
        from core import daily_screen
        monkeypatch.setattr(daily_screen, "POS_SCREEN_FACT_ONLY", False)
    except Exception:            # noqa: BLE001 —— 模块缺失不阻断旧口径夹具
        pass
TMP_DIR = ROOT / ".pytest_tmp"


def pytest_configure(config):
    """固定 basetemp（默认项目内 .pytest_tmp），但**尊重显式指定**。

    2026-10-27 补：原实现无条件覆盖 `--basetemp`，导致
      ① 命令行传的临时目录被吞掉；
      ② 项目 `.pytest_tmp` 长期不清理（实测积累 1433 个文件），pytest 每次
         开工想清空旧目录 → 被本机 safe-delete 批量删除确认拦下 → 9 个用
         `tmp_path` 的用例整片 ERROR（`SystemExit`），看起来像代码坏了。
    现在：`PYTEST_TMP_DIR` 环境变量 > 命令行 `--basetemp` > 默认 `.pytest_tmp`。
    """
    explicit = os.environ.get("PYTEST_TMP_DIR")
    args = getattr(getattr(config, "invocation_params", None), "args", ()) or ()
    if explicit:
        target = pathlib.Path(explicit)
    elif any(str(a).startswith("--basetemp") for a in args):
        return                       # 用户自己在命令行定了，别再抢
    else:
        target = TMP_DIR
    target.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(target)
