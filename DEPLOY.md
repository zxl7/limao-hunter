# 狸猫猎手 · 对外共享部署参考

> 系统现状：**L0（本机 file://）+ 现已补 L1 `serve.py`**。后端数据管线（24 个 WorkBuddy 自动化）继续跑在本机 Mac，serve.py 只做「只读 Web 托管 + 白名单 + 鉴权」。

## 架构原则（铁律）
- **Mac = 数据引擎**：WorkBuddy 自动化写 `cache/*.json`；serve.py 只读这些落盘文件。
- **serve.py 白名单**：只暴露公开盘面数据（market/theme/flow/board/promotion/gene/emotion/zt/intraday/lhb/hithink/institution/eruption/newsflash）。
  - ⛔ **永久排除** `/api/ai/*`、`/api/decision/*`、`/api/account/*`（AI 决策 / 净值 / 决策黑匣子，属私密）。
  - 目录遍历拦截、目录不列举、其余一律 404；`Cache-Control: no-store`。
- **公网必须开 `SHARE_TOKEN`**；本机/局域网调试可留空。

---

## 路径 A：本机内网穿透（最快，小圈子，几分钟）

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

⚠️ 免费档：URL 每次重启会变、单隧道、限带宽、会话会过期 → 只适合小圈子，别指望"所有人"。

---

## 路径 B：云服务器（所有人，7×24）

适合：真·公网所有人。数据仍由 Mac 引擎产出，每分钟增量同步到服务器。

### 1. 服务器准备（任意 Linux，2核2G 起步）
```bash
# 装 Python3（标准库即可，零依赖）
python3 --version
# 把项目（含 serve.py / HTML / cache）rsync 到服务器
```

### 2. Mac → 服务器 增量同步（见 deploy/sync_cache.sh）
```bash
# Mac 上用 launchd 每分钟跑一次，只同步小体积 JSON，排除大文件/私密
rsync -az --delete \
  --exclude='zt_genes' --exclude='decision_log' --exclude='ai_view_*' \
  --exclude='.workbuddy' --exclude='settings.json' \
  cache/*.json cache/**/*.json 用户@服务器:/opt/limao/cache/
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

## 进阶（可选，按需）
- **全量管线迁服**：把 24 个 WorkBuddy agent 自动化还原成裸 `python3 script.py` + cron。工作量大且不确定（自动化绑定本机 WorkBuddy 运行时），非必要不做。
- **多用户个性化**：当前是全市场统一看板，无每人持仓/自选。需要账号+DB 时再升 FastAPI/SQLite。
