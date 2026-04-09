# config.py
import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
TEMP_DIR = BASE_DIR / "temp"
SESSIONS_DIR = DATA_DIR / "sessions"

# Create directories
for dir_path in [DATA_DIR, TEMP_DIR, SESSIONS_DIR]:
    dir_path.mkdir(exist_ok=True)

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    ADMIN_USER_IDS = list(map(int, os.getenv("ADMIN_USER_IDS", "").split(","))) if os.getenv("ADMIN_USER_IDS") else []
    
    # Instagram accounts format: username1:password1,username2:password2
    IG_ACCOUNTS = {}
    if os.getenv("IG_ACCOUNTS"):
        for acc in os.getenv("IG_ACCOUNTS").split(","):
            if ":" in acc:
                user, pwd = acc.split(":", 1)
                IG_ACCOUNTS[user.strip()] = pwd.strip()
    
    DATABASE_PATH = DATA_DIR / "reelbot.db"
    SCHEDULER_INTERVAL = 30  # seconds
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
    
    # Supported platforms
    SUPPORTED_PLATFORMS = ['youtube.com', 'youtu.be', 'instagram.com', 'fb.watch', 'facebook.com']
