import io
import json
import unittest
from urllib.error import HTTPError, URLError

from glados_checkin.client import ExitCode, GladosClient


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def client_with(opener, *, retries=0, sleeps=None):
    return GladosClient(
        cookie="koa:sess=test; koa:sess.sig=test",
        opener=opener,
        retries=retries,
        sleeper=(sleeps.append if sleeps is not None else lambda _: None),
    )


class GladosClientTests(unittest.TestCase):
    def test_success(self):
        result = client_with(lambda *_a, **_kw: FakeResponse({"code": 0, "message": "Checkin success"})).checkin()
        self.assertEqual(result.exit_code, ExitCode.OK)

    def test_already_checked_in(self):
        result = client_with(lambda *_a, **_kw: FakeResponse({"code": -1, "message": "今日已签到"})).checkin()
        self.assertEqual(result.exit_code, ExitCode.OK)

    def test_current_repeat_message_is_success(self):
        result = client_with(
            lambda *_a, **_kw: FakeResponse(
                {"code": 1, "message": "Checkin Repeats! Please Try Tomorrow"}
            )
        ).checkin()
        self.assertEqual(result.exit_code, ExitCode.OK)

    def test_current_observation_message_is_success(self):
        result = client_with(
            lambda *_a, **_kw: FakeResponse(
                {"code": 1, "message": "Today's observation logged"}
            )
        ).checkin()
        self.assertEqual(result.exit_code, ExitCode.OK)

    def test_expired_cookie(self):
        result = client_with(lambda *_a, **_kw: FakeResponse({"code": -2, "message": "请先登录"})).checkin()
        self.assertEqual(result.exit_code, ExitCode.AUTH)
        self.assertNotIn("test", result.message)

    def test_permission_denied_is_expired_auth(self):
        result = client_with(
            lambda *_a, **_kw: FakeResponse({"code": -2, "message": "没有权限"})
        ).checkin()
        self.assertEqual(result.exit_code, ExitCode.AUTH)

    def test_device_mismatch_requires_sign_in(self):
        result = client_with(
            lambda *_a, **_kw: FakeResponse(
                {
                    "code": 4,
                    "reason": "device-mismatch",
                    "message": "Automated check-in detected. Please sign in again to continue.",
                }
            )
        ).checkin()
        self.assertEqual(result.exit_code, ExitCode.AUTH)
        self.assertIn("device verification", result.message)

    def test_moved_endpoint_is_not_false_success(self):
        result = client_with(
            lambda *_a, **_kw: FakeResponse(
                {"code": 0, "message": "please checkin via https://glados.cloud"}
            )
        ).checkin()
        self.assertEqual(result.exit_code, ExitCode.REMOTE)

    def test_token_defaults_to_base_url_hostname(self):
        captured = {}

        def opener(request, **_kwargs):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse({"code": 0, "message": "ok"})

        result = GladosClient(
            cookie="koa:sess=test; koa:sess.sig=test",
            base_url="https://glados.cloud",
            opener=opener,
            retries=0,
        ).checkin()
        self.assertEqual(result.exit_code, ExitCode.OK)
        self.assertEqual(captured["url"], "https://glados.cloud/api/user/checkin")
        self.assertEqual(captured["body"], {"token": "glados.cloud"})

    def test_browser_identity_headers_are_forwarded(self):
        captured = {}
        user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"
        )

        def opener(request, **_kwargs):
            captured["headers"] = dict(request.header_items())
            return FakeResponse({"code": 0, "message": "ok"})

        result = GladosClient(
            cookie="gld:sess=test; gld:sess.sig=test",
            user_agent=user_agent,
            opener=opener,
            retries=0,
        ).checkin()
        self.assertEqual(result.exit_code, ExitCode.OK)
        self.assertEqual(captured["headers"]["Cookie"], "gld:sess=test; gld:sess.sig=test")
        self.assertEqual(captured["headers"]["User-agent"], user_agent)
        self.assertEqual(captured["headers"]["Sec-ch-ua-platform"], '"macOS"')

    def test_retries_transient_network_error(self):
        attempts = []
        sleeps = []

        def opener(*_args, **_kwargs):
            attempts.append(1)
            if len(attempts) < 3:
                raise URLError("temporary")
            return FakeResponse({"code": 0, "message": "ok"})

        result = client_with(opener, retries=2, sleeps=sleeps).checkin()
        self.assertEqual(result.exit_code, ExitCode.OK)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len(sleeps), 2)

    def test_http_auth_error_is_not_retried(self):
        attempts = []

        def opener(*_args, **_kwargs):
            attempts.append(1)
            raise HTTPError("https://glados.rocks", 401, "Unauthorized", {}, io.BytesIO())

        result = client_with(opener, retries=2).checkin()
        self.assertEqual(result.exit_code, ExitCode.AUTH)
        self.assertEqual(len(attempts), 1)

    def test_http_403_json_auth_error_is_classified(self):
        body = io.BytesIO(json.dumps({"code": -2, "message": "没有权限"}).encode())

        def opener(*_args, **_kwargs):
            raise HTTPError("https://glados.rocks", 403, "Forbidden", {}, body)

        result = client_with(opener).checkin()
        self.assertEqual(result.exit_code, ExitCode.AUTH)

    def test_http_403_device_mismatch_is_classified(self):
        body = io.BytesIO(
            json.dumps(
                {
                    "code": 4,
                    "reason": "device-mismatch",
                    "message": "Automated check-in detected. Please sign in again to continue.",
                }
            ).encode()
        )

        def opener(*_args, **_kwargs):
            raise HTTPError("https://glados.rocks", 403, "Forbidden", {}, body)

        result = client_with(opener).checkin()
        self.assertEqual(result.exit_code, ExitCode.AUTH)
        self.assertIn("device verification", result.message)

    def test_http_403_html_is_remote_rejection(self):
        def opener(*_args, **_kwargs):
            raise HTTPError(
                "https://glados.rocks",
                403,
                "Forbidden",
                {},
                io.BytesIO(b"<html>blocked</html>"),
            )

        result = client_with(opener).checkin()
        self.assertEqual(result.exit_code, ExitCode.REMOTE)

    def test_http_500_success_shaped_body_is_not_success(self):
        attempts = []

        def opener(*_args, **_kwargs):
            attempts.append(1)
            body = io.BytesIO(json.dumps({"code": 0, "message": "ok"}).encode())
            raise HTTPError("https://glados.rocks", 500, "Server Error", {}, body)

        result = client_with(opener, retries=1, sleeps=[]).checkin()
        self.assertEqual(result.exit_code, ExitCode.NETWORK)
        self.assertEqual(len(attempts), 2)

    def test_rejects_insecure_base_url(self):
        with self.assertRaises(ValueError):
            GladosClient(cookie="koa:sess=test", base_url="http://glados.rocks")


if __name__ == "__main__":
    unittest.main()
