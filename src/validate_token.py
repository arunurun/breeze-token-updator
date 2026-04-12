from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
from urllib.parse import quote

from breeze_connect import BreezeConnect
from dotenv import load_dotenv
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


def main() -> int:
    load_dotenv()
    api_key = _required("BREEZE_API_KEY")
    api_secret = _required("BREEZE_SECRET")
    supabase_url = _required("SUPABASE_URL")
    supabase_key = _required("SUPABASE_KEY")
    token_update_url = (os.environ.get("TOKEN_UPDATE_URL") or "").strip()
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
        body_lines.extend(
            [
                "",
                "Step 2: Open this URL to get the HTML token form.",
                f"Token form URL: {update_link}",
                "If needed, API mode is also supported via POST JSON using token_input.",
            ]
        )
    body = "\n".join(body_lines)

    send_webhook_alert(body)
    send_email_alert("Breeze token refresh required", body)
    print(body)
    return 2


if __name__ == "__main__":
    sys.exit(main())
