# 狸猫猎手 · 对外共享部署参考

> 系统现状：终端 React 应用是唯一展示端。后端数据管线（24 个 WorkBuddy 自动化）继续跑在本机 Mac，serve.py 只做「只读 Web 托管 + 白名单 + 鉴权」。

## 架构原则（铁律）
- **Mac = 数据引擎**：WorkBuddy 自动化写 `cache/*.json`；serve.py 只读这些落盘文件。
- **serve.py 白名单**：暴露终端用到的全部业务数据（market/theme/flow/board/promotion/gene/emotion/zt/intraday/lhb/hithink/institution/eruption/newsflash + ai/decision/account）。
  - ✅ **2026-10-26 按用户要求放开** `/api/ai/*`、`/api/decision/*`、`/api/account/*`：持链接即可看全部业务页面。
    - 回退开关：`SHARE_PRIVATE=0` 让这三族恢复 404，其余业务路由不受影响。
    - 放开的是**业务事实**，不是凭据：响应里 `webhook / token / secret / password / api_key` 等键名一律掩码为 `***`（`redact_secrets` / `redact_text`）。
    - `cache/backups` 仍按前缀白名单，**不**按 `^.+[.]json$` 通配（该目录混有含明文 webhook 的 settings 备份）。
  - 目录遍历拦截、目录不列举、其余一律 404；`Cache-Control: no-store`。
- **公网必须开 `SHARE_TOKEN`**；本机/局域网调试可留空。

---

## 路径 A+：cloudflared 快隧（**推荐**，无警告页、无源码暴露）

ngrok 免费档会在浏览器访问时插播 `ERR_NGROK_6024` 警告页，且**子资源与 XHR 也会被换成 HTML**，
表现为整页白屏或「没数据」。cloudflared 快隧没有这一层。

```bash
# 1) 生产只读服务（必须先跑，托管 dist + 全部业务 API）
cd /Users/zxl/WorkBuddy/狸猫猎手
SHARE_PORT=8000 SHARE_HOST=127.0.0.1 python3 serve.py

# 2) 另开终端建隧道（二进制在 ~/.limao/bin/cloudflared，免安装）
~/.limao/bin/cloudflared tunnel --url http://127.0.0.1:8000 --no-autoupdate
# 输出形如 https://xxxx-xxxx.trycloudflare.com
```

- 该地址给朋友即可，**无需点任何警告页**。
- 快隧 URL 每次重启都变；要固定域名需 `cloudflared tunnel login` + 命名隧道（免费，需一个自己的域名）。
- `serve.py` 已内置 gzip：主包 600KB → 184KB、`/api/decision/latest` 854KB → 51KB，外网首屏明显更快。

### 常驻（开机自启 + 防睡眠）

```bash
bash /Users/zxl/WorkBuddy/狸猫猎手/scripts/install_terminal_share.sh
```

- ⚠️ **必须在自己的终端窗口执行**。AI/agent 沙箱里注册 LaunchAgent 会报 `Bootstrap failed: 5: Input/output error`（跨 Aqua 会话限制），换到用户终端即成功。
- 注册三个 agent：`com.limao.terminal-serve`（serve.py :8000）、`com.limao.terminal-tunnel`（cloudflared）、`com.limao.terminal-awake`（`caffeinate -i -s` 防空闲/AC 睡眠）。
- 脚本末尾直接打印**当前可用地址**并验活；之后随时查：`bash scripts/share_url.sh`。
- 卸载：同脚本加 `--uninstall`（只停服务，plist 保留）。
- ⚠️ 合盖（clamshell）仍会睡眠，`caffeinate` 挡不住；醒来后 launchd 会拉起进程，但**快隧可能换域名**，以 `share_url.sh` 现查为准。

---

## 路径 A：本机内网穿透（ngrok，⚠️ 有警告页）

适合：家人/铁哥们/几十人以内。依赖你 Mac 一直开机。

```bash
# 1) 起服务（建议带 token）
cd /Users/zxl/WorkBuddy/狸猫猎手
SHARE_TOKEN=你的口令 SHARE_PORT=8123 python3 serve.py

# 2) 另开终端穿透（ngrok / cloudflared 任选）
ngrok http 8123
# 或：cloudflared tunnel --url http://localhost:8123
```

ngrok 给出 `https://xxxx.ngrok-free.app`，把链接发朋友即可（带 token 时地址后加 `?t=你的口令`）。
前端是 SPA，所有 `/api/*` 同源，穿透根地址即同时带静态页 + 实时数据，**无需额外配置**。

