# -*- coding: utf-8 -*-
"""板块接口部分失败时，不能把空榜覆盖上一份完整快照。"""
import importlib.util
import json
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("ip_sector_risk", SCRIPTS / "intraday_pulse.py")
ip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ip)


def test_sector_risk_uses_recent_complete_snapshot_only(monkeypatch, tmp_path):
    monkeypatch.setattr(ip, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ip, "fetch_sector_flow_details", lambda codes: {})

    def complete_board(_host, _fs, po=0, fid="f3", **_kwargs):
        if fid == "f3":
            return [{"f14": "机器人", "f12": "001", "f3": 2.0, "f62": 1e8},
                    {"f14": "半导体", "f12": "002", "f3": -3.0, "f62": -1e8}]
        return [{"f14": "机器人" if po else "半导体", "f12": "001" if po else "002",
                 "f3": 2.0 if po else -3.0, "f62": 1e8 if po else -1e8}]

    monkeypatch.setattr(ip, "_em_board_get", complete_board)
    good = ip.build_sector_risk()
    assert good["ok"] is True and good["complete"] is True
    assert good["inflow_top"] and good["outflow_top"] and good["declining"]

    cache_path = tmp_path / "sector_risk_cache.json"
    cache = json.loads(cache_path.read_text())
    cache["ts"] -= 61  # 越过 60 秒复用窗口，触发本轮重新抓取。
    cache_path.write_text(json.dumps(cache))
    monkeypatch.setattr(ip, "_em_board_get", lambda *_args, **_kwargs: [])
    stale = ip.build_sector_risk()
    assert stale["ok"] is False and stale["stale"] is True
    assert stale["inflow_top"] == good["inflow_top"]
    assert stale["declining"] == good["declining"]
    assert json.loads(cache_path.read_text())["ts"] == cache["ts"]

    cache["ts"] -= 600  # 过期数据不能再冒充当前板块榜。
    cache_path.write_text(json.dumps(cache))
    unavailable = ip.build_sector_risk()
    assert unavailable["ok"] is False and unavailable.get("stale") is not True
    assert unavailable["inflow_top"] == []
    assert unavailable["count"] == 0
