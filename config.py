import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
except ValueError:
    ADMIN_USER_ID = 0

DEFAULT_PASSWORD = os.getenv("DEFAULT_PASSWORD", "53561106Tojo")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
