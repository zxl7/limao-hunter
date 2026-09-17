#!/bin/bash
# 盘中脉冲数据守护（时间感知节流，2026-08-14 提速版）：
#   早盘 09:15-11:30 → 30s；午休 11:30-13:00 → 5min；下午 13:00-15:00 → 60s；非交易 → 10min
# 本脚本由 launchd 每 30s 唤醒一次（plist StartInterval=30），内部按当前时段决定是否真正刷新（未到间隔则静默 exit）。
# 非交易时段由 intraday_pulse.py 的 in_trading 守卫自动跳过抓取。
cd /Users/zxl/WorkBuddy/狸猫猎手
PY=/Users/zxl/.workbuddy/binaries/python/versions/3.13.12/bin/python3
LOG=cache/pulse_snapshots/fetch.log
STATE=cache/pulse_snapshots/.last_fetch_ts

# ⛔ 单实例锁（2026-09-15 双实例并发事故根治）
# 事故：crontab 存在两条完全重复的 `* * * * *` 调用 + launchd 守护(StartInterval=30) 三方抢跑，
#      两条 cron 同秒启动时都会通过下方 STATE 节流（读到同一个旧 LAST）→ 整轮链路并发跑两遍：
#      ① fetch.log 每行重复两次（时间线交错）②异动群同卡同秒双推（10:02:22 实证）
#      ③ gen_dashboard 并发写 dashboard.html → rc=1（当日 5 次 FAIL：00:27/03:28/09:42/09:46/10:49）。
# 方案：锁放在最前（节流检查之前，因「读 STATE→比较→写 STATE」本身非原子），任何来源的并发只能进一个。
# 陈旧锁：进程被 kill 等异常退出会残留 → 锁目录 mtime 超 600s 视为残留，自动清理后接管。
LOCKDIR=/tmp/limao_pulse_fetcher.lock
LOCK_OK=0
if mkdir "$LOCKDIR" 2>/dev/null; then
  LOCK_OK=1
else
  LOCK_AGE=$(( $(date +%s) - $(stat -f%m "$LOCKDIR" 2>/dev/null || echo 0) ))
  if (( LOCK_AGE > 600 )); then
    rm -rf "$LOCKDIR"
    mkdir "$LOCKDIR" 2>/dev/null && LOCK_OK=1
  fi
fi
# ⛔ 未拿到锁 = 已有实例在跑本轮 → 静默退出（不写日志，避免自身刷屏）；
#    且只在真正持锁时才注册 EXIT 清理，否则会误删他人的锁。
if (( LOCK_OK == 1 )); then
  trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT
else
  exit 0
fi

