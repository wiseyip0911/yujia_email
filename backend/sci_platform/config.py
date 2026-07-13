import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "sci_platform.sqlite3"
FRONTEND_DIR = ROOT_DIR / "frontend"
EXPORT_DIR = DATA_DIR / "exports"
SESSION_SECRET_PATH = DATA_DIR / "session_secret"

DEFAULT_LOGIN_USERNAMES = "liaojunhua,pangyanan,tanzhiyun"
SCI_LOGIN_PASSWORD = os.getenv("SCI_LOGIN_PASSWORD", "666666")
_login_usernames_raw = os.getenv("SCI_LOGIN_USERNAMES") or os.getenv("SCI_LOGIN_USERNAME") or DEFAULT_LOGIN_USERNAMES
SCI_LOGIN_USERNAMES = tuple(item.strip() for item in _login_usernames_raw.split(",") if item.strip())
SCI_LOGIN_ACCOUNTS = {username: SCI_LOGIN_PASSWORD for username in SCI_LOGIN_USERNAMES}

MICROSOFT_CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
MICROSOFT_CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
MICROSOFT_REDIRECT_URI = os.getenv(
    "MICROSOFT_REDIRECT_URI",
    "http://127.0.0.1:8000/api/oauth/microsoft/callback",
).strip()
MICROSOFT_TENANT = os.getenv("MICROSOFT_TENANT", "common").strip() or "common"
