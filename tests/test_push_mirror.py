# -*- coding: utf-8 -*-
"""客户群镜像广播（2026-09-10 建立·蒋坤阳 / 2026-09-11 收窄 / 2026-10-14 扩容）回归锁。

client_mirror 判定语义（push_card.mirror_targets）：
  1. 短线精灵（卡名含 short_spirit）→ 命中（客户约定的成品卡之一）
  2. 盘中猎手（卡标题含「盘中猎手」）→ 命中；⛔ 与快照共用 /tmp/limao_card.json
     ⚠️ 2026-10-14 用户指令纳入（原为不命中·见 docstring「白名单扩容」）
  3. 盘中快照（卡标题含「盘中快照」）→ 命中；⚠️ 该卡型 10-14 已并入猎手，此 pattern 现为**已失效项**
  4. 复盘卡（fupan_*/review_* → prod）→ 命中
  5. 异动群内部高频卡（要闻快递/板块拉升/脉冲心跳/核按钮）→ 不命中（2026-09-11 收窄）
  6. 测试群 test 来源 → 一律不命中（硬门·防测试卡外泄）
  7. client_mirror.enabled=false → 全部不命中（一键 kill switch）
  8. client_key == source_key → 跳过（防自指循环）
  9. exclude_patterns 命中卡名或标题 → 排除（优先级最高）
     ⛔ 生产配置必须含 "gangtise"：机构卡 `gangtise_weekend_review.json` 含 "review"
     会被 prod_card_patterns 误命中（2026-09-13 实测曾尝试外发），靠此排除项拦住。
  10. mirror_push 直推：不查主群幂等去重（主推已写 dedup hash，查则必然被误跳）

⚠️ 判定维度铁律：`limao_card.json` 是猎手/快照**共用管道**，两者靠**标题**区分
（`card_patterns` 不含它）⇒ 按文件名放行 = 未来任何写该文件的卡型都会被无差别外发。
"""
import importlib.util
import json
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("push_card", SCRIPTS / "push_card.py")
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)

CLIENT_KEY = "feishu_webhook_client_test"
FAKE_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/00000000-0000-0000-0000-000000000000"

SNAPSHOT_TITLE = "盘中快照-1100·增量 11:00 | 机器人·涨停9"
HUNTER_TITLE = "盘中猎手·增量 | 11:15 | 光通信·沃格光电+10% | 涨停78"


def _feishu_card(title: str) -> dict:
    """已 normalize 的飞书结构（main() 里 mirror_targets 收到的形态）。"""
    return {"msg_type": "interactive",
            "card": {"config": {"wide_screen_mode": True},
                     "header": {"title": {"tag": "plain_text", "content": title},
                                "template": "red"},
                     "elements": []}}


def _write_settings(tmp_path, enabled=True, **over):
    """写一份最小 settings.json，SETTINGS 指向它（隔离真实配置）。"""
    rule = {
        "client_key": CLIENT_KEY,
        "client_name": "测试客户",
        "card_patterns": ["short_spirit"],
        "title_patterns": ["盘中快照"],
        "prod_card_patterns": ["fupan", "review"],
        "exclude_patterns": [],
    }
    rule.update(over)
    cfg = {
        "feishu_webhook_prod": FAKE_URL,
        "feishu_webhook_test": FAKE_URL,
        "feishu_webhook_intraday": FAKE_URL,
        CLIENT_KEY: FAKE_URL,
        "client_mirror": {"enabled": enabled, "rules": [rule]},
    }
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return p


def _targets(monkeypatch, tmp_path, card_name, source_key, card=None, **over):
    monkeypatch.setattr(pc, "SETTINGS", _write_settings(tmp_path, **over))
    return pc.mirror_targets(card_name, source_key, card)


# ---- 0：标题提取兼容两种结构 ----
def test_card_title_reads_both_structures():
    assert pc.card_title(_feishu_card(SNAPSHOT_TITLE)) == SNAPSHOT_TITLE
    assert pc.card_title({"title": HUNTER_TITLE}) == HUNTER_TITLE
    assert pc.card_title({}) == ""
    assert pc.card_title(None) == ""


# ---- 1：短线精灵 → 命中 ----
def test_short_spirit_mirrors(monkeypatch, tmp_path):
    t = _targets(monkeypatch, tmp_path, "short_spirit_card.json", "feishu_webhook_intraday")
    assert len(t) == 1
    assert t[0]["key"] == CLIENT_KEY
    assert t[0]["name"] == "测试客户"


# ---- 2：盘中快照 vs 盘中猎手（同文件名·靠标题区分）----
def test_snapshot_card_mirrors_by_title(monkeypatch, tmp_path):
    t = _targets(monkeypatch, tmp_path, "limao_card.json", "feishu_webhook_prod",
                 card=_feishu_card(SNAPSHOT_TITLE))
    assert len(t) == 1, "盘中快照卡应按标题命中镜像"


