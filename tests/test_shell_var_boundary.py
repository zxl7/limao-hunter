# -*- coding: utf-8 -*-
"""Shell 脚本变量引用安全（2026-10-22 事故）。

事故
----
`wealth_verify_launchd.sh` 第 69 行：

    log "⚠️ 旁白文件陈旧（mtime 非今日 $NARR_DAY，平台任务未及时写入）→ 弃用…"

全角逗号「，」紧跟 `$NARR_DAY`。脚本运行环境下 bash 把该字符的首字节 `0xEF`
并进了变量名 → `NARR_DAY<?>: unbound variable` → **`set -u` 直接终止脚本**。

当时正好命中「旁白文件陈旧」分支（平台 9:20 的 AI 旁白任务没写出今日文件），
脚本在 **09:26:05 推卡前一刻崩掉**，只因另有链路兜底才没缺卡。

⛔ 为什么难发现：只在**特定分支**触发。平时旁白新鲜时走的是另一条路径，脚本正常。
   而「旁白陈旧」恰恰是最需要容错的状态——平台任务失灵那天，推卡脚本不能跟着死。

判据
----
shell 里引用变量一律写 `${VAR}`；`$VAR` 后面不许紧跟非 ASCII 字符
（中文标点/汉字/全角符号都会被 locale 影响解析边界）。
"""
from __future__ import annotations
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

# $VAR 紧跟非 ASCII 字符
_RISKY = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)([^\x00-\x7f])")

# 允许的写法示例（用于自检正则本身没写错）
assert _RISKY.search('echo "$NARR，x"') is not None
assert _RISKY.search('echo "${NARR}，x"') is None
assert _RISKY.search('echo "$NARR,x"') is None


def _shell_files() -> list[Path]:
    out = list(SCRIPTS.glob("*.sh")) + list(ROOT.glob("*.sh"))
    return [p for p in out if p.is_file()]


def test_regex_self_check():
    """自检：正则必须能抓到事故写法、且不误报 `${VAR}` 与 ASCII 分隔。"""
    assert _RISKY.search('log "非今日 $NARR_DAY，平台"')
    assert not _RISKY.search('log "非今日 ${NARR_DAY}，平台"')
    assert not _RISKY.search('log "今日 $NARR_DAY 完成"')


@pytest.mark.parametrize("path", _shell_files() or [None],
                         ids=[p.name for p in _shell_files()] or ["none"])
def test_no_bare_var_before_non_ascii(path):
    """⛔ 不许 `$VAR` 紧跟中文标点/汉字（会被 locale 影响变量名边界）。"""
    if path is None:
        pytest.skip("未找到 shell 脚本")
    src = path.read_text(encoding="utf-8")
    bad: list[str] = []
    for no, line in enumerate(src.split("\n"), 1):
        if line.lstrip().startswith("#"):     # 注释不执行，不判
            continue
        for m in _RISKY.finditer(line):
            bad.append(f"L{no}: $%s ← %r ｜ %s" % (m.group(1), m.group(2), line.strip()[:80]))
    assert not bad, f"{path.name} 存在变量名边界风险（改成 ${{VAR}}）：\n" + "\n".join(bad)


def test_verify_launchd_script_syntax_ok():
    """语法自检：`bash -n` 必须通过（防改动引入语法错误整脚本失效）。"""
    p = SCRIPTS / "wealth_verify_launchd.sh"
    assert p.is_file()
    for env in ({"LC_ALL": "C", "LANG": "C"}, {"LC_ALL": "en_US.UTF-8"}):
        r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True, env=env)
        assert r.returncode == 0, f"bash -n 失败（env={env}）：{r.stderr[:300]}"


def test_stale_narrative_branch_does_not_abort():
    """⛔ 复现原事故分支：`set -u` + 旁白陈旧文案，在 C locale 下不得 abort。

    ⛔ 「旁白陈旧」是最需要容错的状态——平台任务失灵那天，推卡脚本不能跟着死。
    """
    script = r'''
set -u
NARR_DAY=$(date "+%Y-%m-%d")
log() { echo "$*"; }
log "⚠️ 旁白文件陈旧（mtime 非今日 ${NARR_DAY}，平台任务未及时写入）→ 弃用，无旁白推卡"
echo "__OK__"
'''
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr[:300]
    assert "__OK__" in r.stdout
