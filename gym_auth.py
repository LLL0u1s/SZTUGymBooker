"""
SZTU 体育馆 SSO 认证模块

通过统一身份认证 (auth.sztu.edu.cn) 获取 gym.sztu.edu.cn 的 JWT 令牌。

认证流程（两阶段）：
  阶段一 — SAML 登录 auth.sztu.edu.cn：
    1. 通过 jwxt.sztu.edu.cn 触发 SAML 重定向链
    2. 导航 auth.sztu.edu.cn 登录页面
    3. POST 登录凭据（DES 加密密码）
    4. 完成 SAML 响应链 → 会话已认证
  阶段二 — Gym OAuth2 Authorization Code 流程：
    5. GET /idp/oauth2/authorize (client_id=23256178)
    6. 跟随重定向: AuthnEngine → AuthorizationCode/SSO → gym 回调
    7. 从 307 响应的 Location 头中提取 JWT

登录逻辑复用 sztu_course_selector.py Auth.login() 的核心代码。
OAuth2 流程依据 auth2gym.har 抓包。
"""

import base64
import secrets
import requests
import urllib3
from urllib.parse import urlparse, parse_qs, urljoin
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

try:
    from Crypto.Cipher import DES
except ImportError:
    raise ImportError(
        "缺少依赖库 pycryptodome，请执行: pip install pycryptodome"
    )

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 常量 ---
CLIENT_ID = "23256178"
REDIRECT_URI = "https://gym.sztu.edu.cn/api/loginCheck"
SP_AUTH_CHAIN_CODE = "cc2fdbc3599b48a69d5c82a665256b6b"
DES_KEY = "PassB01Il71"
ENTITY_ID = "jiaowu"
# 认证请求超时（auth 服务器可能响应慢）
AUTH_TIMEOUT = (10, 30)


class GymAuthError(Exception):
    """认证失败异常"""
    pass


# ==============================================================================
#  DES 加密（与 sztu_course_selector.py 完全一致）
# ==============================================================================

def _pad(data: str, block_size: int = 8) -> bytes:
    length = block_size - (len(data) % block_size)
    return data.encode(encoding='utf-8') + (chr(length) * length).encode(encoding='utf-8')


def encrypt_password(password: str, key: str = DES_KEY) -> str:
    """DES-ECB 加密密码，返回 Base64 编码字符串"""
    key_bytes = key.encode('utf-8')[:8]
    cipher = DES.new(key=key_bytes, mode=DES.MODE_ECB)
    encrypted = cipher.encrypt(_pad(password, block_size=8))
    return base64.b64encode(encrypted).decode('utf-8')


def _generate_state() -> str:
    """生成随机 state 参数，格式: 2_<16位hex>"""
    return f"2_{secrets.token_hex(8)}"


def _resolve_url(base: str, location: str) -> str:
    """将 Location 头解析为绝对 URL"""
    if location.startswith("http://") or location.startswith("https://"):
        return location
    return urljoin(base, location)


# ==============================================================================
#  GymAuthenticator
# ==============================================================================

