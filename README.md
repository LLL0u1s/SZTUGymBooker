# SZTU GymBooker — 深圳技术大学体育馆自动订票

**向“创建订单失败！”说再见！**

定时轮询订票接口，目标场次一旦有票自动下单并支付。

认证由 `gym_auth.py` 通过 SSO 统一身份认证自动完成，**无需手动抓包获取 Token**。

## 快速开始

```bash
pip install -r requirements.txt
cp config.example.toml config.toml   # 编辑 config.toml 填入学号密码
python3 SZTUGymBooker.py
```

## 配置

所有配置集中在 `config.toml`，无需修改代码。

> **账号密码优先级**：环境变量 `SZTU_USERNAME` / `SZTU_PASSWORD` > `config.toml`。
> 本地运行时可直接编辑 `config.toml`；GitHub Actions 自动注入环境变量，无需将密码写入仓库。

```toml
[account]
username = "202612345678"   # 学号
password = "123456"      # 统一身份认证密码

[booking]
venue_id = 4                # 场馆 ID
block_type = 1
site_date_type = 2          # 1=今天, 2=明天
session_type = 0            # 0=全部, 1=上午, 2=下午, 3=晚上
target_start_time = "17:30:00"

poll_interval = 1           # 售罄轮询间隔 (秒)
retry_interval = 1          # 失败重试间隔 (秒)
max_retries = 60            # 最大重试次数 (0=无限)
mode = "serial"             # 抢票模式："serial"(串行) | "parallel"(并行)
# concurrency = 5           # 并行模式并发请求数 (默认 5)
```

### 场馆列表

| id | 场馆   | id | 场馆   |
| -- | ------ | -- | ------ |
| 1  | 游泳馆 | 2  | 乒乓球 |
| 3  | 羽毛球 | 4  | 健身房 |
| 5  | 足球场 | 41 | 综合馆 |
| 42 | 网球   | 46 | 体能中心 |
| 47 | 匹克球 |    |        |

### 常见时段

`08:30:00` `10:15:00` `14:00:00` `17:30:00` `19:00:00` `20:20:00`

## 工作流程

支持两种抢票模式，在 `config.toml` 中通过 `mode` 选择：

- **`serial`（串行，默认）**：每轮发送 1 个 create 请求，售罄时轮询重试
- **`parallel`（并行）**：每轮同时发送 N 个 create 请求（`concurrency` 控制），最先成功的订单立即支付

```text
认证阶段（gym_auth.py，启动时自动执行）
  SAML 登录 auth.sztu.edu.cn → Gym OAuth2 → 获取 JWT
     │
     ▼
sessionlist ──→ 获取目标场次 id（仅一次）
     │
     ▼
  create ──→ 轮询下单（串行 1 路 / 并行 N 路，token 过期自动刷新）
     │
     ▼
   pay ──→ 支付订单
```

## 运行效果

```text
[08:00:01] ══════════════════════════════════════
[08:00:01] 体育馆自动订票脚本启动
[08:00:01] 目标时段: 17:30:00 | 场馆ID: 4
[08:00:01] 日期类型: 2 (1=今天 2=明天)
[08:00:01] 轮询间隔: 1s | 重试间隔: 1s
[08:00:01] ══════════════════════════════════════
[08:00:01] 正在获取认证令牌...
[08:00:02] ✅ 认证令牌获取成功
[08:00:02] ✓ 锁定目标场次 | id=807753 date=2026-06-17 17:30:00-18:50:00 stock=0 venue=学生场
[08:00:02] --- 第 1 轮 ---
[08:00:02] 票已售罄 (status=-3012)，继续轮询...
[08:00:03] --- 第 2 轮 ---
[08:00:03] ✓ 订单创建成功 | orderNo=SZTUODR0010617180002
[08:00:03] ✓ 支付成功 | orderNo=SZTUODR0010617180002
[08:00:03] 🎉 订票成功！
```

## 文件说明

| 文件 | 用途 |
| ---- | ---- |
| `SZTUGymBooker.py` | 主脚本，读取配置后自动订票 |
| `gym_auth.py` | SSO 认证模块，学号密码 → JWT 令牌 |
| `config.toml` | 配置文件（学号、密码、订票参数） |
| `requirements.txt` | Python 依赖 |
| `pipeline.md` | API 接口文档，包含完整请求/响应格式 |
| `*.har` | 抓包样本，供开发参考 |

## 注意事项

