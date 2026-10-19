"""The demo catalog covers every D0-03 §24 UI key and §11 recovery key."""

import pytest

from src.demo.database import ERRORS
from src.i18n import CATALOG, translate, ui_strings

FALLBACK = "표시할 수 없는 안내입니다."
UI_KEYS = {
    f"demo.{name}"
    for name in (
        "banner content_language supported_only reset_confirm role_deleted "
        "expired start_required tour.start tour.scenario tour.dialogue "
        "tour.analysis tour.admin_csv tour.reset public_link back_to_entry"
    ).split()
}
RECOVERY_KEYS = {
    f"demo.{name}"
    for name in (
        "start_required expired stale_generation stale_role preparing "
        "prepare_failed resetting reset_failed retry_later role_deleted "
        "wait_or_stop action_forbidden csrf_refresh limit_reached capacity "
        "storage_limit upload_limit unavailable supported_only check_input "
        "refresh_state not_found output_failed stopped output_refused"
    ).split()
}
ALL_KEYS = sorted(UI_KEYS | RECOVERY_KEYS)
WORDING = [
    (
        "demo.banner",
        "데모 · 준비된 예시 응답 · 실제 AI 품질 평가용 아님",
        "Demo · Prepared example responses · Not for evaluating real AI quality",
    ),
    (
        "demo.content_language",
        "예시 콘텐츠는 한국어입니다. 표시 언어를 바꾸어도 콘텐츠는 번역되지 않습니다.",
        "Example content is in Korean. Changing the display language does not translate content.",
    ),
    (
        "demo.supported_only",
        "현재 단계의 추천 입력으로 체험해 주세요. 실제 개인정보는 입력하지 마세요.",
        "Use the suggested input for this step. Do not enter real personal information.",
    ),
    (
        "demo.role_deleted",
        "교사 역할이 삭제되었습니다. 내 데모 초기화로 복원하세요.",
        "The teacher role has been deleted. Reset your demo to restore it.",
    ),
    ("demo.reset_confirm", "내 데모 초기화", "Reset my demo"),
    (
        "demo.expired",
        "체험이 만료되었습니다. 새 체험을 시작해 주세요.",
        "Your demo has expired. Start a new demo.",
    ),
]


def test_every_server_recovery_key_is_a_known_recovery_key():
    # A new server recovery key must not ship without a catalog pair.
    produced = {f"demo.{suffix}" for _, _, suffix, _ in ERRORS.values()}
    assert produced <= RECOVERY_KEYS


@pytest.mark.parametrize("key", ALL_KEYS)
def test_demo_entry_has_distinct_non_empty_ko_and_en(key):
    entry = CATALOG["demo"][key.partition(".")[2]]
    assert set(entry) == {"ko", "en"}
    assert all(entry[lang].strip() for lang in ("ko", "en"))
    assert entry["ko"] != entry["en"]


@pytest.mark.parametrize(("key", "ko", "en"), WORDING)
def test_demo_wording_matches_d0_02_section_9(key, ko, en):
    assert translate(key, "ko") == ko
    assert translate(key, "en") == en


@pytest.mark.parametrize("language", ["ko", "en"])
def test_ui_strings_always_include_demo_but_not_other_areas(language):
    payload = ui_strings(language)
    assert all(payload[key] == translate(key, language) for key in ALL_KEYS)
    assert any(key.startswith("common.") for key in payload)
    assert not any(key.startswith("teacher.") for key in payload)


@pytest.mark.parametrize("language", ["ko", "en"])
def test_dotted_name_resolves_after_first_dot(language):
    text = translate("demo.tour.start", language)
    assert text == CATALOG["demo"]["tour.start"][language]
    assert text != FALLBACK
