# -*- coding: utf-8 -*-
"""财富密码 CLI 的模块路径必须自足（2026-10-01 修·根因回归锁）。

背景（不可再犯）：`common/` 包在**项目根**，不在 `wealth_code/` 下。`wealth_code/pipelines/
pipeline_eod.py` 与 `pipeline_verify.py` 都直接 `from common.trading_calendar import ...`，
而调用方（launchd `scripts/wealth_verify_launchd.sh`、平台自动化）只把 `wealth_code` 导进
PYTHONPATH，且**以脚本方式**执行（`sys.path[0]` = 脚本目录，cwd **不入** sys.path）
→ 导入期即抛 ModuleNotFoundError → launchd 整轮 verify 失败（RC=1，当日无竞价卡）。

实证：2026-10-01 `cache/wealth_verify_launchd.log`
  `File ".../pipeline_eod.py", line 48, in <module>`
  `from common.trading_calendar import next_a_share_trading_day`
  `ModuleNotFoundError: No module named 'common'`

修法＝与 `core/ai_caliber.py`、`adapters/input_auction.py` 同法：本模块自插项目根。
本测试用**子进程 + 仅 wealth_code 的 PYTHONPATH + 非项目根 cwd**复现 launchd 的真实运行条件，
只验「CLI 能否导入」（`--help`，不触网、不读写账本）。
⛔ 不要改成跑 eod/verify 真实链路（会触网并写生产缓存）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
WC = BASE / "wealth_code"
CLI = WC / "scripts" / "run_wealth_code.py"


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(WC)  # 只给 wealth_code —— 精确模拟 launchd 的 PYTHONPATH
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_cli_imports_with_wealth_code_only_pythonpath(tmp_path):
    """PYTHONPATH 只有 wealth_code 时，CLI 必须仍能导入（否则 launchd 整轮崩）。"""
    proc = _run_cli(["--help"], tmp_path)
    assert proc.returncode == 0, f"CLI 导入失败：\n{proc.stderr}"
    assert "ModuleNotFoundError" not in proc.stderr


def test_pipelines_declare_common_self_path():
    """pipeline_eod / pipeline_verify 必须自插项目根（与 ai_caliber / input_auction 同法）。"""
    for rel in ("pipelines/pipeline_eod.py", "pipelines/pipeline_verify.py"):
        src = (WC / rel).read_text(encoding="utf-8")
        assert "import sys" in src, f"{rel} 缺 import sys"
        assert "parents[2]" in src, f"{rel} 缺项目根（common/ 所在层）自插"
        assert "from common." in src, f"{rel} 的 common 导入已移除？请同步本测试"
