#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""短线精灵·盘中异动信号引擎（2026-08-18 用户定）。

从盘中异动中筛选高质量、低噪音个股并推送：
  1. 信号池：东财涨幅榜（≥min_pct，两页 200 条，带 市值/成交额/上市日/主力净额/行业）
  2. 噪音过滤：ST/退市、次新(≤max_new_stock_days 天)、市值<min_market_cap_yi、成交额不达标（上午/下午分档）、非板块龙头
  3. 共振度：code 在 top5 板块 leader/followers=强；行业/名称命中热点关键词=中；名称含热点词=弱
  4. 信号强度 = 涨幅*0.4 + 成交额*0.2 + 共振*0.3 + 主力净流入*0.1（权重可配置）
  5. 推送：飞书卡按强度降序（当天每 code 去重 + push_cooldown_min 冷却）

用法：
  python3 scripts/short_spirit.py --push       # 检测+推送（守护链调用）
  python3 scripts/short_spirit.py --dry-run    # 只出 JSON 不推
"""
import argparse
import fcntl
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path  # 2026-09-04 --send 统一出口用

ROOT = pathlib.Path(__file__).resolve().parent.parent
# 守护进程按脚本路径启动时，sys.path 只有 scripts/；补项目根目录以加载 common。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.json_io import append_jsonl, load_json, load_jsonl, save_json

# 题材基座（2026-11-03）：属性标签过滤。选股宝 top5 里会混进「业绩增长」这类
#   **涨停原因归属**（11-03 10:32 实测 top5 第 4 位就是「业绩增长」3 家），
#   它不是题材、没有接力语义，⛔ 不能当「接力方向」推给用户。
try:
    from common.theme_base import is_attr_plate as _is_attr_plate
    from common.theme_base import family_of as _family_of
except Exception:                                  # pragma: no cover - 环境缺失降级
    def _is_attr_plate(_n):  # type: ignore[misc]
        return False

    def _family_of(_n):  # type: ignore[misc]
        return None

# 东财官方板块映射（2026-10-08 用户「系统自造聚合名改为东财官方板块名！！」）：
# 板块名后挂官方 BK 码，让「系统说的锂电池 = 东财 锂电池 BK1303」一目了然，
# 不再与东财板块体系各说各话。纯读表（cache/sector_official_map.json），**不联网**；
# 映射层缺失/异常 → 静默降级（不挂码，卡面同改动前）。
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
try:
    from sector_official import official_tag as _official_tag
except Exception:                                  # pragma: no cover - 环境缺失降级
    _official_tag = None

CFG_PATH = ROOT / "config" / "short_spirit.json"
OUT_PATH = ROOT / "cache" / "short_spirit_signals.json"
PUSHED_PATH = ROOT / "cache" / "short_spirit_pushed.json"
# 当日信号沉淀上限（2026-09-21）：实测一天信号量级为百条，300 足够覆盖且防止极端情形无限增长
HISTORY_TODAY_MAX = 300
SNAP = ROOT / "cache" / "pulse_snapshots" / "latest.json"
ROUND_PATH = ROOT / "cache" / "short_spirit_round.json"    # 涨停观测轮快照（当下性硬门·2026-09-07）
ROUND_DOWN_PATH = ROOT / "cache" / "short_spirit_round_down.json"  # 跌停观测轮快照（下跌当下性硬门·2026-09-21 平行独立）
ENABLE_SPIRIT_TIMELINESS = True  # ⛔ 涨停当下性硬门（用户「时间顺序第一」·2026-09-07 盘中激活）：
#   每轮记录「被观测为涨停」的票；上轮已观测=存量涨停(非当下新封)→剔除不推，
#   仅本轮新观测涨停才推。拦截「激活晚/东财恢复把上午涨停下午补推」的旧闻。

EM_URL = ("https://push2.eastmoney.com/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2"
          "&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
          # f8=换手率 / f10=量比（2026-10-14 后排预判加：判「温和放量」的唯一客观字段）
          "&fields=f2,f3,f6,f8,f10,f12,f14,f18,f20,f21,f26,f62,f100")
# 跌幅榜（2026-09-21 用户「盲区补强」）：po=0=按 f3 降序 → 跌得最狠的排前。其余同 EM_URL。
EM_DOWN_URL = ("https://push2.eastmoney.com/api/qt/clist/get?pn={pn}&pz=100&po=0&np=1&fltt=2&invt=2"
               "&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
               "&fields=f2,f3,f6,f12,f14,f18,f20,f21,f26,f62,f100")
# 回退用（2026-10-14）：与加 f8/f10 前的 fields 完全一致 —— 见 fetch_pool 的保险分支。
EM_URL_LEGACY = ("https://push2.eastmoney.com/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2"
                 "&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
                 "&fields=f2,f3,f6,f12,f14,f18,f20,f21,f26,f62,f100")

# ── 短线精灵·市值门槛豁免（2026-09-07 用户 A+B 方案 · 默认关）──────────────
# 背景：用户情绪核心战法（08-14）连板龙头多中小盘，原 min_market_cap_yi=100 把
#   青山纸业(87.6亿·10天5板)等龙头漏推。A=连板龙头/板块核心票豁免市值门槛；
#   B=非豁免票门槛降到 30 亿。⛔ 两开关默认 False=与改动前行为完全一致；
#   盘中禁改生效，收盘后翻 True + 次日盘中验证（09-03 双推教训）。
ENABLE_SPIRIT_CAPEXEMPT = True    # A：连板(boards>=2)或板块核心票 豁免市值门槛（2026-09-07 用户「直接开始执行」盘中激活）
SPIRIT_LOWCAP_YI = 30             # B：开启 LOWCAP 后非豁免票市值门槛（亿）
ENABLE_SPIRIT_LOWCAP = True       # B：非豁免票市值门槛降至 30 亿（2026-09-07 用户「直接开始执行」盘中激活）
ENABLE_SPIRIT_LEADER_FALLBACK = True  # C：东财抓取失败(下午常空池)→用 latest.json 连板龙头(boards>=2)兜底（同上盘中激活）

# ── 20cm 高辨识度豁免成交额门槛（2026-10-08 用户「直接盘中开始做」）──────────────
# 背景：领湃科技(300530) 10-08 首板 20cm 涨停（锂电池），全天成交仅 3.47 亿 < 下午门槛
#   10 亿 → 被 noise_filter 成交额门槛剔除；而同方向连板龙头(时代万恒4板/传艺科技3板)
#   走 _leaders_as_pool 硬塞 amount_yi=999 绕过了门槛 → 被推。
#   即「首板 20cm 高辨识度票」成系统性盲区（既非连板、又够不到成交额门槛）。
# 规则（_amount_exempt）：创业板 20cm(300/301) + 涨幅≥SPIRIT_AMT_EXEMPT_MIN_PCT（冲板强度）
#   + 已被识别为主线票（latest leaders / top5 核心 / 连板≥1 / 行业命中热点）
#   + 成交额≥SPIRIT_AMT_EXEMPT_MIN_YI（保底流动性，挡无成交小票）。
# 两条路径同源（2026-10-08 修开关不一致 bug）：
#   ① 东财池可用 → noise_filter 内 _amount_exempt 放行；
#   ② 东财池失败 → _leaders_as_pool 兜底池纳入首板 20cm 涨停。
# ⛔ 二者共用本开关：ENABLE_SPIRIT_AMT_EXEMPT_20CM=False 即整体回到改动前行为（与 A/B/C 同套路）。
ENABLE_SPIRIT_AMT_EXEMPT_20CM = True
SPIRIT_AMT_EXEMPT_MIN_PCT = 15.0   # 20cm 票涨幅门槛（15%≈20cm 冲板线，含首板/前强）
SPIRIT_AMT_EXEMPT_MIN_YI = 1.0     # 豁免仍保留的绝对流动性下限（亿）
LEADER_FALLBACK_20CM_MIN_PCT = 19.0  # C 兜底池纳入「首板 20cm 涨停」的涨幅门槛（≈20cm 涨停线）

# ── 板块接力方向（2026-10-08 用户核心功能 · 选项 A「盘中即时」）──────────────────
# 用户原话：「当你捕获到这个板块的强势股涨停时，我们要顺势推出应该买什么」。
# 定位：短线精灵从「只报谁涨了」升级为「报谁涨了 + 该方向该买什么」。
# 输出三段（对应龙头战法体系 · 前排/后排分级）：
#   ① 👑 前排（谁领涨）= 该板块今日**已封板里板数最高**者（limit_up_stocks 按板数降序取首）；
#      带涨幅；若它正是本轮捕捉的那只 → 追加「← 本轮捕捉」标记。
#   ② 🎯 后排可参与（买谁）= latest.json 的 **leaders 识别度榜** 中同板块、涨幅≥
#      RELAY_HINT_MIN_PCT、且**未在涨停清单**的强势票 → 这才是「现在能买什么」，**必须带涨幅**。
#   ③ 🔒 其余已封板（参考）= 前排与本轮已捕捉之外的已封板票，仅供看身位，⛔ 不当作可买标的。
#     （实证 10-08：锂电池 8 家封板 → 前排 时代万恒(4天4板)；
#      后排可参与 金银河(300619)+16.11% / 尚水智能(301513)+12.17%；
#      其余已封板 紫竹高科3板/传艺科技3板/龙蟠科技2板。）
# ⛔ 只对 top5 主线板块提示 —— 非主线不推（体系：跟风杂毛禁做；只推主线身位票）。
# ⛔ 排除：ST / 科创板688（无权限）/ 黑名单（京能系·金能科技）/ 本轮已推 / 涨幅不足。
# ⛔ 可逆开关：ENABLE_SPIRIT_RELAY_HINT=False 即回到「只报异动、不给方向」的改动前行为。
# ── 板块联动归纳（2026-10-12 用户「尽量进行板块联动的观察归纳」）──────────────
# 把本轮信号按板块（东财行业名）聚合 → 卡面「🔥 板块联动」段：每板块一行
#   「板块 N只(涨停M)｜最强 X(+pct)」。纯呈现层·零新数据源·渲染 try/except 降级。
# ⛔ 关开关即回到改动前卡面（与 A/B/C/relay 同套路）。
ENABLE_SPIRIT_SECTOR_GROUP = True
SECTOR_GROUP_MIN_ITEMS = 2      # 至少 N 只才列为「联动」（单只不成板块联动）
SECTOR_GROUP_MAX = 8            # 最多列几个板块（防刷屏）
# 板块联动的**聚合数据源**（2026-11-03）：True = 用过滤后全池；False = 回到截断后的 signals。
#   背景：strength 里成交额占 0.2 权重，一字板成交额天然小 → 被挤出 max_signals 名额 →
#   「板块联动」段跟着丢票。11-03 实证：涨停池 51 只里短剧/互动影游 5 只，卡面只剩 1 只。
#   ⛔ 只影响**展示聚合**，不改推送名额/排序/评分/裁决；关掉即回改动前行为。
ENABLE_SPIRIT_GROUP_FULL_POOL = os.environ.get("ENABLE_SPIRIT_GROUP_FULL_POOL", "1") != "0"
# 板块联动**按族归并**（2026-11-03）：传媒线在东财口径下被拆成 游戏Ⅱ/影视院线/数字媒体/
#   广告营销 四条 2 只的小线 → 用户看到「一堆杂毛」而非「传媒大爆发」。
#   归族后显示「传媒 9只」——与用户心智、与选股宝 plates 口径对齐。
#   ⛔ 纯展示层，不改 signals 的 theme 字段、不改推送/排序/评分。
ENABLE_SPIRIT_GROUP_FAMILY = os.environ.get("ENABLE_SPIRIT_GROUP_FAMILY", "1") != "0"

# ── 后排预判（2026-10-14 用户「再给我推一个今天有可能涨停的个股，就是后排的」）────────
# 用户原话：「短线精灵，现在除了个股和板块联动，再给我推一个今天有可能涨停的个股；
#   板块联动，除了最强那只，后面还要跟一个板块跟风，然后列举几个个股+涨幅！
#   尽量要有辨识度+地位+温和放量」。
# 定位：现有 signals 只报「已经异动」的票 → 本节报「还没封板、但今日有封板可能的后排」，
#   并从**原始涨幅榜**（POOL_CACHE.up）里找，不依赖 signals（signals 已过滤掉未异动票）。
# 选票三判据（用户口径·缺一不可）：
#   ① 温和放量 = 量比 vol_ratio ∈ [BS_VOL_MIN, BS_VOL_MAX]（缩量无动能 / 爆量抛压重，都不要）；
#   ② 有辨识度 = top5 题材成分 / latest leaders 榜 / 连板≥1 / 行业命中热点（至少一项）；
#   ③ 有地位   = 涨幅已进该板块前列（板块内涨幅排名 ≤ BS_SECTOR_RANK）。
# 且必须「仍在路上」：涨幅已启动但**未封板**（主板 [BS_PCT_MIN, 涨停线)，20cm [BS_PCT_MIN_20, 涨停线)）。
# ⛔ 排除：ST/退市/科创板688（无权限）/黑名单（京能系·金能科技）/次新（复用 BS_MAX_NEW_DAYS）。
# ⛔ 两处用途共用同一批候选：① 板块联动段每行后追加「跟风」；② 独立「今日可能涨停」段。
# ⛔ 可逆开关：ENABLE_SPIRIT_BACKSEAT=False → 卡面回到仅「个股 + 板块联动」的改动前形态。
ENABLE_SPIRIT_BACKSEAT = True
BS_PCT_MIN = 4.0            # 主板后排涨幅下限（<4% 动能不足，谈不上「有望涨停」）
BS_PCT_MIN_20 = 7.0         # 20cm 票后排涨幅下限（同一动能等价于主板的 4%）
BS_PCT_MAX = 9.9            # 主板未封板上限（≥9.9 视为已封/炸板 → 归 signals 涨停段，不重复报）
BS_PCT_MAX_20 = 19.5        # 20cm 同上
BS_AMT_RATIO = 0.6          # 后排票成交额天然小于前排龙头 → 成交额门槛按主链打折
BS_VOL_MIN = 1.3            # 温和放量下限（<1.3 = 缩量，没资金推）
BS_VOL_MAX = 6.0            # 温和放量上限（>6 = 爆量，抛压重、容易一日游）
BS_SECTOR_RANK = 3          # 板块内涨幅排名前 N 才算「有地位」
BS_MAX_ITEMS = 3            # 「今日可能涨停」段最多列几只（防刷屏）
BS_FOLLOW_MAX = 2           # 板块联动段每板块最多列几只跟风
BS_MAX_NEW_DAYS = 365       # 次新剔除窗口（与 noise_filter 同口径）
BS_EXCLUDE_NAME_KEYS = ("京能", "金能")   # 黑名单（宪法：京能系 / 金能科技）

# ── B 档·后排关联（2026-10-12 用户「取选股宝题材分类下的数据·含已封板」）──────────
# 用选股宝题材 top5[题材].zt_stocks（该题材涨停票·含 code）补全 relay 后排池，
#   比按 limit_up_stocks.sector 名称匹配更准（选股宝直接标注的该题材票）。
# ⛔ 关开关即回退到仅用 limit_up_stocks 匹配的改动前行为。
ENABLE_SPIRIT_THEME_BACKSEAT = True

ENABLE_SPIRIT_RELAY_HINT = True
RELAY_HINT_MIN_PCT = 7.5        # 后排可参与票涨幅下限（**仅 leaders 兜底路径**用；确保是「强势」而非滞涨跟风）
RELAY_HINT_MAX_ITEMS = 3        # 后排可参与票最多列几只（防刷屏）
RELAY_HINT_LADDER_MAX = 4       # 已封板参考行最多列几只
RELAY_EXCLUDE_NAME_KEYS = ("京能", "金能")   # 黑名单（宪法：京能系 / 金能科技）

# ── 后排「现在能买」真后排池（2026-10-20 用户「推的票都涨停了 没意义」）──────────────
# 病灶：原 tradable 只从 latest.leaders 取（那是**连板龙头/人气榜**，常年已封板），
#   加 RELAY_HINT_MIN_PCT=7.5 的门槛 → 低空经济这类「2 家涨停、其余没动」的板块必然全空，
#   卡面只剩前排（买不进）+ 后排关联（也都封着）→ 对用户等于无信息。
# 修法：直接取**该板块东财 BK 成分涨幅榜**（一次请求），按「已启动未封 + 放量 + 非断板」筛。
ENABLE_SPIRIT_RELAY_BACKSEAT = True   # False → 完全不联网，退回旧 leaders 兜底路径（测试默认关）
RELAY_BACKSEAT_MIN_PCT = 3.0          # 已启动下限：低于 3% 视为没动，不是「可参与」
RELAY_BACKSEAT_VOL_MIN = 1.3          # 量比下限（缩量不动）
# ⚠️ 上限放宽到 15 而不是 6：量比 = 当日均量/过去 5 日同时段均量，**开盘半小时天然虚高**
#   （实测 10-20 09:35 万向钱潮 14 / 杰创智能 6.9 / 保隆科技 95）。卡 6.0 会把真启动票全杀掉，
#   只剩「没动」的票——正是本次要修的病。真正该挡的爆量（>15）留着。
RELAY_BACKSEAT_VOL_MAX = 15.0
RELAY_BACKSEAT_MAX_ITEMS = 2          # 用户口径：推两个就够
# ⚠️ 2026-10-20 10:30 实测：**主域 `push2.eastmoney.com` 可用，四个子域全 RemoteDisconnected**。
#   原实现只轮询子域（33/21/98/82）→ 盘中持续抓取失败 → 卡面「该方向已无未封板票」。
#   那是把「抓不到数据」当成「结论」。主域放第一位，子域做备胎。
RELAY_BACKSEAT_HOSTS = (
    "push2.eastmoney.com",
    "33.push2.eastmoney.com",
    "21.push2.eastmoney.com",
    "98.push2.eastmoney.com",
    "82.push2.eastmoney.com",
)
RELAY_BACKSEAT_URL = (
    "https://{h}/api/qt/clist/get?"
    "ut=bd1d9ddb04089700cf9c27f6f7426281&pn=1&pz=60&po=1&np=1&fltt=2&invt=2&fid=f3"
    "&fs=b:{bk}&fields=f2,f3,f6,f8,f10,f12,f14,f21")

# ── 口径融合 · 辨识度 & 溢价可能性（2026-10-20 用户「两个口径融合 并推送辨识度和溢价可能性」）
# 病灶（用户原话「🔒 后排关联：博敏电子 显示涨幅！！」）：
#   ① 卡面「🔒 后排关联」只打印 `名字(板数)` —— **连涨幅都没有**（博敏电子裸名一个）；
#   ② 已封板(pct 来自面板快照) 与 未封板(pct 来自东财实时) **两个源、两个时刻**并排 → 无法比较，
#      违反宪法「对齐铁律：比较必须同刻」；
#   ③ 每只票只报「涨了多少」，不报**它是谁**（辨识度）和**明天能给多少溢价** —— 而这才是决策依据。
# 修法：
#   ① 一次取板块成分快照 → **已封板与未封板共用同一份 pct/换手/量比**（同源同刻）；
#   ② 每只票补 辨识度标签（板数/板块龙一/身位/人气榜/炸板）；
#   ③ 每只票给 溢价可能性（高/中/低 + 依据），依据必须是数得出来的字段。
ENABLE_SPIRIT_RELAY_ENRICH = True     # False → 卡面回到「裸名 + 无溢价」的改动前形态
PREMIUM_HIGH = 2.0                    # 溢价打分 ≥ 此值 → 高
PREMIUM_MID = 0.5                     # ≥ 此值 → 中；其余 → 低
# 换手率分区（%）：一字锁筹给半档加成，巨量换手是筹码散了
TURN_LOCK_MAX = 1.5
TURN_HEALTHY = (2.0, 10.0)
TURN_HEAVY = 15.0
# 板块强度分档（今日涨停家数）
ZT_STRONG = 5
ZT_WEAK = 2
# 高度分档（连板数）：2~3 板有晋级空间；≥5 板高位风险
BOARDS_SWEET = (2, 3)
BOARDS_HIGH = 5
# 触发口径（用户原话「捕获到这个板块的强势股涨停时」）：只在卡内含**涨停/接近涨停**信号时给方向；
# 快速拉升（低强度）不给 —— 避免每张卡都挂一行方向。
RELAY_TRIGGER_TYPES = ("涨停", "接近涨停")
# 限频（09-15 铁律：**冷却 ∧ 内容指纹变化**，二者需同时满足才重发；⛔ 禁纯时间冷却）：
# 指纹 = 板块名 + 前排 code + 涨停家数 + 已封板 codes + 后排可参与 codes@涨幅。
# 指纹变了（如后排票涨停进前排）→ 立即重发；指纹不变 → 须过冷却才重发，防同板块连卡刷屏。
# 设为 0 = 每张相关卡都显示（等于关闭限频）。
RELAY_COOLDOWN_MIN = 20
# 2026-11-05：接力段「身份 vs 状态」分离。关掉回到旧指纹判定（含涨幅抖动）。
ENABLE_SPIRIT_RELAY_IDENTITY_DELTA = True
# 2026-11-05：后排候选（跟风/今日可能涨停）当日已展示不重展。关掉回到每卡全量重算。
ENABLE_SPIRIT_BACKSEAT_DELTA = True

# ── 低强度合并（2026-09-17 用户「前后端都改」）──────────────────────────────
# 用户规则：① 低强度推送合并（1 分钟内只推了一个、强度不高的合并）
#          ② 高强度联动爆发（多板块联动 → 即时聚合成一张）
#          ③ 普通推送（无板块联动的独立事件，如单个涨停 → 普通平卡）
# ⚠️ 「低强度」按**既有类型语义**表达，**不新造数值阈值**：
#   信号类型由 `signal_type()` 产出 —— 涨停 / 炸板·回落 / 接近涨停 / 快速拉升。
#   其中「快速拉升」= 涨幅未达冲板门槛（主板 <7.5% / 20cm <15%），即强度最低的一档。
#   ⛔ 涨停（规则③要出平卡）、接近涨停（冲板预警·用户明确要「先于涨停播报」）、
#      炸板·回落（风险须即时）**都算高强度**，不受合并影响。
# 规则：本轮池内**只有 1 条且全为低强度** → 不单独出卡，等相邻低强度一起推
#      （用户选「只合并相邻低强度时刻」）。
#      ⚠️ 超时兜底（batch_timeout_min）不受影响 → 信息不丢。
#      ⚠️ 早盘高频窗口 09:25–09:55 豁免：该窗口用户最高优先级要求「有信号就立刻推」。
# 关闭方式：ENABLE_SPIRIT_LOWSTRENGTH_MERGE = False（回到改动前行为）
# ⛔⛔ 默认 **False（关闭）** —— 2026-09-17 用户明确「保证盘中异动那边高频不要变化，
#      这边（展示）慢慢优化」。故本能力**只作为已实现、已测试的待用能力保留**，
#      ⛔ 绝不默认改变盘中推送节奏。要启用须用户明确指示，且收盘后 + 次日盘中验证。
ENABLE_SPIRIT_LOWSTRENGTH_MERGE = False
LOW_STRENGTH_TYPES = ("快速拉升", "快速下跌")  # 2026-09-21 加 快速下跌：单条下跌同单条拉升→合并等待，不单独刷屏
EARLY_REALTIME_START_MIN = 9 * 60 + 25   # 09:25
EARLY_REALTIME_END_MIN = 9 * 60 + 55     # 09:55


# 东财两页请求是否完整。仅用于审计和前端展示，不因单页失败丢弃已取得的信号。
FETCH_STATUS = {"up": {}, "down": {}}

# 板块全成分快照缓存（2026-11-03）：东财 clist **按时刻**限频，密集重试会被临时封禁。
#   {bk: (抓取时刻, 结果|None, 有效期秒)}；成功 60s、失败 20s（短退避）。
_BOARD_SNAP_CACHE: dict = {}
BOARD_SNAP_TTL_SEC = 60
BOARD_SNAP_FAIL_TTL_SEC = 20

# 原始候选池缓存（2026-10-14：「今日可能涨停的后排」段需要**未过滤**的涨幅榜，
#   而 `signals` 只含已异动票 → 后排候选必须回到原始池里找）。
# ⛔ 只写不读业务状态，与 FETCH_STATUS 同套路；东财抓取失败 → 空池 → 后排段自然降级为空。
POOL_CACHE = {"up": [], "down": []}

# 板块联动的聚合数据源（2026-11-03）：`build_signals` 每轮写入**过滤后全池**的 signal 视图，
#   `build_card` 取它做「🔥 板块联动」聚合（而非截断后的推送序列）。
#   ⛔ 只用于展示聚合，不参与推送名额/排序/评分；开关 ENABLE_SPIRIT_GROUP_FULL_POOL=0 时写 None。
GROUP_VIEW_CACHE = {"view": None}


def _lock_path() -> Path:
    """锁文件随 ROOT 动态计算，测试切换临时根目录时不会触及生产锁。"""
    return ROOT / "cache" / "short_spirit.lock"


def http_get(url, t=8, headers=None):
    """本文件专用轻量 GET（裸 urllib，**无重试** —— 重试由调用方按限频策略控制）。

    ⚠️ 2026-11-03：原来只带 `User-Agent`。东财 push2 接口对**缺 Referer** 的请求
    会直接 `RemoteDisconnected`（实测：带 Referer 能拿到 20 行成分，不带则空回）。
    现在允许调用方补齐请求头。
    """
    hdr = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
    if headers:
        hdr.update(headers)
    req = urllib.request.Request(url, headers=hdr)
    with urllib.request.urlopen(req, timeout=t) as resp:
        return json.loads(resp.read().decode())


def load_cfg():
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"min_pct": 3.0, "min_market_cap_yi": 100, "amount_morning_yi": 5,
                "amount_afternoon_yi": 10, "min_pct_down": 7.5, "max_new_stock_days": 365, "top_per_sector": 2,
                "max_signals": 10, "batch_threshold": 10, "batch_timeout_min": 30,
                "weight": {"pct": 0.4, "amount": 0.2, "resonance": 0.3, "flow": 0.1}}


def is_trading_day(now=None) -> bool:
    """A股交易日 —— **唯一真源 = common.trading_calendar**（2026-10-09 唯一化）。

    ⛔ 本文件不得自行判 `weekday()`：`in_trading` 与 `in_early_realtime_window`
       曾各写一份周末判定，把工作日休市（中秋 2026-09-25）与调休补班周末当交易日。
    """
    now = now or datetime.now()
    try:
        from common.trading_calendar import is_a_share_trading_day
        return is_a_share_trading_day(now)
    except Exception:  # pragma: no cover - 极端环境兜底
        return now.weekday() < 5  # trading-day-fallback: 唯一真源不可用时的周末兜底


def in_trading() -> bool:
    now = datetime.now()
    hm = now.hour * 60 + now.minute
    # 09:25 起（2026-08-24：早盘前半小时高频实时窗口从竞价结束 09:25 开始，秒板/冲板
    # 信号不因 09:25-09:30 区间被挡；原 09:30 起点会漏掉开盘前 5 分钟的异动）
    in_window = (9 * 60 + 25) <= hm <= (11 * 60 + 30) or (13 * 60) <= hm <= (15 * 60)
    if not in_window:
        return False
    # 节假日门禁：法定节假日与周末（含调休补班日）一律不推送
    return is_trading_day(now)


def session() -> str:
    """上午 / 下午（成交额阈值分档）"""
    now = datetime.now()
    hm = now.hour * 60 + now.minute
    return "morning" if hm <= (11 * 60 + 30) else "afternoon"


def in_early_realtime_window(now=None) -> bool:
    """早盘前半小时高频实时窗口 09:25–09:55（与 live_signal.EARLY_REALTIME_WINDOW 对齐）。

    该窗口内用户最高优先级要求「接口有信号就立刻推」，故**低强度合并豁免**。
    """
    now = now or datetime.now()
    if not is_trading_day(now):
        return False
    hm = now.hour * 60 + now.minute
    return EARLY_REALTIME_START_MIN <= hm <= EARLY_REALTIME_END_MIN


def fetch_pool(cfg):
    """东财涨幅榜两页 → 原始信号池"""
    raw = []
    meta = {"pages_requested": 2, "pages_ok": [], "failed_pages": [], "errors": []}
    for pn in (1, 2):
        try:
            d = http_get(EM_URL.format(pn=pn))
            raw.extend(((d.get("data") or {}).get("diff") or []))
            meta["pages_ok"].append(pn)
        except Exception as e:
            meta["failed_pages"].append(pn)
            meta["errors"].append(str(e)[:160])
    # 保险（2026-10-14）：加量比/换手字段(f8/f10)后若**两页全失败** → 用原 fields 回退重试一轮，
    #   防「字段变更致东财报错 → 池空 → 短线精灵整体停摆」。仅失败路径触发，正常时零开销。
    #   ⛔ 回退池无 vol_ratio(=0) → 后排预判按「数据缺失放行」，不会空段。
    if not raw:
        for pn in (1, 2):
            try:
                d = http_get(EM_URL_LEGACY.format(pn=pn))
                raw.extend(((d.get("data") or {}).get("diff") or []))
                meta["pages_ok"].append(pn)
                meta["legacy_fallback"] = True
            except Exception as e:
                meta["errors"].append(f"legacy:{str(e)[:120]}")
    meta["complete"] = len(meta["pages_ok"]) == meta["pages_requested"]
    FETCH_STATUS["up"] = meta
    pool = []
    for it in raw:
        try:
            pool.append({
                "code": str(it.get("f12") or ""),
                "name": str(it.get("f14") or ""),
                "pct": float(it.get("f3") or 0),
                "price": float(it.get("f2") or 0),
                "amount_yi": round(float(it.get("f6") or 0) / 1e8, 2),
                "market_cap_yi": round(float(it.get("f20") or 0) / 1e8, 1),
                "float_cap_yi": round(float(it.get("f21") or 0) / 1e8, 1),
                "list_date": str(it.get("f26") or "") or None,
                "prev_close": float(it.get("f18") or 0),  # 2026-09-04 涨停封板校验（炸板票不误标涨停）
                "main_flow_yi": round(float(it.get("f62") or 0) / 1e8, 2),
                "industry": str(it.get("f100") or ""),
                "vol_ratio": float(it.get("f10") or 0),      # 量比（温和放量判据·2026-10-14）
                "turnover": float(it.get("f8") or 0),        # 换手率（辅证·同上）
            })
        except Exception:
            continue
    POOL_CACHE["up"] = pool          # 后排候选池取用（未过滤原始池·2026-10-14）
    return pool


# ── 全量涨停池·已封板事实源（2026-11-03 用户「立即处理！人工决断可改生产链路」）────
# 【病灶·11-03 实证】整个上午精灵手里只有 7~16 只，而真实涨停 **49 只**。
#   根因链条：`fetch_pool`（东财涨幅榜）盘中反复 RemoteDisconnected → 降级
#   `_leaders_as_pool`（latest.json 的**连板龙头**，7~16 只）→ 兜底池**天然不含
#   「今天新封的首板/一字」** → **抓取越失败，越只能看到昨天的老面孔，越看不见今天的一字潮**。
#   11-03 当天：竞价即封 11 只、传媒线 4 只 9:25 一字（大晟10分/华阳9/浙文9/龙版8），
#   全部落在池外 → 家数永远凑不够 → 「板块联动」不出 → 用户看到的「系统没发现传媒爆发」。
#
# 【修法】把东财**实时涨停池**（getTopicZTPool）作为「已封板事实源」并入候选池：
#   ① 独立接口、独立降级 —— 不再依赖涨幅榜抓取成败；
#   ② 一次请求拿到全字段 amount(成交额)/fund(封单)/ltsz(流通)/fbt(首封时刻)/
#      zbc(炸板)/lbc(板数)/hybk(行业) → 正是 seal_gate 四因素所需，无需二次请求；
#   ③ 涨停池的票 = **已确认封住**（不是「看到涨了」），其成交额小是**一字锁死的特征**
#      而非流动性缺陷 → 豁免成交额门槛（与 08-14 情绪核心战法、11-03 seal_gate 回测同向）。
#
# ⛔ 边界（与既有铁律一致）：
#   - 只决定「这只票能不能进候选池」，⛔ 不改评分权重/排序/名额/仓位/09:25 裁决；
#   - 涨幅榜（fetch_pool）继续负责**未封板/冲板/后排**，职责不合并、不互相顶替；
#   - 涨停池抓取失败 → 返回 []，退回改动前行为（⛔ 绝不用空池当「今天没涨停」）；
#   - 688（无权限）/北交所 / ST 在源头剔除，不吃下游过滤的运气。
# ⛔ 可逆开关：ENABLE_SPIRIT_ZT_POOL=0 一键回到「只看涨幅榜」的改动前行为。
ENABLE_SPIRIT_ZT_POOL = os.environ.get("ENABLE_SPIRIT_ZT_POOL", "1") != "0"
ENABLE_SPIRIT_ZT_AMT_EXEMPT = os.environ.get("ENABLE_SPIRIT_ZT_AMT_EXEMPT", "1") != "0"
# 涨停池事实票 **市值门槛** 豁免（2026-11-03 13:20）：与上面成交额豁免同源。
#   已封涨停的票，市值大小只影响「买不买得起」，不影响「这个板块今天几家涨停」。
#   实证：时代万恒 27 亿 → 被 30 亿 LOWCAP 挡掉 → 锂电 8 家看成 7 家。
ENABLE_SPIRIT_ZT_CAP_EXEMPT = os.environ.get("ENABLE_SPIRIT_ZT_CAP_EXEMPT", "1") != "0"
ZT_POOL_URL = ("https://push2ex.eastmoney.com/getTopicZTPool?"
               "ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=300"
               "&sort=fbt%3Aasc&date={date}")
# ⚠️ 东财 push2ex **必须带 Referer**（缺则 RemoteDisconnected，与 push2 同坑）
ZT_POOL_HEADERS = {"Referer": "https://quote.eastmoney.com/"}
ZT_POOL_TTL_SEC = 60          # 成功缓存 60s（涨停状态不会秒变；避免每轮多打一次）
ZT_POOL_FAIL_TTL_SEC = 20     # 失败短退避，防密集重试被限频
_ZT_POOL_CACHE: dict = {}

# 涨停池里的「已封板事实」标记字段（下游 noise_filter 据此豁免成交额门槛）
ZT_FACT_KEY = "_zt_fact"


def _zt_date_str(now=None) -> str:
    return (now or datetime.now()).strftime("%Y%m%d")


def fetch_zt_pool(now=None, *, enabled=None) -> list:
    """东财**实时全量涨停池** → 标准候选池结构（附 `_zt_rich` 原始字段）。

    与 `fetch_pool`（涨幅榜）的分工：
      · 涨幅榜 = 「谁在涨」（含未封板/冲板，用于后排预判）；
      · 涨停池 = 「谁已经封住了」（事实源，用于已封板播报与题材家数）。
    ⛔ 抓取失败返回 []（= 不知道），⛔ 不返回伪造的空结构冒充「今天没涨停」。
    """
    if enabled is None:
        enabled = ENABLE_SPIRIT_ZT_POOL
    if not enabled:
        return []
    date_s = _zt_date_str(now)
    hit = _ZT_POOL_CACHE.get(date_s)
    if hit and (time.time() - hit[0]) < hit[2]:
        return list(hit[1])
    rows: list = []
    ttl = ZT_POOL_TTL_SEC
    try:
        d = http_get(ZT_POOL_URL.format(date=date_s), t=8, headers=ZT_POOL_HEADERS)
        pool = ((d.get("data") or {}).get("pool") or []) if isinstance(d, dict) else []
        for it in pool:
            try:
                code = str(it.get("c") or "").strip()
                name = str(it.get("n") or "").strip()
                if len(code) != 6 or not name:
                    continue
                # 源头剔除：科创板 688（用户无权限）/ 北交所(4/8/9 开头) / ST·退市
                if code[:3] == "688" or code[0] in ("4", "8", "9"):
                    continue
                if "ST" in name.upper() or "退" in name:
                    continue
                pct = float(it.get("zdp") or 0)
                price = round(float(it.get("p") or 0) / 1000.0, 3)
                # prev_close 由 price 反推（与 _leaders_as_pool 同法）→ signal_type 判「涨停」而非「炸板」
                prev_close = round(price / (1 + pct / 100.0), 3) if pct > 0 and price > 0 else 0.0
                rows.append({
                    "code": code,
                    "name": name,
                    "pct": pct,
                    "price": price,
                    "amount_yi": round(float(it.get("amount") or 0) / 1e8, 2),
                    "market_cap_yi": round(float(it.get("tshare") or 0) / 1e8, 1),
                    "float_cap_yi": round(float(it.get("ltsz") or 0) / 1e8, 1),
                    "list_date": None,          # 涨停池不提供上市日 → 次新过滤放行（与兜底池一致）
                    "prev_close": prev_close,
                    "main_flow_yi": 0.0,        # 涨停池无主力净额 → 0（⛔ 不编造）
                    "industry": str(it.get("hybk") or ""),
                    "vol_ratio": 0.0,           # 涨停池无量比 → 0（后排判据不适用已封板票）
                    "turnover": float(it.get("hs") or 0),
                    ZT_FACT_KEY: True,
                    # 富字段：seal_gate 可直接消费（亿为单位，与 seal_gate 口径一致）
                    "_zt_rich": {
                        "fbt": it.get("fbt"),
                        "zbc": it.get("zbc"),
                        "boards": it.get("lbc"),
                        "seal_amt": round(float(it.get("fund") or 0) / 1e8, 3),
                        "lt_mktcap": round(float(it.get("ltsz") or 0) / 1e8, 2),
                        "cje_yi": round(float(it.get("amount") or 0) / 1e8, 3),
                        "boards_days": (it.get("zttj") or {}).get("days"),
                        "boards_ct": (it.get("zttj") or {}).get("ct"),
                    },
                })
            except Exception:
                continue
    except Exception as e:
        FETCH_STATUS["zt_pool"] = {"ok": False, "err": str(e)[:120], "date": date_s}
        ttl = ZT_POOL_FAIL_TTL_SEC
        rows = []
    else:
        FETCH_STATUS["zt_pool"] = {"ok": True, "n": len(rows), "date": date_s}
    _ZT_POOL_CACHE[date_s] = (time.time(), list(rows), ttl)
    return rows


def _merge_zt_pool(pool: list, zt_rows: list) -> list:
    """把涨停池并入候选池（按 code 去重，**涨停池优先**）。

    为什么涨停池优先：两者都是东财同一时刻的口径，但涨停池多带
    `fund/ltsz/fbt/zbc` 四个 seal_gate 必需字段；涨幅榜带量比/主力净额。
    合并后：结构字段以涨停池为准（已确认封板的事实更硬），量比/主力净额保留涨幅榜原值。
    """
    if not zt_rows:
        return pool
    by_code = {}
    for s in pool:
        c = str(s.get("code") or "")
        if c:
            by_code[c] = s
    for z in zt_rows:
        c = str(z.get("code") or "")
        if not c:
            continue
        base = by_code.get(c)
        if base is None:
            pool.append(z)
            by_code[c] = z
            continue
        # 已在涨幅榜 → 只补富字段与事实标记，⛔ 不覆盖涨幅榜的真实成交额/量比口径
        base[ZT_FACT_KEY] = True
        base["_zt_rich"] = z.get("_zt_rich")
        if not base.get("industry"):
            base["industry"] = z.get("industry")
    return pool


def fetch_pool_down(cfg):
    """东财跌幅榜两页 → 原始下跌信号池（2026-09-21 盲区补强，与 fetch_pool 平行）。"""
    raw = []
    meta = {"pages_requested": 2, "pages_ok": [], "failed_pages": [], "errors": []}
    for pn in (1, 2):
        try:
            d = http_get(EM_DOWN_URL.format(pn=pn))
            raw.extend(((d.get("data") or {}).get("diff") or []))
            meta["pages_ok"].append(pn)
        except Exception as e:
            meta["failed_pages"].append(pn)
            meta["errors"].append(str(e)[:160])
    meta["complete"] = len(meta["pages_ok"]) == meta["pages_requested"]
    FETCH_STATUS["down"] = meta
    pool = []
    for it in raw:
        try:
            pool.append({
                "code": str(it.get("f12") or ""),
                "name": str(it.get("f14") or ""),
                "pct": float(it.get("f3") or 0),
                "price": float(it.get("f2") or 0),
                "amount_yi": round(float(it.get("f6") or 0) / 1e8, 2),
                "market_cap_yi": round(float(it.get("f20") or 0) / 1e8, 1),
                "float_cap_yi": round(float(it.get("f21") or 0) / 1e8, 1),
                "list_date": str(it.get("f26") or "") or None,
                "prev_close": float(it.get("f18") or 0),
                "main_flow_yi": round(float(it.get("f62") or 0) / 1e8, 2),
                "industry": str(it.get("f100") or ""),
            })
        except Exception:
            continue
    return pool


def hot_keywords(d):
    """热点关键词集：top5 板块名 + 资金流入板块 + leaders 板块"""
    kws = set()
    for s in (d.get("top5") or [])[:5]:
        nm = str(s.get("name") or "")
        if nm and nm != "其他":
            kws.add(nm)
            kws.add(nm.replace("概念", "").replace("板块", ""))
    from common.em_flow import sector_risk_for_decision
    for x in (sector_risk_for_decision(d.get("sector_risk")).get("inflow_top") or [])[:5]:
        nm = str(x.get("name") or "")
        if nm:
            kws.add(nm)
    for l in (d.get("leaders") or [])[:20]:
        nm = str(l.get("sector") or "")
        if nm and nm != "其他":
            kws.add(nm)
    return {k for k in kws if len(k) >= 2}


def top5_codes(d):
    """top5 板块 leader/followers 的 code 集 = 强共振票"""
    codes = set()
    for s in (d.get("top5") or [])[:5]:
        L = s.get("leader") or {}
        if L.get("code"):
            codes.add(str(L["code"])[:6])
        for f in (s.get("followers") or []):
            if f.get("code"):
                codes.add(str(f["code"])[:6])
    return codes


def _boards_map(d):
    """code → 连板数（来自 latest leaders，短线精灵豁免连板龙头市值门槛用）。"""
    import re
    m = {}
    for l in (d.get("leaders") or []):
        c = str(l.get("code") or "")
        b = l.get("boards")
        if c and b is not None:
            mm = re.search(r"(\d+)", str(b))
            if mm:
                m[c[:6]] = int(mm.group(1))
    return m


def _sector_match(a: str, b: str) -> bool:
    """板块名匹配（⛔ 防「裸双向子串」假命中，同 09-15 回流前兆铁律）：
    精确相等，或短名 ≥3 字且被包含（挡 电子⊄电子布 / 电网⊄智能电网 这类泛行业名）。
    此处两边命名体系不同（东财行业名 vs top5 概念名），故双向都判，但均需 ≥3 字。"""
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 3 and a in b:
        return True
    if len(b) >= 3 and b in a:
        return True
    return False


def _sector_match_any(a: str, b: str) -> bool:
    """板块匹配·族感知（2026-11-03）。

    `_sector_match` 只认字面：「传媒」vs「影视院线」长度不足且不包含 → False。
    但板块联动段归族后显示的是「传媒」，而跟风候选的 sector 仍是东财行业名
    （游戏Ⅱ / 数字媒体 / 广告营销 …）→ 跟风段会被整段饿死。
    故先按字面判，再按 `THEME_FAMILIES` 同族判。
    """
    if _sector_match(a, b):
        return True
    fa, fb = _family_of(a), _family_of(b)
    return bool(fa and fb and fa == fb)


def _code_sector_map(d):
    """code → {板块名...}（来自 limit_up_stocks / leaders 各自的 `sector` 字段）。

    ⛔ 为什么需要它（2026-10-08 生产卡实证）：信号的 `sector` 是**东财行业名**（如「电池」），
    而 top5 是**概念名**（「锂电池」）—— 两套命名体系，名称匹配天然判不中：
    领湃科技(300530) 信号 sector=「电池」 vs top5=「锂电池」→ 接力块整块没出来（用户看不到）。
    但同一只票在 `limit_up_stocks` / `leaders` 里存的是**概念名**「锂电池」→ 用 code 反查即可跨体系对齐。
    ⛔ 严格不放宽 `_sector_match`（避免 电子⊄电子布 那类裸子串假命中重现）。
    """
    m = {}
    for key in ("limit_up_stocks", "leaders"):
        for x in (d.get(key) or []):
            if not isinstance(x, dict):
                continue
            c = str(x.get("code") or "")[:6]
            s = str(x.get("sector") or "")
            if c and s:
                m.setdefault(c, set()).add(s)
    return m


def _relay_sector_of(signals, d):
    """定位接力板块：本轮信号里第一只「涨停/接近涨停」命中 top5 主线板块的所属板块。
    命中口径 = 名称匹配（signal.sector ↔ top5 概念名）**或** code 反查
    （`_code_sector_map`：该 code 在 limit_up_stocks/leaders 里的概念名 ↔ top5）——
    后者解决「行业名 vs 概念名」跨体系判不中（实证：电池 ↔ 锂电池）。
    ⛔ 无命中返回 None —— 非主线不给方向（体系：只推主线身位票，不推跟风）。"""
    top5 = [str(s.get("name") or "") for s in (d.get("top5") or [])[:5] if s.get("name")]
    csmap = _code_sector_map(d)
    for sig in (signals or []):
        if str(sig.get("type") or "") not in RELAY_TRIGGER_TYPES:
            continue
        sec = str(sig.get("sector") or "")
        code = str(sig.get("code") or "")[:6]
        names = {sec} | csmap.get(code, set())
        for nm in top5:
            if _is_attr_plate(nm):
                continue          # ⛔「业绩增长/ST股」是涨停原因不是题材，不接这条线
            if any(_sector_match(nm, x) for x in names if x):
                return nm
    return None


def _theme_members(d, sector):
    """选股宝 top5 中该题材的**权威成分**（2026-11-03 基座化）。

    ⛔ 为什么不能用名字匹配（11-03 传媒实证）：
      `limit_up_stocks[].sector` 是**东财行业名**（广告营销/游戏Ⅱ/出版/纺织制造…），
      `top5[].name` 是**选股宝题材名**（短剧/互动影游）——两套体系，`_sector_match`
      天然判不中，于是 ladder 只剩靠 zt_stocks 补的几只，卡面把「8 家」写成「4 家」。
      而 top5 自己就带 `zt_stocks`（**带 code**）和 `zt_count`（题材涨停家数）——直接取用。

    返回 {name, codes:set[str], zt_count:int|None, stocks:list} 或 None（该题材不在 top5）。
    """
    for t5 in (d.get("top5") or []):
        if not isinstance(t5, dict):
            continue
        if not _sector_match(sector, str(t5.get("name") or "")):
            continue
        codes: set[str] = set()
        stocks: list[dict] = []
        for z in (t5.get("zt_stocks") or []):
            if not isinstance(z, dict):
                continue
            c = str(z.get("code") or "").split(".")[0][:6]
            if not c:
                continue
            codes.add(c)
            stocks.append({"name": z.get("name"), "code": c,
                           "pct": float(z.get("pct") or 0)})
        try:
            n = int(t5.get("zt_count"))
        except (TypeError, ValueError):
            n = None
        # ⛔ zt_count 取不到时返回 None（不是 0）——0 会被当成「该题材没涨停」的错误结论
        return {"name": str(t5.get("name") or ""), "codes": codes,
                "zt_count": n, "stocks": stocks}
    return None


def _relay_state_path() -> Path:
    """接力块限频状态（随 ROOT 动态计算 —— 测试切临时根目录时不触生产，同 _lock_path）。"""
    return ROOT / "cache" / "short_spirit_relay_state.json"


def _relay_fingerprint(r) -> str:
    """内容指纹（09-15 铁律）：板块 + 前排 code + 涨停家数 + 身位 codes + 可接力 codes@涨幅。

    ⚠️ 含 `pct`（状态量），保留作诊断与回滚兼容；**是否重发展示改由 `_relay_identity` 决定**。
    """
    fr = r.get("front") or {}
    return "|".join([
        str(r.get("sector") or ""), str(fr.get("code") or ""), str(r.get("zt_count") or 0),
        ",".join(str(x.get("code")) for x in (r.get("ladder") or [])),
        ",".join(f"{x.get('code')}@{x.get('pct')}" for x in (r.get("tradable") or [])),
    ])


def _relay_identity(r) -> str:
    """方向身份：是哪个方向、龙头是谁、梯队与可参与的是哪几只。

    ⛔ 不含 `pct`（涨幅每轮都在抖）、不含 `zt_count`（家数每分钟都在变）。
       身份变了才叫「换人 / 换方向」；状态抖动只能等冷却，不许绕过限频。
    """
    fr = r.get("front") or {}
    return "|".join([
        str(r.get("sector") or ""), str(fr.get("code") or ""),
        ",".join(str(x.get("code")) for x in (r.get("ladder") or [])),
        ",".join(str(x.get("code")) for x in (r.get("tradable") or [])),
    ])


def _relay_load_state(now) -> dict:
    """按日读取限频状态。跨日旧档一律作废。

    11-05 实测 `short_spirit_relay_state.json` 里还留着 10-09 / 10-12 的板块记录，
    平铺结构从不清理 → 改为 `{date, sectors:{...}}`；旧平铺档能读，但不跨日沿用。
    """
    try:
        state = json.loads(_relay_state_path().read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except Exception:
        state = {}
    today = now.strftime("%Y-%m-%d")
    if state.get("date") != today:
        return {"date": today, "sectors": {}}
    sec = state.get("sectors")
    if not isinstance(sec, dict):
        sec = {k: v for k, v in state.items() if isinstance(v, dict)}
    return {"date": today, "sectors": sec}


def _relay_should_emit(r, now):
    """限频判定（返回 (是否发, 指纹)）：身份变了立即发，状态抖动按冷却发。

    ⛔ 旧版把 `code@pct` 写进指纹 → 后排涨幅每轮微动就判「内容变了」→ 立即绕过冷却。
       11-04 实测同一板块「机器人」一天展示 7 次，多数只是涨幅/家数在抖。
    """
    fp = _relay_fingerprint(r)
    if RELAY_COOLDOWN_MIN <= 0:
        return True, fp
    prev = _relay_load_state(now)["sectors"].get(str(r.get("sector") or "")) or {}
    if not ENABLE_SPIRIT_RELAY_IDENTITY_DELTA:
        if prev.get("sig") != fp:      # 回滚路径：沿用旧指纹判定
            return True, fp
    elif prev.get("ident") != _relay_identity(r):
        return True, fp                # 换人 / 换梯队 / 换可参与票 → 信息不丢
    try:
        last = datetime.strptime(str(prev.get("ts") or ""), "%Y-%m-%d %H:%M:%S")
        return (now - last).total_seconds() >= RELAY_COOLDOWN_MIN * 60, fp
    except Exception:
        return True, fp


def _relay_mark(r, fp, now) -> None:
    """记录本次已展示（身份 + 内容指纹 + 时间），按日切分。"""
    state = _relay_load_state(now)
    state["sectors"][str(r.get("sector") or "")] = {
        "ident": _relay_identity(r), "sig": fp,
        "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
    p = _relay_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _yzt_codes(date_str: str = "") -> set:
    """昨日涨停票 code 集合（判「断板」用）。读失败 → 空集（降级放行，⛔ 不阻断）。"""
    try:
        p = ROOT / "cache" / "yesterday_zt.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        if date_str and str(d.get("date") or "") == date_str:
            return set()          # 同一天（盘中/盘后回放）→ 不是"昨日"，不判断板
        out = set()
        for it in (d.get("zt_list") or d.get("stocks") or []):
            c = str(it.get("code") or it.get("dm") or "").split(".")[0][:6]
            if c:
                out.add(c)
        return out
    except Exception:
        return set()


def _board_snapshot(bk: str) -> dict | None:
    """该板块**全成分实时行情快照**（2026-10-20 口径融合新增·一次请求喂所有段）。

    返回 `{code6: {name, pct, vol_ratio, turnover, amount_yi, float_cap_yi}}`；
    **抓不到返回 None**（=「不知道」），与 `{}`（=「板块确实没数据」）严格区分。

    ⛔ 为什么必须区分 None / {}：卡面曾把抓取失败渲染成
      「🎯 该方向已无未封板票 → 等明日竞价看不追高」。10-20 10:26 PCB 板即如此——
      实测该板块 205 只成分、涨幅榜第 4 的矩子科技 +9.99% 明明能买。
      把「抓不到」当「没有」= 对用户报了一个假结论（宪法①数据即事实）。

    ⚡ 2026-11-03 加 TTL 缓存（**原实现把自己打死了**）：
      东财 clist 是**按时刻**限频，不是按次数——密集重试会触发临时封禁，
      封禁期内连原本能成功的 URL 也一律 `RemoteDisconnected`（实测：成功 1 次后
      连续 8 次全挂，含此前成功的完全相同 URL）。
      原实现 5 host × 2 轮 = 单次调用最多 10 个请求，盘中每 30s 一轮 → 必然被封。
      现在：成功缓存 60s（盘中行情滞后 60s 可接受，卡面自带时间戳）；
      失败缓存 20s（短退避，防立刻重试加剧封禁）；单次最多 5 个请求、不 sleep 重试。
    """
    if not ENABLE_SPIRIT_RELAY_BACKSEAT or not bk:
        return None
    _now = time.time()
    _hit = _BOARD_SNAP_CACHE.get(bk)
    if _hit is not None:
        _ts, _val, _ttl = _hit
        if _now - _ts < _ttl:
            return _val                      # 成功（60s）/ 失败（20s）各自的有效期
    rows = None
    # ⚠️ 本条链路**不能依赖 http_get 的内部重试**（本文件的 http_get 是裸 urllib、无重试）。
    #   限频是东财按时刻控制的，密集重试只会延长封禁 → 策略是
    #   「最多 3 个 host、每个 1 次、失败即退避」，由上面的 TTL 缓存兜住重复调用。
    for h in RELAY_BACKSEAT_HOSTS[:3]:
        try:
            d = http_get(RELAY_BACKSEAT_URL.format(h=h, bk=bk), t=5,
                         headers={"Referer": "https://quote.eastmoney.com/"})
            if not isinstance(d, dict):
                continue
            diff = ((d.get("data") or {}).get("diff") or [])
            if diff:
                rows = diff
                break
        except Exception:
            continue
    FETCH_STATUS["relay_backseat"] = {"bk": bk, "ok": bool(rows), "rows": len(rows or [])}
    if not rows:
        _BOARD_SNAP_CACHE[bk] = (_now, None, BOARD_SNAP_FAIL_TTL_SEC)
        return None

    snap = {}
    for r in rows:
        code = str(r.get("f12") or "")[:6]
        if not code:
            continue
        try:
            snap[code] = {
                "name": str(r.get("f14") or ""),
                "pct": round(float(r.get("f3") or 0), 2),
                "vol_ratio": round(float(r.get("f10") or 0), 1),
                "turnover": round(float(r.get("f8") or 0), 2),
                "amount_yi": round(float(r.get("f6") or 0) / 1e8, 2),
                "float_cap_yi": round(float(r.get("f21") or 0) / 1e8, 1),
            }
        except (TypeError, ValueError):
            continue
    if snap:
        _BOARD_SNAP_CACHE[bk] = (_now, snap, BOARD_SNAP_TTL_SEC)
    return snap


def _seal_line(code: str) -> float:
    """该票涨停线（20cm / 10cm / 北交所 30cm）。"""
    if code.startswith(("920", "8", "4")):
        return 30.0
    return 20.0 if code[:3] in ("300", "301") else 10.0


def _rankable(code: str, q: dict) -> bool:
    """是否参与板块身位排序：ST / 科创板 / 北交所 / 黑名单 不占身位（用户无权限或不做）。"""
    name = str((q or {}).get("name") or "")
    if not name or not code:
        return False
    if code.startswith("688") or code.startswith(("920", "8", "4")):
        return False
    if "ST" in name.upper().replace(" ", "") or "退" in name:
        return False
    return not any(k in name for k in RELAY_EXCLUDE_NAME_KEYS)


def _relay_seg(it: dict) -> str:
    """接力段**统一渲染**（2026-10-20 口径融合的呈现层·三段共用·⛔ 不要各写一套）。

    格式：`名字(代码) 涨幅｜辨识度｜溢价中(依据)`
    为什么必须共用：原来 👑前排 用一套、🎯后排用一套、🔒后排关联只打印名字(板数) →
    同一个板块三段口径不一，用户看不出谁跟谁比（2026-10-20 用户「博敏电子 显示涨幅！！」）。
    """
    code = str(it.get("code") or "")[:6]
    name = str(it.get("name") or "")
    s = name + (f"({code})" if code else "")
    raw_b = str(it.get("boards") or "")
    if "板" in raw_b:                     # 保留「6天3板」的原始口径（几天几板是辨识度的一部分）
        s += f" {raw_b}"
    s += f" {_pct_tag(float(it.get('pct') or 0))}"
    extras = []
    tags = [str(t) for t in (it.get("ident") or []) if t]
    if tags:
        extras.append("·".join(tags))
    p = it.get("premium") or {}
    if p.get("level"):
        t = f"溢价{p['level']}"
        if p.get("why"):
            t += f"({p['why']})"
        extras.append(t)
    else:
        # 溢价算不出（依据不足）→ 至少给换手，⛔ 不拿空结论糊弄
        turn = float(it.get("turnover") or 0)
        if turn > 0:
            extras.append(f"换手{turn:.1f}%")
    if extras:
        s += "｜" + "｜".join(extras)
    return s


def _board_backseat(bk: str, zt_codes: set, pushed_codes: set, limit: int = 2,
                    date_str: str = "", snap: dict | None = None) -> list:
    """该板块**真后排池**：现在还能买的票（2026-10-20 新增·纯筛选不联网）。

    判据（对应用户「短线精灵逻辑」）：
      ① 已启动未封：`RELAY_BACKSEAT_MIN_PCT ≤ pct < 涨停线-0.2`
      ② 放量但不爆量：量比 ∈ [1.3, 15]（缺量比 → 放行，只降权不误杀）
      ③ **不是断板**：昨日涨停、今日没封 → 属「高度衰减」，⛔ 不算后排可买
      ④ 排除：ST / 科创板688 / 北交所(920·8x·4x) / 黑名单 / 本轮已推 / 已在涨停清单
    ⛔ 任何异常（东财限频/断网）→ 返回 []，由调用方退回旧 leaders 兜底路径。盘中链路安全第一。
    `snap` 传入 → 复用调用方已取的快照（**卡面所有段共用同一份数据 = 同源同刻**）。
    """
    if not ENABLE_SPIRIT_RELAY_BACKSEAT or not bk:
        return []
    if snap is None:
        snap = _board_snapshot(bk)
    if not snap:
        return []

    yzt = _yzt_codes(date_str)
    # 身位分母只算「可交易池」：ST / 688 / 北交所不算身位（用户无权限，不参与排序）
    tradeable_pcts = []
    for code, q in snap.items():
        nm = q.get("name") or ""
        if not nm or code.startswith("688") or code.startswith(("920", "8", "4")):
            continue
        if "ST" in nm.upper().replace(" ", "") or "退" in nm:
            continue
        tradeable_pcts.append(float(q.get("pct") or 0))
    out = []
    for code, q in snap.items():
        name = q.get("name") or ""
        if not code or not name:
            continue
        if code in zt_codes or code in pushed_codes:
            continue
        if code.startswith("688") or code.startswith(("920", "8", "4")):
            continue
        if "ST" in name.upper().replace(" ", "") or "退" in name:
            continue
        if any(k in name for k in RELAY_EXCLUDE_NAME_KEYS):
            continue
        pct = float(q.get("pct") or 0)
        seal = _seal_line(code)
        if pct < RELAY_BACKSEAT_MIN_PCT or pct >= seal - 0.2:
            continue
        vr = float(q.get("vol_ratio") or 0)
        if vr and not (RELAY_BACKSEAT_VOL_MIN <= vr <= RELAY_BACKSEAT_VOL_MAX):
            continue
        if code in yzt:
            continue                    # 昨板今断 → 高度衰减，不是后排可买
        # 板块内实时身位（涨幅排名·仅可交易池）—— 同一份快照里算，不另取数
        rank = 1 + sum(1 for p2 in tradeable_pcts if p2 > pct)
        out.append({"name": name, "code": code, "pct": round(pct, 2),
                    "turnover": round(float(q.get("turnover") or 0), 2),
                    "vol_ratio": vr,
                    "amount_yi": float(q.get("amount_yi") or 0),
                    "float_cap_yi": float(q.get("float_cap_yi") or 0),
                    "rank": rank, "state": "open", "sealed": False})
    out.sort(key=lambda x: -x["pct"])
    return out[:limit]


def _parse_boards(v) -> int:
    """连板数解析：`'6天3板'` → **3**（⛔ 不能取第一个数字，取 6 会把 3 板票说成 6 板）。

    兼容 `3` / `'3板'` / `'4天3板'` / `''` / `None`。解析不出 → 0（⛔ 不猜）。
    """
    if isinstance(v, int):
        return v
    s = str(v or "")
    m = re.search(r"(\d+)\s*板", s)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else 0


def _relay_board_ctx(d) -> dict:
    """板块上下文（辨识度/溢价要用到的**面板侧权威字段**·2026-10-20 新增）。

    ⛔ 全部只读 `latest.json`，不联网。字段缺失就给空值，⛔ 绝不猜。
      boards     : 名字 → 连板数（真源 = 面板 `ladder` 连板天梯；辅以 limit_up_stocks.boards）
      broken     : 今日炸过板的名字集（溢价要扣分 —— 炸过板 = 筹码松过）
      leaders    : 人气榜 code 集（辨识度加分）
      zt_names   : 今日涨停名字集（判封板用）
      max_boards : 全市场最高板（判「是不是总龙头」）
    """
    out = {"boards": {}, "broken": set(), "leaders": set(),
           "zt_names": set(), "max_boards": 0, "max_boards_count": 0}
    try:
        # 连板天梯：`[{"boards":4,"stocks":["吉大正元"]}, ...]` —— 板数唯一权威
        for row in (d.get("ladder") or []):
            if not isinstance(row, dict):
                continue
            nb = row.get("boards")
            try:
                nb_i = int(nb)
            except (TypeError, ValueError):
                continue
            for nm in (row.get("stocks") or []):
                nm = str(nm or "").strip()
                if nm:
                    out["boards"][nm] = max(out["boards"].get(nm, 0), nb_i)
                    out["max_boards"] = max(out["max_boards"], nb_i)
        # 涨停清单里的 boards（"3天2板" 形式）
        for x in (d.get("limit_up_stocks") or []):
            nm = str(x.get("name") or "").strip()
            if not nm:
                continue
            out["zt_names"].add(nm)
            b = x.get("boards")
            if not b:
                continue
            nb = _parse_boards(b)
            if nb and nb > out["boards"].get(nm, 0):
                out["boards"][nm] = nb
                out["max_boards"] = max(out["max_boards"], nb)
        br = d.get("broken")
        if isinstance(br, dict):
            out["broken"] = {str(n).strip() for n in (br.get("names") or []) if n}
        for l in (d.get("leaders") or []):
            c = str(l.get("code") or "").split(".")[0][:6]
            if c:
                out["leaders"].add(c)
        # 最高板是否唯一（并列最高 = 分歧，不叫龙一/总龙头 → 卡面不写）
        out["max_boards_count"] = sum(
            1 for v in out["boards"].values() if v == out["max_boards"]) if out["max_boards"] else 0
    except Exception as e:      # ⛔ 上下文异常不得影响接力段
        print(f"[short_spirit] 接力上下文降级(已忽略): {e}")
    return out


def _ident_tags(item: dict, ctx: dict) -> list:
    """辨识度标签（用户「推送辨识度」·只写数得出来的事实，⛔ 不形容词堆砌）。

    判定顺序 = 体系口径：板数（高度）→ 龙一（身位）→ 人气榜 → 炸板。
    """
    tags = []
    code = str(item.get("code") or "")[:6]
    name = str(item.get("name") or "")
    nb = ctx["boards"].get(name) or int(item.get("boards_n") or 0)
    # ⛔ 原始板数串（`6天3板` 含「几天」信息，比裸「3板」有意义：3天2板=洗过盘）
    #    → 有原串时由 `_relay_seg` 直接打在代码后面，此处不重复。
    has_raw = "板" in str(item.get("boards") or "")
    if nb >= 2 and not has_raw:
        tags.append(f"{nb}板")
    elif nb == 1 and not has_raw:
        tags.append("首板")
    elif nb == 0 and item.get("sealed") and name in ctx.get("zt_names", ()):
        # 在今日涨停清单、又不在连板天梯（天梯只收 2 板+）→ **首板**（推断有依据，不是猜）
        tags.append("首板")
    # 板块龙一 = 板块内**唯一**最高板。⛔ 并列最高不写（并列 = 分歧，不是龙头）
    if nb >= 2 and nb >= int(item.get("sector_max_boards") or 0) \
            and int(item.get("sector_max_count") or 0) <= 1:
        tags.append("板块龙一")
    # 总龙头 = 全市场最高板（宪法口径：只做总龙头/最高辨识度）
    if nb >= 2 and nb >= int(ctx.get("max_boards") or 0) \
            and int(ctx.get("max_boards_count") or 0) <= 1:
        tags.append("全场最高")
    if code in ctx["leaders"]:
        tags.append("人气榜")
    if name in ctx["broken"]:
        tags.append("炸过板")
    r = item.get("rank")
    if isinstance(r, int) and r > 0:
        if r == 1:
            tags.append("板块第1")
        elif r <= 5:
            tags.append(f"板块第{r}")
    return tags


def _premium(item: dict, ctx: dict) -> dict | None:
    """溢价可能性（用户「推送溢价可能性」·2026-10-20 新增）。

    回答的是「**明天竞价能拿多少溢价**」——已封板票是明天的预期，未封板票是「封住之后的预期」。

    打分依据全部是**数得出来的字段**（⛔ 无封单数据，故不拿封单说事）：
      板块强度  +1（≥5 家涨停）/+0.5（≥3 家）/ −1（≤2 家·独苗没人接）
      换手分区  +1（2%~10%·分歧中上行）/ +0.5（<1.5%·一字锁筹）/ −1（>15%·筹码散了）
      高度分档  +1（2~3 板·有晋级空间）/ −1（≥5 板·高位风险）
      板块身位  +0.5（前 3）
      炸过板    −1.5（筹码松过一次，次日竞价易低开）
    返回 `{"level": "高|中|低", "why": "板块3家·换手4.4%·首板"}`；
    **依据不足（换手/家数都拿不到）→ 返回 None，卡面不显示溢价**，⛔ 不编。
    """
    if not ENABLE_SPIRIT_RELAY_ENRICH:
        return None
    score = 0.0
    why = []
    zt = int(item.get("sector_zt") or 0)
    turn = float(item.get("turnover") or 0)
    nb = int(ctx["boards"].get(str(item.get("name") or "")) or item.get("boards_n") or 0)
    name = str(item.get("name") or "")

    evidence = 0
    if zt >= ZT_STRONG:
        score += 1.0
        why.append(f"板块{zt}家")
        evidence += 1
    elif zt >= 3:
        score += 0.5
        why.append(f"板块{zt}家")
        evidence += 1
    elif zt <= ZT_WEAK and zt > 0:
        score -= 1.0
        why.append(f"板块仅{zt}家")
        evidence += 1

    if turn > 0:
        evidence += 1
        why.append(f"换手{turn:.1f}%")
        if TURN_HEALTHY[0] <= turn <= TURN_HEALTHY[1]:
            score += 1.0
        elif turn < TURN_LOCK_MAX:
            score += 0.5          # 一字锁筹：明天大概率高开，但今天买不到
        elif turn > TURN_HEAVY:
            score -= 1.0

    if nb >= BOARDS_HIGH:
        score -= 1.0
        why.append(f"{nb}板高位")
    elif BOARDS_SWEET[0] <= nb <= BOARDS_SWEET[1]:
        score += 1.0
        why.append(f"{nb}板")

    r = item.get("rank")
    if isinstance(r, int) and r <= 3:
        score += 0.5

    if name in ctx["broken"]:
        score -= 1.5
        why.append("炸过板")

    if evidence == 0:
        return None               # 依据不足 → 不给（⛔ 不编）
    level = "高" if score >= PREMIUM_HIGH else ("中" if score >= PREMIUM_MID else "低")
    return {"level": level, "why": "·".join(why), "score": round(score, 1)}




def _relay_hint(signals, d):
    """板块接力方向（2026-10-08 用户核心功能·A 盘中即时）。

    捕获到主线板块的强势票涨停时，顺势给出「该方向该买什么」：
      ① front    = 前排（谁领涨·已封板里板数最高；带 front_in_signal = 是否就是本轮捕捉的那只）
      ② ladder   = 其余已封板（limit_up_stocks 同板块 → 今日买不到，明日竞价参考）
      ③ tradable = 后排可参与（leaders 同板块·涨幅≥门槛·未在涨停清单 → 现在能买，带涨幅）
    返回 {"sector", "zt_count", "front", "front_in_signal", "ladder":[...], "tradable":[...]} 或 None。
    """
    if not ENABLE_SPIRIT_RELAY_HINT:
        return None
    sector = _relay_sector_of(signals, d)
    if not sector:
        return None

    def _b(v):
        m = re.search(r"(\d+)", str(v or ""))
        return int(m.group(1)) if m else 0

    # ① 已封板（按板数降序，其次涨幅降序）→ ladder[0] 即「前排」
    #    🎯 2026-11-03 基座化：**选股宝题材成分（code）为权威**，名称匹配退为兜底。
    #    11-03 实证：limit_up_stocks[].sector 是东财行业名（广告营销/游戏Ⅱ/纺织制造…），
    #    与 top5 的「短剧/互动影游」永远匹配不上 → ladder 只剩 zt_stocks 补的几只
    #    → 卡面把「今日 8 家涨停」写成「4 家」，用户看到的是「系统没发现传媒爆发」。
    mem = _theme_members(d, sector) if ENABLE_SPIRIT_THEME_BACKSEAT else None
    mem_codes = set((mem or {}).get("codes") or ())
    ladder = []
    for x in (d.get("limit_up_stocks") or []):
        if not isinstance(x, dict):
            continue
        code = str(x.get("code") or "")[:6]
        if mem_codes:
            if code not in mem_codes:       # 有权威成分 → 只收成分内（⛔ 不再靠名字猜）
                continue
        elif not _sector_match(sector, str(x.get("sector") or "")):
            continue                         # 无成分数据 → 退回旧名称匹配（降级不阻断）
        ladder.append({"name": x.get("name"), "code": code,
                       "boards": x.get("boards"), "pct": float(x.get("pct") or 0)})
    ladder.sort(key=lambda x: (-_parse_boards(x.get("boards")), -x["pct"]))
    zt_codes = {x["code"] for x in ladder}

    # B 档（2026-10-12 用户「取选股宝题材分类下的数据·含已封板」）：
    # 用选股宝题材 top5[题材].zt_stocks 补全该题材涨停票（选股宝权威口径·含 code），
    # 合并进 ladder（去重）→ 「后排关联」列全，不依赖全市场 limit_up 的 sector 名称匹配。
    # 2026-11-03：成分已在上面从 `mem` 取过，此处只补 `limit_up_stocks` 里缺的那几只。
    if ENABLE_SPIRIT_THEME_BACKSEAT and mem:
        try:
            for z in (mem.get("stocks") or []):
                c = str(z.get("code") or "")[:6]
                if c and c not in zt_codes:
                    ladder.append({"name": z.get("name"), "code": c,
                                   "boards": None, "pct": float(z.get("pct") or 0)})
                    zt_codes.add(c)
            ladder.sort(key=lambda x: (-_parse_boards(x.get("boards")), -x["pct"]))
        except Exception as e:  # ⛔ 盘中链路安全第一：补全异常不得影响 relay 主流程
            print(f"[short_spirit] 后排关联补全降级(已忽略): {e}")

    # ② 后排可参与（2026-10-20 重写·用户「推的票都涨停了 没意义」）
    #    优先级：**真后排池**（该板块东财 BK 成分涨幅榜·已启动未封）→ 取不到才退回旧 leaders 路径。
    #    ⚠️ 2026-10-20 二改（用户「两个口径融合」）：快照**只取一次**，
    #       已封板段(ladder)与后排段(tradable)共用同一份 pct/换手/量比 → 同源同刻，可比。
    pushed_codes = {str(s.get("code") or "")[:6] for s in (signals or [])}
    tradable = []
    backseat_src = ""
    _bk = ""
    try:
        _m = re.search(r"BK\d+", (_official_tag(sector) if _official_tag else "") or "")
        _bk = _m.group(0) if _m else ""
    except Exception:
        _bk = ""
    _bk_src = "official_tag" if _bk else ""
    # 📌 2026-11-03（传媒实证）：选股宝题材名「短剧/互动影游」在 sector_official 里没有映射，
    #   `_official_tag` 直接返回空 → 后排池整段拿不到；而下游 `backseat_unknown` 只在
    #   「有 bk 但抓不到」时才为真 → 卡面被渲染成「该方向已无未封板票」。
    #   实测该题材真实存在东财 **传媒(BK0486)**，覆盖率 0.8，海看股份(301262)就在其中。
    #   兜底：用运行时 payload 自带的 `official.boards[0].bk`（由成分匹配得出，非猜）。
    #   ⛔ 仍取不到就留空，由下游写「未取到」，⛔ 绝不许编「没有票」。
    if not _bk:
        try:
            for t5 in (d.get("top5") or []):
                if not _sector_match(sector, str(t5.get("name") or "")):
                    continue
                for b in ((t5.get("official") or {}).get("boards") or []):
                    _bk_b = str(b.get("bk") or "")
                    if _bk_b.startswith("BK"):
                        _bk, _bk_src = _bk_b, "theme_official"
                        break
                break
        except Exception:
            pass
    snap = _board_snapshot(_bk) if _bk else None     # ⛔ None = 抓不到（≠ 板块没票）
    if _bk:
        tradable = _board_backseat(
            _bk, zt_codes, pushed_codes, RELAY_BACKSEAT_MAX_ITEMS,
            date_str=str(d.get("date") or ""), snap=snap)
        if tradable:
            backseat_src = "board"
    for l in (d.get("leaders") or []):
        if backseat_src == "board":     # 真后排池已给出 → 不再用连板龙头顶替
            break
        if not isinstance(l, dict):
            continue
        code = str(l.get("code") or "")[:6]
        name = str(l.get("name") or "")
        if not code or code in zt_codes or code in pushed_codes:
            continue
        if not _sector_match(sector, str(l.get("sector") or "")):
            continue
        if code.startswith("688"):                       # 科创板：用户无权限
            continue
        if "ST" in name.upper().replace(" ", ""):        # ST 不参与
            continue
        if any(k in name for k in RELAY_EXCLUDE_NAME_KEYS):   # 黑名单
            continue
        pct = float(l.get("pct") or 0)
        if pct < RELAY_HINT_MIN_PCT:
            continue
        # 兜底路径也吃快照（有快照就用实时值，没有就用 leaders 自带值）
        q = (snap or {}).get(code) or {}
        tradable.append({"name": name, "code": code, "pct": pct,
                         "turnover": float(q.get("turnover") or 0),
                         "vol_ratio": float(q.get("vol_ratio") or 0),
                         "state": "open", "sealed": False})
    tradable.sort(key=lambda x: -x["pct"])
    if tradable and not backseat_src:
        backseat_src = "leaders"

    if not ladder and not tradable:
        return None

    # ③ 口径融合 + 辨识度/溢价（2026-10-20 用户「博敏电子 显示涨幅！！…推送辨识度和溢价可能性」）
    #    ⛔ 病灶：ladder 原来只有 {name, boards, pct}，卡面还只打印 name(boards) → 博敏电子裸名一个。
    #    修：ladder 与 tradable 走**同一套 enrich**（同一份快照 + 同一套上下文 + 同一组标签函数），
    #        卡面再用**同一个渲染函数**输出 → 这才叫「两个口径融合」。
    ctx = _relay_board_ctx(d)
    zt_all = len(ladder)
    # 身位分母：可交易池（ST/688/北交所不参与排序 —— 用户无权限，不该占身位）
    _rank_pool = [(c2, float(q2.get("pct") or 0)) for c2, q2 in (snap or {}).items()
                  if _rankable(c2, q2)]
    if ENABLE_SPIRIT_RELAY_ENRICH:
        # 第一遍：先把「身份字段」全部落定（ident 依赖 sealed / boards_n，⛔ 顺序不能反）
        for grp in (ladder, tradable):
            for it in grp:
                code = str(it.get("code") or "")[:6]
                q = (snap or {}).get(code)
                if q:
                    # 同源同刻：已封板票也改用快照涨幅（原来用面板 pct，与后排段不同刻 → 不可比）
                    if q.get("pct") is not None:
                        it["pct"] = round(float(q["pct"]), 2)
                    it.setdefault("turnover", round(float(q.get("turnover") or 0), 2))
                    it.setdefault("vol_ratio", float(q.get("vol_ratio") or 0))
                it["pct"] = round(float(it.get("pct") or 0), 2)
                it["boards_n"] = ctx["boards"].get(str(it.get("name") or ""), 0) \
                    or _parse_boards(it.get("boards"))
                it["state"] = "sealed" if code in zt_codes else "open"
                it["sealed"] = it["state"] == "sealed"
                # 身位：已封板票也要给（原来 ladder 没这个字段 → 卡面只说「已封板」不说第几）
                if not it.get("rank") and q:
                    it["rank"] = 1 + sum(1 for _c, _p in _rank_pool if _p > it["pct"])
                it["rank"] = int(it.get("rank") or 0)
        # 板块最高板（只认已封板段）→ 并列最高不叫龙一，故要数个数
        _smb = max([int(x.get("boards_n") or 0) for x in ladder] or [0])
        _smb_cnt = sum(1 for x in ladder if int(x.get("boards_n") or 0) == _smb) if _smb else 0
        # 第二遍：辨识度 / 溢价
        for grp in (ladder, tradable):
            for it in grp:
                it["sector_max_boards"] = _smb
                it["sector_max_count"] = _smb_cnt
                it["sector_zt"] = zt_all
                it["ident"] = _ident_tags(it, ctx)
                it["premium"] = _premium(it, ctx)
        # ⛔ 排序必须在融合**之后**：口径统一成快照涨幅后，前排要按同一个数排，
        #    否则会出现「面板 pct 排出来的前排」与「卡面显示的快照 pct」自相矛盾。
        ladder.sort(key=lambda x: (-int(x.get("boards_n") or 0), -float(x.get("pct") or 0)))
        tradable.sort(key=lambda x: -float(x.get("pct") or 0))

    # ④ 前排定位（2026-10-08 用户：「如果他是前排，记得把这个后排也给我标记出来」）：
    #    板块内今日身位最高者 = 已封板里板数最高（ladder 已按板数降序）。
    #    卡面顺序 = 谁领涨(前排) → 买谁(后排可参与) → 其余已封板(参考)。
    #    ⛔ 前排只在「已封板」里取；ladder 空时退回本轮信号（罕见：板块在 top5 但无涨停清单）。
    if ladder:
        front = dict(ladder[0])
    else:
        front = None
        for sig in (signals or []):
            if (str(sig.get("type") or "") in RELAY_TRIGGER_TYPES
                    and _sector_match(sector, str(sig.get("sector") or ""))):
                front = {"name": sig.get("name"), "code": str(sig.get("code") or "")[:6],
                         "boards": None, "pct": float(sig.get("pct") or 0)}
                break
    # 家数口径（2026-11-03）：`zt_count` 原来是 `len(ladder)`——即「我们列出来几只」，
    #   与「该题材今日几家涨停」是两回事。11-03 传媒：选股宝 zt_count=8，而按东财行业名
    #   只收到 4 只 → 卡面写「今日 4 家涨停」，用户看到真实盘面 8 家全一字 → 判定系统漏报。
    #   现在：**有选股宝权威家数就用它**；没有才退回 len(ladder)。
    #   `ladder_count` 单独留存（= 卡面实际列出几只），供卡面区分「8 家·列 5 只」。
    _zt_n = (mem or {}).get("zt_count")
    zt_count_final = _zt_n if isinstance(_zt_n, int) and _zt_n > 0 else len(ladder)
    return {"sector": sector, "zt_count": zt_count_final,
            "zt_count_src": "xgb" if isinstance(_zt_n, int) and _zt_n > 0 else "ladder",
            "ladder_count": len(ladder), "front": front,
            "front_in_signal": bool(front) and front["code"] in pushed_codes,
            "signal_in_ladder": bool(pushed_codes & zt_codes),
            "ladder": ladder[:RELAY_HINT_LADDER_MAX],
            "backseat_src": backseat_src,          # board=真后排池 / leaders=旧兜底 / ""=无
            "backseat_bk": _bk,
            "backseat_bk_src": _bk_src,            # official_tag / theme_official / ""
            # ⛔ 关键：快照抓不到 ≠ 板块没有未封板票。卡面据此改口，不出假结论。
            # 2026-11-03 修：原来写 `bool(_bk) and snap is None` —— 板块码**根本没解析出来**
            # 时为 False → 卡面渲染成「该方向已无未封板票」（当天传媒就是这么错的）。
            # 现在：只要没拿到快照（无码 or 抓取失败）→ 一律「未取到」，⛔ 不许当成「没有」。
            "backseat_unknown": snap is None,
            "tradable": tradable[:RELAY_HINT_MAX_ITEMS]}


def _leaders_as_pool(d):
    """东财涨幅榜抓取失败时的兜底个股池：用本地面板快照 latest.json 的连板龙头
    （boards>=2）直接构造候选，不依赖东财网络。这些票经 A 豁免（_boards_map 命中）
    绕过市值门槛，确保连板龙头在盘中任何时段都能即时推送（2026-09-07 用户铁律）。
    prev_close 由 price/(1+pct) 反推，使 signal_type 正确标「涨停」而非「炸板·回落」。"""
    import re
    out = []
    for l in (d.get("leaders") or []):
        b = l.get("boards")
        nb = 0
        if b:
            mm = re.search(r"(\d+)", str(b))
            if mm:
                nb = int(mm.group(1))
        code = str(l.get("code") or "")
        if not code:
            continue
        code = code[:6]
        pct = float(l.get("pct") or 0)
        price = float(l.get("price") or 0)
        # 2026-10-08 用户「直接盘中开始做」：首板 20cm 涨停（高辨识度但非连板）也纳入兜底池。
        #   否则东财抓取失败时，领湃科技(300530·首板20cm涨停·boards 为空)全天无路径进入推送池。
        # ⛔ 与 _amount_exempt 共用同一总开关 ENABLE_SPIRIT_AMT_EXEMPT_20CM（一条开关整体可逆）。
        _is20 = bool(l.get("is_20cm")) or code[:3] in ("300", "301")
        if nb < 2 and not (ENABLE_SPIRIT_AMT_EXEMPT_20CM and _is20
                           and pct >= LEADER_FALLBACK_20CM_MIN_PCT):
            continue
        prev_close = price / (1 + pct / 100) if pct > 0 else 0.0
        out.append({
            "code": code,
            "name": str(l.get("name") or ""),
            "pct": pct,
            "price": price,
            "amount_yi": 999.0,
            "market_cap_yi": 9999.0,
            "float_cap_yi": 9999.0,
            "list_date": None,
            "prev_close": prev_close,
            "main_flow_yi": 0.0,
            "industry": str(l.get("sector") or ""),
        })
    # 2026-11-05：latest.leaders 可能同一票多条（不同榜单），兜底池又直接 `pool + fb_pool`
    #   → 同卡出现两行完全一样的票（09:57 实测 002428）。源头按 code 去重。
    seen, uniq = set(), []
    for row in out:
        if row["code"] in seen:
            continue
        seen.add(row["code"])
        uniq.append(row)
    return uniq


def _zt_observe_codes(pool, signals):
    """本轮「被观测为涨停」的 code 集 = 信号标涨停 ∪ 池内涨幅达涨停线（20cm 按 19.7）。"""
    zt = set()
    for s in signals:
        if s.get("type") == "涨停":
            zt.add(str(s.get("code") or ""))
    for p in pool:
        code = str(p.get("code") or "")
        pct = float(p.get("pct") or 0)
        if not code:
            continue
        if code[:3] in ("300", "301", "688"):
            if pct >= 19.7:
                zt.add(code)
        elif pct >= 9.8:
            zt.add(code)
    return zt


def _timeliness_gate(signals, pool, now):
    """⛔ 涨停当下性硬门（2026-09-07 用户「时间顺序第一」·盘中激活）。

    涨停信号必须是「本轮新观测到」——上轮(30-60s前)已被观测为涨停 = 存量涨停
    （可能上午就封住一直封到现在），不是当下新封板 → 一律剔除不推。
    这拦截三类旧闻补推：①A/B 开关盘中激活晚，存量连板龙头被当新信号；
    ②东财涨幅榜下午 RemoteDisconnected 恢复后，把上午涨停的票补推；
    ③降级源(latest.json 连板龙头)把整天封板的存量当「涨停事件」反复产出。
    非涨停信号(接近涨停/快速拉升)天然是当下行为，不受限。
    每轮覆盖更新观测快照（炸板那轮不记录 → 回封算新一轮，可再推）。"""
    date = now.strftime("%Y-%m-%d")
    prev_zt = set()
    r = load_json(ROUND_PATH) or {}
    if r.get("date") == date:
        prev_zt = set(r.get("zt") or [])
    keep = [s for s in signals
            if not (s.get("type") == "涨停" and str(s.get("code") or "") in prev_zt)]
    save_json(ROUND_PATH, {
        "date": date,
        "ts": now.strftime("%H:%M:%S"),
        "zt": sorted(_zt_observe_codes(pool, signals)),
    })
    return keep


def _dt_observe_codes(pool, signals):
    """本轮「被观测为跌停」的 code 集 = 信号标跌停 ∪ 池内跌幅达跌停线（20cm 按 -19.7）。"""
    dt = set()
    for s in signals:
        if s.get("type") == "跌停":
            dt.add(str(s.get("code") or ""))
    for p in pool:
        code = str(p.get("code") or "")
        pct = float(p.get("pct") or 0)
        if not code:
            continue
        if code[:3] in ("300", "301", "688"):
            if pct <= -19.7:
                dt.add(code)
        elif pct <= -9.8:
            dt.add(code)
    return dt


def _timeliness_gate_down(signals, pool, now):
    """⛔ 跌停当下性硬门（2026-09-21 用户「盲区补强」·与 _timeliness_gate 平行、独立 round 快照）。

    跌停信号必须是「本轮新观测到」——上轮已观测为跌停 = 存量跌停（可能上午就封死一直封到现在），
    不是当下新封跌停 → 剔除不推，避免下午把上午跌停补推成「新异动」。
    打开跌停/快速下跌天然是当下行为，不受限（同 接近涨停/快速拉升）。"""
    date = now.strftime("%Y-%m-%d")
    prev_dt = set()
    r = load_json(ROUND_DOWN_PATH) or {}
    if r.get("date") == date:
        prev_dt = set(r.get("dt") or [])
    keep = [s for s in signals
            if not (s.get("type") == "跌停" and str(s.get("code") or "") in prev_dt)]
    save_json(ROUND_DOWN_PATH, {
        "date": date,
        "ts": now.strftime("%H:%M:%S"),
        "dt": sorted(_dt_observe_codes(pool, signals)),
    })
    return keep


def _amount_exempt(s, identified_codes=None, boards_map=None, hot_kws=None):
    """20cm 高辨识度票豁免成交额门槛（2026-10-08 用户「直接盘中开始做」）。

    背景：领湃科技(300530) 10-08 首板 20cm 涨停（锂电池），全天成交仅 3.47 亿 < 下午门槛
    10 亿 → 被成交额门槛剔除；而同方向连板龙头(时代万恒4板/传艺科技3板)走 `_leaders_as_pool`
    硬塞 amount_yi=999 绕过了门槛 → 被推。于是「首板 20cm 高辨识度」成系统性盲区。
    规则：创业板 20cm(300/301) + 涨幅≥SPIRIT_AMT_EXEMPT_MIN_PCT（冲板强度）
      + 已被识别为主线票（latest leaders / top5 核心 / 连板≥1 / 行业命中热点关键词）
      + 成交额≥SPIRIT_AMT_EXEMPT_MIN_YI（保底流动性）。
    ⛔ 科创板(688)不纳入（用户无权限）；⛔ 主板不纳入（非 20cm，成交额门槛即其保护）。
    """
    if not ENABLE_SPIRIT_AMT_EXEMPT_20CM:
        return False
    code = str(s.get("code") or "")
    if code[:3] not in ("300", "301"):
        return False
    if float(s.get("pct") or 0) < SPIRIT_AMT_EXEMPT_MIN_PCT:
        return False
    if float(s.get("amount_yi") or 0) < SPIRIT_AMT_EXEMPT_MIN_YI:
        return False
    if code in (identified_codes or set()):
        return True
    if int((boards_map or {}).get(code, 0) or 0) >= 1:
        return True
    return str(s.get("industry") or "") in (hot_kws or set())


def noise_filter(pool, cfg, now, exempt_codes=None, boards_map=None,
                 identified_codes=None, hot_kws=None):
    """噪音过滤链：ST/次新/市值/成交额/涨幅下限

    `identified_codes`（2026-10-08 新增）= 高辨识度票集（latest leaders ∪ top5 核心），
    仅用于 20cm 成交额豁免判定（_amount_exempt），**不影响**市值门槛的 exempt_codes 语义。
    """
    keep = []
    min_cap = cfg["min_market_cap_yi"]
    amt_thr = cfg["amount_morning_yi"] if session() == "morning" else cfg["amount_afternoon_yi"]
    cutoff = now - timedelta(days=int(cfg["max_new_stock_days"]))
    _exempt_set = exempt_codes or set()
    _bmap = boards_map or {}
    _ident = identified_codes if identified_codes is not None else _exempt_set
    _hot = hot_kws or set()
    for s in pool:
        if "ST" in s["name"] or "退" in s["name"] or "*" in s["name"]:
            continue
        if not s["code"] or not s["name"]:
            continue
        if s["pct"] < cfg["min_pct"]:
            continue
        # ── 市值门槛（2026-09-07 A/B 方案·开关默认关）──
        # 2026-11-03：板数判据补上**涨停池自带板数**（`_zt_rich.boards`）。
        #   原实现只从 latest.json 的 leaders 取 `_boards_map` → 盘前/降级轮 leaders 为空时
        #   板数恒 0 → A 豁免（连板≥2 免市值门槛）整体失效 → 大晟文化(2板/流通27亿)
        #   这类情绪核心被 30 亿 LOWCAP 门槛挡在池外。涨停池的 lbc 是同日权威字段。
        _zt_boards = int((s.get("_zt_rich") or {}).get("boards") or 0)
        # 2026-11-03 13:20：**涨停池事实票豁免市值门槛**（与成交额豁免同源）。
        #   实证：时代万恒 27 亿流通、成交 3.77 亿、10:21 封板 → 被 30 亿 LOWCAP 挡在池外，
        #   于是 8 只锂电只剩 7 只。它已经用「封住涨停」证明了自己，市值大小属于
        #   「买不买」的范畴，⛔ 不该由它决定「这条线几家涨停」。
        #   开关 ENABLE_SPIRIT_ZT_CAP_EXEMPT=0 可单独关掉这一条。
        _zt_cap_exempt = bool(s.get(ZT_FACT_KEY)) and ENABLE_SPIRIT_ZT_CAP_EXEMPT
        mc = s["market_cap_yi"]
        _exempt = _zt_cap_exempt or (ENABLE_SPIRIT_CAPEXEMPT and (
            s["code"] in _exempt_set or int(_bmap.get(s["code"], 0) or 0) >= 2
            or _zt_boards >= 2))
        if not _exempt:
            _cap_thr = SPIRIT_LOWCAP_YI if ENABLE_SPIRIT_LOWCAP else min_cap
            if mc < _cap_thr:
                continue
        # 成交额门槛（2026-10-08：20cm 高辨识度票可豁免，见 _amount_exempt）
        # 2026-11-03：**涨停池事实票豁免**——一字/竞价封的成交额天然极小（筹码锁死，
        #   没换手），这是特征不是缺陷。11-03 实证：大晟文化成交 0.26 亿但封单 4.55 亿
        #   （占流通 16.9%），旧门槛把它和整条短剧线一起挡在池外。
        #   ⛔ 只豁免「已确认封板」的票；涨幅榜里未封板的小成交额票照旧被挡。
        _zt_fact = bool(s.get(ZT_FACT_KEY)) and ENABLE_SPIRIT_ZT_AMT_EXEMPT
        if s["amount_yi"] < amt_thr and not _zt_fact and not _amount_exempt(s, _ident, _bmap, _hot):
            continue
        # 次新：上市日期距今 < max_new_stock_days
        if s["list_date"] and len(s["list_date"]) == 8:
            try:
                ld = datetime.strptime(s["list_date"], "%Y%m%d")
                if ld > cutoff:
                    continue
            except ValueError:
                pass
        keep.append(s)
    return keep


def resonance(s, kws, t5codes, t5names):
    """共振度 0~1：强=top5 板块核心票；中=行业/名称命中热点；弱=名称含热点词"""
    score = 0.0
    hit = []
    if s["code"] in t5codes:
        score += 2.0
        hit.append("板块核心")
    for kw in kws:
        if kw and (kw in s["industry"] or s["industry"] in kw):
            score += 1.0
            hit.append(kw)
            break
    for kw in t5names:
        if kw and kw in s["name"]:
            score += 0.5
            hit.append("题材名")
            break
    return min(score / 2.5, 1.0), hit[:2]


def signal_type(s):
    """异动类型：涨停 / 冲板 / 快速拉升 + 大单流入/放量 标签"""
    code3 = s["code"][:3]
    is20 = code3 in ("300", "301") or code3 == "688"
    thr_zt, thr_chg = (19.9, 15.0) if is20 else (9.9, 7.5)
    # 2026-09-04 封板校验（用户「欢瑞世纪炸板被标涨停」）：涨停=现价≈涨停价（真封住）。
    # 涨幅达涨停线但现价未到位（曾触板炸开/回落中）=「炸板·回落」，不标涨停——
    # 实证欢瑞世纪 000892 反复炸板：封住时 +10.04%（价=5.59），炸开后 +9.25%（价 5.55<5.59）。
    pc = float(s.get("prev_close") or 0)
    limit = round(pc * (1.2 if is20 else 1.1), 2) if pc > 0 else 0.0
    if limit > 0 and s["price"] >= limit - 0.001:
        t = "涨停"
    elif s["pct"] >= thr_zt:
        t = "炸板·回落"
    elif s["pct"] >= thr_chg:
        t = "接近涨停"
    else:
        t = "快速拉升"
    tags = []
    if s["main_flow_yi"] > 0:
        tags.append("大单流入")
    if s["amount_yi"] >= 10:
        tags.append("放量")
    return t, tags


def signal_type_down(s):
    """下跌异动类型（2026-09-21 盲区补强，与 signal_type 平行）：
    跌停（真封死）/ 打开跌停（曾触跌停炸开·撬板异动）/ 快速下跌（跌幅未达跌停线但急杀）。
    ⛔ 科创板(688)按用户纪律剔除（无权限），上游过滤已挡，这里仍按 is20 算阈值兜底。"""
    code3 = s["code"][:3]
    is20 = code3 in ("300", "301") or code3 == "688"
    thr_zt, thr_chg = (19.9, 15.0) if is20 else (9.9, 7.5)
    pc = float(s.get("prev_close") or 0)
    limit_down = round(pc * (0.8 if is20 else 0.9), 2) if pc > 0 else 0.0
    if limit_down > 0 and s["price"] <= limit_down + 0.001:
        t = "跌停"
    elif s["pct"] <= -thr_zt:
        # 跌幅达跌停线但现价未封死（曾触跌停炸开/回升中）=「打开跌停」撬板异动
        t = "打开跌停"
    elif s["pct"] <= -thr_chg:
        t = "快速下跌"
    else:
        t = None  # 跌幅未达阈值 → 不视为下跌异动
    tags = []
    if s["main_flow_yi"] < 0:
        tags.append("大单流出")
    if s["amount_yi"] >= 10:
        tags.append("放量")
    return t, tags


def strength(s, res, flow_ratio, cfg):
    """信号强度 = 涨幅归一 + 成交额归一 + 共振 + 主力净流入方向"""
    w = cfg["weight"]
    pct_n = min(s["pct"] / 20.0, 1.0)
    amt_n = min(s["amount_yi"] / 30.0, 1.0)
    flow_n = 1.0 if s["main_flow_yi"] > 0 else 0.0
    return round(pct_n * w["pct"] + amt_n * w["amount"] + res * w["resonance"] + flow_n * w["flow"], 3)


def build_signals(d, now, cfg):
    """主流程：拉池 → 过滤 → 龙头保留 → 共振 → 排序"""
    pool = fetch_pool(cfg)
    # 【2026-11-03 用户「立即处理」】涨停池（已封板事实源）**无条件并入**：
    #   涨幅榜抓取成功也要并 —— 因为涨幅榜只有两页 200 条、按涨幅排序，
    #   而涨停池是「今天所有封住的票」的权威全集。
    #   ⛔ 失败返回 []，此时行为与改动前完全一致（不冒充「今天没涨停」）。
    _zt_rows = fetch_zt_pool(now)
    if _zt_rows:
        pool = _merge_zt_pool(pool, _zt_rows)
    # ⛔ C 降级：东财涨幅榜抓取失败（沙箱下午常 RemoteDisconnected→池0）时，
    # 用本地面板快照 latest.json 的连板龙头(boards>=2)兜底，确保连板龙头盘中任何时段可推
    if ENABLE_SPIRIT_LEADER_FALLBACK and len(pool) < 5:
        fb_pool = _leaders_as_pool(d)
        if fb_pool:
            pool = pool + fb_pool
    kws = hot_keywords(d)
    t5codes = top5_codes(d)
    t5names = [str(s.get("name") or "") for s in (d.get("top5") or [])[:5]]
    boards_map = _boards_map(d)
    # 高辨识度票集（2026-10-08 用户「直接盘中开始做」）：top5 板块核心票 ∪ latest leaders 全部 code。
    #   leaders 是系统自身的「识别度榜」，领湃科技(300530·首板20cm涨停·boards 空)即在榜内 ——
    #   使 20cm 高辨识度票可豁免成交额门槛（_amount_exempt），修复「首板20cm被成交额门槛误杀」盲区。
    identified_codes = set(t5codes) | {
        str(l.get("code") or "")[:6] for l in (d.get("leaders") or []) if l.get("code")}
    keep = noise_filter(pool, cfg, now, exempt_codes=t5codes, boards_map=boards_map,
                        identified_codes=identified_codes, hot_kws=kws)
    # 板块龙头保留：同行业按涨幅取 top_per_sector
    sec_groups = {}
    for s in keep:
        sec_groups.setdefault(s["industry"] or "其他", []).append(s)
    core = []
    for sec, arr in sec_groups.items():
        arr.sort(key=lambda x: -x["pct"])
        core.extend(arr[: cfg["top_per_sector"]])
    # 共振 + 强度 + 题材/逻辑（2026-08-18 用户定：只显示题材+逻辑，去掉标签词）
    # code → top5 题材名映射（强共振的题材化表达，替代"主线核心"标签）
    code_t5 = {}
    for s5 in (d.get("top5") or [])[:5]:
        nm = str(s5.get("name") or "")
        L = s5.get("leader") or {}
        if L.get("code"):
            code_t5[str(L["code"])[:6]] = nm
        for f in (s5.get("followers") or []):
            if f.get("code"):
                code_t5[str(f["code"])[:6]] = nm
    _mk_memo: dict = {}

    def _mk(s: dict) -> dict:
        """单个池票 → signal 字段（提取自原内联循环·纯计算）。"""
        _k = s["code"]
        if _k in _mk_memo:
            return dict(_mk_memo[_k])
        res, hit = resonance(s, kws, t5codes, t5names)
        typ, tags = signal_type(s)
        sc = strength(s, res, 0, cfg)
        # 逻辑描述：共振[题材]（top5板块 > 行业热点词 > 题材共振），无则类型兜底
        t5 = code_t5.get(s["code"], "")
        kw_hit = [h for h in hit if h not in ("板块核心", "题材名")]
        if not t5 and kw_hit:
            t5 = kw_hit[0]
        if not t5 and "题材名" in hit:
            t5 = "题材共振"
        reason = f"共振[{t5[:8]}]" if t5 else typ
        out = {
            "name": s["name"], "code": s["code"], "sector": s["industry"],
            # 板块联动口径（2026-10-12）：题材名优先（top5 成分票）、东财行业兜底 ——
            #   与「🎯 接力方向」同为题材名，卡面板块口径一致；非 top5 票才落到行业。
            "theme": code_t5.get(s["code"], "") or s["industry"],
            "market_cap_yi": s["market_cap_yi"], "amount_yi": s["amount_yi"],
            "pct": s["pct"], "type": typ, "tags": tags,
            "resonance": round(res * 100), "flow_yi": s["main_flow_yi"],
            "strength": sc, "reason": reason,
        }
        _mk_memo[_k] = out
        return dict(out)

    signals = [_mk(s) for s in core]
    signals.sort(key=lambda x: -x["strength"])
    # 【2026-11-03 二次修】全池视图 = **截断前**的 `keep`，不是 `core`/`signals`。
    #   ⚠️ 上一版写成 `list(signals)` 仍然看不出「一条线几家」——`core` 已过
    #      `arr[:top_per_sector]`（每行业最多 2 只）：13:07 实盘锂电 8 家，
    #      卡面「🔥 板块联动」只显示「电池 2只」，那条线等于不存在。
    #   `top_per_sector` 是**推送名额**规则，⛔ 无权决定「这个板块今天几家涨停」这个事实。
    #   病根同源：把「说什么」和「推多少」混在同一个变量上。
    #   ⛔ 只改展示聚合的数据源，不改推送名额（max_signals / top_per_sector）、不改排序、不改评分。
    #   ⚠️ 走模块级缓存而非第 5 个返回值：本文件 POOL_CACHE / FETCH_STATUS 同套路，
    #      且 build_signals 返回值被 5 处测试 mock，改签名等于无谓地打断它们。
    GROUP_VIEW_CACHE["view"] = (_dedup_signals([_mk(s) for s in keep])
                                if ENABLE_SPIRIT_GROUP_FULL_POOL else None)
    # ⛔ 涨停当下性硬门（2026-09-07 用户「时间顺序第一」）：剔除上轮已观测为涨停的存量票
    if ENABLE_SPIRIT_TIMELINESS:
        signals = _timeliness_gate(signals, pool, now)
    return signals[: cfg["max_signals"]], session(), len(keep), len(pool)


# 下跌支路对「部分 cfg」容错（2026-09-22）：缺键不得让整个 `main()` 崩 ——
# 否则会连带**向上路径的推送**一起停摆（单测/半成品配置缺键即触发）。
# 与 `load_cfg()` 的兜底默认同值。
_CFG_FALLBACK = {"amount_morning_yi": 5, "amount_afternoon_yi": 10,
                 "max_new_stock_days": 365, "min_market_cap_yi": 100,
                 "top_per_sector": 2, "max_signals": 10}


def build_signals_down(d, now, cfg):
    """下跌支路主流程（2026-09-21 用户「盲区补强」）：与 build_signals 平行、独立 round 快照、
    不改动已验证的向上路径。抓跌幅榜 → 过滤 → 龙头保留 → 共振 → 向下 signal_type。
    ⛔ 下跌支路不做连板龙头市值豁免（下跌票非连板龙头）；科创板(688)剔除（无权限）。"""
    pool = fetch_pool_down(cfg)
    # ⚠️ 下跌支路不兜底 latest.json（下跌票无稳定连板龙头快照可复用），东财空池则本轮无下跌信号。
    min_pct_down = float(cfg.get("min_pct_down", 7.5))
    amt_thr = (cfg.get("amount_morning_yi", _CFG_FALLBACK["amount_morning_yi"])
               if session() == "morning"
               else cfg.get("amount_afternoon_yi", _CFG_FALLBACK["amount_afternoon_yi"]))
    cutoff = now - timedelta(days=int(cfg.get("max_new_stock_days", _CFG_FALLBACK["max_new_stock_days"])))
    cap_thr = (SPIRIT_LOWCAP_YI if ENABLE_SPIRIT_LOWCAP
               else cfg.get("min_market_cap_yi", _CFG_FALLBACK["min_market_cap_yi"]))
    keep = []
    for s in pool:
        if "ST" in s["name"] or "退" in s["name"] or "*" in s["name"]:
            continue
        if not s["code"] or not s["name"]:
            continue
        if s["code"][:3] == "688":  # ⛔ 科创板剔除
            continue
        if s["pct"] > -min_pct_down:  # 跌幅未达阈值 → 跳过（下跌只看大面）
            continue
        if s["market_cap_yi"] < cap_thr:
            continue
        if s["amount_yi"] < amt_thr:
            continue
        if s["list_date"] and len(s["list_date"]) == 8:
            try:
                ld = datetime.strptime(s["list_date"], "%Y%m%d")
                if ld > cutoff:
                    continue
            except ValueError:
                pass
        keep.append(s)
    # 板块龙头保留（同行业按跌幅取 top_per_sector，跌得最狠的优先）
    sec_groups = {}
    for s in keep:
        sec_groups.setdefault(s["industry"] or "其他", []).append(s)
    core = []
    for sec, arr in sec_groups.items():
        arr.sort(key=lambda x: x["pct"])  # 跌得最狠的排前
        core.extend(arr[: cfg.get("top_per_sector", _CFG_FALLBACK["top_per_sector"])])
    kws = hot_keywords(d)
    t5codes = top5_codes(d)
    t5names = [str(s.get("name") or "") for s in (d.get("top5") or [])[:5]]
    code_t5 = {}
    for s5 in (d.get("top5") or [])[:5]:
        nm = str(s5.get("name") or "")
        L = s5.get("leader") or {}
        if L.get("code"):
            code_t5[str(L["code"])[:6]] = nm
        for f in (s5.get("followers") or []):
            if f.get("code"):
                code_t5[str(f["code"])[:6]] = nm
    signals = []
    for s in core:
        res, hit = resonance(s, kws, t5codes, t5names)
        typ, tags = signal_type_down(s)
        if typ is None:
            continue
        sc = strength({**s, "pct": abs(s["pct"])}, res, 0, cfg)  # 强度按跌幅绝对值，与向上同量级可比
        t5 = ""
        kw_hit = [h for h in hit if h not in ("板块核心", "题材名")]
        if not t5 and kw_hit:
            t5 = kw_hit[0]
        if not t5 and "题材名" in hit:
            t5 = "题材共振"
        reason = f"共振[{t5[:8]}]" if t5 else typ
        signals.append({
            "name": s["name"], "code": s["code"], "sector": s["industry"],
            # 板块联动口径（2026-10-12）：题材名优先（top5 成分票）、东财行业兜底 ——
            #   与「🎯 接力方向」同为题材名，卡面板块口径一致；非 top5 票才落到行业。
            "theme": code_t5.get(s["code"], "") or s["industry"],
            "market_cap_yi": s["market_cap_yi"], "amount_yi": s["amount_yi"],
            "pct": s["pct"], "type": typ, "tags": tags,
            "resonance": round(res * 100), "flow_yi": s["main_flow_yi"],
            "strength": sc, "reason": reason,
        })
    signals.sort(key=lambda x: x["pct"])  # 跌得最狠在前（强度语义：跌幅越大越强）
    # ⛔ 跌停当下性硬门（2026-09-07 涨停硬门的下跌平行版）：仅本轮新观测跌停才推
    if ENABLE_SPIRIT_TIMELINESS:
        signals = _timeliness_gate_down(signals, pool, now)
    return (signals[: cfg.get("max_signals", _CFG_FALLBACK["max_signals"])],
            session(), len(keep), len(pool))


def load_pushed():
    return load_json(PUSHED_PATH) or {"date": "", "codes": [], "last_push": "", "pending": [], "pending_ts": ""}


def save_pushed(pushed, now, update_last=True):
    """整体保存去重/池状态（含待推池 pending，2026-08-18 双触发改造）。
    update_last=False：仅保存池状态，不刷新 last_push（超时兜底计时不受干扰）。"""
    pushed["date"] = now.strftime("%Y-%m-%d")
    if update_last:
        pushed["last_push"] = now.strftime("%H:%M:%S")
    save_json(PUSHED_PATH, pushed)


# 个股名语义色（2026-08-25 用户定·与涨幅数字同色号）：涨停/快速拉升=red（与涨幅红一致）、
# 接近涨停=purple（机会紫）。⛔ 类型区分靠分组标题（🔥/🚀/⚡），个股名不再承担类型色——
# 曾用 orange 标快速拉升，飞书渲染偏橘红与涨幅正红不一致（用户抓包「红色颜色不对」）。
TYPE_COLORS = {"涨停": "red", "接近涨停": "purple", "快速拉升": "red", "炸板·回落": "red",
               "跌停": "green", "打开跌停": "green", "快速下跌": "green"}


def _pct_tag(pct: float) -> str:
    """涨幅数字红涨绿跌（A股铁律·shared_rules 配色铁律）：+红 / -绿 / 0中性。"""
    if pct > 0:
        return f"<text_tag color='red'>{pct:+.2f}%</text_tag>"
    if pct < 0:
        return f"<text_tag color='green'>{pct:+.2f}%</text_tag>"
    return f"<text_tag color='neutral'>{pct:+.2f}%</text_tag>"


def _dedup_signals(signals):
    """同轮同 code 只保留一条（入参通常已按 strength 降序 → 保留首条）。

    2026-11-05 实测 09:57 一张卡里 `002428` 出现两行完全一样的「接近涨停」：
    池由涨幅榜 + 涨停池 + leaders 兜底三路拼接，任一路径重复都会让同一票
    占两个名额、卡面重两行、板块联动家数虚高。⛔ 只做身份去重，不改排序/评分/名额。
    """
    out, seen = [], set()
    for s in signals or []:
        code = str(s.get("code") or "")
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(s)
    return out


def _filter_backseat_shown(backseat, shown):
    """后排候选跨卡增量：当日已展示过的 code 不再重复列。纯函数，不修改入参。

    ⛔ 只过滤展示候选，不改 `backseat_pick` 的筛选逻辑，也不碰主信号推送。
    """
    if not isinstance(backseat, dict) or not shown:
        return backseat
    seen = {str(c) for c in shown}
    out = dict(backseat)
    for key in ("candidates", "potential"):
        out[key] = [r for r in (out.get(key) or [])
                    if str(r.get("code") or "") not in seen]
    return out


def _sector_group_summary(signals, *, merge_family=None):
    """板块联动归纳（纯函数·2026-10-12 用户「尽量进行板块联动的观察归纳」）。

    把本轮信号按板块聚合（题材名 `theme` 优先·东财行业 `sector` 兜底 → 与接力段同口径）。
    返回 [(sector, total, zt_count, top_signal), ...]，
    排序 = 涨停数降 → 家数降 → 组内最强强度降（「有涨停 + 家数多」的板块顶到前面）。

    `merge_family`（2026-11-03）：是否按 `theme_base.THEME_FAMILIES` 归族后再聚合。
      病根：盘中板块名来自东财 hybk → 传媒线被拆成 游戏Ⅱ/影视院线/数字媒体/广告营销
      四条 2 只的小线，用户看到的是「一堆杂毛」而不是「传媒大爆发」。
      ⛔ 只改**这一段的展示聚合口径**，不改 signals 的 theme 字段、不改推送/排序/评分。

    归族的第二判据（2026-11-03 13:07 补）：`theme` 不中族时再拿 `sector` 试一次。
      实证：8 只锂电里圣阳股份 theme=「锂电池」（top5 命中）、其余 theme 落到东财行业
      「电池」→ 同一批票被拆成「电池 6只」+「锂电池 1只」。同一批票的两种叫法，
      必须归到同一组，否则家数永远算不对。
    """
    if merge_family is None:
        merge_family = ENABLE_SPIRIT_GROUP_FAMILY
    groups: dict = {}
    for s in (signals or []):
        sec = str(s.get("theme") or s.get("sector") or "").strip() or "其他"
        if merge_family:
            fam = _family_of(sec) or _family_of(str(s.get("sector") or "").strip())
            if fam:
                sec = fam[:-1] if fam.endswith("族") else fam   # 「传媒族」→「传媒」
        g = groups.setdefault(sec, {"items": [], "zt": 0})
        g["items"].append(s)
        if s.get("type") == "涨停":
            g["zt"] += 1
    out = []
    for sec, g in groups.items():
        g["items"].sort(key=lambda x: -float(x.get("strength") or 0))
        out.append((sec, len(g["items"]), g["zt"], g["items"][0]))
    out.sort(key=lambda x: (-x[2], -x[1], -float(x[3].get("strength") or 0)))
    return out


def _theme_code_map(d):
    """code → 题材名（top5 的 leader / followers / zt_stocks 全量 code 反查题材名）。

    与 `build_signals` 内联口径**同源**（题材化表达的唯一权威）；此处抽成函数供后排预判复用，
    ⛔ 不改 build_signals 的既有行为。zt_stocks 的 code 形如 `603528.SS` → 取点号前 6 位。
    """
    m: dict = {}
    for s5 in (d.get("top5") or [])[:5]:
        nm = str(s5.get("name") or "")
        if not nm:
            continue
        L = s5.get("leader") or {}
        if L.get("code"):
            m[str(L["code"])[:6]] = nm
        for f in (s5.get("followers") or []):
            if f.get("code"):
                m[str(f["code"])[:6]] = nm
        for z in (s5.get("zt_stocks") or []):
            c = str(z.get("code") or "").split(".")[0]
            if c:
                m[c[:6]] = nm
    return m


def backseat_pick(pool, signals, d, sess, cfg=None):
    """后排候选池（2026-10-14 用户「今天有可能涨停的后排·辨识度+地位+温和放量」）。

    从**原始涨幅榜**（未过滤池）里挑「已启动但还没封板、今日有封板可能」的后排票。
    ⛔ 与 signals（已异动票）互斥 —— 已报过的不重复报。
    三判据（用户口径·交集）：
      ① 温和放量：量比 ∈ [BS_VOL_MIN, BS_VOL_MAX]（缺数据 vol_ratio=0 时放行，只降权重）；
      ② 有辨识度：主线题材成分 / 人气榜(leaders) / 连板≥1 / 行业命中热点（至少一项）；
      ③ 有地位  ：同板块内涨幅排名 ≤ BS_SECTOR_RANK（板块内身位靠前）。
    返回 {"potential": [...最多 BS_MAX_ITEMS 只...], "candidates": [...全部...]}。
    ⛔ 任何异常 → 返回空（降级），绝不抛出（盘中链路安全第一）。
    """
    try:
        cfg = cfg or {}
        sigs = signals or []
        empty = {"potential": [], "candidates": []}
        if not pool:
            return empty
        t5codes = top5_codes(d)
        theme_map = _theme_code_map(d)
        leader_codes = {str(l.get("code") or "")[:6]
                        for l in (d.get("leaders") or []) if isinstance(l, dict) and l.get("code")}
        board_map = _boards_map(d)
        kws = hot_keywords(d)
        cap_thr = SPIRIT_LOWCAP_YI if ENABLE_SPIRIT_LOWCAP else float(cfg.get("min_market_cap_yi") or 0)
        amt_thr = (float(cfg.get("amount_morning_yi") or 5) if sess == "morning"
                   else float(cfg.get("amount_afternoon_yi") or 10)) * BS_AMT_RATIO
        cutoff = datetime.now() - timedelta(days=BS_MAX_NEW_DAYS)
        seen = {str(s.get("code") or "")[:6] for s in sigs}

        cands = []
        for s in pool:
            code = str(s.get("code") or "")
            name = str(s.get("name") or "")
            if not code or not name or code in seen:
                continue
            if "ST" in name or "退" in name or "*" in name:
                continue
            if code[:3] == "688":                       # 科创板：用户无权限
                continue
            if any(k in name for k in BS_EXCLUDE_NAME_KEYS):
                continue
            pct = float(s.get("pct") or 0)
            is20 = code[:3] in ("300", "301")
            lo, hi = (BS_PCT_MIN_20, BS_PCT_MAX_20) if is20 else (BS_PCT_MIN, BS_PCT_MAX)
            if pct < lo or pct >= hi:                   # 未启动 或 已封板 → 都不是「后排可冲板」
                continue
            vr = float(s.get("vol_ratio") or 0)
            if vr > 0 and not (BS_VOL_MIN <= vr <= BS_VOL_MAX):   # 缩量/爆量剔除（缺数据放行）
                continue
            if float(s.get("market_cap_yi") or 0) < cap_thr:
                continue
            if float(s.get("amount_yi") or 0) < amt_thr:
                continue
            ld_ = s.get("list_date")
            if ld_ and len(str(ld_)) == 8:
                try:
                    if datetime.strptime(str(ld_), "%Y%m%d") > cutoff:
                        continue
                except ValueError:
                    pass
            # ② 辨识度：主线题材 / 人气榜 / 连板 / 行业命中热点（至少一项，否则不列）
            boards = int(board_map.get(code, 0) or 0)
            ident = []
            if code in t5codes or code in theme_map:
                ident.append("主线题材")
            if code in leader_codes:
                ident.append("人气榜")
            if boards >= 1:
                ident.append(f"{boards}板")
            if not ident and str(s.get("industry") or "") in kws:
                ident.append("板块热点")
            if not ident:
                continue                                # 无任何辨识度 → 不列（体系：不推杂毛）
            sector = theme_map.get(code) or str(s.get("industry") or "") or "其他"
            cands.append({
                "name": name, "code": code, "sector": sector,
                "industry": str(s.get("industry") or ""),
                "pct": pct, "vol_ratio": vr, "boards": boards,
                "amount_yi": float(s.get("amount_yi") or 0),
                "flow_yi": float(s.get("main_flow_yi") or 0),
                "why": "·".join(ident[:2]) or "题材内靠前",
            })
        # ③ 地位：同板块按涨幅降序 → 只留身位靠前的（前 BS_SECTOR_RANK）
        by_sec: dict = {}
        for c in cands:
            by_sec.setdefault(c["sector"], []).append(c)
        ranked = []
        for _sec, arr in by_sec.items():
            arr.sort(key=lambda x: -x["pct"])
            ranked.extend(arr[:BS_SECTOR_RANK])
        # 排序：越接近涨停线越有动能；量比越居中（温和）越好
        for c in ranked:
            is20 = c["code"][:3] in ("300", "301")
            prox = min(c["pct"] / (BS_PCT_MAX_20 if is20 else BS_PCT_MAX), 1.0)
            vol_sc = 1.0 if 1.5 <= c["vol_ratio"] <= 3.5 else (0.6 if c["vol_ratio"] > 0 else 0.5)
            c["score"] = round(prox * 0.6 + vol_sc * 0.4, 4)
        ranked.sort(key=lambda x: -x["score"])
        return {"potential": ranked[:BS_MAX_ITEMS], "candidates": ranked}
    except Exception as e:                              # ⛔ 盘中链路安全第一：新段异常不得影响主卡
        print(f"[short_spirit] 后排预判降级(已忽略): {e}")
        return {"potential": [], "candidates": []}


ENABLE_SPIRIT_LINKAGE_DELTA = True  # 同板块只报首次及家数/涨停数新高，关闭恢复全量附带


def _linkage_delta(summary, sent):
    """仅比较成功送达的高水位；排名/涨幅/最强票抖动不算新联动。纯函数。"""
    rows, updates = [], {}
    for row in summary:
        sector, count, sealed, _ = row
        if count < SECTOR_GROUP_MIN_ITEMS and sealed < 1:
            continue
        previous = sent.get(sector)
        if (not ENABLE_SPIRIT_LINKAGE_DELTA or previous is None
                or count > previous.get("count", 0) or sealed > previous.get("sealed", 0)):
            rows.append(row)
            updates[sector] = {
                "count": max(count, (previous or {}).get("count", 0)),
                "sealed": max(sealed, (previous or {}).get("sealed", 0)),
            }
    return rows[:SECTOR_GROUP_MAX], {row[0]: updates[row[0]] for row in rows[:SECTOR_GROUP_MAX]}


def build_card(signals, sess, now, relay=None, backseat=None, group_view=None,
               group_summary=None, shown_sink=None):
    """飞书卡（2026-08-18 用户定·向窗口卡事件流风格看齐）：
    按异动类型分组（🔥涨停/🚀冲板/⚡快速拉升），一行一只，字段压缩到一行。
    ⛔ 2026-08-25 修复（用户抓包「又忘了红涨绿跌」+「红色颜色不对」）：
       - `<font color>` 飞书不渲染（shared_rules 实测 08-14）→ 一律 `<text_tag>` 胶囊；
       - 涨幅数字按涨跌着色（+红/-绿），个股名与涨幅同色号：涨停/快速拉升=red、接近涨停=purple
         （快速拉升曾用 orange，飞书渲染偏橘红与涨幅正红不一致 → 已改 red，类型靠分组标题区分）。
    📈 2026-10-14 加 `backseat`（后排预判·见 `backseat_pick`）：板块联动段每行后追加「跟风」，
       并新增「今日可能涨停（后排）」段。`backseat=None` 或开关关 → 卡面回到改动前形态。
    """
    def md(*lines):
        return {"tag": "lark_md", "content": "\n".join(lines)}

    def block(*lines):
        """正文块：飞书 elements 顶层必须是 div，lark_md 须包在 text 里（否则空白）"""
        return {"tag": "div", "text": md(*lines)}

    sess_cn = "上午" if sess == "morning" else "下午"
    groups = {"涨停": [], "接近涨停": [], "快速拉升": []}
    for s in signals:
        groups.setdefault(s["type"], []).append(s)

    lines = []
    if signals:
        # ⛔ 2026-11-13 修：原来一律写「最强 signals[0]」，纯下跌批会出现
        #    「最强 中稀有色 −8.19%」这种自相矛盾的文案。
        #    现在按方向取：有上涨类信号 → 最强（涨幅最大）；全是下跌类 → 领跌（跌幅最大）。
        _UP_TYPES = ("涨停", "接近涨停", "快速拉升", "炸板·回落")
        _up = [x for x in signals if x.get("type") in _UP_TYPES]
        if _up:
            top = max(_up, key=lambda x: float(x.get("pct") or 0))
            label, tcol = "最强", TYPE_COLORS.get(top["type"], "red")
        else:
            top = min(signals, key=lambda x: float(x.get("pct") or 0))
            label, tcol = "领跌", "green"
        # 单条不写摘要头（2026-11-13 用户：1 只票在头部和明细里复读两遍）
        # 多条才写「本批 N 只｜最强/领跌」摘要；理由交给明细行。
        if len(signals) > 1:
            lines.append(f"**🧭 本批 {len(signals)} 只**｜{label} "
                     f"<text_tag color='{tcol}'>{top['name']}({top['code']})</text_tag> "
                         f"{_pct_tag(top['pct'])}（{top['sector']}）｜{sess_cn}时段")
    for typ, title, col in (("涨停", "🔥 涨停信号", "red"),
                            ("炸板·回落", "🔥 炸板回落", "red"),
                            ("接近涨停", "🚀 接近涨停", "purple"),
                            ("快速拉升", "⚡ 快速拉升", "red"),
                            ("跌停", "🔻 跌停", "green"),
                            ("打开跌停", "🔓 打开跌停", "green"),
                            ("快速下跌", "💥 快速下跌", "green")):
        arr = groups.get(typ, [])
        if not arr:
            continue
        lines.append(f"**{title} {len(arr)}只**：")
        for s in arr:
            lines.append(
                f"　<text_tag color='{col}'>{s['name']}({s['code']})</text_tag> "
                f"{_pct_tag(s['pct'])}（{s['sector']}）｜⚡{s['reason']}")
    # 后排候选（2026-10-14）：板块联动「跟风」与「今日可能涨停」段共用同一批候选（backseat_pick 产出）。
    _bs = backseat if isinstance(backseat, dict) else {}
    _bs_cands = _bs.get("candidates") or []
    _bs_followed: set = set()
    # 🔥 板块联动归纳（2026-10-12 用户「尽量进行板块联动的观察归纳」）：
    #   把本轮异动按板块聚合 → 「风电 3只(涨停1)｜最强 X」。纯呈现层；异常降级不影响主卡。
    if ENABLE_SPIRIT_SECTOR_GROUP:
        try:
            # 2026-11-03：聚合数据源 = 过滤后**全池**（`group_view`），不是截断后的推送序列。
            #   strength 里成交额占 0.2 权重 → 一字板被挤出 max_signals 名额 → 板块段跟着丢票。
            #   ⛔ 只影响这一段的家数展示，不改上面「涨停信号」推送的名额与排序。
            _summ = (group_summary if group_summary is not None else
                     _sector_group_summary(group_view if group_view is not None else signals))
            _linked = [x for x in _summ if x[1] >= SECTOR_GROUP_MIN_ITEMS or x[2] >= 1]
            if _linked:
                lines.append("")
                lines.append(f"**🔥 板块联动 {len(_linked)}个**：")
                for _sec, _n, _zt, _top in _linked[:SECTOR_GROUP_MAX]:
                    _seg = f"　**{_sec}** {_n}只"
                    if _zt:
                        _seg += f"(涨停{_zt})"
                    # ⛔ 2026-11-13：板块内全是下跌票时不能写「最强」，改「领跌」
                    _tp = float(_top.get("pct") or 0)
                    _lab = "最强" if _tp > 0 else "领跌"
                    _seg += (f"｜{_lab} {_top.get('name')}({_top.get('code')}) "
                             f"{_pct_tag(_tp)}")
                    lines.append(_seg)
                    # 跟风（2026-10-14 用户「除了最强那只，后面还要跟一个板块跟风，列举几个个股+涨幅」）：
                    #   从后排候选里取**同板块**别的票（候选已剔除 signals → 天然不含「最强那只」），
                    #   辨识度/地位/温和放量已由 backseat_pick 保证。开关同 ENABLE_SPIRIT_BACKSEAT。
                    if ENABLE_SPIRIT_BACKSEAT and _bs_cands:
                        _top_code = str(_top.get("code") or "")[:6]
                        _fol = [c for c in _bs_cands
                                if str(c.get("code") or "") not in _bs_followed
                                and str(c.get("code") or "") != _top_code
                                and _sector_match_any(_sec, str(c.get("sector") or ""))][:BS_FOLLOW_MAX]
                        if _fol:
                            _bs_followed |= {str(x.get("code") or "") for x in _fol}
                            if shown_sink is not None:
                                shown_sink.update(str(x.get("code") or "") for x in _fol)
                            lines.append("　　↳ 跟风：" + "｜".join(
                                f"{x['name']}({x['code']}) {_pct_tag(float(x['pct'] or 0))}"
                                for x in _fol))
        except Exception as e:  # ⛔ 盘中链路安全第一：新段异常不得影响主卡
            print(f"[short_spirit] 板块联动段降级(已忽略): {e}")
    # 📈 今日可能涨停（后排）（2026-10-14 用户「再给我推一个今天有可能涨停的个股，就是后排的」）：
    #   与「涨停信号」互补 —— 那边报已封板，这边报「已启动、还没封、今日有封板可能」的后排。
    #   已被板块联动当「跟风」展示过的不重复列（同卡不自我重复）。
    if ENABLE_SPIRIT_BACKSEAT:
        try:
            # 同卡不重复：已被板块联动当「跟风」、或已被接力段当「后排可参与」的 → 此处不再列。
            _relay_codes = {str(x.get("code") or "")[:6]
                            for x in ((relay or {}).get("tradable") or [])}
            _pot = [c for c in (_bs.get("potential") or [])
                    if str(c.get("code") or "") not in _bs_followed
                    and str(c.get("code") or "") not in _relay_codes]
            if _pot:
                lines.append("")
                lines.append("**📈 今日可能涨停（后排）**：")
                if shown_sink is not None:
                    shown_sink.update(str(c.get("code") or "") for c in _pot)
                for c in _pot:
                    _why = str(c.get("why") or "")
                    if float(c.get("vol_ratio") or 0) > 0:      # 量比有数据才写「温和放量」
                        _why = (_why + "·" if _why else "") + "温和放量"
                    lines.append(
                        f"　{c['name']}({c['code']}) {_pct_tag(float(c['pct'] or 0))}"
                        f"（{c['sector']}）" + (f"｜{_why}" if _why else ""))
        except Exception as e:  # ⛔ 盘中链路安全第一：新段异常不得影响主卡
            print(f"[short_spirit] 后排预判段降级(已忽略): {e}")
    # 🎯 板块接力方向（2026-10-08 用户核心功能·A 盘中即时）：
    #   「捕获到主线板块强势票涨停 → 顺势给该方向该买什么」。
    #   2026-10-08 二改（用户：「如果他是前排，记得把这个后排也标记出来，带上可参与后排的涨幅」）：
    #   卡面固定三段 → 👑 前排（谁领涨·板数最高）｜🎯 后排可参与（买谁·带涨幅）｜🔒 后排关联（含已封板·参考）。
    #   2026-10-12 B 档：第三段后排池用选股宝题材 top5[题材].zt_stocks 补全（见 _relay_hint）。
    if relay:
        sec = relay.get("sector") or ""
        zt_n = relay.get("zt_count") or 0
        # 官方码（2026-10-08 用户「改为东财官方板块名」）：锂电池 → 锂电池(BK1303)。
        # 只有名字证据充分时才挂；聚合别名（大消费）不挂，避免误导。
        o_tag = _official_tag(sec) if _official_tag else ""
        head = f"**🎯 接力方向｜{sec}{o_tag}**"
        if zt_n:
            head += f"（今日 {zt_n} 家涨停）"
            # 2026-11-03：`zt_count` 现在是选股宝权威家数，可能多于本卡能列出的只数
            #   （limit_up_stocks 覆盖不全 / 前排与本轮已单独列）。差 ≥2 才标注，
            #   ⛔ 否则用户看到「8 家」却只数到 5 只 → 以为系统又漏了。
            _ld = relay.get("ladder_count")
            if isinstance(_ld, int) and zt_n - _ld >= 2:
                head += f"·本卡列 {_ld} 只"
        lines.append("")
        lines.append(head)
        fr = relay.get("front") or {}
        fcode = str(fr.get("code") or "")
        if fr.get("name"):
            # 2026-10-20：前排也走统一渲染（带辨识度/溢价）——原来只给「名字 板数 涨幅」
            if ENABLE_SPIRIT_RELAY_ENRICH:
                fseg = _relay_seg(fr)
            else:
                fseg = f"{fr['name']}" + (f"({fcode})" if fcode else "")
                if fr.get("boards"):
                    fseg += f" {fr['boards']}"
                fseg += f" {_pct_tag(float(fr.get('pct') or 0))}"
            # 本轮这只到底是前排还是后排 —— 用户「逻辑就很清晰」的关键一句
            if relay.get("front_in_signal"):
                mark = "　← 本轮捕捉"
            elif relay.get("signal_in_ladder"):
                mark = "　（本轮这只属后排）"
            else:
                mark = ""
            lines.append(f"　👑 前排：{fseg}{mark}")
        tr = relay.get("tradable") or []
        if tr:
            label = "🎯 后排可参与"
            if (relay.get("backseat_src") or "") == "board":
                label += "（未封板·现在能买）"
            lines.append(f"　{label}：")
            for x in tr:
                # ⛔ 每只一行：融合后字段变多，挤一行会看不清（用户要的是能决策的信息密度）
                if ENABLE_SPIRIT_RELAY_ENRICH:
                    lines.append(f"　　{_relay_seg(x)}")
                else:
                    lines.append(
                        f"　　{x['name']}({x['code']}) {_pct_tag(float(x['pct'] or 0))}"
                        + (f" 换手{x['turnover']:.1f}%" if x.get("turnover") else ""))
        elif relay.get("backseat_unknown"):
            # ⛔ 抓不到 ≠ 没有。10-20 PCB 板曾把限频渲染成「已无未封板票」，而实际涨幅榜第 4 就能买。
            lines.append("　🎯 ⚠️ 板块实时行情未取到 → 本段留空（不代表没有未封板票）")
        else:
            lines.append("　🎯 该方向已无未封板票 → 等明日竞价看前排溢价，不追高")
        # 其余已封板（前排与本轮已捕捉的不重复列 → 卡面不自我重复）
        seen = {fcode} | {str(s.get("code") or "")[:6] for s in (signals or [])}
        others = [x for x in (relay.get("ladder") or []) if str(x.get("code") or "") not in seen]
        if others:
            label3 = "🔒 后排关联"
            if ENABLE_SPIRIT_RELAY_ENRICH:
                label3 += "（已封板·看明日竞价溢价）"
            lines.append(f"　{label3}：")
            for x in others:
                if ENABLE_SPIRIT_RELAY_ENRICH:
                    lines.append(f"　　{_relay_seg(x)}")
                else:
                    lines.append("　　" + (f"{x['name']}({x['boards']})" if x.get("boards")
                                           else f"{x['name']}"))
    lines.append("")
    return {
        "title": f"短线精灵·异动信号 | {now.strftime('%H:%M')} | 命中{len(signals)}只",
        "color": "red",
        "elements": [
            block(*lines),
            {"tag": "hr"},
            {"tag": "note", "elements": [md(f"📡 选股宝+东财 | {now.strftime('%H:%M')}")]},
        ],
    }


def _send_dedup_path() -> Path:
    return ROOT / "cache" / "spirit_send_dedup.json"


def _events_path() -> Path:
    """逐事件审计落盘路径（按日切分·append-only）。"""
    return ROOT / "cache" / f"short_spirit_events_{datetime.now().strftime('%Y%m%d')}.jsonl"


def _scan_history_today() -> dict:
    """当日逐事件流 → 「今天出现过哪些信号」的身份表（code → 首次/末次时刻 + 出现轮次）。

    ⛔ 为什么需要它：`OUT_PATH` 的 `signals` 是**本轮触发**的信号，文件每轮**覆盖写**；
    而 `_timeliness_gate` 又会剔掉存量涨停 —— 于是「早盘触发过、现在已封住」的票
    在本轮 signals 里**必然为空** → 前端一刷新列表就没了（用户 2026-09-21 抓包
    「短线精灵…我这一刷新，列表那里就空了」）。
    事件流（`short_spirit_events_<date>.jsonl`）是 append-only 的审计主源，
    故从它回填「当日信号身份 + 首次时刻」—— **修复前的历史记录也能捞回来**。
    """
    out: dict = {}
    p = _events_path()
    if not p.exists():
        return out
    try:
        for r in load_jsonl(p):
            if r.get("kind") != "scan":
                continue
            at = str(r.get("at") or "")[-8:]  # 'HH:MM:SS'
            for it in (r.get("items") or []):
                code = str(it.get("code") or "")
                if not code:
                    continue
                cur = out.get(code)
                if cur is None:
                    out[code] = {
                        "code": code, "name": it.get("name"), "type": it.get("type"),
                        "sector": it.get("sector"), "strength": it.get("strength"),
                        "first_seen": at, "last_seen": at, "seen_count": 1,
                    }
                else:
                    cur["last_seen"] = at
                    cur["seen_count"] += 1
    except Exception as e:  # ⛔ 盘中链路安全第一：解析失败不影响主流程
        print(f"[short_spirit] scan 历史解析失败(已忽略): {e}")
    return out


def build_signals_today(prev: dict, signals: list, now) -> list:
    """当日信号沉淀（2026-09-21 用户「做好沉淀」）。

    三源合并（同 code 归并）：
      ① 上一版 `signals_today` —— 保住已累积的完整字段；
      ② 当日事件流的 scan 身份表 —— **回填今天更早轮次**（含修复前的记录）；
      ③ 本轮 `signals` —— 带 pct / 资金 / 共振等完整字段，覆盖同 code 的旧值。
    ⛔ `first_seen` 一经确定**不再改写**：沉淀的核心就是「第一次什么时候出现的」。
    排序 = `first_seen` **倒序**（盯盘先看最近发生了什么）。
    """
    date_now = now.strftime("%Y-%m-%d")
    acc: dict = {}
    if prev.get("date") == date_now:
        for it in (prev.get("signals_today") or []):
            code = str(it.get("code") or "")
            if code:
                acc[code] = dict(it)
    for code, it in _scan_history_today().items():
        cur = acc.get(code)
        if cur is None:
            acc[code] = dict(it)
        else:
            if not cur.get("first_seen"):
                cur["first_seen"] = it.get("first_seen")
            cur["last_seen"] = it.get("last_seen") or cur.get("last_seen")
            cur["seen_count"] = max(cur.get("seen_count") or 0, it.get("seen_count") or 0)
    hhmmss = now.strftime("%H:%M:%S")
    for s in signals:
        code = str(s.get("code") or "")
        if not code:
            continue
        cur = acc.get(code) or {}
        merged = {**cur, **s}
        merged["first_seen"] = cur.get("first_seen") or hhmmss
        merged["last_seen"] = hhmmss
        merged["seen_count"] = (cur.get("seen_count") or 0) + 1
        acc[code] = merged
    rows = sorted(acc.values(), key=lambda x: (x.get("first_seen") or ""), reverse=True)
    return rows[:HISTORY_TODAY_MAX]


def append_event(rec: dict) -> None:
    """⛔ 事件级审计（2026-09-17 A3）：把**逐事件**（含时间与身份）追加落盘，
    供「合并后不丢不重 / 送达状态」核验 —— 原实现只有覆盖写快照，无法还原每次推送。

    ⚠️ 盘中链路安全第一：**任何异常都不得影响主流程**，故整体 try 包裹、失败仅打印。
    ⛔ 本函数只追加记录，**不改变任何推送行为/节奏**。
    """
    try:
        r = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **rec}
        append_jsonl(_events_path(), r)
    except Exception as e:
        print(f"[short_spirit] 事件落盘降级（不影响主流程）: {e}", file=sys.stderr)


def _send_card(card_path: str, webhook: str | None = None, dry: bool = False) -> int:
    """统一出口模式（2026-09-04 深度合并·短线精灵=异动群唯一推送执行引擎）：
    live_signal（苗头/核按钮/N字/回流/资金切换/首板/盘中异动）与节拍卡不再自行
    push_card，全部透传本函数出卡——标题已统一「⚡短线精灵·」系列。
    幂等=内容 hash 短窗 45s 防抖（防守护轮+外部双写同一卡文件重推）；
    各卡自身语义幂等（核按钮档/苗头 map/精灵 codes）仍由各自检测侧负责。
    """
    cf = Path(card_path)
    if not cf.exists():
        print(f"[short_spirit] --send 卡文件不存在: {cf}")
        return 1
    try:
        card = json.loads(cf.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[short_spirit] --send 卡解析失败: {e}")
        return 1
    if not in_trading():
        print("[short_spirit] --send 非交易时段·跳过")
        return 0
    content = json.dumps(card, ensure_ascii=False)
    h = hashlib.sha1(content.encode()).hexdigest()
    dp = _send_dedup_path()
    ded = load_json(dp) or {}
    now = datetime.now()
    last = ded.get(h, 0)
    if now.timestamp() - float(last) < 45.0:
        print(f"[short_spirit] --send 同内容 45s 内已推·跳过（防抖 {cf.name}）")
        return 0
    ded = {k: v for k, v in ded.items() if now.timestamp() - float(v) < 600}  # 只留 10min 窗口
    if dry:
        # ⛔ 有界修复（2026-09-17）：dry 仅预览/校验，不得写下内容去重——
        # 否则把「未真正推送」的卡记成已推，真实发送时 45s 防抖会误拦本应重发的卡。
        print(f"[short_spirit] --send(dry) {cf.name}: {card.get('title', '')[:60]}")
        return 0
    wc = f"--webhook {webhook} " if webhook else "--webhook feishu_webhook_intraday "
    rc = __import__("os").system(f'"{sys.executable}" "{ROOT}/scripts/push_card.py" {wc}"{cf}"')
    if rc == 0:
        # ⛔ 有界修复（2026-09-17）：仅「实际发送成功」才提交内容去重；
        # 发送失败不提交，下一轮可重试（不把失败卡误判为已推）。
        ded[h] = now.timestamp()
        save_json(dp, ded)
        print(f"[short_spirit] --send 推送成功 {cf.name} | {card.get('title', '')[:50]}")
        return 0
    print(f"[short_spirit] --send 推送失败 {cf.name} | {card.get('title', '')[:50]}")
    return 1


def _run(args):
    """持锁后的实际执行入口；避免多个守护分支同时读改写同一批状态。"""
    global cfg
    cfg = load_cfg()
    if args.send:
        return _send_card(args.send, webhook=args.webhook, dry=args.dry_run)
    if not in_trading():
        print("[short_spirit] 非交易时段，跳过")
        return
    now = datetime.now()
    if not SNAP.exists():
        print("[short_spirit] latest.json 缺失")
        return
    d = json.loads(SNAP.read_text(encoding="utf-8"))

    signals, sess, kept, total = build_signals(d, now, cfg)
    # 下跌支路（2026-09-21 用户「盲区补强」）：平行独立链路，合并进同一信号流 →
    # 沉淀/扫描事件/推送池/飞书卡分组都自然覆盖（build_card 按 type 动态分组）。
    sig_down, _sess_d, kept_d, total_d = build_signals_down(d, now, cfg)
    if sig_down:
        signals = signals + sig_down
        kept += kept_d
        total += total_d
    # ⛔ 2026-11-05：上涨/下跌两路 + 三路池源合并后，同 code 只留一条。
    #   09:57 实测同一张卡把 002428 推了两行。此处是唯一装配点，覆盖审计/沉淀/推送池。
    signals = _dedup_signals(signals)
    # 当日信号沉淀（2026-09-21 用户「做好沉淀」）：`signals` 只表达「本轮新触发」，
    # 覆盖写后上一轮的触发会消失 → 前端一刷新列表就空。故另存一份当日累积。
    _prev = load_json(OUT_PATH) or {}
    signals_today = build_signals_today(_prev, signals, now)
    result = {"ts": now.strftime("%Y-%m-%d %H:%M:%S"),
              "date": now.strftime("%Y-%m-%d"),
              "session": sess,
              "thresholds": {k: cfg[k] for k in ("min_pct", "min_market_cap_yi", "amount_morning_yi", "amount_afternoon_yi")},
              "source": {"up": dict(FETCH_STATUS["up"]), "down": dict(FETCH_STATUS["down"])},
              "min_pct_down": float(cfg.get("min_pct_down", 7.5)),
              "pool_total": total, "after_filter": kept,
              "signals": signals, "signals_today": signals_today}
    save_json(OUT_PATH, result)
    print(f"[short_spirit] 池{total}→过滤{kept}→本轮信号{len(signals)}｜当日沉淀{len(signals_today)} | 时段={sess}")
    # ⛔ A3 事件级审计：本轮「看到了什么」（逐事件含类型/行业/强度，时间=本轮 at）
    append_event({"kind": "scan", "session": sess, "pool_total": total, "after_filter": kept,
                  "count": len(signals),
                  "items": [{"code": s["code"], "name": s.get("name"), "type": s.get("type"),
                             "sector": s.get("sector"), "strength": s.get("strength")}
                            for s in signals]})

    if args.dry_run or not args.push:
        return

    # 双触发推送（2026-08-18 用户定：数量累计 + 超时兜底，替代固定冷却）：
    #   - 新信号（code ∉ 已推 ∪ 池内）入待推池
    #   - 池 ≥ batch_threshold（数量达标） 或 距上推 ≥ batch_timeout_min（超时兜底且池非空）→ 推卡
    pushed = load_pushed()
    date_now = now.strftime("%Y-%m-%d")
    if pushed.get("date") != date_now:
        pushed = {"date": date_now, "codes": [], "last_push": "", "pending": [], "pending_ts": ""}
    pending = pushed.get("pending", []) or []
    pending_codes = {s["code"] for s in pending}
    pushed_codes = set(pushed.get("codes", []) or [])
    new_pool = [s for s in signals if s["code"] not in pushed_codes and s["code"] not in pending_codes]
    if new_pool:
        pending.extend(new_pool)
        pushed["pending"] = pending
        if not pushed.get("pending_ts"):
            pushed["pending_ts"] = now.isoformat()

    batch = cfg["batch_threshold"]
    timeout_min = cfg["batch_timeout_min"]
    last = pushed.get("last_push", "")
    elapsed = 1e9
    if last:
        try:
            elapsed = (now - datetime.strptime(f"{date_now} {last}", "%Y-%m-%d %H:%M:%S")).total_seconds()
        except ValueError:
            elapsed = 1e9
    by_count = len(pending) >= batch
    by_timeout = bool(pending) and elapsed >= timeout_min * 60
    # --force（2026-08-24 早盘高频实时窗口调用）：跳过双触发门槛，有信号即推。
    # 幂等：当天 code 去重仍生效（新 code 才入池），--force 只是"不等数量/超时"。
    if not (by_count or by_timeout) and not args.force:
        save_pushed(pushed, now, update_last=False)
        print(f"[short_spirit] 累计池 {len(pending)}/{batch}（或{timeout_min}min超时）未达阈值，跳过"
              f"{'，本轮新增' + str(len(new_pool)) if new_pool else ''}")
        return
    # --force 但待推池为空 → 无信号可推，防空卡误推（2026-08-24 早盘高频窗口）
    if not pending:
        save_pushed(pushed, now, update_last=False)
        print("[short_spirit] --force 但待推池为空，跳过（无新信号）")
        return

    # ⛔ 低强度合并（2026-09-17 用户「前后端都改」）：
    #   池内**只有 1 条且全为低强度（快速拉升）** → 不单独出卡，等相邻低强度一起推。
    #   ⚠️ by_timeout 兜底不受影响（超时仍推）→ 信息不丢；涨停/冲板/炸板不受影响。
    #   ⚠️ 早盘高频窗口 09:25–09:55 豁免（用户最高优先级：窗口内「有信号就立刻推」）。
    if (ENABLE_SPIRIT_LOWSTRENGTH_MERGE
            and len(pending) == 1
            and all(s.get("type") in LOW_STRENGTH_TYPES for s in pending)
            and not by_timeout
            and not in_early_realtime_window(now)):
        save_pushed(pushed, now, update_last=False)
        print("[short_spirit] 低强度单条·合并等待（只合并相邻低强度，不单独出卡）")
        append_event({"kind": "held", "reason": "low_strength_single",
                      "codes": [s["code"] for s in pending]})
        return

    card_signals = sorted(pending, key=lambda x: -x["strength"])[: cfg["max_signals"]]
    # 🎯 板块接力方向（2026-10-08 用户核心功能·A 盘中即时）：由本轮实际入卡信号定位主线板块。
    # 限频（09-15 铁律 冷却∧指纹变化）：同板块内容未变且未过冷却 → 本卡不带方向，防连卡刷屏。
    relay = _relay_hint(card_signals, d)
    if relay:
        emit, fp = _relay_should_emit(relay, now)
        if emit:
            _relay_mark(relay, fp, now)
            append_event({"kind": "relay", "sector": relay["sector"], "zt_count": relay["zt_count"],
                          "tradable": [x["code"] for x in relay["tradable"]]})
        else:
            relay = None
    # 📈 后排预判（2026-10-14 用户「今天有可能涨停的后排」）：从**原始涨幅榜**挑候选。
    #   数据源 = build_signals 当轮缓存的未过滤池（POOL_CACHE.up）；东财抓取失败 → 空池 →
    #   板块联动「跟风」与「今日可能涨停」两段自然不渲染（降级安全）。
    _backseat = None
    if ENABLE_SPIRIT_BACKSEAT:
        try:
            _backseat = backseat_pick(POOL_CACHE.get("up") or [], card_signals, d, sess, cfg)
        except Exception as e:
            print(f"[short_spirit] 后排预判降级(已忽略): {e}")
        # 2026-11-05：后排/跟风候选当日已展示不重展（只影响附带段，不动主信号）。
        if ENABLE_SPIRIT_BACKSEAT_DELTA and _backseat:
            _backseat = _filter_backseat_shown(
                _backseat, pushed.get("backseat_shown") or [])
    _group_view = GROUP_VIEW_CACHE.get("view")
    _group_rows, _group_updates = _linkage_delta(
        _sector_group_summary(_group_view if _group_view is not None else card_signals),
        pushed.get("linkage_sent") or {}) if ENABLE_SPIRIT_SECTOR_GROUP else ([], {})
    _backseat_shown: set = set()
    card = build_card(card_signals, sess, now, relay=relay, backseat=_backseat,
                      group_view=_group_view, group_summary=_group_rows,
                      shown_sink=_backseat_shown if ENABLE_SPIRIT_BACKSEAT_DELTA else None)
    cf = ROOT / "cache" / "short_spirit_card.json"
    save_json(cf, card)
    # ⛔ 有界修复（2026-09-17）：入池后、任何发送尝试之前可靠落盘 pending（update_last=False）。
    # 下方 push_card 失败（rc!=0）时：pending 已持久化、codes/last_push 未登记 → 下一轮可重试；
    # 成功时下方 save_pushed(pushed, now)（update_last=True）再正式结算。不可改 batch/force/选股/过滤/调度业务。
    save_pushed(pushed, now, update_last=False)
    rc = __import__("os").system(f'"{sys.executable}" "{ROOT}/scripts/push_card.py" --webhook feishu_webhook_intraday "{cf}"')
    if rc == 0:
        pushed.setdefault("linkage_sent", {}).update(_group_updates)
        # ⛔ 仅成功送达才登记「已展示」：发送失败下一轮仍可重新给出这些候选。
        if ENABLE_SPIRIT_BACKSEAT_DELTA and _backseat_shown:
            pushed["backseat_shown"] = sorted(
                set(pushed.get("backseat_shown") or []) | _backseat_shown)
        pushed["codes"] = (pushed.get("codes", []) or []) + [s["code"] for s in card_signals]
        # 池内超出 max_signals 的部分保留，不丢（下次触发再推）
        pushed["pending"] = [s for s in pending if s["code"] not in {x["code"] for x in card_signals}]
        if not pushed["pending"]:
            pushed["pending_ts"] = ""
        save_pushed(pushed, now)
        print(f"[short_spirit] 推送成功 {len(card_signals)} 只（触发：{'数量达标' if by_count else '超时兜底'}）")
        # ⛔ A3 事件级审计：本轮「送达了什么」（仅在成功分支 → 送达状态可信）
        append_event({"kind": "pushed", "count": len(card_signals),
                      "codes": [s["code"] for s in card_signals],
                      "trigger": "count" if by_count else ("timeout" if by_timeout else "force")})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="检测并推送")
    ap.add_argument("--force", action="store_true",
                    help="跳过 batch/timeout 双触发，直接推当前待推池（2026-08-24 早盘高频窗口用）")
    ap.add_argument("--dry-run", action="store_true", help="只出 JSON 不推")
    ap.add_argument("--send", metavar="CARD_JSON",
                    help="统一出口模式（2026-09-04）：透传已拼好的短线精灵系列卡到异动群·内容级 45s 防抖")
    ap.add_argument("--webhook", default=None, help="--send 时可选覆盖推送目标（默认异动群；测试用 feishu_webhook_test）")
    args = ap.parse_args()

    # live_signal 的多条分支都会唤起精灵；整段流程持同一把锁，才不会重复发送或覆盖 pending。
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[short_spirit] 已有实例执行中，跳过")
            return 0
        try:
            return _run(args)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    sys.exit(main() or 0)
