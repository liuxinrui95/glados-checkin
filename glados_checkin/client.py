from __future__ import annotations

import json
import random
import re
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
        token: str | None = None,
        user_agent: str = "glados-checkin/2.0",
        timeout: float = 15,
        retries: int = 2,
        opener: Callable[..., HTTPResponse] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cookie = cookie.strip()
        self.base_url = base_url.rstrip("/") + "/"
        self.checkin_path = checkin_path.lstrip("/")
        parsed = urlparse(self.base_url)
        self.token = (token or parsed.hostname or "").strip()
        self.user_agent = user_agent.strip() or "glados-checkin/2.0"
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
        if not self.token:
            raise ValueError("GLADOS_CHECKIN_TOKEN could not be derived from GLADOS_BASE_URL")

    def checkin(self) -> CheckinResult:
        url = urljoin(self.base_url, self.checkin_path)
        body = json.dumps({"token": self.token}).encode("utf-8")
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/json;charset=UTF-8",
            "Cookie": self.cookie,
            "Origin": self.base_url.rstrip("/"),
            "Referer": urljoin(self.base_url, "console/checkin"),
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": self.user_agent,
        }
        headers.update(self._client_hint_headers())
        request = Request(
            url,
            data=body,
            method="POST",
            headers=headers,
        )

        for attempt in range(self.retries + 1):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    payload = self._decode(response.read())
                return self._classify(payload)
            except HTTPError as exc:
                try:
                    payload = self._decode(exc.read())
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = None
                if payload is not None:
                    classified = self._classify(payload)
                    # A non-2xx response can refine the authentication error,
                    # but it must never be turned into a successful check-in.
                    if classified.exit_code == ExitCode.AUTH:
                        return classified
                if exc.code == 401:
                    return CheckinResult(ExitCode.AUTH, "authentication rejected; update GLADOS_COOKIE")
                if exc.code == 403:
                    return CheckinResult(
                        ExitCode.REMOTE,
                        "HTTP 403; the request was rejected or blocked by site protection",
                    )
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
        reason = str(payload.get("reason") or "").casefold()

        success_markers = (
            "checkin success",
            "check-in success",
            "checkin! got",
            "checkin repeats! please try tomorrow",
            "today's observation logged",
            "already checked",
            "签到成功",
            "已经签到",
            "已签到",
        )
        auth_markers = (
            "login",
            "sign in",
            "auth",
            "cookie",
            "unauthorized",
            "permission",
            "没有权限",
            "登录",
            "未登陆",
            "未登录",
        )
        migration_markers = ("please checkin via", "please check-in via")

        if code in (4, "4") or reason == "device-mismatch" or "automated check-in" in lowered:
            return CheckinResult(
                ExitCode.AUTH,
                "device verification rejected this request; sign in again in the official site",
                code,
            )
        if code in (-2, "-2") or any(marker in lowered for marker in auth_markers):
            return CheckinResult(ExitCode.AUTH, "authentication expired; update GLADOS_COOKIE", code)
        if any(marker in lowered for marker in migration_markers):
            return CheckinResult(
                ExitCode.REMOTE,
                f"check-in endpoint moved: {message}",
                code,
            )

        if code in (0, "0") or any(marker in lowered for marker in success_markers):
            return CheckinResult(ExitCode.OK, message, code)
        return CheckinResult(ExitCode.REMOTE, f"check-in rejected: {message}", code)

    def _backoff(self, attempt: int) -> None:
        self.sleeper((2**attempt) + random.random())

    def _client_hint_headers(self) -> dict[str, str]:
        chrome = re.search(r"(?:Chrome|Chromium)/(\d+)", self.user_agent)
        if not chrome:
            return {}
        if "Macintosh" in self.user_agent:
            platform = "macOS"
        elif "Windows" in self.user_agent:
            platform = "Windows"
        elif "Android" in self.user_agent:
            platform = "Android"
        elif "Linux" in self.user_agent:
            platform = "Linux"
        else:
            platform = "Unknown"
        major = chrome.group(1)
        return {
            "Sec-CH-UA": f'"Chromium";v="{major}", "Google Chrome";v="{major}", "Not_A Brand";v="99"',
            "Sec-CH-UA-Mobile": "?1" if "Mobile" in self.user_agent else "?0",
            "Sec-CH-UA-Platform": f'"{platform}"',
        }