- SSO 认证代码参考 [SZTU-Course-Selector](https://github.com/SummerOneTwo/SZTU-Course-Selector)
- Token 过期后脚本自动刷新，无需手动干预
- 订票成功后脚本自动停止
- Python 3.11+（如需 Python 3.10，`pip install tomli`）
- **场馆适配**：目前仅适配**不需选择馆内具体场地**的场馆（如健身房、游泳馆、体能中心）。
  羽毛球、乒乓球等需要选择具体场号/桌号的场馆暂未适配，预约时可能因缺少场地参数而失败。

## ☁️ GitHub Actions 手动预约（推荐）

无需自备服务器或保持电脑开机，打开 GitHub 即可一键抢票。

> ⚠️ **执行平台建议**：优先使用 **iOS App（GitHub Mobile）** 或 **Web 端** 触发 Workflow。
> **Android App 端**执行存在参数序列化兼容问题（如数值变为 `180.0`），可能导致失败。

### 原理

```text
放票前打开 GitHub → 手动触发 Workflow → 选择场馆和时段
        │
        ▼
  启动 Ubuntu 虚拟机  →  安装 Python 依赖  →  运行订票脚本
        │
        ▼
  ✅ 成功 / ❌ 失败  →  日志可在 Actions 页面查看
```

> **安全性**：GitHub Secrets 不会传递给 Fork 仓库。每个同学使用自己的学号密码，互不干扰。
> Workflow 已配置 `if: github.actor == github.repository_owner`，仅仓库所有者可手动触发。

---

### 🚀 快速上手（3 步）

#### 第 1 步：Fork 本仓库

点击右上角 **Fork** 按钮，将仓库复制到你的 GitHub 账号下。

#### 第 2 步：配置 Secrets

在你 Fork 的仓库中：

1. 进入 **Settings** → **Secrets and variables** → **Actions**
2. 点击 **New repository secret**，依次添加：

| Name | Secret | 说明 |
| ---- | ------ | ---- |
| `SZTU_USERNAME` | 你的学号 | 如 `202412345678` |
| `SZTU_PASSWORD` | 统一身份认证密码 | 登录 `auth.sztu.edu.cn` 的密码 |

> ⚠️ **注意**：密码不要有多余空格或换行，直接粘贴即可。

添加完成后，Secrets 页面应显示两项：

```text
SZTU_USERNAME  ***
SZTU_PASSWORD  ***
```

#### 第 3 步：启用 Actions

1. 进入 **Settings** → **Actions** → **General**
2. 选择 **Allow all actions and reusable workflows**
3. 点击 **Save**

---

### 🎯 执行抢票

1. 在放票时间前打开 GitHub，进入 **Actions** → **SZTU Gym Booker**
2. 点击 **Run workflow** ▼
3. 在表单中选择场馆和时段，点击 **Run workflow**
4. 等待运行完成，点击进入查看日志确认结果

表单选项：

| 参数 | 说明 | 可选值 |
| ---- | ---- | ------ |
| 场馆 | 要预约的场馆 | 游泳馆 / 乒乓球 / 羽毛球 / 健身房 / 足球场 / 综合馆 / 网球 / 体能中心 / 匹克球 |
| 日期 | 预约哪天的场次 | 今天 / 明天 |
| 目标时段 | 想抢的时段 | 08:30 / 10:15 / 14:00 / 16:00 / 17:30 / 19:00 / 20:20 |
| 抢票模式 | 串行/并行 | 串行 (保守) / 并行 (激进) |
| 并发数 | 并行模式同时发送的请求数 | 默认 5 |
| 最大重试 | 抢不到时的重试轮数 | 默认 180 |

---

### 🔍 查看日志

1. 进入 **Actions** → 点击某次运行记录
2. 展开 **Run SZTU Gym Booker** 步骤
3. 绿色 ✓ = 订票成功，红色 ✗ = 失败（展开查看原因）

---

### ❓ 常见问题

| 问题 | 原因 | 解决办法 |
| ---- | ---- | -------- |
| Workflow 不执行 | Actions 未启用 | Settings → Actions → Allow all actions |
| 认证失败 | Secrets 中学号或密码错误 | 检查 Secrets 值，确认无空格和换行 |
| 未找到目标时段 | 日期/时段配置不匹配 | 检查表单中「日期」和「目标时段」选择是否正确 |
| 票已售罄 | 该时段已无余票 | 正常现象，脚本会持续重试直到上限 |
| Fork 后 Actions 不触发 | Fork 默认禁用 Actions | Settings → Actions → Allow all actions |
| Android App 执行失败 | 参数序列化兼容问题 | 改用 iOS App 或 Web 端触发 Workflow |
| 羽毛球/乒乓球预约失败 | 需选择具体场号/桌号 | 目前未适配，仅支持不需选场地的场馆 |
