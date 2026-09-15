import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("sector_family", SCRIPTS / "sector_family.py")
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)


def _sec(name, zt):
    return {"name": name, "zt_count": zt, "leader": {"name": name + "龙"}, "delta": "ongoing"}


def test_aggregate_family_merges_zt():
    top5 = [_sec("大农业", 7), _sec("养猪", 2), _sec("机器人", 5)]
    agg = sf.aggregate_family(top5, "泛农业")
    assert agg is not None
    assert agg["zt_count"] == 9  # 7+2 合并
    assert agg["sub_text"] == "大农业7+养猪2"
    assert agg["_family"] is True
    assert agg["member_count"] == 2


def test_get_family_maps_member():
    assert sf.get_family("大农业") == "泛农业"
    assert sf.get_family("养猪") == "泛农业"
    assert sf.get_family("机器人") is None


def test_family_of_top5_only_counts_present_members():
    top5 = [_sec("大农业", 7)]  # 养猪不在 top5
    fams = sf.family_of_top5(top5)
    assert "泛农业" in fams
    assert fams["泛农业"]["zt_count"] == 7  # 只聚合在场成员


def test_flatten_does_not_double_count():
    top5 = [_sec("大农业", 7), _sec("养猪", 2), _sec("机器人", 5)]
    flat = sf.flatten_top5_with_family(top5)
    names = [f.get("name") for f in flat]
    assert "泛农业" in names
    assert "大农业" not in names and "养猪" not in names  # 成员被家族覆盖
    assert "机器人" in names  # 非家族板块保留
