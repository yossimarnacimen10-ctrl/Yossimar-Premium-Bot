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
            "invalid api", "invalid key", "insufficient",
            "not enough balance", "rejected:", "error:", "failed:"
        )
        if any(item in lower for item in obvious_errors):
            raise SickwError(text[:1000])

        return data

def _flatten(obj, prefix=""):
    out = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.extend(_flatten(value, f"{prefix}.{key}" if prefix else str(key)))
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
    ("Modelo", ["model", "product description", "description", "device"]),
    ("IMEI 1", ["imei 1", "imei1", "imei"]),
    ("IMEI 2", ["imei 2", "imei2"]),
    ("Número de Serie", ["serial number", "serial", "sn"]),
    ("Estado de Garantía", ["warranty status", "warranty", "coverage status"]),
    ("Fecha de Compra", ["purchase date", "estimated purchase date"]),
    ("País de Compra", ["purchase country", "country"]),
    ("Dispositivo Demo", ["demo unit", "demo device", "demo"]),
    ("Dispositivo de Préstamo", ["loaner device", "loaner"]),
    ("Reemplazado por Apple", ["replaced device", "replacement", "replaced"]),
    ("Operador", ["locked carrier", "carrier", "network", "sold to name"]),
    ("Simlock Status", ["sim-lock", "simlock", "sim lock"]),
    ("iCloud Lock", ["icloud lock", "find my iphone", "fmi", "find my"]),
    ("Blacklist", ["blacklist"]),
]

def format_yossimar_report(data: dict) -> str:
    normalized = [(k.lower(), _clean(v)) for k, v in _flatten(data) if v is not None]

    for _, value in list(normalized):
        if ":" in value:
            for line in value.splitlines():
                if ":" in line:
                    key, val = line.split(":", 1)
                    normalized.append((key.strip().lower(), val.strip()))

    def find_value(aliases):
        for key, value in normalized:
            last = re.split(r"[.\[\]]+", key)[-1]
            if any(last == alias for alias in aliases):
                return value
        for key, value in normalized:
            if any(alias in key for alias in aliases):
                return value
        return "No disponible"

    lines = ["⚡ *Check Apple Yossimar Premium*", ""]
    for label, aliases in FIELD_ALIASES:
        value = find_value(aliases).replace("*", "").replace("_", " ")

        if label == "Simlock Status":
            low = value.lower()
            if "unlock" in low:
                value = "✅ " + value
            elif "lock" in low and "unlocked" not in low:
                value = "🔒 " + value

        if label == "iCloud Lock" and value != "No disponible":
            if value.lower() in {"off", "clean", "unlocked", "no"}:
                value = "✅ " + value
            else:
                value = "🔒 " + value

        lines.append(f"*{label}:* {value}")

    return "\n".join(lines)

def raw_preview(data: dict) -> str:
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return str(data)
