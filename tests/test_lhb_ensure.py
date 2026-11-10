# -*- coding: utf-8 -*-
"""龙虎榜落盘守护（`scripts/lhb_ensure.py`）回归锁 —— 2026-11-09

背景：`【盘后】龙虎榜速览卡` automation 于 2026-09-29 被删，tasks.yaml 仍标 ACTIVE，
数据断更 41 天无人发现。新增的守护必须满足两条，否则等于换个地方继续静默：

  ① **只补缺的**：已有非空文件不重复抓（幂等，每 10 分钟一拍也安全）；
  ② **空数据不算就绪**：`ready` 只看「最近交易日文件确有内容」，空文件/缺失都要报 `remaining`。

全隔离：`fetch` 注入假实现 + `tmp_path`，不读真实 cache、不调 npx/网络。
"""
import importlib.util
import json
from datetime import date

import pytest

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "lhb_ensure", ROOT / "scripts" / "lhb_ensure.py")
    mod = importlib.util.module_from_spec(spec)      # type: ignore[arg-type]
    spec.loader.exec_module(mod)                     # type: ignore[union-attr]
    return mod


@pytest.fixture()
def mod():
    return _load()


def test_recent_trading_days_skips_weekend(mod):
    """最近 N 个交易日不得包含周末（⛔ 不自己数星期几，走 trading_calendar）。"""
    days = mod.recent_trading_days(5, today=date(2026, 11, 9))
    assert len(days) == 5
    assert days[-1] == "2026-11-09"
    assert days == sorted(days)
    assert all(date(*map(int, d.split("-"))).weekday() < 5 for d in days)


def test_ensure_backfills_only_missing(mod, tmp_path):
    """已有非空文件的日期跳过；缺的补；补完 ready=True。"""
    calls = []

    def fake_fetch(d: str):
        calls.append(d)
        out = {"date": d, "institution": [{"code": "600000"}], "hotmoney": [], "ok": True}
        (tmp_path / f"lhb_{d.replace('-', '')}.json").write_text(
            json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out

    (tmp_path / "lhb_20261105.json").write_text(
        json.dumps({"institution": [{"code": "000001"}]}), encoding="utf-8")

    st = mod.ensure(["2026-11-05", "2026-11-06"], fetch=fake_fetch,
                    cache=tmp_path, with_hithink=False)

    assert calls == ["2026-11-06"], "已有数据的日期不应重复抓"
    assert st["filled"] == ["2026-11-06"]
    assert st["remaining"] == []
    assert st["ready"] is True


def test_empty_result_is_not_ready(mod, tmp_path):
    """抓取返回空（未披露/接口异常）→ 不写文件、不算就绪、必须报 remaining。"""
    def fake_fetch(_d: str):
        return {"date": _d, "institution": [], "hotmoney": [], "ok": False}

    st = mod.ensure(["2026-11-09"], fetch=fake_fetch, cache=tmp_path, with_hithink=False)

    assert st["ready"] is False
    assert st["remaining"] == ["2026-11-09"]
    assert st["errors"], "空数据必须留痕，不能静默"
    assert not (tmp_path / "lhb_20261109.json").exists(), "空数据不得落盘伪装成已更新"
