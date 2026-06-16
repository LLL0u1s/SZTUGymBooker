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

所有配置集中在 `config.toml`，无需修改代码：

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