def test_hunter_card_same_filename_not_mirrored(monkeypatch, tmp_path):
    """⛔ 关键：判定维度是**标题**，不是文件名。

    ⚠️ 2026-10-14 语义变更（勿误读）：本测试用的是 `_write_settings` 造的**替身配置**
    （`title_patterns: ["盘中快照"]`），断言的是「文件名 `limao_card.json` 本身不构成命中」
    —— 即 **`card_patterns` 不含 limao_card**，判定必须落到标题。
    **它不再是「猎手不外发」的证据**：生产配置 2026-10-14 起 `title_patterns` 已含「盘中猎手」，
    猎手卡在生产语义下**应当命中**（见 `test_real_settings_title_patterns_include_hunter`）。
    """
    assert _targets(monkeypatch, tmp_path, "limao_card.json", "feishu_webhook_prod",
                    card=_feishu_card(HUNTER_TITLE)) == []


def test_hunter_card_mirrors_when_title_pattern_enabled(monkeypatch, tmp_path):
    """生产语义：`title_patterns` 含「盘中猎手」→ 猎手卡命中（2026-10-14 用户拍板纳入客户群）。

    与上一条互为阴阳：同一张卡、同一文件名，只因配置不同而结果相反 ⇒ 锁定「配置是唯一开关」。
    """
    t = _targets(monkeypatch, tmp_path, "limao_card.json", "feishu_webhook_prod",
                 card=_feishu_card(HUNTER_TITLE),
                 title_patterns=["盘中猎手", "盘中快照", "复盘"])
    assert len(t) == 1, "生产语义下猎手卡应命中客户群"
    assert t[0]["key"] == CLIENT_KEY


def test_real_settings_title_patterns_include_hunter():
    """锁死生产配置：`client_mirror` 的 title_patterns 必须含「盘中猎手」（2026-10-14 用户指令）。

    防回归：日后有人「整理配置」时删掉它 → 客户群静默收不到猎手卡（不报错、无告警）。
    """
    p = Path(__file__).resolve().parents[1] / "config" / "settings.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    for r in cfg["client_mirror"]["rules"]:
        tp = r.get("title_patterns") or []
        assert "盘中猎手" in tp, \
            f"规则 {r.get('client_name')} 的 title_patterns 缺少「盘中猎手」（客户群将静默收不到猎手卡）"


# ---- 3：复盘卡（仅 prod）----
def test_fupan_card_to_prod_mirrors(monkeypatch, tmp_path):
    for name in ("fupan_card_2026-09-10.json", "review_card_2026-08-17.json",
                 "card_review_20260903.json", "fupan_20260827.json"):
        t = _targets(monkeypatch, tmp_path, name, "feishu_webhook_prod")
        assert len(t) == 1, name


def test_fupan_pattern_only_applies_to_prod(monkeypatch, tmp_path):
    """prod_card_patterns 限定 prod 通道，异动群推的 fupan_* 不因此命中。"""
    assert _targets(monkeypatch, tmp_path, "fupan_card_2026-09-10.json",
                    "feishu_webhook_intraday") == []


# ---- 4：内部高频卡不命中（2026-09-11 收窄）----
def test_internal_intraday_cards_not_mirrored(monkeypatch, tmp_path):
    for name in ("limao_newsflash_express.json", "limao_sector_boost_card.json",
                 "limao_intraday_beat.json", "limao_pre_resonance_card.json",
                 "limao_nuke_card.json"):
        assert _targets(monkeypatch, tmp_path, name, "feishu_webhook_intraday") == [], name


def test_other_prod_cards_not_mirrored(monkeypatch, tmp_path):
    for name in ("panqian_card.json", "lhb_card_20260910.json",
                 "gangtise_evening_research.json", "card_20260910_1110_plain_eod.json"):
        assert _targets(monkeypatch, tmp_path, name, "feishu_webhook_prod") == [], name


# ---- 4.5：机构卡误命中回归锁（2026-09-14 新增）----
def test_research_card_with_review_name_needs_exclude(monkeypatch, tmp_path):
    """⛔ 机构卡命名含 review 会被 prod_card_patterns 误命中。

    起因（2026-09-14 排查）：`gangtise_weekend_review.json`（机构晚报·周末复盘）
    含子串 "review" → 命中 prod_card_patterns → 09-13 19:40 已实际尝试镜像到客户群
    （仅因客户群 token 失效 19001 而未遂）。机构内容不在白名单内。
    修正 = 生产配置 `exclude_patterns: ["gangtise"]`，本测试锁定该语义。
    """
    name = "gangtise_weekend_review.json"
    # 未加排除项时确实命中（记录脆弱点，防日后误删排除项）
    assert len(_targets(monkeypatch, tmp_path, name, "feishu_webhook_prod")) == 1
    # 生产配置语义下必须拦住
    assert _targets(monkeypatch, tmp_path, name, "feishu_webhook_prod",
                    exclude_patterns=["gangtise"]) == []


