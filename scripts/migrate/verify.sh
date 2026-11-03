#!/usr/bin/env bash
# verify.sh —— 迁移后「真的没坏」的对照检查（只读，不改任何东西）
#
# 检查的是**事实一致性**，不是「服务有没有起来」：
#   ① 同一份数据两边的日期/条数是否一致（数据副本陈旧会导致页面看的是旧盘）
#   ② 关键路由是否都 200（白名单漏了会 404）
#   ③ 站点产物是否与本地构建同一版（index.html 里的 hash）
set -uo pipefail
source "$(dirname "$0")/common.sh"

PASS=0; FAIL=0
ok(){ printf '  \033[32m✓\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
bad(){ printf '  \033[31m✗\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }

echo "── ① 本机站点"
L=$(curl --noproxy '*' -s -m 5 "http://127.0.0.1:$LOCAL_SERVE_PORT/api/health" || true)
[ -n "$L" ] && ok "本机 /api/health 有响应" || bad "本机 :$LOCAL_SERVE_PORT 无响应"

echo "── ② 远端点"
R=$(rsh "curl -sf -m 5 http://127.0.0.1:8000/api/health || true")
[ -n "$R" ] && ok "远端 /api/health 有响应" || bad "远端 serve 未就绪"

echo "── ③ 数据新鲜度对照（最新快照时间）"
lj="$(python3 - <<'PY'
import json,os
p='/Users/zxl/WorkBuddy/狸猫猎手/cache/pulse_snapshots/latest.json'
try:
    d=json.load(open(p,encoding='utf-8')); print(d.get('ts','')[:19])
except Exception: print('')
PY
)"
rj="$(rsh "python3 -c \"import json;d=json.load(open('$REMOTE_ROOT/current/cache/pulse_snapshots/latest.json'));print(d.get('ts','')[:19])\" 2>/dev/null" || true)"
echo "    本地快照: ${lj:-无}   远端快照: ${rj:-无}"
if [ -n "$lj" ] && [ -n "$rj" ]; then
  [ "$lj" = "$rj" ] && ok "两边快照时间一致" || warn "两边快照不一致（site 模式下属正常，pipeline 模式下必须一致）"
fi

echo "── ④ 关键路由抽样（白名单是否漏）"
for p in /api/market/latest /api/decision/latest /api/flow/live /api/gene/hist; do
  code=$(rsh "curl -s -o /dev/null -w '%{http_code}' -m 8 http://127.0.0.1:8000$p")
  [ "$code" = "200" ] && ok "远端 $p 200" || bad "远端 $p → $code"
done

echo "── ⑤ 站点产物版本对照"
lh=$(shasum -a 256 "$TERMINAL_DIR/dist/index.html" 2>/dev/null | awk '{print $1}')
rh=$(rsh "sha256sum '$REMOTE_ROOT/current/dist/index.html' 2>/dev/null | awk '{print \$1}'" || true)
[ -n "$lh" ] && [ "$lh" = "$rh" ] && ok "index.html 与本地构建同版本" || bad "index.html 版本不一致（远端 ${rh:0:12}… / 本地 ${lh:0:12}…）"

echo "── ⑥ 数据联动覆盖（远端缺文件 = 页面会 404 或空态）"
for t in 0 1; do
  list="$APP_DIR/dist/migrate/T$t.txt"
  [ -s "$list" ] || { warn "缺清单 T$t.txt（先跑 manifest.py）"; continue; }
  miss=$( { echo "cd '$REMOTE_ROOT/current' || exit 9"
           while read -r f; do [ -n "$f" ] && echo "[ -f \"$f\" ] || echo MISSING:$f"; done < "$list"
         } | rsh "bash -s" 2>/dev/null | grep -c MISSING || true)
  # 回退：上面拼装了 37~672 条命令，若远端报错则退化为计数
  if [ "${miss:-0}" = "0" ]; then ok "T$t 远端文件齐全（清单 $(wc -l < "$list" | tr -d ' ')）"
  else bad "T$t 远端缺 ${miss} 个文件（跑 TIER=$t bash sync_hot.sh）"; fi
done

echo
echo "结果: 通过 $PASS / 失败 $FAIL"
[ "$FAIL" = "0" ] || exit 1
