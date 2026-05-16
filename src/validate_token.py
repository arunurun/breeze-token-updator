from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote, urlencode

from breeze_connect import BreezeConnect
from dotenv import load_dotenv
import requests
from supabase import create_client

from notify import send_email_alert, send_webhook_alert


def _required(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def breeze_login_url(api_key: str) -> str:
    return f"https://api.icicidirect.com/apiuser/login?api_key={quote(api_key, safe='')}"


def build_signed_state(signing_secret: str, ttl_seconds: int = 900) -> str:
    payload = {"exp": int(time.time()) + int(ttl_seconds), "scope": "breeze-token-update-v1"}
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload_json).decode("ascii").rstrip("=")
    sig = hmac.new(
        signing_secret.encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded}.{sig}"


def token_update_link(base_url: str, state: str | None) -> str:
    if not base_url:
        return ""
    if not state:
        return base_url
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}state={quote(state, safe='')}"


def token_form_link(form_base_url: str, state: str | None, update_url: str) -> str:
    base = form_base_url.strip()
    if not base:
        return ""
    params: dict[str, str] = {}
    if state:
        params["state"] = state
    if update_url:
        params["update_url"] = update_url
    if not params:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode(params, quote_via=quote)}"


def load_token_from_supabase(url: str, supabase_key: str) -> str:
    client = create_client(url, supabase_key)
    res = client.table("session_config").select("breeze_session_token").eq("id", 1).limit(1).execute()
    data = getattr(res, "data", None) or []
    if not data:
        raise RuntimeError("No row found in session_config with id=1")
    token = (data[0].get("breeze_session_token") or "").strip()
    if not token:
        raise RuntimeError("breeze_session_token is empty in session_config")
    return token


def is_token_valid(api_key: str, api_secret: str, token: str) -> tuple[bool, str]:
    breeze = BreezeConnect(api_key=api_key)
    try:
        breeze.generate_session(api_secret=api_secret, session_token=token)
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def parse_ist_holidays_env(raw: str) -> set[date]:
    values = [item.strip() for item in re.split(r"[,\n;]+", raw or "") if item.strip()]
    parsed: set[date] = set()
    for value in values:
        parsed.add(datetime.strptime(value, "%Y-%m-%d").date())
    return parsed


def _extract_dates_from_obj(obj: object) -> set[date]:
    found: set[date] = set()
    if isinstance(obj, dict):
        for value in obj.values():
            found.update(_extract_dates_from_obj(value))
        return found
    if isinstance(obj, list):
        for item in obj:
            found.update(_extract_dates_from_obj(item))
        return found
    if isinstance(obj, str):
        text = obj.strip()
        for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                found.add(datetime.strptime(text, fmt).date())
                break
            except ValueError:
                continue
    return found


def load_nse_holidays_ist() -> set[date]:
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.nseindia.com/",
    }
    try:
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        response = session.get(
            "https://www.nseindia.com/api/holiday-master?type=trading",
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        return _extract_dates_from_obj(response.json())
    except Exception:  # noqa: BLE001
        return set()


def market_closed_reason_ist(now_ist: datetime) -> str | None:
    today_ist = now_ist.date()
    if today_ist.weekday() >= 5:
        return "Indian market is closed (weekend)."

    env_holidays_raw = (os.environ.get("MARKET_HOLIDAYS_IST") or "").strip()
    env_holidays: set[date] = set()
    if env_holidays_raw:
        try:
            env_holidays = parse_ist_holidays_env(env_holidays_raw)
        except ValueError as exc:
            print(f"Ignoring MARKET_HOLIDAYS_IST due to parse error: {exc}")

    nse_holidays = load_nse_holidays_ist()
    all_holidays = nse_holidays | env_holidays
    if today_ist in all_holidays:
        return "Indian market is closed (NSE trading holiday)."
    return None


def is_manual_github_dispatch() -> bool:
    return (os.environ.get("GITHUB_EVENT_NAME") or "").strip() == "workflow_dispatch"


def main() -> int:
    load_dotenv()
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    manual_dispatch = is_manual_github_dispatch()
    closed_reason = market_closed_reason_ist(now_ist)
    if closed_reason and not manual_dispatch:
        message = f"{closed_reason} Skipping token validation for {now_ist.date().isoformat()} (IST)."
        print(message)
        return 0
    if closed_reason and manual_dispatch:
        print(
            f"{closed_reason} Manual dispatch detected, continuing token validation for"
            f" {now_ist.date().isoformat()} (IST)."
        )

    api_key = _required("BREEZE_API_KEY")
    api_secret = _required("BREEZE_SECRET")
    supabase_url = _required("SUPABASE_URL")
    supabase_key = _required("SUPABASE_KEY")
    token_update_url = (os.environ.get("TOKEN_UPDATE_URL") or "").strip()
    token_form_url = (os.environ.get("TOKEN_FORM_URL") or "").strip()
    state_signing_secret = (os.environ.get("STATE_SIGNING_SECRET") or "").strip()

    token = load_token_from_supabase(supabase_url, supabase_key)
    valid, reason = is_token_valid(api_key, api_secret, token)
    if valid:
        print("Breeze token is valid.")
        return 0

    login_url = breeze_login_url(api_key)
    body_lines = [
        "Breeze session token appears expired/invalid.",
        f"Reason: {reason}",
        "",
        "Step 1: Open Breeze login URL and complete mobile 2FA.",
        f"Login URL: {login_url}",
    ]
    if token_update_url:
        state = build_signed_state(state_signing_secret) if state_signing_secret else None
        update_link = token_update_link(token_update_url, state)
        form_link = token_form_link(token_form_url, state, token_update_url)
        body_lines.extend(
            [
                "",
                "Step 2: Open the token form URL and submit your API_Session.",
                f"Token form URL: {form_link or update_link}",
                "API fallback: POST JSON to token update URL with token_input.",
            ]
        )
    body = "\n".join(body_lines)

    send_webhook_alert(body)
    send_email_alert("Breeze token refresh required", body)
    print(body)
    return 2


if __name__ == "__main__":
    sys.exit(main())