⚠️ **ngrok 免费档的访问警告页会打断接口（2026-10-26 实测）**：
- 首次访问会先看到 ngrok 的 `ERR_NGROK_6024` 警告页，需点一次「Visit Site」。
- 更关键：对**浏览器 UA**（含页面自己发的同源 XHR），ngrok 会把 `/api/*` 也回成 `200 text/html` 警告页 → 前端拿到 HTML 当 JSON 解析，页面报「渲染出错」。
- 已在 `src/shared/api/client.ts` 的所有取数请求带 `ngrok-skip-browser-warning: 1` 规避；`serve.py` / vite 忽略该头。
- 想要**完全无警告页**的链接，改用 cloudflared（`brew install cloudflared` → `cloudflared tunnel --url http://localhost:8000`），或上付费域名。

⚠️ 免费档：URL 每次重启会变、单隧道、限带宽、会话会过期 → 只适合小圈子，别指望"所有人"。

---

## 路径 B：云服务器（所有人，7×24）

适合：真·公网所有人。数据仍由 Mac 引擎产出，每分钟增量同步到服务器。

### 1. 服务器准备（任意 Linux，2核2G 起步）
```bash
# 装 Python3（标准库即可，零依赖）
python3 --version
# 把项目（含 serve.py / cache）rsync 到服务器
```

### 2. Mac → 服务器 增量同步（权威实现见 deploy/sync_cache.sh）
```bash
# Mac 上用 launchd 每分钟跑一次，只同步小体积 JSON，排除大文件/私密。
# ⚠️ 必须同步两个目录：cache/（基础业务）与 wealth_code/cache/。后者在仓库根、与 cache/
#    同级（非 cache/ 子目录），2026-10-26 放开的 ai/decision/account 三族路由及卡片存档全部
#    读此目录（serve.py:324-338,350-353）；漏同步→服务器这些页面全部 404（阻断级）。
# ⚠️ 不得排除 decision_log 与 ai_view_*：/api/decision/{archive,zt-pool,manifest} 与
#    /api/ai/view 已放开，依赖这两个来源。要临时关私密页用服务器侧 SHARE_PRIVATE=0 回退
#    （serve.py:59,617），而非在此排除。

# ① 基础业务缓存（排除 62MB zt_genes / 私密 workbuddy / 设置密钥 / 访问日志）
rsync -az --delete \
  --exclude='zt_genes/' --exclude='.workbuddy' --exclude='settings.json' \
  --exclude='access_log.jsonl' \
  cache/ 用户@服务器:/opt/limao/cache/

# ② 决策/AI/账户/卡片存档（已放开路由依赖，阻断级必同步）
rsync -az --delete \
  --exclude='.workbuddy' \
  wealth_code/cache/ 用户@服务器:/opt/limao/wealth_code/cache/

# ③ 前端构建产物
rsync -a ../狸猫终端/dist/ 用户@服务器:/opt/limao/dist/
```

### 3. 服务器跑 serve.py（systemd，见 deploy/limao-hunter.service）
```bash
SHARE_TOKEN=你的口令 python3 /opt/limao/serve.py
# 建议前置 nginx 做域名 + TLS（见 deploy/nginx.conf），对外只暴露 443
```

### 4. 可选：nginx 反向代理 + 域名 + HTTPS
见 `deploy/nginx.conf`。对外只开 443，serve.py 监听内网 127.0.0.1:8000。

---

## 环境变量速查
| 变量 | 默认 | 说明 |
|---|---|---|
| `SHARE_PORT` | 8000 | 监听端口 |
| `SHARE_HOST` | 0.0.0.0 | 监听地址（云上配 127.0.0.1 + nginx） |
| `SHARE_TOKEN` | 空 | 鉴权口令；公网必设 |
| `LIMAO_DATA_ROOT` | 项目根 | cache 根（路径含 `cache/` 前缀的路由基于此） |
| `LIMAO_DIST_DIR` | `../狸猫终端/dist` | 前端构建目录 |

## 验收（每次改完必做）
```bash
SHARE_TOKEN=x SHARE_PORT=8123 python3 serve.py &   # 后台
curl -s --noproxy '*' "http://localhost:8123/api/market/feedback?t=x" | head   # 应含 quotes
curl -s --noproxy '*' -o /dev/null -w "%{http_code}" "http://localhost:8123/api/ai/view?t=x"  # 应 404
curl -s --noproxy '*' -o /dev/null -w "%{http_code}" "http://localhost:8123/api/market/feedback"  # 无 token 应 403
```

