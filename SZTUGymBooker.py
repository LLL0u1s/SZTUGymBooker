"""
体育馆自动化订票脚本

流程: sessionlist → create → pay
根据 HAR 抓包格式实现，三个接口均为 POST JSON。
支持通过统一身份认证自动获取 JWT 令牌，无需手动抓包。
"""

import requests
import time
import sys
import os
import re
import logging
import urllib3
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# 日志配置
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)

# ============================================================
# 配置加载
# ============================================================

CONFIG_HELP = """请创建 config.toml，参考以下格式:

[account]
username = "202400000000"
password = "your_password"

[booking]
venue_id = 4
block_type = 1
site_date_type = 2
session_type = 0
target_start_time = "17:30:00"
poll_interval = 1
retry_interval = 1
max_retries = 60
"""

try:
    import tomllib
except ImportError:
    # Python < 3.11 fallback
    try:
        import tomli as tomllib
    except ImportError:
        print("❌ 需要 tomllib (Python 3.11+) 或 tomli 库来解析配置文件")
        print("   pip install tomli")
        sys.exit(1)

config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")

def _env(key, default):
    """读取环境变量，为空或不存在时回退到默认值。

    与 os.getenv(key, default) 的关键区别：
    os.getenv 在 key 存在但值为 "" 时返回 ""，不回退到 default。
    当 GitHub Actions 由 schedule 触发时，inputs.* 均为空字符串，
    必须回退到 config.toml 中的值。

    此外防御处理 GitHub Actions 传入完整 choice option 对象的情况：
    '{"label" => "综合馆", "value" => "41"}' → 自动提取 '41'
    """
    val = os.getenv(key)
    if not val:
        return default
    # 防御：GitHub Actions 某些情况下会将 choice option 的完整对象
    # {label, value} 作为字符串传入，而非仅取 value 字段。
    # 匹配 Ruby hash (=>) / JSON (:) / TOML-like (=) 中的 value 键
    if isinstance(val, str) and val.strip().startswith("{"):
        m = re.search(
            r"""["']?value["']?\s*[=:>]+\s*["'](\d+\.?\d*)["']""", val
        )
        if m:
            logging.warning(
                "⚠ %s 传入对象而非纯值，已自动提取 value=%s", key, m.group(1)
            )
            return m.group(1)
    return val


def to_int(v):
    """安全转换为整数，兼容 GitHub Web / Mobile App 的各种输入格式。

    GitHub Web 端 workflow_dispatch 的 number 输入 →  "180"
    GitHub Mobile App 同参数                  →  "180.0"
    choice option 被整体序列化                →  {"value": "41"}
    config.toml 中的整数                      →  4

    统一用 float(str(v)) 桥接，再转 int，覆盖以上所有情况。
    """
    if isinstance(v, dict):
        v = v.get("value", v)
    if v is None:
        raise ValueError("Cannot convert None to int")
    return int(float(str(v).strip()))


def to_float(v):
    """安全转换为浮点数，兼容多种输入格式（同上）。"""
    if isinstance(v, dict):
        v = v.get("value", v)
    if v is None:
        raise ValueError("Cannot convert None to float")
    return float(str(v).strip())


try:
    with open(config_path, "rb") as f:
        config = tomllib.load(f)
    # 环境变量优先：优先使用 SZTU_USERNAME / SZTU_PASSWORD 环境变量，
    # 未设置时回退到 config.toml 中的值（向后兼容本地运行方式）
    STUDENT_ID = _env("SZTU_USERNAME", config["account"]["username"])
    PASSWORD   = _env("SZTU_PASSWORD", config["account"]["password"])
    bk = config["booking"]
    # 所有预约参数均支持环境变量覆盖
    #   优先级: workflow_dispatch input → 环境变量 → config.toml → 默认值
    #   本地运行时直接编辑 config.toml；GitHub Actions 通过下拉表单选择
    VENUE_ID          = to_int(_env("SZTU_VENUE_ID",          bk["venue_id"]))
    BLOCK_TYPE        = to_int(_env("SZTU_BLOCK_TYPE",        bk["block_type"]))
    SITE_DATE_TYPE    = to_int(_env("SZTU_SITE_DATE_TYPE",     bk["site_date_type"]))
    SESSION_TYPE      = to_int(_env("SZTU_SESSION_TYPE",       bk["session_type"]))
    TARGET_START_TIME = _env("SZTU_TARGET_START_TIME",         bk["target_start_time"])
    POLL_INTERVAL     = to_float(_env("SZTU_POLL_INTERVAL",   bk["poll_interval"]))
    RETRY_INTERVAL    = to_float(_env("SZTU_RETRY_INTERVAL",  bk["retry_interval"]))
    MAX_RETRIES       = to_int(_env("SZTU_MAX_RETRIES",       bk["max_retries"]))
    BOOKING_MODE      = _env("SZTU_MODE",                      bk.get("mode", "serial"))
    CONCURRENCY       = to_int(_env("SZTU_CONCURRENCY",       bk.get("concurrency", 5)))

    # 调试日志：输出最终生效的配置（密码脱敏）
    logging.info("最终配置: venue=%s date_type=%s time=%s mode=%s concurrency=%s max_retries=%s",
                 VENUE_ID, SITE_DATE_TYPE, TARGET_START_TIME, BOOKING_MODE, CONCURRENCY, MAX_RETRIES)
