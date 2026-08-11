from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from enum import IntEnum
from http.client import HTTPResponse
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


class ExitCode(IntEnum):
    OK = 0
    CONFIG = 1
    AUTH = 2
    REMOTE = 3
    NETWORK = 4


@dataclass(frozen=True)
class CheckinResult:
    exit_code: ExitCode
    message: str
    api_code: int | str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == ExitCode.OK


class GladosClient:
    """Small stdlib-only client for the GLaDOS internal check-in endpoint."""

    def __init__(
        self,
        *,
        cookie: str,
        base_url: str = "https://glados.rocks",
        checkin_path: str = "/api/user/checkin",
        token: str = "glados.one",
        timeout: float = 15,
        retries: int = 2,
        opener: Callable[..., HTTPResponse] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cookie = cookie.strip()
        self.base_url = base_url.rstrip("/") + "/"
        self.checkin_path = checkin_path.lstrip("/")
        self.token = token
        self.timeout = timeout
        self.retries = retries
        self.opener = opener
        self.sleeper = sleeper
        self._validate()

    def _validate(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("GLADOS_BASE_URL must be a valid HTTPS URL")
        if not self.cookie or "replace-me" in self.cookie:
            raise ValueError("GLADOS_COOKIE is missing or still contains the example value")
        if self.timeout <= 0:
            raise ValueError("GLADOS_TIMEOUT must be positive")
        if self.retries < 0 or self.retries > 5:
            raise ValueError("GLADOS_RETRIES must be between 0 and 5")

    def checkin(self) -> CheckinResult:
        url = urljoin(self.base_url, self.checkin_path)
        body = json.dumps({"token": self.token}).encode("utf-8")
        request = Request(
            url,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json;charset=UTF-8",
                "Cookie": self.cookie,
                "Origin": self.base_url.rstrip("/"),
                "Referer": urljoin(self.base_url, "console/checkin"),
                "User-Agent": "glados-checkin/1.0",
            },
        )

        for attempt in range(self.retries + 1):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    payload = self._decode(response.read())
                return self._classify(payload)
            except HTTPError as exc:
                if exc.code in (401, 403):
                    return CheckinResult(ExitCode.AUTH, "authentication rejected; update GLADOS_COOKIE")
                if exc.code == 429 or 500 <= exc.code < 600:
                    if attempt < self.retries:
                        self._backoff(attempt)
                        continue
                    return CheckinResult(ExitCode.NETWORK, f"temporary HTTP error after retries: {exc.code}")
                return CheckinResult(ExitCode.REMOTE, f"unexpected HTTP status: {exc.code}")
            except (URLError, TimeoutError, OSError) as exc:
                if attempt < self.retries:
                    self._backoff(attempt)
                    continue
                reason = getattr(exc, "reason", exc)
                return CheckinResult(ExitCode.NETWORK, f"network error after retries: {reason}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                return CheckinResult(ExitCode.REMOTE, "endpoint returned invalid JSON; the API may have changed")

        return CheckinResult(ExitCode.NETWORK, "request failed")

    @staticmethod
    def _decode(raw: bytes) -> dict[str, Any]:
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise json.JSONDecodeError("response is not an object", "", 0)
        return payload

    @staticmethod
    def _classify(payload: dict[str, Any]) -> CheckinResult:
        code = payload.get("code")
        message = str(payload.get("message") or payload.get("msg") or "no message")
        lowered = message.casefold()

        success_markers = ("checkin success", "check-in success", "already checked", "签到成功", "已经签到", "已签到")
        auth_markers = ("login", "auth", "cookie", "unauthorized", "登录", "未登陆", "未登录")

        if code in (0, "0") or any(marker in lowered for marker in success_markers):
            return CheckinResult(ExitCode.OK, message, code)
        if any(marker in lowered for marker in auth_markers):
            return CheckinResult(ExitCode.AUTH, "authentication expired; update GLADOS_COOKIE", code)
        return CheckinResult(ExitCode.REMOTE, f"check-in rejected: {message}", code)

    def _backoff(self, attempt: int) -> None:
        self.sleeper((2**attempt) + random.random())
