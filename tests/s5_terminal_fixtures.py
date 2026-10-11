"""Shared saved-state/SSE screen contract for structured comparison results."""

from tests.s4_screen_fixtures import analysis_fixture


def terminal_fixtures():
    def local_ids(value, key=""):
        if isinstance(value, list):
            return [local_ids(item, key) for item in value]
        if isinstance(value, dict):
            return {name: local_ids(item, name) for name, item in value.items()}
        if isinstance(value, int) and (
            key in ("id", "message_id") or key.endswith("_ids")
        ):
            return value - 100
        return value

    fixtures = {
        "mentor_coaching": {
            "should_intervene": True,
            "coaching": "검증된 코칭",
        },
        "mentor_no_intervention": {"should_intervene": False, "coaching": ""},
    }
    for status, state in (("ok", "ok"), ("degraded", "partial")):
        result = local_ids(analysis_fixture(state))
        result["accepted_report"]["coverage"]["chunks"] = []
        fixtures[f"analysis_{status}"] = dict(
            report=result["accepted_report"], messages=result["messages"]
        )
    return fixtures
