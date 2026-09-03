import html
import json
import re

import httpx


class SickwError(RuntimeError):
    pass


class SickwClient:
    def __init__(self, base_url: str, api_key: str, service_id: int):
        self.base_url = base_url
        self.api_key = api_key
        self.service_id = service_id

    async def check(self, identifier: str) -> dict:
        params = {
            "format": "json",
            "key": self.api_key,
            "imei": identifier,
            "service": str(self.service_id),
        }

        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.get(self.base_url, params=params)
            response.raise_for_status()

        text = response.text.strip()

        try:
            data = response.json()
        except ValueError:
            data = {"result": text}

        lower = text.lower()
        obvious_errors = (
            "invalid api",
            "invalid key",
            "insufficient",
            "not enough balance",
            "rejected:",
            "error:",
            "failed:",
        )
        if any(item in lower for item in obvious_errors):
            raise SickwError(text[:1000])

        return data


def _flatten(obj, prefix=""):
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


def _clean(value) -> str:
    value = html.unescape(str(value))
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    return value.strip()


FIELD_ALIASES = [
    ("Modelo", ["model description", "model", "product description", "description", "device"]),
    ("IMEI 1", ["imei 1", "imei1", "imei number", "imei"]),
    ("IMEI 2", ["imei 2", "imei2"]),
    ("Número de Serie", ["serial number", "serial", "sn"]),
    ("Estado de Garantía", ["warranty status", "warranty status description", "warranty", "coverage status"]),
    ("Fecha de Compra", ["purchase date", "estimated purchase date"]),
    ("País de Compra", ["purchase country", "purchase country desc", "country"]),
    ("Dispositivo Demo", ["demo unit", "demo device", "demo"]),
    ("Dispositivo de Préstamo", ["loaner device", "loaner"]),
    ("Reemplazado por Apple", ["replaced device", "replacement device", "replacement", "replaced"]),
]

OPERATOR_ALIASES = [
    "locked carrier",
    "carrier",
    "network",
    "sold to name",
]

SIMLOCK_ALIASES = ["sim-lock status", "sim-lock", "simlock status", "simlock", "sim lock"]
ICLOUD_ALIASES = ["icloud lock", "find my iphone", "fmi", "find my"]
BLACKLIST_ALIASES = ["blacklist", "blacklist status"]


def _normalized_pairs(data: dict):
    normalized = []

    for key, value in _flatten(data):
        if value is None:
            continue

        clean_key = str(key).strip().lower()
        clean_value = _clean(value)
        normalized.append((clean_key, clean_value))

        # Algunos servicios SICKW devuelven todos los datos dentro de
        # un único string "result". Convertimos cada "Campo: Valor"
        # en un par adicional para poder encontrarlo de forma exacta.
        if ":" in clean_value:
            for line in clean_value.splitlines():
                if ":" not in line:
                    continue
                sub_key, sub_value = line.split(":", 1)
                normalized.append(
                    (sub_key.strip().lower(), sub_value.strip())
                )

    return normalized


def _find_value(normalized, aliases):
    # Respeta estrictamente el orden de aliases.
    # Primero busca coincidencia exacta para evitar que un campo genérico
    # "carrier" gane sobre "locked carrier".
    for alias in aliases:
        alias = alias.lower().strip()

        for key, value in normalized:
            key_clean = key.lower().strip()
            last = re.split(r"[.\[\]]+", key_clean)[-1]

            if key_clean == alias or last == alias:
                return value

    return "No disponible"


def _carrier_company(operator_value: str) -> str:
    if not operator_value or operator_value == "No disponible":
        return "No disponible"

    low = operator_value.lower()

    carriers = [
        ("Verizon", ("verizon", "visible")),
        ("AT&T", ("at&t", "att-", "att ", "at&t mobility")),
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

    for display_name, needles in carriers:
        if any(needle in low for needle in needles):
            return display_name

    return "No disponible"


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
    simlock = simlock.replace("*", "").replace("_", " ")
    low = simlock.lower()
    if "unlock" in low:
        simlock = "✅ " + simlock
    elif "lock" in low and "unlocked" not in low:
        simlock = "🔒 " + simlock
    lines.append(f"*Simlock Status:* {simlock}")

    icloud = _find_value(normalized, ICLOUD_ALIASES)
    icloud = icloud.replace("*", "").replace("_", " ")
    if icloud != "No disponible":
        if icloud.lower() in {"off", "clean", "unlocked", "no"}:
            icloud = "✅ " + icloud
        else:
            icloud = "🔒 " + icloud
    lines.append(f"*iCloud Lock:* {icloud}")

    blacklist = _find_value(normalized, BLACKLIST_ALIASES)
    blacklist = blacklist.replace("*", "").replace("_", " ")
    lines.append(f"*Blacklist:* {blacklist}")

    return "\n".join(lines)


def raw_preview(data: dict) -> str:
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return str(data)
