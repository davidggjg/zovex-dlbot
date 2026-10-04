import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _bool(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()
}

USE_LOCAL_API = _bool("USE_LOCAL_API", True)
LOCAL_API_URL = os.getenv("LOCAL_API_URL", "http://127.0.0.1:8081").rstrip("/")

MAX_FILESIZE_MB = _int("MAX_FILESIZE_MB", 1950)
MAX_FILESIZE = MAX_FILESIZE_MB * 1024 * 1024
DAILY_QUOTA_MB = _int("DAILY_QUOTA_MB", 4000)
DAILY_QUOTA = DAILY_QUOTA_MB * 1024 * 1024
DAILY_JOBS = _int("DAILY_JOBS", 25)
GLOBAL_CONCURRENCY = _int("GLOBAL_CONCURRENCY", 2)
USER_CONCURRENCY = _int("USER_CONCURRENCY", 1)
COOLDOWN_SECONDS = _int("COOLDOWN_SECONDS", 3)
MAX_DURATION_MINUTES = _int("MAX_DURATION_MINUTES", 240)
ALLOW_PLAYLISTS = _bool("ALLOW_PLAYLISTS", False)

DATA_DIR = Path(os.getenv("DATA_DIR", "/var/lib/dlbot"))
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", str(DATA_DIR / "downloads")))
COOKIES_DIR = Path(os.getenv("COOKIES_DIR", str(DATA_DIR / "cookies")))
DB_PATH = DATA_DIR / "dlbot.sqlite3"

DEFAULT_SUB_LANGS = [
    s.strip() for s in os.getenv("DEFAULT_SUB_LANGS", "he,en").split(",") if s.strip()
]
PROGRESS_INTERVAL = _int("PROGRESS_INTERVAL", 4)

for d in (DATA_DIR, DOWNLOAD_DIR, COOKIES_DIR):
    d.mkdir(parents=True, exist_ok=True)
