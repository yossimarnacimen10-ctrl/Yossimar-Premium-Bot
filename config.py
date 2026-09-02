from dataclasses import dataclass
from decimal import Decimal
import os
from dotenv import load_dotenv

load_dotenv()


def _as_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _as_decimal(name: str, default: str) -> Decimal:
    value = os.getenv(name, default).strip()
    return Decimal(value)


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
    support_username: str
    admin_ids: set[int]
    owner_id: int
    client_check_cost_credits: Decimal
    database_path: str
    bac_holder: str
    bac_account: str
    atlantida_holder: str
    atlantida_account: str
    paypal_url: str


def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    api_key = os.getenv("SICKW_API_KEY", "").strip()

    if not token:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en .env")
    if not api_key:
        raise RuntimeError("Falta SICKW_API_KEY en .env")

    admin_ids = _parse_admins(os.getenv("ADMIN_IDS", ""))
    owner_id = _as_int("OWNER_ID", 0)

    if not owner_id and admin_ids:
        owner_id = min(admin_ids)

    if owner_id:
        admin_ids.add(owner_id)

    return Settings(
        telegram_bot_token=token,
        sickw_api_key=api_key,
        sickw_api_url=os.getenv("SICKW_API_URL", "https://sickw.com/api.php").strip(),
        sickw_service_id=_as_int("SICKW_SERVICE_ID", 61),
        bot_name=os.getenv("BOT_NAME", "Yossimar Premium").strip(),
        support_username=os.getenv("SUPPORT_USERNAME", "@soporte").strip(),
        admin_ids=admin_ids,
        owner_id=owner_id,
        client_check_cost_credits=_as_decimal("CLIENT_CHECK_COST_CREDITS", "0.90"),
        database_path=os.getenv("DATABASE_PATH", "data/yossimar_bot.sqlite3").strip(),
        bac_holder=os.getenv("BAC_HOLDER", "").strip(),
        bac_account=os.getenv("BAC_ACCOUNT", "").strip(),
        atlantida_holder=os.getenv("ATLANTIDA_HOLDER", "").strip(),
        atlantida_account=os.getenv("ATLANTIDA_ACCOUNT", "").strip(),
        paypal_url=os.getenv("PAYPAL_URL", "").strip(),
    )