def test_real_settings_excludes_research_cards():
    """锁死生产配置：client_mirror 规则必须保留 gangtise 排除项。"""
    p = Path(__file__).resolve().parents[1] / "config" / "settings.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    rules = cfg["client_mirror"]["rules"]
    assert rules, "client_mirror.rules 不应为空"
    for r in rules:
        assert "gangtise" in (r.get("exclude_patterns") or []), \
            f"规则 {r.get('client_name')} 缺少 gangtise 排除项（机构卡含 review 会外泄）"


# ---- 5：测试群硬门 ----
def test_test_group_never_mirrors(monkeypatch, tmp_path):
    """⛔ 关键安全锁：测试群推的卡（含短线精灵/快照测试卡）绝不外泄到客户群。"""
    cases = [("fupan_card_2026-09-10.json", None),
             ("review_evolution.json", None),
             ("limao_card.json", _feishu_card(SNAPSHOT_TITLE)),
             ("short_spirit_card.json", None)]
    for name, card in cases:
        assert _targets(monkeypatch, tmp_path, name, "feishu_webhook_test", card=card) == [], name


# ---- 6-9：开关与边界 ----
def test_kill_switch_disables_all(monkeypatch, tmp_path):
    assert _targets(monkeypatch, tmp_path, "fupan_card_2026-09-10.json",
                    "feishu_webhook_prod", enabled=False) == []
    assert _targets(monkeypatch, tmp_path, "short_spirit_card.json",
                    "feishu_webhook_intraday", enabled=False) == []


def test_self_reference_skipped(monkeypatch, tmp_path):
    """客户群自身作为源时不镜像，防自指循环。"""
    assert _targets(monkeypatch, tmp_path, "fupan_card_2026-09-10.json", CLIENT_KEY) == []


def test_exclude_pattern_wins(monkeypatch, tmp_path):
    assert _targets(monkeypatch, tmp_path, "fupan_card_2026-09-10.json",
                    "feishu_webhook_prod", exclude_patterns=["fupan"]) == []


def test_exclude_pattern_matches_title(monkeypatch, tmp_path):
    """排除项对标题同样生效（如临时停发快照）。"""
    assert _targets(monkeypatch, tmp_path, "limao_card.json", "feishu_webhook_prod",
                    card=_feishu_card(SNAPSHOT_TITLE), exclude_patterns=["盘中快照"]) == []


def test_missing_client_key_degrades_safely(monkeypatch, tmp_path):
    """client_key 未在 settings.json 定义 → 跳过不崩。"""
    p = _write_settings(tmp_path)
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg.pop(CLIENT_KEY)
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pc, "SETTINGS", p)
    assert pc.mirror_targets("fupan_card_2026-09-10.json", "feishu_webhook_prod") == []


def test_channel_rule_still_supported(monkeypatch, tmp_path):
    """channels 整通道全量能力保留（当前未启用，便于日后一键放宽）。"""
    t = _targets(monkeypatch, tmp_path, "limao_newsflash_express.json",
                 "feishu_webhook_intraday", channels=["feishu_webhook_intraday"])
    assert len(t) == 1


# ---- 10：镜像直推不走 dedup，且逐目标独立 ----
def test_mirror_push_bypasses_dedup_and_pushes(monkeypatch, tmp_path):
    monkeypatch.setattr(pc, "SETTINGS", _write_settings(tmp_path))
    sent = []

    def _fake_push(card, wh, retries=2):
        sent.append(wh)
        return True, {"code": 0}

    def _boom(*a, **k):
        raise AssertionError("mirror_push 不应调用 dedup_check（主推已写 hash，查必误跳）")

    monkeypatch.setattr(pc, "push", _fake_push)
    monkeypatch.setattr(pc, "dedup_check", _boom)
    monkeypatch.setattr(pc, "_audit", lambda *a, **k: None)

    targets = [{"key": CLIENT_KEY, "name": "测试客户", "webhook": FAKE_URL}]
    pc.mirror_push({"msg_type": "interactive", "card": {}}, targets, "fupan_card_2026-09-10.json")
    assert sent == [FAKE_URL]


def test_mirror_push_failure_does_not_raise(monkeypatch, tmp_path):
    """镜像失败只记审计，不抛异常、不阻断主流程。"""
    monkeypatch.setattr(pc, "SETTINGS", _write_settings(tmp_path))
    audits = []
    monkeypatch.setattr(pc, "push", lambda card, wh, retries=2: (False, {"code": 19001, "msg": "chat not found"}))
    monkeypatch.setattr(pc, "_audit", lambda *a, **k: audits.append(k))

    pc.mirror_push({"msg_type": "interactive", "card": {}},
                   [{"key": CLIENT_KEY, "name": "测试客户", "webhook": FAKE_URL}],
                   "fupan_card_2026-09-10.json")
    assert len(audits) == 1
    assert audits[0]["result"] == "fail"
    assert audits[0]["webhook"] == CLIENT_KEY
