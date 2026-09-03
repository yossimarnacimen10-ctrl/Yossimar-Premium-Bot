import html
import json
import re
from typing import Any

import httpx


class SickwError(RuntimeError):
    pass


class SickwClient:
    def __init__(self, base_url: str, api_key: str, service_id: int):
        self.base_url = (base_url or "").strip()
        self.api_key = (api_key or "").strip()
        self.service_id = int(service_id)

    async def check(self, identifier: str) -> dict:
        identifier = str(identifier or "").strip()

        params = {
            "format": "json",
            "key": self.api_key,
            "imei": identifier,
            "service": str(self.service_id),
        }

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.get(self.base_url, params=params)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SickwError(f"Error de conexión con SICKW: {exc}") from exc

        text = response.text.strip()

        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError):
            raise SickwError("SICKW devolvió una respuesta no válida.")

        if not isinstance(data, dict):
            raise SickwError("SICKW devolvió un formato inesperado.")

        lower = text.lower()
        obvious_errors = (
            "invalid key",
            "insufficient",
            "not enough balance",
            "rejected",
            "failed",
            "invalid imei",
            "invalid serial",
        )
        if any(err in lower for err in obvious_errors):
            message = (
                data.get("message")
                or data.get("error")
                or data.get("result")
                or "Consulta rechazada por SICKW."
            )
            raise SickwError(str(message))

        return data


def _flatten(obj: Any, prefix: str = ""):
    out = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_flatten(value, child))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            out.extend(_flatten(value, f"{prefix}[{i}]"))
    else:
        out.append((prefix, obj))

    return out


def _clean(value: Any) -> str:
    if value is None:
        return ""
    value = html.unescape(str(value))
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    return value.strip()


FIELD_ALIASES = [
    ("Modelo", (
        "model description", "model", "product description",
        "description", "device", "device model", "product"
    )),
    ("IMEI 1", (
        "imei 1", "imei1", "imei number", "imei", "primary imei"
    )),
    ("IMEI 2", (
        "imei 2", "imei2", "secondary imei", "imei 2 number"
    )),
    ("Número de Serie", (
        "serial number", "serial", "sn", "serial no", "serialnumber"
    )),
    ("Estado de Garantía", (
        "warranty status", "warranty status description",
        "warranty", "coverage status", "coverage"
    )),
    ("Fecha de Compra", (
        "purchase date", "estimated purchase date",
        "purchase_date", "purchase date estimated"
    )),
    ("País de Compra", (
        "purchase country", "purchase country desc",
        "country", "country of purchase"
    )),
    ("Dispositivo Demo", (
        "demo unit", "demo device", "demo"
    )),
    ("Dispositivo de Préstamo", (
        "loaner device", "loaner", "loan device"
    )),
    ("Reemplazado por Apple", (
        "replaced device", "replacement device",
        "replacement", "replaced", "apple replacement"
    )),
]

OPERATOR_ALIASES = (
    "locked carrier", "carrier", "network", "sold to name",
    "operator", "sim carrier", "carrier policy", "next tether policy",
    "initial activation policy", "activation policy"
)

SIMLOCK_ALIASES = (
    "sim-lock status", "sim-lock", "simlock status",
    "simlock", "sim lock", "sim lock status", "carrier lock"
)

ICLOUD_ALIASES = (
    "icloud lock", "find my iphone", "find my",
    "fmi", "fmi status", "findmyiphone"
)

BLACKLIST_ALIASES = (
    "blacklist", "blacklist status", "blacklisted",
    "gsma blacklist", "lost stolen", "lost/stolen",
    "lost or stolen", "device status"
)


def _normalized_pairs(data: dict):
    normalized = []

    for key, value in _flatten(data):
        if value is None:
            continue

        clean_key = str(key).strip().lower()
        clean_value = _clean(value)
        normalized.append((clean_key, clean_value))

        # Algunas respuestas SICKW meten "Campo: Valor" dentro de result/message.
        if ":" in clean_value:
            for line in clean_value.splitlines():
                if ":" not in line:
                    continue
                sub_key, sub_value = line.split(":", 1)
                normalized.append((
                    sub_key.strip().lower(),
                    sub_value.strip()
                ))

    return normalized


def _key_tail(key: str) -> str:
    return re.split(r"[.\[\]]+", key.lower().strip())[-1].strip()


