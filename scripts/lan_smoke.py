"""Check a LocalMind host from a second device using only Python's standard library.

Example: python scripts/lan_smoke.py http://192.168.1.10:8000 --output lan-result.json
Keep the LAN connected while disconnecting the router's internet connection.
"""

import argparse
import getpass
import http.cookiejar
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener


def check(base_url, username, password, signup=False):
    cookies = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookies))
    results = {}

    def request(path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        req = Request(base_url.rstrip("/") + path, data=data, headers=headers)
        try:
            with opener.open(req, timeout=180) as response:
                return response.read().decode("utf-8"), response.status
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"{path} returned HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Cannot reach {base_url}: {exc.reason}") from exc

    page, _ = request("/")
    results["frontend"] = "<html" in page.lower()
    health_text, _ = request("/api/health")
    health = json.loads(health_text)
    results["model_reachable"] = health.get("model_server") == "reachable"
    results["account_storage_reachable"] = health.get("account_storage") == "reachable"
    if not all((results["frontend"], results["model_reachable"],
                results["account_storage_reachable"], health.get("login_configured"))):
        raise RuntimeError(f"Host is not ready: {results}, login_configured={health.get('login_configured')}")

    route = "/api/auth/signup" if signup else "/api/auth/login"
    user_text, _ = request(route, {"username": username, "password": password})
    results["login"] = json.loads(user_text).get("username", "").lower() == username.lower()
    results["session"] = json.loads(request("/api/auth/me")[0]).get("username", "").lower() == username.lower()
    reply = json.loads(request("/api/chat", {"message": "Reply with READY only."})[0])
    results["chat"] = bool(reply.get("reply", "").strip())
    streamed = request("/api/chat/stream", {"message": "Reply with READY only."})[0]
    results["stream"] = bool(streamed.strip()) and "[The " not in streamed
    summary = json.loads(request("/api/summarize", {
        "text": "A local area network connects devices in a limited area. It can operate without internet.",
        "length": "short",
    })[0])
    results["summary"] = bool(summary.get("summary", "").strip())
    improved = json.loads(request("/api/improve-text", {
        "text": "This are a test.", "task": "grammar",
    })[0])
    results["grammar"] = bool(improved.get("improved_text", "").strip())
    return results


def main():
    parser = argparse.ArgumentParser(description="Verify LocalMind from a LAN client.")
    parser.add_argument("base_url", help="Host address, for example http://192.168.1.10:8000")
    parser.add_argument("--username", help="Existing username (prompted if omitted)")
    parser.add_argument("--signup", action="store_true", help="Create a new test account")
    parser.add_argument("--output", type=Path, help="Write a JSON evidence report")
    args = parser.parse_args()
    username = args.username or input("LocalMind username: ").strip()
    password = getpass.getpass("LocalMind password: ")
    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "client": "second LAN device (confirm manually)",
        "internet_disconnected": "confirm manually before running",
    }
    try:
        report["checks"] = check(args.base_url, username, password, args.signup)
        report["passed"] = all(report["checks"].values())
    except (RuntimeError, ValueError, KeyError) as exc:
        report["passed"] = False
        report["error"] = str(exc)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
