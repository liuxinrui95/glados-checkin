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

    def test_expired_cookie(self):
        result = client_with(lambda *_a, **_kw: FakeResponse({"code": -2, "message": "请先登录"})).checkin()
        self.assertEqual(result.exit_code, ExitCode.AUTH)
        self.assertNotIn("test", result.message)

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

    def test_rejects_insecure_base_url(self):
        with self.assertRaises(ValueError):
            GladosClient(cookie="koa:sess=test", base_url="http://glados.rocks")


if __name__ == "__main__":
    unittest.main()