def _find_value(normalized, aliases, default="No disponible"):
    # 1. Coincidencia exacta: evita que "carrier" gane a "locked carrier".
    for alias in aliases:
        alias = alias.lower().strip()
        for key, value in normalized:
            key_clean = key.lower().strip()
            if key_clean == alias or _key_tail(key_clean) == alias:
                if value:
                    return value

    # 2. Coincidencia flexible, solo si no hubo exacta.
    for alias in aliases:
        alias = alias.lower().strip()
        for key, value in normalized:
            if alias in key.lower() and value:
                return value

    return default


def _carrier_company(operator_value: str) -> str:
    if not operator_value or operator_value == "No disponible":
        return "No disponible"

    low = operator_value.lower()

    carriers = [
        ("Verizon", ("verizon", "visible")),
        ("AT&T", ("at&t", "at&t mobility", "att mobility", "att-", "att ")),
        ("T-Mobile", ("t-mobile", "tmobile")),
        ("Metro by T-Mobile", ("metro pcs", "metropcs", "metro by t-mobile")),
        ("Cricket", ("cricket",)),
        ("Boost Mobile", ("boost",)),
        ("Sprint", ("sprint",)),
        ("US Cellular", ("us cellular", "u.s. cellular")),
        ("Xfinity Mobile", ("xfinity",)),
        ("Spectrum Mobile", ("spectrum",)),
        ("TracFone", ("tracfone",)),
        ("Straight Talk", ("straight talk",)),
    ]

    # Primero las marcas más específicas.
    for display_name, needles in carriers:
        if any(needle in low for needle in needles):
            return display_name

    # "2303 - Multi-Mode Unlock" es una política, no una compañía.
    return "No disponible"


def _decorate_simlock(value: str) -> str:
    if not value or value == "No disponible":
        return "No disponible"

    clean = value.replace("*", "").replace("_", " ").strip()
    low = clean.lower()

    unlocked_terms = ("unlocked", "unlock", "no sim restrictions", "no restrictions")
    locked_terms = ("locked", "lock", "sim locked")

    if any(term in low for term in unlocked_terms):
        return "✅ " + clean
    if any(term in low for term in locked_terms):
        return "🔒 " + clean
    return clean


def _decorate_icloud(value: str) -> str:
    if not value or value == "No disponible":
        return "No disponible"

    clean = value.replace("*", "").replace("_", " ").strip()
    low = clean.lower()

    off_terms = {"off", "clean", "unlocked", "no", "false", "disabled", "0"}
    on_terms = {"on", "locked", "yes", "true", "enabled", "1"}

    if low in off_terms:
        return "✅ " + clean
    if low in on_terms:
        return "🔒 " + clean
    return clean


def _decorate_blacklist(value: str) -> str:
    if not value or value == "No disponible":
        return "No disponible"

    clean = value.replace("*", "").replace("_", " ").strip()
    low = clean.lower()

    clean_terms = ("clean", "not blacklisted", "not listed", "no", "false")
    bad_terms = ("blacklisted", "lost", "stolen", "blocked", "yes", "true")

    if any(term == low or term in low for term in clean_terms):
        return "✅ " + clean
    if any(term == low or term in low for term in bad_terms):
        return "⚠️ " + clean
    return clean


def format_yossimar_report(data: dict) -> str:
    normalized = _normalized_pairs(data)

    lines = ["⚡ *Check Apple Yossimar Premium*", ""]

    for label, aliases in FIELD_ALIASES:
        value = _find_value(normalized, aliases)
        value = value.replace("*", "").replace("_", " ")
        lines.append(f"*{label}:* {value}")

    operator_value = _find_value(normalized, OPERATOR_ALIASES)
    operator_value = operator_value.replace("*", "").replace("_", " ")
    company = _carrier_company(operator_value)

    lines.append(f"*Compañía:* {company}")
    lines.append(f"*Operador:* {operator_value}")

    simlock = _find_value(normalized, SIMLOCK_ALIASES)
    lines.append(f"*Simlock Status:* {_decorate_simlock(simlock)}")

    icloud = _find_value(normalized, ICLOUD_ALIASES)
    lines.append(f"*iCloud Lock:* {_decorate_icloud(icloud)}")

    blacklist = _find_value(normalized, BLACKLIST_ALIASES)
    lines.append(f"*Blacklist:* {_decorate_blacklist(blacklist)}")

    return "\n".join(lines)


def raw_preview(data: dict) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return str(data)