class GymAuthenticator:
    """体育馆 SSO 认证器"""

    def __init__(self):
        self.session = requests.session()

        retry_strategy = Retry(total=3, connect=3, backoff_factor=0.1)
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=10,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.verify = False

        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 "
                "Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) "
                "NetType/WIFI MiniProgramEnv/Mac MacWechat/WMPF "
                "MacWechat/3.8.7(0x13080712) UnifiedPCMacWechat(0xf2641a1f) "
                "XWEB/19934"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            # 以下头从 sztu_course_selector.py Auth 类继承，
            # auth.sztu.edu.cn 会校验这些头才接受登录请求
            "Referer": (
                "https://auth.sztu.edu.cn/idp/authcenter/"
                f"ActionAuthChain?entityId={ENTITY_ID}"
            ),
            "Origin": "https://auth.sztu.edu.cn",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-ch-ua": (
                '" Not A;Brand";v="99", "Chromium";v="98",'
                ' "Google Chrome";v="98"'
            ),
            "Content-Type": (
                "application/x-www-form-urlencoded; charset=UTF-8"
            ),
        })

    # -- HTTP helpers ----------------------------------------------------------

    def _get(self, url: str) -> requests.Response:
        return self.session.get(
            url, timeout=AUTH_TIMEOUT, verify=False, allow_redirects=False
        )

    def _post(self, url: str, data: dict) -> requests.Response:
        return self.session.post(
            url, timeout=AUTH_TIMEOUT, verify=False, data=data,
            allow_redirects=False,
        )

    @staticmethod
    def _expect_redirect(resp: requests.Response, step: str) -> str:
        location = resp.headers.get("Location")
        if not location:
            raise GymAuthError(
                f"{step}: 未收到重定向 (status={resp.status_code})"
            )
        return location

    # ==========================================================================
    #  阶段一: SAML 登录 auth.sztu.edu.cn（复用 sztu_course_selector.py）
    # ==========================================================================

    def _login_auth_engine(self, username: str, password: str) -> None:
        """
        通过 jwxt 服务提供商触发 SAML 登录流程，
        在 auth.sztu.edu.cn 上建立已认证会话。
        与 sztu_course_selector.py Auth.login() 核心逻辑一致。
        """
        # Step 1: 访问 jwxt → 跟随 CAS 重定向到 auth.sztu.edu.cn
        self.session.headers["Host"] = "jwxt.sztu.edu.cn"
        resp = self._get("https://jwxt.sztu.edu.cn/")
        resp = self._get(self._expect_redirect(resp, "jwxt redirect 1"))
        resp = self._get(self._expect_redirect(resp, "jwxt redirect 2"))

        # Step 2: 进入 auth.sztu.edu.cn
        self.session.headers["Host"] = "auth.sztu.edu.cn"
        self._get(self._expect_redirect(resp, "auth redirect"))

        # Step 3: 导航 AuthnEngine → 登录页面
        self._get("https://auth.sztu.edu.cn/idp/AuthnEngine")
        self._get(
            "https://auth.sztu.edu.cn/idp/authcenter/ActionAuthChain"
            f"?entityId={ENTITY_ID}"
        )

        # 登录页 JS 设置 x=x cookie，手动补上
        self.session.cookies.set("x", "x", domain="auth.sztu.edu.cn")

        # Step 4: 提交登录凭据
        login_data = {
            "j_username": username,
            "j_password": encrypt_password(password),
            "j_checkcode": "验证码",
            "op": "login",
            "spAuthChainCode": SP_AUTH_CHAIN_CODE,
        }
        resp = self._post(
            "https://auth.sztu.edu.cn/idp/authcenter/ActionAuthChain",
            login_data,
        )

        try:
            resp_json = resp.json()
        except ValueError:
            raise GymAuthError(
                f"登录响应非 JSON: {resp.text[:200]}"
            )

        if resp_json.get("loginFailed") != "false":
            err_tip = resp_json.get("authnErrorTip", "")
            raise GymAuthError(
                f"登录失败，请检查学号密码是否正确"
                + (f" ({err_tip})" if err_tip else "")
            )

        # Step 5: SAML 认证回传
        resp = self._post(
            "https://auth.sztu.edu.cn/idp/AuthnEngine"
            "?currentAuth=urn_oasis_names_tc_SAML_2.0_ac_classes_BAMUsernamePassword",
            data=login_data,
        )
        sso_url = self._expect_redirect(resp, "SAML AuthnEngine POST")

        # Step 6: 跟随 SAML 响应链回到 jwxt
        resp = self._get(sso_url)
        logon_url = self._expect_redirect(resp, "SSO redirect")

        self.session.headers["Host"] = "jwxt.sztu.edu.cn"
        resp = self._get(logon_url)
        login_to_tk_url = self._expect_redirect(resp, "logon redirect")

        self._get(login_to_tk_url)
        self._get("https://jwxt.sztu.edu.cn/jsxsd/framework/xsMain.htmlx")

        # 此时 session 已持有 auth.sztu.edu.cn 的 _idp_session / SESSION 等 cookies

    # ==========================================================================
    #  阶段二: Gym OAuth2 Authorization Code 流程（依据 auth2gym.har）
    # ==========================================================================

    def _fetch_gym_jwt(self) -> str:
        """
        使用已认证的 session 完成 gym OAuth2 流程，返回 JWT 令牌。

        依据 auth2gym.har 中已认证用户的请求序列：
          GET /idp/oauth2/authorize?...client_id=23256178
          → 302 → AuthnEngine → 302 → AuthorizationCode/SSO
          → 302 → gym 回调 (含 code)
          → 307 → /oauthLogin?token=<JWT>
        """
        state = _generate_state()

        # Step A: 发起 gym OAuth2 授权请求
        self.session.headers["Host"] = "auth.sztu.edu.cn"
        auth_url = (
            "https://auth.sztu.edu.cn/idp/oauth2/authorize"
            f"?response_type=code"
            f"&redirect_uri={REDIRECT_URI}"
            f"&client_id={CLIENT_ID}"
            f"&state={state}"
        )
        resp = self._get(auth_url)
        loc = self._expect_redirect(resp, "OAuth2 authorize")

        # 检查会话是否有效（若被重定向到登录页则说明会话过期）
        if "ActionAuthChain" in loc or "login" in loc.lower():
            raise GymAuthError("认证会话已过期，需要重新登录")

        # Step B: AuthnEngine
        resp = self._get(_resolve_url("https://auth.sztu.edu.cn", loc))
        loc = self._expect_redirect(resp, "AuthnEngine")

        # Step C: AuthorizationCode/SSO → 获取授权码
        resp = self._get(_resolve_url("https://auth.sztu.edu.cn", loc))
        callback_url = self._expect_redirect(resp, "AuthorizationCode/SSO")

        # Step D: gym 回调 → 获取 JWT
        self.session.headers["Host"] = "gym.sztu.edu.cn"
        resp = self._get(callback_url)
        redirect_url = self._expect_redirect(resp, "gym callback")

        # 解析 JWT
        token_full_url = _resolve_url("https://gym.sztu.edu.cn", redirect_url)
        parsed = urlparse(token_full_url)
        params = parse_qs(parsed.query)

        token = params.get("token", [None])[0]
        if not token:
            raise GymAuthError(
                f"回调 URL 中未找到 token 参数: {token_full_url[:120]}"
            )

        # 验证 state 参数
        returned_state = params.get("state", [None])[0]
        if returned_state != state:
            raise GymAuthError(
                f"state 不匹配: 期望 {state}, 收到 {returned_state}"
            )

        return token

    # ==========================================================================
    #  公共入口
    # ==========================================================================

    def get_token(self, username: str, password: str) -> str:
        """
        完整认证流程：SAML 登录 → OAuth2 → 获取 JWT 令牌。

        Args:
            username: 学号
            password: 统一身份认证密码

        Returns:
            JWT 令牌字符串

        Raises:
            GymAuthError: 认证失败
        """
        self._login_auth_engine(username, password)
        return self._fetch_gym_jwt()


