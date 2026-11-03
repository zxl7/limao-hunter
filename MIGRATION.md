# 狸猫系列迁移手册（macOS → Ubuntu）

> 目标：把「狸猫猎手（数据/推送）」与「狸猫终端（站点）」搬到服务器，**不破坏现有功能**，且以后能重复执行。
> 工具目录：`scripts/migrate/`。所有脚本默认 `DRY_RUN=1`，改什么都先打印再执行。

---

## 一、可行性评估（基于 2026-10-30 实测）

### 1.1 服务器底子

| 项 | 实测 | 判断 |
|---|---|---|
| CPU / 内存 | 2 核 / 1.9G（可用 950M） | 站点 + 数据脚本够用；**不要再装 docker/node** |
| 磁盘 | 50G，已用 26G（55%） | 项目整包 ~350M，无压力 |
| 时区 | `Asia/Shanghai` | ✓ 交易日历不会错位（这是迁移最容易被忽略的坑） |
| Python | 3.12.3 + pip + venv | ✓ 项目 99% 是标准库 |
| 第三方依赖 | 只有 `duckdb`（1 个文件引用） | ✓ 几乎零安装成本 |
| 出网 | 东财 200 / 腾讯 200 | ✓ 国内服务器比家庭宽带更稳 |
| nginx | 已运行，占着 **80 端口**（oscarwl.icu 站点） | ⚠️ 不能抢 80，用 8080 或子域 |
| systemd | 用户态 running，`Linger=yes`；且 sudo 免密可用 | ✓ 定时任务可迁 |

### 1.2 三类东西，迁法完全不同

| 类别 | 具体 | 能否迁 | 说明 |
|---|---|---|---|
| **A. 站点（只读）** | `serve.py` + `dist/` | ✅ 已迁通 | 纯只读，零副作用。**已在服务器跑起来** |
| **B. 数据面** | 抓取/聚合/指标计算/落盘 | ✅ 可迁，需先修 3 处平台耦合 | 见 1.3 |
| **C. AI 研判面** | 25 个 WorkBuddy 自动化（盘前预测、盘中猎手、尾盘潜伏研判、复盘成文） | ❌ 不建议迁 | 这是**平台调度 + 大模型推理**，服务器上没有 agent 运行时。强行迁等于自己写一套 LLM 流水线，成本 >> 收益 |
| **D. 推送面** | 飞书 webhook 卡片 | ✅ 可迁 | 但要防「两边同时推」⇒ 必须单向接管 |

**结论：现实的最优解是「服务器管数据与展示，Mac 管 AI 判断」。**

### 1.3 已修的平台耦合（不改就会在 Linux 上静默出错）

| 位置 | 原写法 | 问题 | 修法 |
|---|---|---|---|
| `scripts/pulse_fetcher.sh` | `cd /Users/zxl/WorkBuddy/狸猫猎手` | Linux 上路径不存在，脚本 `cd` 失败后仍在错误 cwd 跑 | 改 `${LIMAO_ROOT:-原默认值}` |
| 同上 | `PY=/Users/zxl/.workbuddy/binaries/...` | 服务器没有这个解释器 | 改 `${LIMAO_PY:-原默认值}` |
| 同上 | `stat -f%m` / `stat -f%z` | 这是 **BSD 语法**，GNU stat 会报错 → 锁与日志轮转失效 | 加 `|| stat -c ...` 兼容分支 |

> 默认值原样保留 ⇒ **Mac 侧行为零变化**。这是「不影响现有功能」的实现方式：用可选覆盖，而不是改写。

### 1.4 剩下的耦合（有意不迁）

- `launchctl` 相关脚本（`fix_wealth_verify_launchd.sh`、`wealth_verify_launchd.sh`、`share_url.sh`）——这些是**本机守护进程管理**，留在 Mac。
- 17 个脚本里硬编码 `/Users/zxl/...`：多为一键脚本/测试辅助，不在定时链路上，迁到服务器时按需处理（`grep -rl /Users/zxl scripts/*.py` 可查）。

---

## 二、两种模式：先跑 site，再谈 pipeline

```
site（推荐先上）                          pipeline（后续可选）
Mac 产数据 ──rsync──▶ 服务器只读副本      服务器自己抓取 → 算指标 → 推卡片
Mac 继续推卡片 / 跑 AI                    Mac 只留 AI 研判
风险：0（不动任何现有链路）               风险：高（重复抓取/重复推送/数据分叉）
```

