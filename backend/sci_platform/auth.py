import base64
import hashlib
import hmac
import json
import secrets
import time
from http.cookies import SimpleCookie
from typing import Optional

from .config import DATA_DIR, SCI_LOGIN_PASSWORD, SCI_LOGIN_USERNAME, SESSION_SECRET_PATH


SESSION_COOKIE = "sci_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60


def credentials_valid(username: str, password: str) -> bool:
    return hmac.compare_digest(username or "", SCI_LOGIN_USERNAME) and hmac.compare_digest(password or "", SCI_LOGIN_PASSWORD)


def session_secret() -> bytes:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SESSION_SECRET_PATH.exists():
        return SESSION_SECRET_PATH.read_text(encoding="utf-8").strip().encode("utf-8")
    secret = secrets.token_urlsafe(48)
    SESSION_SECRET_PATH.write_text(secret, encoding="utf-8")
    SESSION_SECRET_PATH.chmod(0o600)
    return secret.encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def make_session_cookie(username: str, now: Optional[int] = None) -> str:
    issued_at = int(now if now is not None else time.time())
    payload = {"u": username, "exp": issued_at + SESSION_TTL_SECONDS}
    payload_b64 = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(session_secret(), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64(signature)}"


def verify_session_cookie(value: Optional[str], now: Optional[int] = None) -> Optional[str]:
    if not value or "." not in value:
        return None
    payload_b64, signature_b64 = value.split(".", 1)
    expected = _b64(hmac.new(session_secret(), payload_b64.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature_b64, expected):
        return None
    try:
        payload = json.loads(_unb64(payload_b64).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    expires_at = int(payload.get("exp") or 0)
    current = int(now if now is not None else time.time())
    if expires_at < current:
        return None
    username = str(payload.get("u") or "")
    return username if username == SCI_LOGIN_USERNAME else None


def cookie_value(cookie_header: Optional[str], name: str = SESSION_COOKIE) -> Optional[str]:
    if not cookie_header:
        return None
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    morsel = cookie.get(name)
    return morsel.value if morsel else None
