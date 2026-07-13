import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "sci_platform.sqlite3"
FRONTEND_DIR = ROOT_DIR / "frontend"
EXPORT_DIR = DATA_DIR / "exports"
SESSION_SECRET_PATH = DATA_DIR / "session_secret"

SCI_LOGIN_USERNAME = os.getenv("SCI_LOGIN_USERNAME", "liaojunhua").strip() or "liaojunhua"
SCI_LOGIN_PASSWORD = os.getenv("SCI_LOGIN_PASSWORD", "666666")

MICROSOFT_CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
MICROSOFT_CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
MICROSOFT_REDIRECT_URI = os.getenv(
    "MICROSOFT_REDIRECT_URI",
    "http://127.0.0.1:8000/api/oauth/microsoft/callback",
).strip()
MICROSOFT_TENANT = os.getenv("MICROSOFT_TENANT", "common").strip() or "common"
