"""D2-01 §16.1: supported_config projection and canonical SHA-256."""

import copy

import pytest

from src.api.schemas.scenario_config import ScenarioConfig
from src.services.model_capabilities import validate_model_and_options
from src.services.model_configuration import (
    supported_config_projection,
    supported_config_sha256,
)

VECTOR = {
    "problem": {"public_text": "문제"},
    "student": {"behavior_instruction": "지시"},
    "mentor": {},
    "analysis": {},
    "runtime": {},
}
CANONICAL = (
    '{"config":{"analysis":{"category_name":"","classification_enabled":true,'
    '"context":"","expected_understanding":"","instruction":"",'
    '"resolved_model_config":null,"rubric":[],"rubric_description":"",'
    '"rubric_name":""},"mentor":{"behavior_instruction":"",'
    '"intervention_policy":{"condition":"","max_interventions":3,'
    '"min_interval_turns":2,"start_turn":3,"window_turns":10},'
    '"mode":"manual","name":"멘토","resolved_model_config":null,'
    '"welcome_message":""},"problem":{"learning_objective":"",'
    '"public_text":"문제"},"student":{"behavior_instruction":"지시",'
    '"internal_profile":"","misconception":"","name":"","public_profile":"",'
    '"resolved_model_config":null}},"config_schema_version":1}'
)
DIGEST = "84c3988b5ab0c5ad9aa47e1c7c66edb755909e8b65a820eac9456bcf56dfaadb"


def selection(**changes):
    return (
        dict(
            model_config_id=1,
            provider_connection_id=1,
            provider="demo",
            model_id="demo-classroom-v1",
            options={"max_output_tokens": 4096},
        )
        | changes
    )


def supported():
    config = copy.deepcopy(VECTOR)
    for role in ("student", "mentor", "analysis"):
        config[role]["resolved_model_config"] = selection()
    config["analysis"]["instruction"] = "분석 지시"
    config["analysis"]["rubric"] = [
        {"id": "r1", "name": "전체", "criteria": "같은 전체"},
        {"id": "r2", "name": "조각", "criteria": "한 조각", "level": "high"},
    ]
    return config


def test_canonical_vector_bytes_and_digest():
    import json

    projection = supported_config_projection(1, VECTOR)
    assert (
        json.dumps(
            projection,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        == CANONICAL
    )
    assert supported_config_sha256(1, VECTOR) == DIGEST
    assert supported_config_sha256(
        1, ScenarioConfig.model_validate(VECTOR)
    ) == (DIGEST)


def test_omitted_and_explicit_defaults_and_nulls_hash_equally():
    explicit = copy.deepcopy(VECTOR)
    explicit["mentor"] = {
        "mode": "manual",
        "name": "멘토",
        "welcome_message": "",
        "behavior_instruction": "",
        "intervention_policy": {
            "condition": "",
            "start_turn": 3,
            "min_interval_turns": 2,
            "window_turns": 10,
            "max_interventions": 3,
        },
        "resolved_model_config": None,
    }
    explicit["student"]["resolved_model_config"] = None
    explicit["analysis"] = {"resolved_model_config": None, "rubric": []}
    explicit["runtime"] = {"context_turn_limit": 10}
    assert supported_config_sha256(1, explicit) == DIGEST
    omitted, nulled = supported(), supported()
    nulled["analysis"]["rubric"][0]["level"] = None
    assert supported_config_projection(1, omitted)["config"]["analysis"][
        "rubric"
    ][0] == {"id": "r1", "name": "전체", "criteria": "같은 전체", "level": None}
    assert supported_config_sha256(1, omitted) == supported_config_sha256(
        1, nulled
    )


def test_projection_keeps_only_provider_and_model_id_without_trimming():
    config = supported()
    config["problem"]["public_text"] = "  문제　 "
    projection = supported_config_projection(1, config)
    for role in ("student", "mentor", "analysis"):
        assert projection["config"][role]["resolved_model_config"] == {
            "provider": "demo",
            "model_id": "demo-classroom-v1",
        }
    assert projection["config"]["problem"]["public_text"] == "  문제　 "
    assert "runtime" not in projection["config"]


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c["runtime"].update(context_turn_limit=50),
        lambda c: c["student"]["resolved_model_config"].update(
            model_config_id=7, provider_connection_id=9
        ),
        lambda c: c["mentor"]["resolved_model_config"].update(
            options={"max_output_tokens": 8192}
        ),
        lambda c: c["analysis"]["resolved_model_config"].update(options={}),
    ],
)
def test_runtime_options_and_primary_keys_do_not_change_hash(change):
    config = supported()
    change(config)
    assert supported_config_sha256(1, config) == supported_config_sha256(
        1, supported()
    )


def test_excluded_options_are_still_validated_separately():
    config = supported()
    config["student"]["resolved_model_config"]["options"] = {
        "max_output_tokens": 9000
    }
    assert supported_config_sha256(1, config) == supported_config_sha256(
        1, supported()
    )
    with pytest.raises(ValueError):
        validate_model_and_options(
            "demo",
            "demo-classroom-v1",
            {"max_output_tokens": 9000},
            app_mode="demo",
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c["analysis"].update(instruction="다른 지시"),
        lambda c: c["analysis"]["rubric"][0].update(criteria="다른 기준"),
        lambda c: c["analysis"]["rubric"].reverse(),
        lambda c: c["student"].update(behavior_instruction="다른"),
        lambda c: c["mentor"].update(behavior_instruction="다른"),
        lambda c: c["student"].update(name="학생봇"),
        lambda c: c["student"]["resolved_model_config"].update(
            model_id="demo-student-v1"
        ),
        lambda c: c["mentor"].update(resolved_model_config=None),
    ],
)
def test_content_order_and_model_changes_change_hash(change):
    config = supported()
    change(config)
    assert supported_config_sha256(1, config) != supported_config_sha256(
        1, supported()
    )


@pytest.mark.parametrize("version", [0, 2, True, 1.0, "1", None])
def test_unsupported_config_schema_version_is_invalid_request(version):
    with pytest.raises(ValueError, match="INVALID_REQUEST"):
        supported_config_projection(version, VECTOR)
    with pytest.raises(ValueError, match="INVALID_REQUEST"):
        supported_config_sha256(version, VECTOR)


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c["problem"].update(public_text=1),
        lambda c: c["problem"].update(public_text=None),
        lambda c: c["mentor"].update(mode=None),
        lambda c: c["analysis"].update(classification_enabled=1),
        lambda c: c["problem"].update(extra="x"),
        lambda c: c.update(extra={}),
        lambda c: c.pop("runtime"),
        lambda c: c["student"].update(behavior_instruction="\ud800"),
    ],
)
def test_invalid_config_is_invalid_request_without_hash(change):
    config = copy.deepcopy(VECTOR)
    change(config)
    with pytest.raises(ValueError, match="INVALID_REQUEST"):
        supported_config_sha256(1, config)
