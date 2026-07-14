import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

raw_channel_map = os.getenv("DISCORD_CHANNEL_MAP", "{}")
try:
    DISCORD_CHANNEL_MAP = json.loads(raw_channel_map)
    DISCORD_CHANNEL_MAP = {str(k).lower(): int(v) for k, v in DISCORD_CHANNEL_MAP.items()}
except Exception as e:
    print(f"Warning: Failed to parse DISCORD_CHANNEL_MAP: {e}. Fallback to empty map.")
    DISCORD_CHANNEL_MAP = {}

raw_urls = os.getenv("UPWORK_SEARCH_URLS", "")
UPWORK_SEARCH_URLS = [url.strip() for url in raw_urls.split(",") if url.strip()]

try:
    MONITOR_INTERVAL = int(os.getenv("MONITOR_INTERVAL", "300"))
except ValueError:
    MONITOR_INTERVAL = 300

BROWSER_MODE = os.getenv("BROWSER_MODE", "headless").lower()
UPWORK_USERNAME = os.getenv("UPWORK_USERNAME", "")
UPWORK_PASSWORD = os.getenv("UPWORK_PASSWORD", "")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/jobs.db")
if DATABASE_URL.startswith("sqlite:///data/"):
    DATABASE_URL = f"sqlite:///{DATA_DIR / 'jobs.db'}"

PROXY_URL = os.getenv("PROXY_URL", "")

def validate_config() -> tuple[bool, list[str]]:
    errors = []
    if not DISCORD_BOT_TOKEN:
        errors.append("DISCORD_BOT_TOKEN is required.")
    if not DISCORD_CHANNEL_MAP:
        errors.append("DISCORD_CHANNEL_MAP is required and must be a valid JSON map.")
    elif "default" not in DISCORD_CHANNEL_MAP:
        errors.append("DISCORD_CHANNEL_MAP must contain a 'default' channel fallback.")
    if not UPWORK_SEARCH_URLS:
        errors.append("UPWORK_SEARCH_URLS must contain at least one valid Upwork search URL.")
    
    return len(errors) == 0, errors