# 当前时段的目标刷新间隔(秒)
target_interval() {
  local h m mins
  h=$(date '+%H'); m=$(date '+%M')
  mins=$((10#$h * 60 + 10#$m))
  if   (( mins >= 555 && mins <= 690 )); then echo 30   # 09:15-11:30 早盘实时 30s（2026-08-14 提速 60→30）
  elif (( mins >  690 && mins <  780 )); then echo 300  # 11:30-13:00 午休 5min
  elif (( mins >= 780 && mins <= 900 )); then echo 60   # 13:00-15:00 下午 60s（2026-08-14 提速 300→60：下午异动不再等5分钟）
  else echo 600                                          # 非交易时段 10min
  fi
}

# fetch.log 轻量轮转：超过 2MB 归档为 .1，避免无限增长
rotate_log() {
  local f="$1" max=2097152 sz=0
  [[ -f "$f" ]] && sz=$(stat -f%z "$f" 2>/dev/null || echo 0)
  if (( sz > max )); then
    mv "$f" "$f.1"
    echo "[$(date '+%H:%M:%S')] fetch.log 轮转(>$((max/1024/1024))MB → .1)" >> "$f"
  fi
}

# 步骤执行 + 退出码监控：明细仍写各自 .log，fetch.log 仅记录起止与失败告警（不中断后续步骤）
run_step() {
  local label="$1" cmd="$2" logf="$3"
  echo "[$(date '+%H:%M:%S')] >>> $label" >> "$LOG"
  eval "$cmd" >> "$logf" 2>&1
  local rc=$?
  if (( rc != 0 )); then
    echo "[$(date '+%H:%M:%S')] ⚠️ FAIL: $label rc=$rc" >> "$LOG"
  fi
  return $rc
}

NOW=$(date +%s)
FORCE=0
[[ "$1" == "--force" ]] && FORCE=1
LAST=$(cat "$STATE" 2>/dev/null || echo 0)
TARGET=$(target_interval)
ELAPSED=$(( NOW - LAST ))
if (( ELAPSED < TARGET )) && (( FORCE == 0 )); then
  exit 0   # 未到刷新间隔，静默退出（launchd 下次唤醒再判；--force 可绕过）
fi
echo "$NOW" > "$STATE"
rotate_log "$LOG"
echo "[$(date '+%H:%M:%S')] fetch start (interval=${TARGET}s)" >> "$LOG"
run_step "intraday_pulse"  "$PY scripts/intraday_pulse.py --mode intra --json-only --force" "cache/pulse_snapshots/intraday.log"
run_step "gen_candidates"  "$PY scripts/gen_candidates.py"                                          "cache/pulse_snapshots/candidates.log"
run_step "feedback_matrix" "$PY scripts/feedback_matrix.py"                                         "cache/pulse_snapshots/feedback.log"
run_step "ma5_check"       "$PY scripts/ma5_check.py"                                              "cache/pulse_snapshots/ma5.log"
run_step "memory_detect"   "$PY scripts/memory_lib.py detect"                                      "cache/pulse_snapshots/detect.log"
run_step "gen_dashboard"   "$PY HTML/gen_dashboard.py"                                             "cache/pulse_snapshots/dash.log"
run_step "gen_watch"       "$PY HTML/gen_watch.py"                                                 "cache/pulse_snapshots/watch.log"
run_step "live_signal"     "$PY scripts/live_signal.py"                                            "cache/pulse_snapshots/signal.log"
# 2026-09-04 精选快讯速递（用户「异动群加一点快讯·精选速递」）：消息面催化直推异动群，
# 只播「点名个股/带板块+重磅词」的硬快讯（每轮≤2·当日 id 去重·走短线精灵 --send 统一出口）。
run_step "newsflash_express" "$PY scripts/newsflash_express.py"                                     "cache/pulse_snapshots/express.log"
# 2026-09-04 异动群节拍汇总：空窗≥15min 自动补一条「📡 盘中节拍」轻卡（无事件时保持群节奏·
# 用户「异动群至少 15 分钟推一次」）；有事件推送自动顺延不重复（独立脚本幂等判 gap）。
run_step "intraday_beat"   "$PY scripts/intraday_beat.py"                                           "cache/pulse_snapshots/beat.log"
# 2026-09-17 盘中情绪时间轴记录（用户「时间轴要记录整个交易时间的情绪起落·既有爆发也有杀跌」）：
# ⛔ 纯记录 · 零推送 · 不改任何推送行为/节奏；每轮把「本轮新发生的情绪事件」（涨停/炸板/跌停/
#    板块走强/杀跌板块）**追加**到 cache/intraday_timeline_<date>.jsonl（原 latest.json 是覆盖写，盘中历史全丢）。
# ⛔ 独立 try 包裹：任何异常都不影响守护链（run_step 已失败不中断）。
run_step "intraday_timeline" "$PY scripts/intraday_timeline.py"                                     "cache/pulse_snapshots/timeline.log"
# 2026-08-18 用户「脉冲与精灵分工」方案B：砍掉精灵日常持续调用，退化为仅 S级共振(live_signal.py:903)
# + 冷却补推(:776) 触发。日常异动全交给窗口卡(close_window_step)；精灵不再每轮守护链跑，避免双线冗余刷屏。
# run_step "short_spirit"    "$PY scripts/short_spirit.py --push"                                    "cache/pulse_snapshots/short_spirit.log"

# 脉冲窗口闭合（档位A·2026-08-15）：live_signal 常规信号已入缓冲(cache/pulse_buffer.json)，
# 时间保底节拍（2026-08-21 用户「早盘尽量5min一次·数量不够也推；下午15min一次」）：
#   早盘 09:15-11:30 → 每 5min 边界强制闭合（--force：信号不足也出卡）
#   下午 13:00-15:00 → 每 15min 边界合并闭合（--span 15 --force：段内未闭合桶合并一张卡，08-21 30→15）
# 数量驱动（volume_trigger_step，≥15条+120s冷却）随时可提前推 —— 双触发（数量 OR 时间）互不干扰。
# 守卫：
#   1) 交易日(周一至五) + 交易时段(09:15-11:30 / 13:00-15:00，含尾盘边界闭合)
#   2) 早盘分钟%5==0 / 下午分钟%15==0 才是边界
#   3) 防重：cache/pulse_windows/.closed_YYYYMMDD_HHMM（早盘单桶）/ .closed_YYYYMMDD_HHMM_s15（下午段）标记存在则跳过
# 闭合的是「刚结束的上一窗口/上一段」(--close prev)，避免把刚开盘的新桶提前闭合。
close_window_step() {
  local wd h m mins span prev_key mark p ph pm
  wd=$(date '+%u'); h=$(date '+%H'); m=$(date '+%M')
  mins=$((10#$h * 60 + 10#$m))
  (( wd >= 6 )) && return 0
  # 早盘前半小时高频实时窗口（2026-08-24 用户「解除频率限制·直接实时接口·高频脉冲」）：
  # 09:25-09:55 由 live_signal 触发短线精灵即时推，跳过 5min 窗口闭合，
  # 避免双通道重复刷屏；09:55 后恢复 5min 保底节拍（可逆：删掉本行即回退）。
  (( mins >= 565 && mins <= 595 )) && return 0
  span=0
  if   (( mins >= 555 && mins <= 690 )); then span=5   # 早盘 5min
  elif (( mins >= 780 && mins <= 900 )); then span=15  # 下午 15min（2026-08-21 30→15）
  else return 0; fi
  (( 10#$m % span != 0 )) && return 0
  # 闭合目标 = 上一窗口桶（now-1min 按5分钟取整），与 pulse_aggregator --close prev 完全一致。
  # 2026-08-18 修：原先用「触发时刻桶」命名 mark，09:30 提前写 .closed_0930，
  # 09:45 真正要闭合 0930 时被防重误杀 → 除首个窗口外全部窗口卡从未推送。
  p=$(( (mins - 1) / 5 * 5 ))
  ph=$(( p / 60 )); pm=$(( p % 60 ))
  prev_key=$(printf '%s_%02d%02d' "$(date '+%Y%m%d')" "$ph" "$pm")
  if (( span == 5 )); then
    mark="cache/pulse_windows/.closed_${prev_key}"
    [[ -f "$mark" ]] && return 0
    if run_step "pulse_window_close" "$PY scripts/pulse_aggregator.py --close prev --push --force --webhook feishu_webhook_intraday" "cache/pulse_snapshots/window.log"; then
      touch "$mark"
      echo "[$(date '+%H:%M:%S')] pulse window ${prev_key} closed & pushed (保底5min)" >> "$LOG"
    fi
  else
    mark="cache/pulse_windows/.closed_${prev_key}_s15"
    [[ -f "$mark" ]] && return 0
    if run_step "pulse_window_close" "$PY scripts/pulse_aggregator.py --close prev --push --force --span 15 --webhook feishu_webhook_intraday" "cache/pulse_snapshots/window.log"; then
      touch "$mark"
      echo "[$(date '+%H:%M:%S')] pulse span15 ${prev_key} closed & pushed (保底15min)" >> "$LOG"
    fi
  fi
}
# 2026-08-18 用户选方案A：恢复窗口聚合卡（盘中脉冲·XX窗口）持续推送。
# ⛔ 2026-09-03 异动群高频模式（HIGH_FREQ_INTRADAY=True）：live_signal 全信号直推为单通道，
#    窗口闭合卡/量触发=聚合节拍会产生同批信号的第二通道（直推一遍+段汇总一遍=双通道重复）
#    → 停用下方两调用（函数保留供手动 --simulate/排查）。
#    回滚：取消注释本行即可恢复窗口卡节拍（此时应把 live_signal HIGH_FREQ_INTRADAY=False）。
# close_window_step
# volume_trigger_step

# 量触发（2026-08-20 用户「早盘5分钟累积不止一张，别等5分钟」）：
# 当前窗口桶信号数 ≥ VOLUME_MIN(15) 且距上次量触发 ≥120s → 立即闭合推送（--close now --push --clear），
# 推完清空该桶 → 后续新信号重新入桶，5min 节拍闭合 prev 时推「增量」→ 早盘爆发可出 2-3 张快卡。
# 守卫：交易日 + 交易时段 + 冷却 + 桶内信号数达标（与 close_window_step 的 prev 闭合互不干扰）。
volume_trigger_step() {
  local wd h m mins wk n nowts last_vol el pm
  wd=$(date '+%u')
  h=$(date '+%H'); m=$(date '+%M')
  mins=$((10#$h * 60 + 10#$m))
  (( wd >= 6 )) && return 0
  # 早盘前半小时高频实时窗口（2026-08-24）：09:25-09:55 实时推送已接管，跳过量触发防双通道。
  (( mins >= 565 && mins <= 595 )) && return 0
  (( (mins >= 570 && mins <= 690) || (mins >= 780 && mins <= 900) )) || return 0
  # 冷却：距上次量触发 ≥120s
  nowts=$(date +%s)
  last_vol=$(cat cache/pulse_snapshots/.last_volume_ts 2>/dev/null || echo 0)
  el=$(( nowts - last_vol ))
  (( el >= 120 )) || return 0
  # 当前桶（now 按 5 分钟取整，与 window_key 一致）
  pm=$(( 10#$m / 5 * 5 ))
  wk=$(printf '%s_%02d%02d' "$(date '+%Y%m%d')" "$((10#$h))" "$pm")
  n=$($PY -c "
import json
try:
    d=json.load(open('cache/pulse_buffer.json'))
    print(len(d.get('windows',{}).get('$wk',[])))
except Exception:
    print(0)
" 2>/dev/null || echo 0)
  (( n >= 15 )) || return 0
  if run_step "pulse_volume_trigger" "$PY scripts/pulse_aggregator.py --close now --push --clear --webhook feishu_webhook_intraday" "cache/pulse_snapshots/window.log"; then
    echo "$nowts" > cache/pulse_snapshots/.last_volume_ts
    echo "[$(date '+%H:%M:%S')] 量触发: ${wk} 信号${n}条 ≥15 → 立即闭合推送" >> "$LOG"
  fi
}
# volume_trigger_step   # 2026-09-03 高频单通道停用（见 close_window_step 注释）
echo "[$(date '+%H:%M:%S')] detect+dashboard done" >> "$LOG"

# 每日一次清理过期记忆/去重状态（保留最近5个交易日）。守卫：同一天只清一次。
PRUNE_STATE=cache/pulse_snapshots/.last_prune_date
TODAY_D=$(date '+%Y-%m-%d')
LAST_PRUNE=$(cat "$PRUNE_STATE" 2>/dev/null || echo "")
if [[ "$TODAY_D" != "$LAST_PRUNE" ]]; then
  run_step "memory_prune" "$PY scripts/memory_lib.py prune 5" "cache/pulse_snapshots/prune.log"
  echo "$TODAY_D" > "$PRUNE_STATE"
  echo "[$(date '+%H:%M:%S')] memory pruned (retain=5 交易日)" >> "$LOG"
fi

# 日志按天归档（每天一次）：全部 *.log → logs/<base>_YYYYMMDD.log，原文件截断（守护 fd 继续写安全），
# 归档保留 10 天（find -mtime +10 删除）。prune.log 先于本步执行，当天的清理记录一并归档。
ARCH_DIR=cache/pulse_snapshots/logs
ARCH_STATE=cache/pulse_snapshots/.last_arch_date
LAST_ARCH=$(cat "$ARCH_STATE" 2>/dev/null || echo "")
if [[ "$TODAY_D" != "$LAST_ARCH" ]]; then
  mkdir -p "$ARCH_DIR"
  for f in cache/pulse_snapshots/*.log; do
    [[ -f "$f" ]] || continue
    base=$(basename "$f" .log)
    cp "$f" "$ARCH_DIR/${base}_$(date '+%Y%m%d').log" 2>/dev/null
    : > "$f"
  done
  echo "$TODAY_D" > "$ARCH_STATE"
  find "$ARCH_DIR" -type f -name "*.log" -mtime +10 -delete 2>/dev/null
  echo "[$(date '+%H:%M:%S')] logs archived → logs/ (retain=10d)" >> "$LOG"
fi
