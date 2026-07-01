"""
SZTU 体育馆 SSO 认证模块

通过统一身份认证 (auth.sztu.edu.cn) 获取 gym.sztu.edu.cn 的 JWT 令牌。

认证流程：
  1. 直接通过 gym OAuth2 authorize 进入统一身份认证
  2. POST 登录凭据（DES 加密密码）
  3. 若认证系统要求短信验证，发送短信验证码并提示用户输入
  4. POST AuthnEngine 完成 OAuth2 Authorization Code 流程
  5. 从 gym 307 响应的 Location 头中提取 JWT

OAuth2 流程依据 auth_phone.har 抓包。
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
GYM_SP_AUTH_CHAIN_CODE = "f3dd9170b8eb4c15bca650a2d7c7ea3f"
DES_KEY = "PassB01Il71"
ENTITY_ID = CLIENT_ID
PASSWORD_AUTH_CLASS = "urn_oasis_names_tc_SAML_2.0_ac_classes_BAMUsernamePassword"
SMS_AUTH_CLASS = "urn_oasis_names_tc_SAML_2.0_ac_classes_SMSUsernamePassword"
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
    """生成随机 state 参数，格式与新版 Gym OAuth 抓包一致: 1_<16位hex>"""
    return f"1_{secrets.token_hex(8)}"


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

    def __init__(self, sms_code_provider=None):
        self.session = requests.session()
        self.sms_code_provider = sms_code_provider or self._prompt_sms_code
        self.sp_auth_chain_code = GYM_SP_AUTH_CHAIN_CODE
        self._last_sms_code = ""

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

    def _navigation_get(self, url: str) -> requests.Response:
        headers = {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Content-Type": None,
            "Origin": None,
            "X-Requested-With": None,
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        }
        return self.session.get(
            url, timeout=AUTH_TIMEOUT, verify=False, allow_redirects=False,
            headers=headers,
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

    @staticmethod
    def _json(resp: requests.Response, step: str) -> dict:
        try:
            return resp.json()
        except ValueError:
            raise GymAuthError(f"{step}: 响应非 JSON: {resp.text[:200]}")

    @staticmethod
    def _prompt_sms_code(username: str) -> str:
        return input(f"请输入学号 {username} 收到的短信验证码: ").strip()

    # ==========================================================================
    #  Gym OAuth2 + SSO 登录
    # ==========================================================================

    def _open_gym_login(self, state: str) -> None:
        """进入 Gym OAuth 登录页，建立后续登录所需的认证上下文。"""
        self.session.headers["Host"] = "auth.sztu.edu.cn"
        self.session.headers["Referer"] = (
            "https://auth.sztu.edu.cn/idp/authcenter/"
            f"ActionAuthChain?entityId={ENTITY_ID}"
        )

        auth_url = (
            "https://auth.sztu.edu.cn/idp/oauth2/authorize"
            f"?response_type=code"
            f"&redirect_uri={REDIRECT_URI}"
            f"&client_id={CLIENT_ID}"
            f"&state={state}"
        )
        resp = self._get(auth_url)
        loc = self._expect_redirect(resp, "OAuth2 authorize")

        resp = self._get(_resolve_url("https://auth.sztu.edu.cn", loc))
        loc = self._expect_redirect(resp, "AuthnEngine")

        if "AuthorizationCode/SSO" in loc:
            return

        self._get(_resolve_url("https://auth.sztu.edu.cn", loc))

        # 登录页 JS 设置 x=x cookie，手动补上
        self.session.cookies.set("x", "x", domain="auth.sztu.edu.cn")

    def _submit_password(self, username: str, password: str) -> dict:
        login_data = self._password_login_data(username, password)
        resp = self._post(
            "https://auth.sztu.edu.cn/idp/authcenter/ActionAuthChain",
            login_data,
        )
        return self._json(resp, "密码登录")

    def _password_login_data(self, username: str, password: str) -> dict:
        return {
            "j_username": username,
            "j_password": encrypt_password(password),
            "j_checkcode": "验证码",
            "op": "login",
            "spAuthChainCode": self.sp_auth_chain_code,
        }

    def _sms_login_data(self, username: str, sms_code: str) -> dict:
        return {
            "j_username": username,
            "sms_checkcode": sms_code,
            "popViewException": "Pop2",
            "op": "login",
            "spAuthChainCode": self.sp_auth_chain_code,
            "j_checkcode": "验证码",
        }

    def _send_sms_code(self, username: str) -> None:
        resp = self._post(
            "https://auth.sztu.edu.cn/idp/sendSMSCheckCode.do",
            {"j_username": username},
        )
        data = self._json(resp, "发送短信验证码")
        if str(data.get("flag")).lower() != "true":
            message = data.get("message") or data.get("msg") or data
            raise GymAuthError(f"短信验证码发送失败: {message}")

    def _submit_sms_code(self, username: str) -> dict:
        self._send_sms_code(username)
        sms_code = self.sms_code_provider(username)
        if not sms_code:
            raise GymAuthError("短信验证码不能为空")
        self._last_sms_code = sms_code

        resp = self._post(
            "https://auth.sztu.edu.cn/idp/authcenter/ActionAuthChain",
            self._sms_login_data(username, sms_code),
        )
        return self._json(resp, "短信验证码登录")

    def _complete_authn_engine(self, auth_class: str, data: dict) -> str:
        resp = self._post(
            "https://auth.sztu.edu.cn/idp/AuthnEngine"
            f"?currentAuth={auth_class}",
            data=data,
        )
        return self._expect_redirect(resp, "AuthnEngine POST")

    def _fetch_callback_from_sso(self, sso_url: str) -> str:
        self.session.headers["Host"] = "auth.sztu.edu.cn"
        resp = self._navigation_get(
            _resolve_url("https://auth.sztu.edu.cn", sso_url)
        )
        return self._expect_redirect(resp, "AuthorizationCode/SSO")

    def _fetch_jwt_after_authn(self, sso_url: str, state: str) -> str:
        callback_url = self._fetch_callback_from_sso(sso_url)
        return self._extract_jwt_from_callback(callback_url, state)

    def _extract_jwt_from_callback(self, callback_url: str, state: str) -> str:
        self.session.headers["Host"] = "gym.sztu.edu.cn"
        self.session.headers["Referer"] = "https://auth.sztu.edu.cn/"
        resp = self._navigation_get(callback_url)
        redirect_url = self._expect_redirect(resp, "gym callback")

        token_full_url = _resolve_url("https://gym.sztu.edu.cn", redirect_url)
        parsed = urlparse(token_full_url)
        params = parse_qs(parsed.query)

        token = params.get("token", [None])[0]
        if not token:
            raise GymAuthError(
                f"回调 URL 中未找到 token 参数: {token_full_url[:120]}"
            )

        returned_state = params.get("state", [None])[0]
        if returned_state != state:
            raise GymAuthError(
                f"state 不匹配: 期望 {state}, 收到 {returned_state}"
            )

        return token

    def _login_and_fetch_gym_jwt(self, username: str, password: str) -> str:
        state = _generate_state()
        self._open_gym_login(state)

        password_resp = self._submit_password(username, password)

        if password_resp.get("loginFailed") == "false":
            sso_url = self._complete_authn_engine(
                PASSWORD_AUTH_CLASS,
                self._password_login_data(username, password),
            )
            return self._fetch_jwt_after_authn(sso_url, state)

        if str(password_resp.get("view")) == "4":
            chain_code = password_resp.get("currentAuChainCodeEx")
            if chain_code:
                self.sp_auth_chain_code = chain_code

            sms_resp = self._submit_sms_code(username)
            if sms_resp.get("loginFailed") != "false":
                err_tip = (
                    sms_resp.get("authnErrorTip")
                    or sms_resp.get("loginErrorKey")
                    or sms_resp.get("message")
                    or sms_resp.get("msg")
                    or ""
                )
                raise GymAuthError(
                    "短信验证码登录失败"
                    + (f" ({err_tip})" if err_tip else "")
                )

            sso_url = self._complete_authn_engine(
                SMS_AUTH_CLASS,
                self._sms_login_data(username, self._last_sms_code),
            )
            return self._fetch_jwt_after_authn(sso_url, state)

        err_tip = (
            password_resp.get("authnErrorTip")
            or password_resp.get("loginErrorKey")
            or password_resp.get("message")
            or password_resp.get("msg")
            or ""
        )
        raise GymAuthError(
            "登录失败，请检查学号密码是否正确"
            + (f" ({err_tip})" if err_tip else "")
        )

    # ==========================================================================
    #  公共入口
    # ==========================================================================

    def get_token(self, username: str, password: str) -> str:
        """
        完整认证流程：Gym OAuth2 登录 → 获取 JWT 令牌。

        Args:
            username: 学号
            password: 统一身份认证密码

        Returns:
            JWT 令牌字符串

        Raises:
            GymAuthError: 认证失败
        """
        return self._login_and_fetch_gym_jwt(username, password)


# ==============================================================================
#  便捷函数
# ==============================================================================

def get_gym_token(username: str, password: str, sms_code_provider=None) -> str:
    """获取体育馆 JWT 令牌的便捷函数"""
    auth = GymAuthenticator(sms_code_provider=sms_code_provider)
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