**site 模式的代价**：服务器数据有延迟（取决于 `sync_data.sh` 频率），适合「给人看历史与结构」，不适合「盘中看实时」。要实时就得 pipeline。

---

## 三、命令速查

```bash
cd /Users/zxl/WorkBuddy/狸猫猎手/scripts/migrate
export REMOTE_PASS='***'          # 配了密钥后这行删掉

# ① 打包（site 默认；FULL=1 连 K 线缓存）
bash pack.sh

# ② 上传 + 解到 releases/<stamp> + 切 current 软链（保留最近 3 版）
bash push.sh

# ③ 服务器引导（目录、env、systemd 单元、nginx 片段；只装不启，可重复跑）
bash bootstrap_remote.sh

# ④ 切换（默认 dry-run）
DRY_RUN=1 bash cutover.sh
DRY_RUN=0 bash cutover.sh                     # site：只起站点
CONFIRM_PIPELINE=1 DRY_RUN=0 bash cutover.sh  # pipeline：连抓取一起接管，并停 Mac fetcher

# ⑤ 数据增量泵（site 模式盘中用；建议 launchd/cron 每 60s）
bash sync_data.sh

# ⑥ 校验（7 项事实对照：健康/快照时间/关键路由/产物 hash）
bash verify.sh

# ⑦ 回滚（先恢复 Mac 生产，再停服务器）
DRY_RUN=0 bash rollback.sh
```

服务器上的东西：

```
/home/ubuntu/limao/
├── current -> releases/limao-site-<stamp>/app   # 原子切换，回滚只换软链
├── releases/                                    # 保留最近 3 版
├── incoming/                                    # 上传包暂存
├── logs/                                        # serve.log / serve.err / pulse.*
└── remote_env.sh                                # LIMAO_ROOT / LIMAO_PY / TZ
```

---

## 四、已完成 vs 待办

**已完成（实测通过）**

- 打包 52M / 6298 条目，含 MANIFEST + sha256
- 上传 → 校验 → 解包 → `current` 软链
- systemd 单元落盘（`limao-serve` 已启动，`enable` 到 `default.target`）
- 远端 `/api/health` 健康、4 条关键路由全 200、`index.html` hash 与本地一致
- 数据增量同步跑通（远端快照时间已追平本地：`2026-10-30T13:54:09`）

**待办（需要你拍板）**

1. **对外访问**：现在服务器 `serve` 只绑 `127.0.0.1:8000`，外部还进不来。三条路：
   - nginx 反代 8080（片段已落盘，需 `ln -s` + 安全组放行）
   - 用 `oscarwl.icu` 加子域 `limao.oscarwl.icu`（要加 DNS + 证书，最正规）
   - 继续用 cloudflared 快隧但指向服务器（域名仍会变）
2. **定时同步**：`sync_data.sh` 目前手动，要不要装成 launchd（每 60s）或服务器侧 cron 反向拉？
3. **密钥登录**：密码已出现在聊天记录里，建议 `ssh-copy-id` 后关 `PasswordAuthentication`。
4. **pipeline 是否推进**：要迁数据面，必须先做 3~5 天「影子运行」（服务器跑但不推送），用 `verify.sh` 对比快照一致性，再决定接管。

---

## 五、风险清单（迁移前必读）

| 风险 | 触发条件 | 防线 |
|---|---|---|
| **重复推送** | Mac 与服务器同时跑 `realtime_push_pipeline.py` | pipeline 模式 `cutover.sh` 会停 Mac fetcher；`--push` 只在一边开 |
| **数据分叉** | 两边都写 `cache/pulse_snapshots` | site 模式服务器只读；pipeline 模式 Mac 停抓取 |
| **半版本** | 只传了代码没传 dist，或反之 | `pack.sh` 一次性打齐 + `verify.sh` 比对 hash |
| **时区错位** | 服务器 UTC | `bootstrap` 会检查并告警；单元里也写死 `TZ=Asia/Shanghai` |
| **内存打爆** | 500M 级 JSON 并发读 | 服务器可用 950M，`serve.py` 单进程只读；不要并行跑回测 |
| **80 端口冲突** | 直接 listen 80 | 模板默认 8080，注释里写明原因 |

---

## 六、为什么这么封装

