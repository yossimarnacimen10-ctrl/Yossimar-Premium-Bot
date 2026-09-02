from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()

def _as_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default

def _parse_admins(raw: str) -> set[int]:
    out = set()
    for item in (raw or "").split(","):
        item = item.strip()
        if item:
            out.add(int(item))
    return out

@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    sickw_api_key: str
    sickw_api_url: str
    sickw_service_id: int
    bot_name: str
    check_cost_credits: int
    support_username: str
    admin_ids: set[int]
    database_path: str

def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    api_key = os.getenv("SICKW_API_KEY", "").strip()
    if not token:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en .env")
    if not api_key:
        raise RuntimeError("Falta SICKW_API_KEY en .env")

    return Settings(
        telegram_bot_token=token,
        sickw_api_key=api_key,
        sickw_api_url=os.getenv("SICKW_API_URL", "https://sickw.com/api.php").strip(),
        sickw_service_id=_as_int("SICKW_SERVICE_ID", 61),
        bot_name=os.getenv("BOT_NAME", "Yossimar Premium").strip(),
        check_cost_credits=_as_int("CHECK_COST_CREDITS", 1),
        support_username=os.getenv("SUPPORT_USERNAME", "@soporte").strip(),
        admin_ids=_parse_admins(os.getenv("ADMIN_IDS", "")),
        database_path=os.getenv("DATABASE_PATH", "data/yossimar_bot.sqlite3").strip(),
    )
