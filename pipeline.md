# 体育馆自动化订票 — 接口流水线

## 认证方式

Token 由 `gym_auth.py` 通过 SSO 统一身份认证自动获取（Gym OAuth2 → 学号密码 → 手机短信验证码 → JWT），无需手动抓包。

认证模块在 `SZTUGymBooker.py` 启动时自动调用。首次本地运行需要在终端输入短信验证码；成功后 JWT 缓存在 `.sztu_gym_token.json`，有效期内直接复用，过期或即将过期后自动重新认证。

## 配置参数

所有参数集中在 `config.toml`，无需修改代码：

```toml
[account]
username = "202612345678"   # 学号
password = "123456"      # 统一身份认证密码

[booking]
venue_id = 4                # 场馆 ID，可选值见下方场馆列表
block_type = 1
site_date_type = 2          # 日期类型：1=今天, 2=明天
session_type = 0            # 场次类型：0=全部, 1=上午, 2=下午, 3=晚上
target_start_time = "17:30:00"

# 轮询策略
poll_interval = 1           # 售罄轮询间隔 (秒)
retry_interval = 1          # 失败重试间隔 (秒)
max_retries = 60            # 最大重试次数 (0=无限)
mode = "serial"             # 抢票模式："serial"(串行) | "parallel"(并行)
# concurrency = 5           # 并行模式并发请求数 (默认 5)
```

### 场馆列表（来自 /mapi/index/venue/list）

| id | 场馆   | id | 场馆   |
| -- | ------ | -- | ------ |
| 1  | 游泳馆 | 2  | 乒乓球 |
| 3  | 羽毛球 | 4  | 健身房 |
| 5  | 足球场 | 41 | 综合馆 |
| 42 | 网球   | 46 | 体能中心 |
| 47 | 匹克球 |    |        |

---

## 1. 请求 sessionlist

获取可订场次列表，筛选目标时段。

```http
POST /mapi/venue/site/session/list
```

### 请求体字段

| 字段              | 类型 | 说明                                    | 来源        |
| ----------------- | ---- | --------------------------------------- | ----------- |
| `venueId`         | int  | 场馆 ID                                 | config.toml |
| `blockType`       | int  | 区块类型                                | config.toml |
| `siteDateType`    | int  | 日期类型：1=今天, 2=明天                | config.toml |
| `sessionType`     | int  | 场次类型：0=全部, 1=上午, 2=下午, 3=晚上 | config.toml |
| `stock`           | null | 库存筛选（固定 null）                   | —           |
| `timeQuantumType` | null | 时段类型（固定 null）                   | —           |

> 请求头需携带 `Web-X-Auth-Token`（由 gym_auth 自动注入）。代码中用 `target_start_time` 匹配响应中的 `startTime` 字段，获取对应场次的 `id` 作为下一步的 `siteSessionId`。

---

## 2. 请求 create

创建订单。

```http
POST /mapi/user/order/create
```

### create 请求体

| 字段            | 类型 | 说明                        | 来源     |
| --------------- | ---- | --------------------------- | -------- |
| `siteSessionId` | int  | 场次 ID（来自 sessionlist） | 动态填入 |
| `payType`       | int  | 支付方式，固定值            | 5        |

> 响应 `code` 为 `"TicketsSoldOut"` 时表示售罄，脚本自动轮询重试。成功时返回 `data.orderNo`。

---

## 3. 请求 pay

支付订单。

```http
POST /mapi/pay/pay
```

### pay 请求体

| 字段      | 类型   | 说明                 | 来源     |
| --------- | ------ | -------------------- | -------- |
| `orderNo` | string | 订单号（来自 create） | 动态填入 |
| `payType` | int    | 支付方式，固定值     | 5        |

> 响应 `code` 为 `"Success"` 且 `success` 为 `true` 表示支付成功。

---

## SSO 认证流程（供参考）

`gym_auth.py` 内部执行两阶段认证：

```text
阶段一 — Gym OAuth2 Authorization Code 登录入口（依据 auth_phone.har）
  GET /idp/oauth2/authorize?client_id=23256178&response_type=code&state=...
  → AuthnEngine → ActionAuthChain?entityId=23256178
  → POST 登录凭据（DES-ECB 加密密码，key=PassB01Il71）
  → 若返回 view=4，POST /idp/sendSMSCheckCode.do 并提示输入短信验证码
  → POST 短信验证码 → AuthnEngine → AuthorizationCode/SSO
  → gym.sztu.edu.cn/api/loginCheck?code=...（307 重定向，Location 含 JWT）
  → 解析 token 参数，验证 state 匹配
```

关键常量：

| 参数              | 值                                       |
| ----------------- | ---------------------------------------- |
| `client_id`       | `23256178`                               |
| `redirect_uri`    | `https://gym.sztu.edu.cn/api/loginCheck` |
| `spAuthChainCode` | `f3dd9170b8eb4c15bca650a2d7c7ea3f`       |
| `entityId`        | `23256178`                               |
| DES key           | `PassB01Il71`                            |
