import re

IMEI_RE = re.compile(r"^\d{15}$")
SERIAL_RE = re.compile(r"^[A-Z0-9]{8,18}$", re.IGNORECASE)

def normalize_identifier(value: str) -> str:
    return re.sub(r"\s+", "", value or "").upper()

def is_valid_identifier(value: str) -> bool:
    value = normalize_identifier(value)
    return bool(IMEI_RE.fullmatch(value) or SERIAL_RE.fullmatch(value))


def is_valid_imei(value: str) -> bool:
    """Valida longitud, dígitos y checksum Luhn antes de llamar a SICKW."""
    imei = normalize_identifier(value)
    if not IMEI_RE.fullmatch(imei):
        return False
    total = 0
    for index, char in enumerate(imei[:14]):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    expected = (10 - (total % 10)) % 10
    return expected == int(imei[14])
