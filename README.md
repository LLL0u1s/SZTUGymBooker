# SZTU GymBooker — 深圳技术大学体育馆自动订票

**向"订单创建失败！"说再见！**

定时轮询订票接口，目标场次一旦有票自动下单并支付。认证通过 SSO 统一身份认证自动完成，**无需手动抓包 Token**。

## 🚀 快速开始（GitHub Actions，推荐）

无需自备服务器，Fork → 配置 → 一键抢票。

> ⚠️ **平台建议**：优先使用 **iOS App** 或 **Web 端**触发。Android App 存在参数序列化兼容问题，可能导致失败。

### 第 1 步：Fork 本仓库

点击右上角 **Fork**，将仓库复制到你的 GitHub 账号下。

### 第 2 步：配置 Secrets

在你 Fork 的仓库中：**Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
| ---- | ----- |
| `SZTU_USERNAME` | 你的学号 |
| `SZTU_PASSWORD` | 统一身份认证密码 |
| `TG_BOT_TOKEN` | （可选）Telegram Bot Token，订票成功后推送通知 |
| `TG_CHAT_ID` | （可选）接收通知的 Chat ID |
| `FEISHU_WEBHOOK_URL` | （可选）飞书机器人 Webhook URL，订票成功后推送通知 |

### 第 3 步：启用 Actions

进入 **Settings → Actions → General**，选择 **Allow all actions and reusable workflows**，点击 **Save**。

### 第 4 步：执行抢票

放票前打开 **Actions → SZTU Gym Booker → Run workflow**，选择场馆和时段后运行。

| 参数 | 可选值 |
| ---- | ------ |
| 场馆 | 游泳馆 / 乒乓球 / 羽毛球 / 健身房 / 足球场 / 综合馆 / 网球 / 体能中心 / 匹克球 |
| 日期 | 今天 / 明天 |
| 目标时段 | 08:30 / 10:15 / 14:00 / 16:00 / 17:30 / 19:00 / 20:20 |
| 抢票模式 | 串行 (保守) / 并行 (激进) |
| 并发数 | 仅并行模式生效，默认 5 |
| 最大重试 | 默认 180 轮 |

绿色 ✓ = 成功，红色 ✗ = 失败（展开日志查看原因）。

### 🔔 通知（可选）

订票成功时可通过以下渠道推送通知，可同时配置多个：

| 渠道 | 所需 Secret | 获取方式 |
| ---- | ----------- | -------- |
| Telegram | `TG_BOT_TOKEN` + `TG_CHAT_ID` | 找 **@BotFather** 创建 Bot → 找 **@userinfobot** 获取 Chat ID |
| 飞书 | `FEISHU_WEBHOOK_URL` | 飞书群 → 设置 → 群机器人 → 添加 → Webhook 地址 |

> ℹ️ Telegram API 在国内无法直连，本地运行时需开启 Mihomo 代理（默认 `127.0.0.1:7893`）。通知请求会自动走代理，不影响抢票接口（直连国内服务器）。飞书走直连。

未配置则不会发送通知，不影响抢票功能。

### 常见问题

| 问题 | 解决办法 |
| ---- | -------- |
| Workflow 不执行 | Settings → Actions → Allow all actions |
| Fork 后不触发 | Fork 默认禁用 Actions，需手动启用 |
| 认证失败 | 检查 Secrets 值，确认无空格和换行 |
| Android App 失败 | 改用 iOS App 或 Web 端触发 |
| 羽毛球/乒乓球失败 | 目前仅适配不需选具体场地的场馆（健身房、游泳馆等） |

---

## 💻 本地运行

如需精准的定时执行（不受 GitHub Actions 延迟影响），可在本地部署 cron 或任务计划。

```bash
pip install -r requirements.txt
cp config.example.toml config.toml   # 编辑填入学号密码
python3 SZTUGymBooker.py
```

Python 3.11+（3.10 需 `pip install tomli`）。

### 配置文件 `config.toml`

```toml
[account]
username = "202612345678"
password = "123456"

[booking]
venue_id = 4                # 场馆 ID（见下表）
block_type = 1
site_date_type = 2          # 1=今天 2=明天
session_type = 0            # 0=全部 1=上午 2=下午 3=晚上
target_start_time = "17:30:00"

poll_interval = 1           # 售罄轮询间隔 (秒)
retry_interval = 1          # 失败重试间隔 (秒)
max_retries = 100           # 最大重试次数
mode = "serial"             # "serial"(串行) | "parallel"(并行)
concurrency = 5             # 仅并行模式生效

[telegram]
bot_token = ""              # 可选：Telegram Bot Token
chat_id = ""                # 可选：接收通知的 Chat ID

[feishu]
webhook_url = ""            # 可选：飞书机器人 Webhook URL
```

> 环境变量 `SZTU_USERNAME` / `SZTU_PASSWORD` / `SZTU_VENUE_ID` 等优先于 `config.toml`。

### 场馆 ID

| id | 场馆 | id | 场馆 |
| -- | -- | -- | -- |
| 1 | 游泳馆 | 2 | 乒乓球 |
| 3 | 羽毛球 | 4 | 健身房 |
| 5 | 足球场 | 41 | 综合馆 |
| 42 | 网球 | 46 | 体能中心 |
| 47 | 匹克球 | | |

### 工作流程

```text
sessionlist → 获取目标场次 → create (轮询) → pay → 完成
```

支持串行（每轮 1 请求）和并行（每轮 N 请求，`concurrency` 控制）两种模式，Token 过期自动刷新。

---

## 注意事项

- SSO 认证参考 [SZTU-Course-Selector](https://github.com/SummerOneTwo/SZTU-Course-Selector)
- 目前仅适配**不需选择馆内具体场地**的场馆（健身房、游泳馆、体能中心等）
- 订票成功后脚本自动停止
