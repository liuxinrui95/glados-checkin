from __future__ import annotations

import json
import os
import random
import sys
import time

from glados_checkin import ExitCode, GladosClient


def _load_dotenv(path: str = ".env") -> None:
    """Load a simple local .env without adding a runtime dependency."""
    try:
        with open(path, encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    except FileNotFoundError:
        pass


def main() -> int:
    _load_dotenv()
    try:
        jitter = int(os.getenv("GLADOS_JITTER_SECONDS", "0"))
        if jitter < 0 or jitter > 3600:
            raise ValueError("GLADOS_JITTER_SECONDS must be between 0 and 3600")
        if jitter:
            time.sleep(random.randint(0, jitter))

        client = GladosClient(
            cookie=os.getenv("GLADOS_COOKIE", ""),
            base_url=os.getenv("GLADOS_BASE_URL", "https://glados.rocks"),
            checkin_path=os.getenv("GLADOS_CHECKIN_PATH", "/api/user/checkin"),
            token=os.getenv("GLADOS_CHECKIN_TOKEN") or None,
            user_agent=os.getenv("GLADOS_USER_AGENT", "glados-checkin/2.0"),
            timeout=float(os.getenv("GLADOS_TIMEOUT", "15")),
            retries=int(os.getenv("GLADOS_RETRIES", "2")),
        )
        result = client.checkin()
        output = json.dumps({
            "ok": result.ok,
            "message": result.message,
            "api_code": result.api_code,
        }, ensure_ascii=False)
        print(output)
        if not result.ok and os.getenv("GITHUB_ACTIONS") == "true":
            annotation = output.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::error title=GLaDOS check-in failed::{annotation}")
        return int(result.exit_code)
    except (ValueError, TypeError) as exc:
        print(json.dumps({"ok": False, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return int(ExitCode.CONFIG)


if __name__ == "__main__":
    raise SystemExit(main())