# ==============================================================================
#  便捷函数
# ==============================================================================

def get_gym_token(username: str, password: str) -> str:
    """获取体育馆 JWT 令牌的便捷函数"""
    auth = GymAuthenticator()
    return auth.get_token(username, password)


# ==============================================================================
#  独立测试入口
# ==============================================================================

if __name__ == "__main__":
    import sys
    import os

    print("=" * 50)
    print("SZTU 体育馆 SSO 认证测试")
    print("=" * 50)

    username = os.environ.get("SZTU_USERNAME")
    password = os.environ.get("SZTU_PASSWORD")

    if not username or not password:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib

        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config.toml"
        )
        try:
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
            username = config["account"]["username"]
            password = config["account"]["password"]
        except FileNotFoundError:
            print(
                "未设置环境变量且未找到 config.toml。\n"
                "用法1: SZTU_USERNAME=学号 SZTU_PASSWORD=密码 python3 gym_auth.py\n"
                "用法2: 创建 config.toml，含 [account] 段 username/password"
            )
            sys.exit(1)
        except Exception as e:
            print(f"读取 config.toml 失败: {e}")
            sys.exit(1)

    try:
        print(f"正在认证 (学号: {username})...")
        token = get_gym_token(username, password)
        print("✅ 认证成功！")
        print(f"\nJWT Token:\n{token}")
    except GymAuthError as e:
        print(f"❌ 认证失败: {e}")
        sys.exit(1)
    except requests.RequestException as e:
        print(f"❌ 网络错误: {e}")
        sys.exit(1)
