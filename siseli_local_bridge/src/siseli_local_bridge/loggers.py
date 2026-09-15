import json
from typing import Literal
from typing import Optional

from .config import LOG_LEVEL_STR

LogLevel = Literal["debug", "info", "warning", "error"]

_LOG_LEVEL_ORDER = {
    "debug": 10,
    "info": 20,
    "warning": 30,
    "error": 40,
}


def _normalize_level(level: str) -> str:
    value = str(level or "").strip().lower()
    if value not in _LOG_LEVEL_ORDER:
        return "info"
    return value


CURRENT_LOG_LEVEL = _normalize_level(LOG_LEVEL_STR)


def _should_log(level: str) -> bool:
    return _LOG_LEVEL_ORDER[_normalize_level(level)] >= _LOG_LEVEL_ORDER[CURRENT_LOG_LEVEL]


def log(message: str, level: LogLevel = "info") -> None:
    if _should_log(level):
        print(message, flush=True)


def log_error_always(message: str) -> None:
    """Always print critical errors regardless of configured log level."""
    print(message, flush=True)


def json_log(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return repr(value)


def log_kv(tag: str, level: LogLevel = "info", **kwargs) -> None:
    if not _should_log(level):
        return
    parts = []
    for key, value in kwargs.items():
        parts.append(f"{key}={json_log(value)}")
    log(f"{tag} " + " ".join(parts), level=level)


def printable_text_preview(data: Optional[bytes], limit: int = 240) -> str:
    if not data:
        return ""
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        text = ""
    text = text.replace("\r", " ").replace("\n", " ").replace("\x00", " ").strip()
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def hex_preview(data: Optional[bytes], limit: int = 240) -> str:
    if not data:
        return ""
    view = data[:limit]
    out = view.hex()
    if len(data) > limit:
        out += "…"
    return out


def log_payload_preview(tag: str, payload: Optional[bytes], level: LogLevel = "info", **kwargs) -> None:
    log_kv(
        tag,
        level=level,
        payload_len=len(payload or b""),
        payload_text=printable_text_preview(payload),
        payload_hex=hex_preview(payload),
        **kwargs,
    )



