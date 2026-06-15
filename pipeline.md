# 体育馆自动化订票 — 接口流水线

## 认证方式

Token 由 `gym_auth.py` 通过 SSO 统一身份认证自动获取（学号 + 密码 → auth.sztu.edu.cn SAML 登录 → Gym OAuth2 → JWT），无需手动抓包。

认证模块在 `SZTUGymBooker.py` 启动时自动调用，token 过期后自动刷新。

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
阶段一 — SAML 登录 auth.sztu.edu.cn（复用 sztu_course_selector.py 模式）
  jwxt.sztu.edu.cn → 302 重定向链 → auth.sztu.edu.cn
  → AuthnEngine → ActionAuthChain?entityId=jiaowu
  → POST 登录凭据（DES-ECB 加密密码，key=PassB01Il71）
  → SAML AuthnEngine → SSO 响应链 → 会话已认证

阶段二 — Gym OAuth2 Authorization Code 流程（依据 auth2gym.har）
  GET /idp/oauth2/authorize?client_id=23256178&response_type=code&state=...
  → AuthnEngine → AuthorizationCode/SSO
  → gym.sztu.edu.cn/api/loginCheck?code=...（307 重定向，Location 含 JWT）
  → 解析 token 参数，验证 state 匹配
```

关键常量：

| 参数              | 值                                       |
| ----------------- | ---------------------------------------- |
| `client_id`       | `23256178`                               |
| `redirect_uri`    | `https://gym.sztu.edu.cn/api/loginCheck` |
| `spAuthChainCode` | `cc2fdbc3599b48a69d5c82a665256b6b`       |
| `entityId`        | `jiaowu`                                 |
| DES key           | `PassB01Il71`                            |
