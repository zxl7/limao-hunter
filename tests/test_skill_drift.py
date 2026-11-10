# -*- coding: utf-8 -*-
"""技能判据副本守卫（`scripts/check_skill_drift.py`）回归锁 —— 2026-11-09

锁四件事：
  ① 副本**增加**必须被判定为违规（棘轮的核心）；
  ② 副本**减少**不算违规（这是清理成果，不是回归）；
  ③ 只扫我们自己的技能（`财神-*` / `龙头战法*` / `狸猫终端`），第三方技能不代管；
  ④ `references/` 下的参考文档不计（那是资料，不是规则入口）。

另有一条真基线检查：若本机存在 `~/.workbuddy/skills`，则当前词频与
`config/skill_drift_baseline.json` 比对，**不得高于基线**。缺目录则跳过（CI/他机）。
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_skill_drift", ROOT / "scripts" / "check_skill_drift.py")
    m = importlib.util.module_from_spec(spec)     # type: ignore[arg-type]
    spec.loader.exec_module(m)                    # type: ignore[union-attr]
    return m


@pytest.fixture()
def mod():
    return _load()


def test_diff_flags_increase(mod):
    base = {"财神-选股": {"5%": 46}}
    cur = {"财神-选股": {"5%": 47}}
    inc, dec = mod.diff(base, cur)
    assert inc == [("财神-选股", "5%", 46, 47)], "副本增加必须被拦下"
    assert dec == []


def test_diff_accepts_decrease(mod):
    base = {"财神-选股": {"5%": 46, "晋级率": 21}}
    cur = {"财神-选股": {"5%": 40, "晋级率": 21}}
    inc, dec = mod.diff(base, cur)
    assert inc == [], "清理副本不应判违规"
    assert dec == [("财神-选股", "5%", 46, 40)]


def test_scan_scope_and_references(mod, tmp_path):
    s = tmp_path / "skills"
    (s / "财神-甲").mkdir(parents=True)
    (s / "财神-甲" / "SKILL.md").write_text("5% 一次 5% 两次", encoding="utf-8")
    (s / "财神-甲" / "references").mkdir()
    (s / "财神-甲" / "references" / "教学.md").write_text("5% " * 100, encoding="utf-8")
    (s / "第三方-乙").mkdir()
    (s / "第三方-乙" / "SKILL.md").write_text("5% " * 50, encoding="utf-8")

    got = mod.scan(s, ("财神-", "龙头战法", "狸猫终端"), ["5%"])
    assert set(got) == {"财神-甲"}, "第三方技能不应纳入口径"
    assert got["财神-甲"]["5%"] == 2, "references/ 下的参考文档不应计入"


def test_baseline_not_regressed(mod):
    """真基线：当前技能词频不得高于 2026-11-09 冻结的基线。"""
    base_dir = Path.home() / ".workbuddy" / "skills"
    if not base_dir.exists():
        pytest.skip("本机没有 ~/.workbuddy/skills，跳过真基线检查")

    data = json.loads((ROOT / "config" / "skill_drift_baseline.json").read_text(encoding="utf-8"))
    cur = mod.scan(base_dir, tuple(data["scope"]), data["tokens"])
    inc, _ = mod.diff(data["counts"], cur)
    assert not inc, (
        "技能正文新增了唯一源词副本，改回引用 config/shared_rules.md，"
        f"或人工确认后执行 `python3 scripts/check_skill_drift.py --update`：{inc}"
    )
