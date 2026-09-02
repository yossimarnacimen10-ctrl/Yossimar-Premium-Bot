import re

IMEI_RE = re.compile(r"^\d{15}$")
SERIAL_RE = re.compile(r"^[A-Z0-9]{8,18}$", re.IGNORECASE)

def normalize_identifier(value: str) -> str:
    return re.sub(r"\s+", "", value or "").upper()

def is_valid_identifier(value: str) -> bool:
    value = normalize_identifier(value)
    return bool(IMEI_RE.fullmatch(value) or SERIAL_RE.fullmatch(value))
