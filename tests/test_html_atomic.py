#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTML 看板/看盘页 原子写 + 日志降噪 回归测试（2026-09-02 · 中枢体检 P0-1/P1-1）。

背景（真实故障）：守护循环每 30s 重写 latest.json / watch.html / dashboard.html，
原实现用 Path.write_text（O_TRUNC + 按自身 offset 顺序写）。两个进程并发时会出现
「新内容 + 旧文件尾部残余」→ 解析报 JSONDecodeError: Extra data: line 1697。
本测试锁死三件事：
  ① 页面写入原子（tmp 落盘 + 校验 + os.replace），校验失败不覆盖正式文件
  ② 快照读取容错（损坏文件返回 None，不抛异常打断守护循环）
  ③ 日志降噪（同源错误窗口内合并，首条必落、不被静默吞掉）
"""
import json
import pathlib
import shutil
import sys
import tempfile
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "HTML"))

import gen_watch  # noqa: E402
import gen_dashboard  # noqa: E402
from common import http_client  # noqa: E402


@pytest.fixture
def workdir():
    """自带临时目录（放在项目内，避免依赖系统 /tmp 权限）。"""
    d = pathlib.Path(tempfile.mkdtemp(dir=str(ROOT / ".pytest_tmp")))
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ────────── ① 原子写 ──────────
def _page(snap_ts="2026-09-02 15:00:00"):
    snap = {"ts": snap_ts, "index": [{"name": "上证", "pct": 0.5}]}
    return "window.SNAP = " + json.dumps(snap, ensure_ascii=False) + ";\nconst $ = id => id;\n"


def test_write_html_atomic_writes_valid_page(workdir):
    out = workdir / "watch.html"
    gen_watch.write_html_atomic(out, _page())
    text = out.read_text(encoding="utf-8")
    assert text.startswith("window.SNAP = ")
    s = text.index("window.SNAP = ") + len("window.SNAP = ")
    e = text.index("\nconst $", s)
    assert json.loads(text[s:e].rstrip().rstrip(";"))["ts"] == "2026-09-02 15:00:00"


def test_write_html_atomic_leaves_no_tmp_file(workdir):
    out = workdir / "watch.html"
    gen_watch.write_html_atomic(out, _page())
    leftovers = [p.name for p in workdir.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"临时文件未清理: {leftovers}"


def test_write_html_atomic_rejects_missing_marker(workdir):
    """模板缺 SNAP 标记 → 拒绝写入，且不能破坏已有正式文件。"""
    out = workdir / "watch.html"
    gen_watch.write_html_atomic(out, _page())
    before = out.read_text(encoding="utf-8")
    try:
        gen_watch.write_html_atomic(out, "<html><body>坏页面</body></html>")
    except ValueError:
        pass
    else:  # pragma: no cover - 必须抛错
        raise AssertionError("缺少 SNAP 标记时应抛 ValueError")
    assert out.read_text(encoding="utf-8") == before, "校验失败时不应覆盖正式文件"


def test_dashboard_atomic_rejects_tiny_output(workdir):
    """生成内容异常偏小（渲染中断）→ 拒绝覆盖正式看板。"""
    out = workdir / "dashboard.html"
    out.write_text("<html>上一版正常看板</html>" * 200, encoding="utf-8")
    before = out.read_text(encoding="utf-8")
    try:
        gen_dashboard.write_html_atomic(out, "<html>半截</html>")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("内容过小时应抛 ValueError")
    assert out.read_text(encoding="utf-8") == before


# ────────── ② 快照读取容错 ──────────
def test_load_snapshot_returns_none_on_corrupt(workdir):
    """Extra data 型损坏文件 → 返回 None 而不是抛异常（守护循环不中断）。"""
    bad = workdir / "latest.json"
    bad.write_text('{"ts": "x"}\n{"ts": "y"}\n', encoding="utf-8")
    assert gen_watch._load_snapshot(bad) is None


def test_load_snapshot_ok(workdir):
    good = workdir / "latest.json"
    good.write_text(json.dumps({"ts": "2026-09-02 15:00:02"}), encoding="utf-8")
    assert gen_watch._load_snapshot(good)["ts"] == "2026-09-02 15:00:02"


def test_load_snapshot_missing_file(workdir):
    assert gen_watch._load_snapshot(workdir / "nope.json") is None


# ────────── ③ 日志降噪 ──────────
def test_log_sampled_merges_same_source(monkeypatch):
    """窗口内同源错误只落首条，窗口结束补汇总（含次数）——不静默丢真实异常。"""
    msgs = []
    monkeypatch.setattr(http_client, "_log", lambda m: msgs.append(m))
    monkeypatch.setattr(http_client, "NOISE_WINDOW_SEC", 600)
    http_client._NOISE_STATE.clear()

    for _ in range(5):
        http_client._log_sampled("req|a.com|URLError", "请求失败 A")
    assert len(msgs) == 1, f"窗口内应只落 1 条，实际 {len(msgs)}"

    # 不同主机不合并
    http_client._log_sampled("req|b.com|URLError", "请求失败 B")
    assert len(msgs) == 2

    # 窗口过期 → 补汇总条，含次数
    st = http_client._NOISE_STATE["req|a.com|URLError"]
    st["first"] = time.time() - 601
    http_client._log_sampled("req|a.com|URLError", "请求失败 A")
    assert len(msgs) == 3
    assert "合并同类 5 次" in msgs[-1]