except FileNotFoundError:
    print(f"❌ 找不到配置文件 {config_path}")
    print(CONFIG_HELP)
    sys.exit(1)
except KeyError as e:
    print(f"❌ 配置文件缺少字段: {e}")
    print(CONFIG_HELP)
    sys.exit(1)
except Exception as e:
    print(f"❌ 读取配置文件失败: {e}")
    sys.exit(1)

# ============================================================
# 认证: 从 gym_auth 获取 JWT 令牌
# ============================================================

from gym_auth import get_gym_token, GymAuthError

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://gym.sztu.edu.cn/mapi"

# 基础请求头（不含 token，启动时动态填入）
BASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Referer": "https://servicewechat.com/wx841f34453e694e39/19/page-frame.html",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
        "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
        "MiniProgramEnv/Mac MacWechat/WMPF MacWechat/3.8.7(0x13080712) "
        "UnifiedPCMacWechat(0xf2641a1f) XWEB/19934"
    ),
}


def fetch_token() -> str:
    """获取 JWT 令牌，失败时自动重试"""
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            logging.info("正在获取认证令牌...")
            token = get_gym_token(STUDENT_ID, PASSWORD)
            logging.info("✅ 认证令牌获取成功")
            return token
        except GymAuthError as e:
            logging.info(f"❌ 认证失败 (第{attempt}次): {e}")
            if attempt == max_attempts:
                raise
            wait = 2 ** attempt
            logging.info(f"   {wait} 秒后重试...")
            time.sleep(wait)
        except requests.RequestException as e:
            logging.info(f"❌ 网络错误 (第{attempt}次): {e}")
            if attempt == max_attempts:
                raise
            wait = 5
            logging.info(f"   {wait} 秒后重试...")
            time.sleep(wait)


def build_session(token: str) -> requests.Session:
    """创建带认证令牌的请求会话"""
    s = requests.Session()
    s.headers.update(BASE_HEADERS)
    s.headers["Web-X-Auth-Token"] = token
    s.verify = False
    return s


# ============================================================
# 全局可变 session（支持令牌刷新）
# ============================================================

_session: requests.Session | None = None
_current_token: str | None = None


def get_session() -> requests.Session:
    """获取当前全局 session（延迟初始化）"""
    global _session, _current_token
    if _session is None:
        _current_token = fetch_token()
        _session = build_session(_current_token)
    return _session


def refresh_token() -> None:
    """刷新令牌并更新全局 session"""
    global _session, _current_token
    logging.info("🔄 令牌已过期，正在刷新...")
    _current_token = fetch_token()
    _session = build_session(_current_token)


# ============================================================
# Step 1: 获取场次列表，筛选目标时段
# ============================================================

def get_target_session_id() -> int | None:
    """
    请求 sessionlist（仅调用一次），返回匹配 TARGET_START_TIME 的场次 id。
    不判断 stock —— 库存由 create 接口负责校验。
    """
    url = f"{BASE_URL}/venue/site/session/list"
    payload = {
        "venueId": VENUE_ID,
        "blockType": BLOCK_TYPE,
        "siteDateType": SITE_DATE_TYPE,
        "sessionType": SESSION_TYPE,
        "stock": None,
        "timeQuantumType": None,
    }

    resp = _api_post(url, payload)
    data = resp.json()

    if data.get("code") != "Success":
        raise RuntimeError(f"sessionlist 请求失败: {data}")

    for date_str, sessions in data.get("data", {}).items():
        for s in sessions:
            if s.get("startTime") == TARGET_START_TIME:
                logging.info(
                    f"✓ 锁定目标场次 | id={s['id']} date={date_str} "
                    f"{s['startTime']}-{s['endTime']} "
                    f"stock={s.get('stock', '-')} venue={s.get('venueSiteName', '-')}"
                )
                return s["id"]

    return None