- **一包一版本**：`releases/<stamp>` + `current` 软链 ⇒ 回滚是 O(1)，不是「重新部署一遍」。
- **脚本只读 `migrate.conf`**：换服务器改一行 `REMOTE_HOST`，不用翻脚本。
- **默认 dry-run**：`cutover` / `rollback` 不显式 `DRY_RUN=0` 就只打印。迁移这种动作，误触的代价比多点一次大。
- **连接层重试，业务层不重试**：SSH 返回 255（连接失败）才重试；远端命令返回 1 是结论（比如健康检查没过），重试只会掩盖问题。

---

## 七、数据联动：本地产数据，服务器只是一面镜子

### 7.1 核心原则

**数据只有一个生产者（Mac）。** 服务器不抓取、不计算、不写入，只被动接收副本并对外展示。
因此「两边数据不一致」这个担心在架构上不成立 —— 只存在**延迟**，不存在**分叉**。

### 7.2 三层冷热（实测体积）

| 层 | 内容 | 文件数 | 体积 | 频率 | 单次耗时 |
|---|---|---|---|---|---|
| **T0R 实时** | `cadence=intraday` 的接口（主快照/决策/资金/接力效应/时间线…） | 16 | 1.8MB | **每 30s**（仅盘中） | ~1.0s |
| **T0 热** | 全部单文件路由（含盘后产物） | 37 | 3.0MB | 每 5min | ~1.5s |
| **T1 温** | 各目录路由的最新切片（题材/情绪/龙虎榜/资金历史） | 672 | 14.8MB | 每 5min | ~2s |
| **T2 冷** | 股性全库 / 财富码缓存 | 1206 | 44MB | 每交易日 15:45 | ~10s |

清单不是手写的，由 `manifest.py` **从 `serve.py` 的 `PUBLIC_ROUTES` 反推**——
终端读什么，就同步什么。新增路由后自动纳入，不需要改同步脚本。

### 7.3 延迟是怎么构成的

```
Mac 抓取（pulse_fetcher 30s 一拍）→ 落盘 → 同步 tick（30s）→ 服务器文件更新 → 页面刷新
最坏 ≈ 60s（抓取间隔）+ 30s（同步间隔）+ 1s（传输）≈ 91s
实测（14:24 那次）：本地 14:24:08 / 远端 14:24:08 → 延迟 0s（恰好同拍）
```

tick 间隔现为 30s（`com.limao.migrate-sync.plist` 的 `StartInterval`）。⛔ 别再往下压：实测抓取端 `pulse_fetcher` 是 **60s 才落一次盘**（fetch.log 相邻两拍 14:40:01 → 14:42:00），同步快于生产者只会空转。
代价是 SSH 建连频率上升 —— 已用 `ControlMaster` 连接复用（`ControlPersist=300`）摊掉握手。

### 7.4 装/查/卸

```bash
bash scripts/migrate/install_migrate_sync.sh          # 装（launchd，失败自动退 cron）
bash scripts/migrate/install_migrate_sync.sh --status  # 各层最近同步 + 延迟
bash scripts/migrate/status.sh                        # 同上的详细版
bash scripts/migrate/install_migrate_sync.sh --uninstall
```

⚠️ 安装必须在你自己的终端跑：agent 沙箱里 `launchctl` 报 EIO、
`crontab` 报 operation not permitted。

### 7.5 一条链路三个断言

`status.sh` 只回答三件事，用来判断「线上看到的是不是当前盘」：

1. 定时机制在不在（launchd 或 cron 任一在册）
2. 各层最近同步距今多少秒（超过阈值标 ⚠️）
3. 本地与远端 `latest.json` 的 `ts` 差多少秒（≤120s 判 ✓）

### 7.6 再次迁移到新服务器（这是封装的用处）

改一处配置即可，其余脚本不变：

```bash
# 1) 只改 migrate.conf 的三行
REMOTE_HOST / REMOTE_USER / REMOTE_ROOT

# 2) 重跑同一串命令
bash scripts/migrate/setup_ssh_key.sh      # 新机器要重新建免密
bash scripts/migrate/pack.sh && bash scripts/migrate/push.sh
bash scripts/migrate/bootstrap_remote.sh
DRY_RUN=0 bash scripts/migrate/cutover.sh
bash scripts/migrate/install_migrate_sync.sh   # 同步任务指向新机器，无需改脚本

# 3) 校验
bash scripts/migrate/verify.sh
```