---

## 回滚预案（上线前必读）

> 数据原则：本系统数据为追加落盘、无破坏性 schema 变更；回滚主要是「停写新格式 + 旧路由仍可读旧文件」，风险低。
> 四级回滚从「最轻（配置/逻辑）」到「最重（数据）」，按事故范围择级执行；任意一级都可独立操作，不必逐级来。

### ① 代码 / 配置回滚（git）
```bash
cd /Users/zxl/WorkBuddy/狸猫猎手
git revert <有问题的 commit>          # 或 git checkout <上一个好 tag> -- serve.py
git log --oneline -5                  # 确认回退点
```
- 适用：serve.py / 前端改动导致线上异常。纯文档（含本文件的 rsync 修正）改动不影响运行进程。

### ② 逻辑回滚（SHARE_PRIVATE=0，秒级、无需改码）
- 仅当「放开的 ai/decision/account 三族页面出问题」时使用；其余业务路由不受影响。
- 机制：serve.py:59 默认 `SHARE_PRIVATE=1`（放开）；serve.py:617 在 `SHARE_PRIVATE=0` 时
  对 `/api/ai/`、`/api/decision/`、`/api/account/`（PRIVATE_PREFIXES，serve.py:371）整族返回 404。
- 操作：启动命令加 `SHARE_PRIVATE=0`，重启 serve.py（见 ③）即生效。
```bash
SHARE_PRIVATE=0 SHARE_TOKEN=你的口令 python3 /opt/limao/serve.py
```

### ③ 服务回滚（重跑旧 serve.py）
```bash
# 云服务器（systemd，见 deploy/limao-hunter.service）
sudo systemctl restart limao-hunter          # 或先 stop 再 start
# 本机 Mac（launchd，见 scripts/install_terminal_share.sh）
launchctl unload ~/Library/LaunchAgents/com.limao.terminal-serve.plist
launchctl load   ~/Library/LaunchAgents/com.limao.terminal-serve.plist
```
- 适用：服务进程本身异常、需切回历史版本 serve.py。

### ④ 数据回滚（cache/backups 前缀白名单）
- 机制：serve.py:365 `/api/system/backups` 仅暴露 `nav_` / `ai_decisions_` / `learning_rollback_`
  前缀的备份（`cache/backups/` 混有含明文 webhook 的 settings 备份，故白名单制，不按 `^.+[.]json$` 通配）。
- 操作：从本机 `cache/backups/` 取对应日期备份，rsync 回服务器对应目录；或把旧 JSON 覆写回
  `cache/`（及 `wealth_code/cache/`）对应路径。
```bash
ls cache/backups/ | grep -E '^(nav_|ai_decisions_|learning_rollback_)'   # 看可用备份点
# 例：rsync -az cache/backups/nav_20261027.json 用户@服务器:/opt/limao/cache/nav_20261027.json
```
- ⚠️ 备份点须先于新功能上线建立；新增可回滚数据须加对应前缀备份 + 总开关。

### ⑤ 验证点（每级回滚后必做）
```bash
# 健康端点：issues 必须为空（prod /api/health 仅校验 exists/mtime/size，无 asof，详见审计报告）
curl -s --noproxy '*' "http://localhost:8123/api/health" | jq '.issues'
# 抽样：基础业务页有数据
curl -s --noproxy '*' "http://localhost:8123/api/market/feedback?t=x" | head
# 抽样：按 SHARE_PRIVATE 预期——
#   SHARE_PRIVATE=1：/api/ai/view 应 200 且含数据
#   SHARE_PRIVATE=0：/api/ai/view 应 404（三族整族关闭）
curl -s --noproxy '*' -o /dev/null -w "%{http_code}\n" "http://localhost:8123/api/ai/view?t=x"
# 同步完整性：确认服务器已含 wealth_code/cache/（放开路由依赖，见上方 rsync ②）
ssh 用户@服务器 'ls /opt/limao/wealth_code/cache/ | head'
```
- 建议把上述抽样固化进「验收 / 每日开盘前清单」，每次改完与回滚后都跑一遍。

---

## 进阶（可选，按需）
- **全量管线迁服**：把 24 个 WorkBuddy agent 自动化还原成裸 `python3 script.py` + cron。工作量大且不确定（自动化绑定本机 WorkBuddy 运行时），非必要不做。
- **多用户个性化**：当前是全市场统一看板，无每人持仓/自选。需要账号+DB 时再升 FastAPI/SQLite。
