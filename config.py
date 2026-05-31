import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHECK_INTERVAL = 300
GORZDRAV_BASE = "https://gorzdrav.spb.ru/_api/api/v2"