旧机器要下线时，先 `rollback.sh` 再删 nginx 片段与 systemd 单元，避免两台同时对外。

---

## 八、把数据面搬到服务器（去掉对 Mac 的依赖）

### 8.1 现状问题

第七节那套是「Mac 生产 → 服务器镜像」。Mac 一关机，站点就停在最后一帧。
要真正独立，必须让**服务器自己产数据**。

### 8.2 可行性：已实测通过

在服务器上直接跑完整数据链（`realtime_data_pipeline.py`，10 个子步骤）：

```
[intraday_pulse] [gen_candidates] [feedback_matrix] [intraday_exit_peaks]
[effect_paths] [ma5_check] [memory_detect] [intraday_timeline] [realtime_analytics] [theme_series]
→ ready=True  全部 rc=0  耗时 3 秒
```

三个前提都已核实：

| 前提 | 结论 |
|---|---|
| 依赖 | 全链**纯标准库**，无 pandas/numpy/requests；`duckdb` 只出现在 `market_daily_store.py`，不在盘中链 |
| 路径 | 链内 10 个脚本**零** `/Users/zxl` 硬编码，ROOT 由脚本位置推导，换机自洽 |
| 出网 | 服务器直连东财/腾讯均 200（国内机房反而更稳） |

### 8.3 为什么不能现在切

盘中脚本有**时段守卫**（`in_trading()` 是唯一真源，`--force` 也绕不过）。
盘后强行跑只会输出「非交易时段，跳过」——所以端到端必须在真实交易时段验证。

已部署**影子管线**（现在就在跑）：

```
limao-shadow.timer  每 30s
  → remote_pipeline.sh  SHADOW=1  LIMAO_ROOT=/home/ubuntu/limao/shadow
  → 只写 shadow/cache，**不推送**、不碰 current/、不影响现役
```

周一开盘它会自动积累数据，用来和 Mac 产出的数据做逐项对照。

### 8.4 切换三步（都可逆）

```bash
# ① 影子对比（至少 3 个时点：10:00 / 11:00 / 14:00）
bash scripts/migrate/compare_shadow.sh
#   期望：latest.ts / total_zt / sentiment / top5 / 涨停池 / ready / 候选数 全一致

# ② 切正式（服务器接管生产与推送）
ssh limao-server 'sudo sed -i "s/SHADOW=1/SHADOW=0/" /etc/systemd/system/limao-pipeline.service'
ssh limao-server 'sudo systemctl daemon-reload && sudo systemctl enable --now limao-pipeline.timer'
ssh limao-server 'sudo systemctl disable --now limao-shadow.timer'
# ⛔ 同时停掉 Mac 的抓取，否则同一张卡推两遍
crontab -l | grep -v pulse_fetcher | crontab -

# ③ Mac 改成「只读消费者」
bash scripts/migrate/mirror_back.sh     # 服务器 -> 本机 cache，AI 脚本零改造
bash scripts/migrate/install_migrate_sync.sh --uninstall   # 停掉正向推送
```

### 8.5 切换后的边界

| 能力 | 归属 | Mac 关机后 |
|---|---|---|
| 采集 / 计算 / 落盘 | **服务器** | ✓ 照常 |
| 对外站点 | **服务器** | ✓ 照常 |
| 规则卡片推送（脉冲/窗口卡） | **服务器** | ✓ 照常 |
| AI 研判与成文（盘前预测/盘中猎手/尾盘/复盘） | Mac（agent + 模型） | ✗ 停 |
| 飞书 AI 卡片 | Mac | ✗ 停 |

也就是说：**站点与数据永远在线，AI 判断依赖 Mac。** 这正是你要的分界。

### 8.6 尚未验证的

- **盘后链**（`capture_daily.py` / `guard_eod_chain.py`）还没在服务器上跑过。它决定日终文件是否齐全，
  是 AI 复盘的数据来源。切换前必须补验。
- **推送**：`realtime_push_pipeline.py` 在服务器上未实跑（影子模式跳过）。切换时才是首次真实推送，
  建议先在测试群验证一张卡。
- **盘后 AI 任务**若直接读本机 `cache/`，靠 `mirror_back.sh` 保供；若某个任务在**开盘前**就要读
  当日文件，需要把 `mirror_back` 排在它前面（时序问题）。
