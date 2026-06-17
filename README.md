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

## GitHub Actions 自动预约

通过 GitHub Actions 实现每天定时自动执行，无需本地保持运行。

> **Fork 后开箱即用**：Workflow 已配置仅在仓库所有者自己的仓库中运行。
> Fork 后不会被上游的 Secrets 干扰，也不会用上游账号执行。

### 使用步骤（同学）

1. **Fork** 本仓库
2. 进入你的 Fork 仓库：**Settings → Secrets and variables → Actions**，新增两个 Secrets：
   - `SZTU_USERNAME`：你的学号
   - `SZTU_PASSWORD`：统一身份认证密码
3. **Settings → Actions → General → Allow all actions and reusable workflows**，确保 Actions 已启用
4. 等待每天 17:58 自动执行，或手动触发测试：
   进入 **Actions** → **SZTU Gym Booker** → **Run workflow**，选择场馆和时段后运行。

> 脚本会自动优先使用 Secrets 中的环境变量，本地运行时仍从 `config.toml` 读取。

### 自动运行

每天 **北京时间 17:58** 自动执行（对应 UTC 09:58），在放票前 2 分钟开始抢票。

### 查看日志

1. 进入 Actions 页面，点击具体运行记录
2. 展开 **Run SZTU Gym Booker** 步骤查看详细日志
3. 成功时显示 ✓ 绿色标记，失败时显示 ✗ 红色标记

### 常见问题

| 问题 | 排查方向 |
| ---- | -------- |
| Workflow 未触发 | 检查 Actions 是否被启用（Settings → Actions → Allow all actions） |
| 认证失败 | 确认 Secrets 中学号密码正确，无多余空格 |
| 未找到目标时段 | 检查 `config.toml` 中 `site_date_type` 和 `target_start_time` 配置 |
| 票已售罄 | 正常现象，脚本会持续轮询直到达到 `max_retries` 上限 |
| Python 版本错误 | 确保 workflow 使用 `python-version: '3.11'` |