# ============================================================
# Step 2: 创建订单
# ============================================================

def create_order(site_session_id: int) -> str | None:
    """
    创建订单，返回 orderNo。
    售罄时返回 None，其他错误抛出异常。
    """
    url = f"{BASE_URL}/user/order/create"
    payload = {
        "siteSessionId": site_session_id,
        "payType": 5,
    }

    resp = _api_post(url, payload)
    data = resp.json()

    code = data.get("code")

    if code == "TicketsSoldOut":
        logging.info(f"票已售罄 (status={data.get('status')})，继续轮询...")
        return None
    # elif code == "PleaseDoNotPlaceDuplicateOrders":
    #     logging.info("订单已存在，请勿重复下单")
    #     return None

    if code != "Success" or not data.get("success"):
        raise RuntimeError(f"create 请求失败: {data}")

    order_no = data["data"]["orderNo"]
    logging.info(f"✓ 订单创建成功 | orderNo={order_no}")
    return order_no


# ============================================================
# 并行创建订单（线程池 + 独立 session）
# ============================================================

def _create_session_with_token(token: str) -> requests.Session:
    """为并行 worker 创建独立的 Session（非共享，线程安全）"""
    s = requests.Session()
    s.headers.update(BASE_HEADERS)
    s.headers["Web-X-Auth-Token"] = token
    s.verify = False
    return s


def parallel_create_orders(site_session_id: int, concurrency: int = 5) -> str | None:
    """
    并行创建订单 — 启动 N 个线程同时 POST create。
    返回最先成功的 orderNo；全部售罄时返回 None。

    不修改原有的串行版 create_order()。
    """
    # 防御：若 session 尚未初始化则先获取 token
    token = _current_token
    if token is None:
        token = fetch_token()
    url = f"{BASE_URL}/user/order/create"
    payload = {"siteSessionId": site_session_id, "payType": 5}

    def worker(session: requests.Session) -> str | None:
        try:
            resp = session.post(url, json=payload, timeout=15)
            data = resp.json()
            code = data.get("code")

            if code == "TicketsSoldOut":
                logging.info(f"  [并行 worker] 票已售罄")
                return None

            if code == "PleaseDoNotPlaceDuplicateOrders":
                logging.info(f"  [并行 worker] 重复下单 (已有订单)")
                return None

            if code == "SystemError":
                logging.info(f"  [并行 worker] 系统繁忙: {data.get('msg', '')}")
                return None

            if code != "Success" or not data.get("success"):
                raise RuntimeError(f"create 请求失败: {data}")

            return data["data"]["orderNo"]
        except Exception as e:
            logging.info(f"✗ [并行 worker] {e}")
            return None

    sessions = [_create_session_with_token(token) for _ in range(concurrency)]

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(worker, s) for s in sessions]
        for future in as_completed(futures):
            order_no = future.result()
            if order_no:
                logging.info(f"✓ 订单创建成功 | orderNo={order_no}")
                return order_no

    # 全部返回 None → 售罄
    logging.info("票已售罄，继续轮询...")
    return None


# ============================================================
# Step 3: 支付订单
# ============================================================

def pay_order(order_no: str) -> bool:
    """
    支付订单，返回是否成功。
    """
    url = f"{BASE_URL}/pay/pay"
    payload = {
        "orderNo": order_no,
        "payType": 5,
    }

    resp = _api_post(url, payload)
    data = resp.json()

    if data.get("code") != "Success" or not data.get("success"):
        raise RuntimeError(f"pay 请求失败: {data}")

    logging.info(f"✓ 支付成功 | orderNo={order_no}")
    return True


# ============================================================
# API 调用封装（含令牌刷新）
# ============================================================

def _needs_token_refresh(response: requests.Response) -> bool:
    """判断响应是否指示令牌已过期"""
    if response.status_code == 401:
        return True
    try:
        data = response.json()
        code = data.get("code", "")
        if code in ("TokenExpired", "InvalidToken", "Unauthorized"):
            return True
        msg = data.get("message", data.get("msg", ""))
        if "token" in str(msg).lower() and ("过期" in str(msg) or "无效" in str(msg) or "expired" in str(msg).lower()):
            return True
    except Exception:
        pass
    return False


def _api_post(url: str, payload: dict) -> requests.Response:
    """
    POST 请求封装，支持令牌自动刷新重试一次。
    """
    session = get_session()
    try:
        resp = session.post(url, json=payload, timeout=15)
    except requests.RequestException:
        raise

    if _needs_token_refresh(resp):
        refresh_token()
        resp = get_session().post(url, json=payload, timeout=15)

    return resp


