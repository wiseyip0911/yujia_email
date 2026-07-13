import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from .config import (
    MICROSOFT_CLIENT_ID,
    MICROSOFT_CLIENT_SECRET,
    MICROSOFT_REDIRECT_URI,
    MICROSOFT_TENANT,
)


MICROSOFT_SCOPES = [
    "openid",
    "profile",
    "email",
    "offline_access",
    "https://outlook.office.com/IMAP.AccessAsUser.All",
]
AUTHORIZE_ENDPOINT = f"https://login.microsoftonline.com/{MICROSOFT_TENANT}/oauth2/v2.0/authorize"
TOKEN_ENDPOINT = f"https://login.microsoftonline.com/{MICROSOFT_TENANT}/oauth2/v2.0/token"


class OAuthConfigError(ValueError):
    pass


class OAuthExchangeError(ValueError):
    pass


def microsoft_oauth_configured() -> bool:
    return bool(MICROSOFT_CLIENT_ID)


def scope_string() -> str:
    return " ".join(MICROSOFT_SCOPES)


def make_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def new_state() -> str:
    return secrets.token_urlsafe(32)


def build_authorization_url(state: str, code_challenge: str, login_hint: Optional[str]) -> str:
    if not microsoft_oauth_configured():
        raise OAuthConfigError("MICROSOFT_CLIENT_ID is not configured")
    params = {
        "client_id": MICROSOFT_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": MICROSOFT_REDIRECT_URI,
        "response_mode": "query",
        "scope": scope_string(),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTHORIZE_ENDPOINT}?{urllib.parse.urlencode(params)}"


def exchange_code_for_token(code: str, code_verifier: str) -> dict:
    data = {
        "client_id": MICROSOFT_CLIENT_ID,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": MICROSOFT_REDIRECT_URI,
        "code_verifier": code_verifier,
        "scope": scope_string(),
    }
    if MICROSOFT_CLIENT_SECRET:
        data["client_secret"] = MICROSOFT_CLIENT_SECRET
    return post_token(data)


def refresh_access_token(refresh_token: str) -> dict:
    data = {
        "client_id": MICROSOFT_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": scope_string(),
    }
    if MICROSOFT_CLIENT_SECRET:
        data["client_secret"] = MICROSOFT_CLIENT_SECRET
    return post_token(data)


def post_token(data: dict) -> dict:
    if not microsoft_oauth_configured():
        raise OAuthConfigError("MICROSOFT_CLIENT_ID is not configured")
    body = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise OAuthExchangeError(detail[:1000]) from exc
    except urllib.error.URLError as exc:
        raise OAuthExchangeError(str(exc.reason)) from exc


def token_expires_at(payload: dict, safety_seconds: int = 60) -> int:
    expires_in = int(payload.get("expires_in") or 0)
    if expires_in <= safety_seconds:
        return int(time.time())
    return int(time.time()) + expires_in - safety_seconds


def decode_jwt_payload(token: Optional[str]) -> dict:
    if not token or token.count(".") < 2:
        return {}
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        return json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return {}


def account_hint_from_claims(claims: dict, fallback: str) -> str:
    for key in ("preferred_username", "email", "upn"):
        value = claims.get(key)
        if isinstance(value, str) and value:
            return value.lower()
    return fallback.lower()
