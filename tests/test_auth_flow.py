import base64
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import gym_auth


class FakeResponse:
    def __init__(self, status_code=200, headers=None, json_data=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self._json_data = json_data
        self.text = text

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        if self._json_data is None:
            raise ValueError("no json")
        return self._json_data


def jwt_with_payload(payload):
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    encoded = encoded.rstrip("=")
    return f"header.{encoded}.sig"


class GymAuthFlowTests(unittest.TestCase):
    def _auth_with_mocks(self, post_responses, get_responses, sms_code="123456"):
        auth = gym_auth.GymAuthenticator(sms_code_provider=lambda username: sms_code)
        post_calls = []
        get_calls = []

        def fake_post(url, data):
            post_calls.append((url, data))
            return post_responses.pop(0)

        def fake_get(url):
            get_calls.append(url)
            return get_responses.pop(0)

        auth._post = fake_post
        auth._get = fake_get
        auth._navigation_get = fake_get
        return auth, post_calls, get_calls

    def test_generated_state_matches_new_gym_oauth_prefix(self):
        self.assertTrue(gym_auth._generate_state().startswith("1_"))

    def test_sms_flow_sends_code_and_returns_jwt(self):
        token = jwt_with_payload({"exp": 4102444800})
        post_responses = [
            FakeResponse(json_data={
                "view": "4",
                "currentAuChainCodeEx": "chain-from-server",
                "loginFailed": "true",
            }),
            FakeResponse(json_data={"flag": True, "validTime": 2}),
            FakeResponse(json_data={"loginFailed": "false"}),
            FakeResponse(
                status_code=302,
                headers={
                    "Location": "https://auth.sztu.edu.cn/idp/profile/OAUTH2/AuthorizationCode/SSO"
                },
            ),
        ]
        get_responses = [
            FakeResponse(status_code=302, headers={"Location": "/idp/AuthnEngine"}),
            FakeResponse(
                status_code=302,
                headers={"Location": "/idp/authcenter/ActionAuthChain?entityId=23256178"},
            ),
            FakeResponse(status_code=200),
            FakeResponse(
                status_code=302,
                headers={"Location": "https://gym.sztu.edu.cn/api/loginCheck?code=abc&state=state-test"},
            ),
            FakeResponse(
                status_code=307,
                headers={"Location": f"/oauthLogin?token={token}&state=state-test"},
            ),
        ]
        auth, post_calls, _ = self._auth_with_mocks(post_responses, get_responses)

        with patch("gym_auth._generate_state", return_value="state-test"):
            result = auth.get_token("202400000000", "pw")

        self.assertEqual(result, token)
        self.assertEqual(post_calls[1][0], "https://auth.sztu.edu.cn/idp/sendSMSCheckCode.do")
        self.assertEqual(post_calls[2][1]["sms_checkcode"], "123456")
        self.assertEqual(post_calls[2][1]["popViewException"], "Pop2")
        self.assertEqual(post_calls[3][1]["sms_checkcode"], "123456")
        self.assertEqual(post_calls[3][1]["spAuthChainCode"], "chain-from-server")

    def test_sms_send_failure_raises_clear_error(self):
        post_responses = [
            FakeResponse(json_data={"view": "4", "loginFailed": "true"}),
            FakeResponse(json_data={"flag": False, "message": "send failed"}),
        ]
        get_responses = [
            FakeResponse(status_code=302, headers={"Location": "/idp/AuthnEngine"}),
            FakeResponse(
                status_code=302,
                headers={"Location": "/idp/authcenter/ActionAuthChain?entityId=23256178"},
            ),
            FakeResponse(status_code=200),
        ]
        auth, _, _ = self._auth_with_mocks(post_responses, get_responses)

        with patch("gym_auth._generate_state", return_value="state-test"):
            with self.assertRaisesRegex(gym_auth.GymAuthError, "短信验证码发送失败"):
                auth.get_token("202400000000", "pw")

    def test_sms_login_failure_raises_clear_error(self):
        post_responses = [
            FakeResponse(json_data={"view": "4", "loginFailed": "true"}),
            FakeResponse(json_data={"flag": True}),
            FakeResponse(json_data={"loginFailed": "true", "message": "bad code"}),
        ]
        get_responses = [
            FakeResponse(status_code=302, headers={"Location": "/idp/AuthnEngine"}),
            FakeResponse(
                status_code=302,
                headers={"Location": "/idp/authcenter/ActionAuthChain?entityId=23256178"},
            ),
            FakeResponse(status_code=200),
        ]
        auth, _, _ = self._auth_with_mocks(post_responses, get_responses)

        with patch("gym_auth._generate_state", return_value="state-test"):
            with self.assertRaisesRegex(gym_auth.GymAuthError, "短信验证码登录失败"):
                auth.get_token("202400000000", "pw")

    def test_password_only_compat_flow_still_works(self):
        token = jwt_with_payload({"exp": 4102444800})
        post_responses = [
            FakeResponse(json_data={"loginFailed": "false"}),
            FakeResponse(
                status_code=302,
                headers={
                    "Location": "https://auth.sztu.edu.cn/idp/profile/OAUTH2/AuthorizationCode/SSO"
                },
            ),
        ]
        get_responses = [
            FakeResponse(status_code=302, headers={"Location": "/idp/AuthnEngine"}),
            FakeResponse(
                status_code=302,
                headers={"Location": "/idp/authcenter/ActionAuthChain?entityId=23256178"},
            ),
            FakeResponse(status_code=200),
            FakeResponse(
                status_code=302,
                headers={"Location": "https://gym.sztu.edu.cn/api/loginCheck?code=abc&state=state-test"},
            ),
            FakeResponse(
                status_code=307,
                headers={"Location": f"/oauthLogin?token={token}&state=state-test"},
            ),
        ]
        auth, post_calls, _ = self._auth_with_mocks(post_responses, get_responses)

        with patch("gym_auth._generate_state", return_value="state-test"):
            result = auth.get_token("202400000000", "pw")

        self.assertEqual(result, token)
        self.assertIn(gym_auth.PASSWORD_AUTH_CLASS, post_calls[1][0])
        self.assertIn("j_password", post_calls[1][1])

    def test_callback_requires_token_and_matching_state(self):
        auth = gym_auth.GymAuthenticator()
        fake_state_mismatch = lambda url: FakeResponse(
            status_code=307,
            headers={"Location": "/oauthLogin?token=abc&state=wrong"},
        )
        auth._get = fake_state_mismatch
        auth._navigation_get = fake_state_mismatch

        with self.assertRaisesRegex(gym_auth.GymAuthError, "state 不匹配"):
            auth._extract_jwt_from_callback("https://gym.sztu.edu.cn/api/loginCheck", "right")

        fake_missing_token = lambda url: FakeResponse(
            status_code=307,
            headers={"Location": "/oauthLogin?state=right"},
        )
        auth._get = fake_missing_token
        auth._navigation_get = fake_missing_token
        with self.assertRaisesRegex(gym_auth.GymAuthError, "未找到 token"):
            auth._extract_jwt_from_callback("https://gym.sztu.edu.cn/api/loginCheck", "right")


class TokenCacheTests(unittest.TestCase):
    def setUp(self):
        import SZTUGymBooker as booker

        self.booker = booker
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_cache_path = booker.TOKEN_CACHE_PATH
        booker.TOKEN_CACHE_PATH = os.path.join(self.tmpdir.name, "token.json")

    def tearDown(self):
        self.booker.TOKEN_CACHE_PATH = self.old_cache_path
        self.tmpdir.cleanup()

    def test_cache_round_trip_uses_jwt_exp(self):
        token = jwt_with_payload({"exp": 2000})
        self.booker.save_cached_token("user-a", token, now=1000)

        self.assertEqual(self.booker.load_cached_token("user-a", now=1200), token)
        with open(self.booker.TOKEN_CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
        self.assertEqual(cache["expires_at"], 2000)

    def test_cache_rejects_wrong_user_corrupt_and_expiring_tokens(self):
        token = jwt_with_payload({"exp": 2000})
        self.booker.save_cached_token("user-a", token, now=1000)

        self.assertIsNone(self.booker.load_cached_token("user-b", now=1200))
        self.assertIsNone(self.booker.load_cached_token("user-a", now=1500))

        with open(self.booker.TOKEN_CACHE_PATH, "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertIsNone(self.booker.load_cached_token("user-a", now=1200))

    def test_cache_falls_back_to_seven_day_ttl_without_exp(self):
        token = "not.a.jwt"
        self.booker.save_cached_token("user-a", token, now=1000)

        with open(self.booker.TOKEN_CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
        self.assertEqual(cache["expires_at"], 1000 + 7 * 24 * 60 * 60)

    def test_fetch_token_uses_cache_without_auth(self):
        token = jwt_with_payload({"exp": 4102444800})
        self.booker.save_cached_token(self.booker.STUDENT_ID, token, now=1000)

        with patch.object(self.booker, "get_gym_token") as get_gym_token, \
             patch.object(self.booker, "validate_token_with_server", return_value=True) as validate:
            self.assertEqual(self.booker.fetch_token(), token)

        validate.assert_called_once_with(token)
        get_gym_token.assert_not_called()

    def test_fetch_token_reauths_when_cached_token_is_server_invalid(self):
        old_token = jwt_with_payload({"exp": 4102444800})
        new_token = jwt_with_payload({"exp": 4102444900})
        self.booker.save_cached_token(self.booker.STUDENT_ID, old_token, now=1000)

        with patch.object(self.booker, "validate_token_with_server", return_value=False) as validate, \
             patch.object(self.booker, "get_gym_token", return_value=new_token) as get_gym_token:
            self.assertEqual(self.booker.fetch_token(), new_token)

        validate.assert_called_once_with(old_token)
        get_gym_token.assert_called_once_with(self.booker.STUDENT_ID, self.booker.PASSWORD)
        self.assertEqual(self.booker.load_cached_token(self.booker.STUDENT_ID), new_token)

    def test_validate_token_with_server_accepts_success_json(self):
        with patch.object(
            self.booker.requests,
            "get",
            return_value=FakeResponse(json_data={
                "code": "Success",
                "success": True,
                "data": {"id": 1},
            }),
        ) as get:
            self.assertTrue(self.booker.validate_token_with_server("cached-token"))

        headers = get.call_args.kwargs["headers"]
        self.assertEqual(headers["Web-X-Auth-Token"], "cached-token")
        self.assertEqual(headers["xweb_xhr"], "1")
        self.assertEqual(get.call_args.args[0], self.booker.TOKEN_VALIDATE_URL)

    def test_validate_token_with_server_rejects_unauthorized(self):
        with patch.object(
            self.booker.requests,
            "get",
            return_value=FakeResponse(
                json_data={"code": "401", "msg": "请先登录", "data": None},
            ),
        ):
            self.assertFalse(self.booker.validate_token_with_server("cached-token"))

    def test_fetch_token_auths_and_saves_without_cache(self):
        token = jwt_with_payload({"exp": 4102444800})

        with patch.object(self.booker, "get_gym_token", return_value=token) as get_gym_token:
            self.assertEqual(self.booker.fetch_token(), token)

        get_gym_token.assert_called_once_with(self.booker.STUDENT_ID, self.booker.PASSWORD)
        self.assertEqual(self.booker.load_cached_token(self.booker.STUDENT_ID), token)

    def test_fetch_token_does_not_retry_auth_errors(self):
        with patch.object(
            self.booker,
            "get_gym_token",
            side_effect=self.booker.GymAuthError("bad sms"),
        ) as get_gym_token:
            with self.assertRaisesRegex(self.booker.GymAuthError, "bad sms"):
                self.booker.fetch_token()

        get_gym_token.assert_called_once_with(self.booker.STUDENT_ID, self.booker.PASSWORD)


if __name__ == "__main__":
    unittest.main()