# ============================================================
# 串行主流程
# ============================================================

def serial_main() -> None:
    logging.info("══════════════════════════════════════")
    logging.info("体育馆自动订票脚本启动 [串行模式]")
    logging.info(f"目标时段: {TARGET_START_TIME} | 场馆ID: {VENUE_ID}")
    logging.info(f"日期类型: {SITE_DATE_TYPE} (1=今天 2=明天)")
    logging.info(f"轮询间隔: {POLL_INTERVAL}s | 重试间隔: {RETRY_INTERVAL}s")
    logging.info("══════════════════════════════════════")

    # Step 1: 获取目标场次 id（仅一次）
    try:
        site_session_id = get_target_session_id()
    except Exception as e:
        logging.info(f"✗ sessionlist 请求异常: {e}")
        sys.exit(1)

    if site_session_id is None:
        logging.info(f"✗ 未找到目标时段 {TARGET_START_TIME} 的场次，请检查 SITE_DATE_TYPE 配置")
        sys.exit(1)

    # Step 2: 轮询创建订单（由 create 接口判断是否售罄）
    attempt = 0
    while True:
        attempt += 1

        if MAX_RETRIES > 0 and attempt > MAX_RETRIES:
            logging.info(f"已达最大重试次数 {MAX_RETRIES}，退出")
            sys.exit(1)

        logging.info(f"--- 第 {attempt} 轮 ---")

        try:
            order_no = create_order(site_session_id)
        except Exception as e:
            logging.info(f"✗ create 异常: {e}")
            time.sleep(RETRY_INTERVAL)
            continue

        if order_no is None:
            # 售罄，继续轮询
            time.sleep(POLL_INTERVAL)
            continue

        # Step 3: 支付
        time.sleep(0.2)
        try:
            pay_order(order_no)
            logging.info("══════════════════════════════════════")
            logging.info("🎉 订票成功！")
            logging.info("══════════════════════════════════════")
            sys.exit(0)
        except Exception as e:
            logging.info(f"✗ pay 异常: {e}（订单 {order_no} 已创建但支付失败，请检查）")
            time.sleep(RETRY_INTERVAL)
            continue


# ============================================================
# 并行主流程
# ============================================================

def parallel_main() -> None:
    logging.info("══════════════════════════════════════")
    logging.info("体育馆自动订票脚本启动 [并行模式]")
    logging.info(f"目标时段: {TARGET_START_TIME} | 场馆ID: {VENUE_ID}")
    logging.info(f"日期类型: {SITE_DATE_TYPE} (1=今天 2=明天)")
    logging.info(f"并发数: {CONCURRENCY} | 轮询间隔: {POLL_INTERVAL}s")
    logging.info("══════════════════════════════════════")

    # Step 1: 获取目标场次 id（仅一次）
    try:
        site_session_id = get_target_session_id()
    except Exception as e:
        logging.info(f"✗ sessionlist 请求异常: {e}")
        sys.exit(1)

    if site_session_id is None:
        logging.info(f"✗ 未找到目标时段 {TARGET_START_TIME} 的场次，请检查 SITE_DATE_TYPE 配置")
        sys.exit(1)

    # Step 2: 并行轮询创建订单
    attempt = 0
    while True:
        attempt += 1

        if MAX_RETRIES > 0 and attempt > MAX_RETRIES:
            logging.info(f"已达最大重试次数 {MAX_RETRIES}，退出")
            sys.exit(1)

        logging.info(f"--- 第 {attempt} 轮 (并行 {CONCURRENCY} 路) ---")

        try:
            order_no = parallel_create_orders(site_session_id, CONCURRENCY)
        except Exception as e:
            logging.info(f"✗ parallel create 异常: {e}")
            time.sleep(RETRY_INTERVAL)
            continue

        if order_no is None:
            time.sleep(POLL_INTERVAL)
            continue

        # Step 3: 支付（串行，使用现有 pay_order）
        time.sleep(0.2)
        try:
            pay_order(order_no)
            logging.info("══════════════════════════════════════")
            logging.info("🎉 订票成功！")
            logging.info("══════════════════════════════════════")
            sys.exit(0)
        except Exception as e:
            logging.info(f"✗ pay 异常: {e}（订单 {order_no} 已创建但支付失败，请检查）")
            time.sleep(RETRY_INTERVAL)
            continue


# ============================================================
# 入口（按模式分发）
# ============================================================

def main() -> None:
    if BOOKING_MODE == "parallel":
        parallel_main()
    else:
        serial_main()


if __name__ == "__main__":
    main()
