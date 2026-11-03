#!/usr/bin/env bash
# compare_shadow.sh —— 影子管线 vs 现役管线：同一时刻两边算出来的数一样吗
#
# 迁数据面最大的风险不是「能不能跑」，而是「跑出来的结果不一样还不报错」。
# 比如东财接口在服务器返回的字段顺序不同、或某个脚本依赖本机时区 —— 都会静默改变数值。
# 这个脚本只做一件事：把两边**关键字段**并排列出来，让人一眼看出差在哪。
set -uo pipefail
source "$(dirname "$0")/common.sh"

SHADOW_DIR="${SHADOW_DIR:-$REMOTE_ROOT/shadow}"
LIVE_DIR="${LIVE_DIR:-$REMOTE_ROOT/current}"

probe() {
  rsh "python3 - '$1' <<'PY'
import json,sys,os
root=sys.argv[1]
def load(p):
    try: return json.load(open(os.path.join(root,p),encoding='utf-8'))
    except Exception: return None
out=[]
d=load('cache/pulse_snapshots/latest.json')
out.append(('latest.ts', (d or {}).get('ts')))
out.append(('total_zt', (d or {}).get('total_zt')))
out.append(('sentiment', (d or {}).get('sentiment')))
out.append(('top5首名', ((d or {}).get('top5') or [{}])[0].get('name')))
out.append(('top5家数', len((d or {}).get('top5') or [])))
out.append(('涨停池', len((d or {}).get('limit_up_stocks') or [])))
r=load('cache/pulse_snapshots/data_ready.json')
out.append(('ready', (r or {}).get('ready')))
c=load('cache/pulse_snapshots/candidates.json')
out.append(('候选数', len((c or {}).get('candidates') or (c or {}).get('pool') or [])))
for k,v in out: print(f'{k}={v}')
PY" 2>/dev/null
}

echo "── 影子（服务器自产）: $SHADOW_DIR"
S=$(probe "$SHADOW_DIR")
[ -n "$S" ] && echo "$S" | sed 's/^/  /' || echo "  （还没有数据，等开盘后影子跑起来）"

echo "── 现役（Mac 产出 -> 同步）: $LIVE_DIR"
L=$(probe "$LIVE_DIR")
[ -n "$L" ] && echo "$L" | sed 's/^/  /' || echo "  （读不到）"

echo "── 差异"
if [ -n "$S" ] && [ -n "$L" ]; then
  diff <(echo "$S") <(echo "$L") >/dev/null && echo "  ✓ 关键字段完全一致" || {
    echo "  ⚠️ 存在差异："
    diff <(echo "$S") <(echo "$L") | sed 's/^/    /'
  }
else
  echo "  无法比较（缺一边数据）"
fi
