"""The shipped catalog and its finite dynamic key families stay in sync."""

import json
import re
from pathlib import Path

from src.api.routes.teacher_language import ERROR_KEYS
from src.i18n import ui_strings
from src.i18n.admin import FIELD_KEYS, REASON_KEYS

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_has_both_languages_matching_parameters_and_no_unused_keys():
    catalog = json.loads((ROOT / "src/i18n/catalog.json").read_text())
    keys = {
        f"{area}.{key}" for area, entries in catalog.items() for key in entries
    }
    sources = "\n".join(
        path.read_text()
        for folder in (ROOT / "src", ROOT / "static/js")
        for path in folder.rglob("*")
        if path.suffix in (".py", ".html", ".js") and path.name != "htmx.min.js"
    )
    literal_keys = set(
        re.findall(
            r"""["']((?:common|login|about|teacher|admin)\.[\w]+)["']""",
            sources,
        )
    )
    called_keys = set(
        re.findall(
            r"""\b(?:t|translate)\(\s*["']((?:common|login|about|teacher|admin)\.[\w]+)["']\s*[,)]""",
            sources,
        )
    )
    # These consumers build keys from finite protocol enums or field mappings.
    dynamic = {
        "common": {"guard_busy", "guard_input", "cookie_failed"},
        "teacher": set(ERROR_KEYS.values())
        | {
            "unknown_scenario_context",
            "unknown_config",
            "unknown_problem",
            "unknown_student",
            "unknown_mentor",
            "unknown_analysis",
            "unknown_runtime",
            "unknown_other",
        },
        "admin": set(FIELD_KEYS.values())
        | set(REASON_KEYS.values())
        | {
            "review_input",
            "rubric_id",
            "name",
            "criteria",
            "level",
            "student",
            "mentor",
            "post_session_analysis",
            "teacher",
            "admin",
            "stop_panel",
            "retry_panel",
            "adopt_student_response",
            "apply_this_model",
            "date_and_time",
            "scenario",
            "messages",
            "download",
            "valid",
            "errors",
            "total",
            "trigger_auto",
            "trigger_manual",
            "decision_admitted",
            "decision_before_start",
            "decision_cap",
            "decision_interval",
            "maximum_output_tokens",
            "temperature",
            "thinking_token_budget_1_auto_0_off",
            "thinking_level",
            "minimal_does_not_guarantee_that_thinking_is_fully_disabled",
            "minimal_is_unsupported",
            "unspecified_uses_dynamic_thinking_1_is_automatic_0_is_off_and_1_24576_is_a_numeric_budget_level_is_u",
            "unspecified_uses_the_provider_default_level_level_and_budget_cannot_be_combined_this_app_edits_gemin",
        },
    }
    dynamic_keys = {
        f"{area}.{name}" for area, names in dynamic.items() for name in names
    }
    assert not (called_keys | dynamic_keys) - keys, "Missing catalog keys"
    assert not keys - (literal_keys | dynamic_keys), "Unused catalog keys"
    for area, entries in catalog.items():
        payloads = {lang: ui_strings(lang, [area]) for lang in ("ko", "en")}
        for name, entry in entries.items():
            key = f"{area}.{name}"
            assert set(entry) == {"ko", "en"}, key
            assert all(
                isinstance(text, str) and text.strip()
                for text in entry.values()
            ), key
            assert set(re.findall(r"\{([^{}]+)\}", entry["ko"])) == set(
                re.findall(r"\{([^{}]+)\}", entry["en"])
            ), key
            for language, payload in payloads.items():
                assert payload[key] == entry[language], key
                assert payload[key] != key
