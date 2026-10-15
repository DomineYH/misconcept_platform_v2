"""Request-scoped display language shared by templates and route errors."""

import json
from pathlib import Path

from jinja2 import pass_context
from starlette.requests import Request

CATALOG = json.loads(Path(__file__).with_name("catalog.json").read_text())


def display_language(request: Request | None = None) -> str:
    """Valid header > valid cookie > Korean; never retain request state."""
    if request is not None:
        for value in (
            request.headers.get("x-ui-language"),
            request.cookies.get("ui_lang"),
        ):
            if value in ("ko", "en"):
                return value
    return "ko"


def translate(key: str, language: str = "ko", **values) -> str:
    area, _, name = key.partition(".")
    entry = CATALOG.get(area, {}).get(name, {})
    text = (
        entry.get(language) or entry.get("ko") or "표시할 수 없는 안내입니다."
    )
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text


@pass_context
def t(context, key: str, **values) -> str:
    """Return plain text so Jinja autoescapes interpolated user values."""
    return translate(key, display_language(context.get("request")), **values)


def ui_strings(language: str, areas=()) -> dict:
    """Only ship the shared and explicitly requested page sections."""
    return {
        f"{area}.{name}": translate(f"{area}.{name}", language)
        for area in dict.fromkeys(("common", "about", *areas))
        for name in CATALOG.get(area, {})
    }
