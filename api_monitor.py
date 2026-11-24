"""Monitor HTTP e quote residue, senza richieste aggiuntive né credenziali."""
import atexit
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
import requests

EVENTS = []
LAST_QUOTA = {}
ORIGINAL_GET = requests.get
ORIGINAL_POST = requests.post
OUTPUT = Path("api_monitor.json")
LOW_CREDITS = int(os.getenv("API_LOW_CREDITS_THRESHOLD", "100"))
LOW_PERCENT = float(os.getenv("API_LOW_CREDITS_PERCENT", "10"))


def number(value):
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def record(method, original):
    def wrapped(url, *args, **kwargs):
        start = time.monotonic()
        response = None
        error = None
        try:
            response = original(url, *args, **kwargs)
            return response
        except requests.RequestException as exc:
            error = type(exc).__name__
            raise
        finally:
            host = urlsplit(str(url)).hostname or "unknown"
            service = "telegram" if host == "api.telegram.org" else host
            entry = {
                "utc": datetime.now(timezone.utc).isoformat(),
                "service": service,
                "method": method,
                "status": response.status_code if response is not None else None,
                "duration_ms": round((time.monotonic() - start) * 1000),
            }
            if error:
                entry["error"] = error
            if response is not None:
                headers = response.headers
                remaining = number(headers.get("x-requests-remaining"))
                used = number(headers.get("x-requests-used"))
                last = number(headers.get("x-requests-last"))
                if remaining is not None:
                    entry["credits_remaining"] = remaining
                if used is not None:
                    entry["credits_used"] = used
                if last is not None:
                    entry["credits_last_request"] = last
                if remaining is not None:
                    quota = {"credits_remaining": remaining}
                    if used is not None:
                        quota["credits_used"] = used
                    total = remaining + used if used is not None else None
                    if total and total > 0:
                        quota["percent_remaining"] = round(100 * remaining / total, 2)
                    quota["utc"] = entry["utc"]
                    LAST_QUOTA[service] = quota
                    if remaining <= LOW_CREDITS or (
                        total and 100 * remaining / total <= LOW_PERCENT
                    ):
                        print("API_QUOTA_WARNING:", json.dumps(
                            {"service": service, **quota}, ensure_ascii=False))
                if response.status_code == 429:
                    print("API_RATE_LIMIT_WARNING:", service, "HTTP 429")
                elif response.status_code in (401, 403):
                    print("API_AUTH_WARNING:", service, "HTTP", response.status_code)
            EVENTS.append(entry)
            print("API_MONITOR:", json.dumps(entry, ensure_ascii=False))
    return wrapped


def save():
    try:
        OUTPUT.write_text(json.dumps({
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "request_count": len(EVENTS),
            "last_reported_quotas": LAST_QUOTA,
            "quota_note": "Unknown when provider does not send usage headers; cached values are not current balances.",
            "requests": EVENTS,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("API_QUOTA_SUMMARY:", json.dumps(LAST_QUOTA, ensure_ascii=False))
    except OSError as exc:
        print("API_MONITOR_SAVE_ERROR:", type(exc).__name__)


def install():
    requests.get = record("GET", ORIGINAL_GET)
    requests.post = record("POST", ORIGINAL_POST)
    atexit.register(save)
