"""D2-01: explicit app_mode, demo model/options/limits and local role evidence."""

import inspect
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.api.schemas.ai_settings import CallLimits
from src.api.schemas.scenario_config import ResolvedModelConfig
from src.config import config
from src.services import (
    call_execution,
    lesson_connections,
    model_capabilities,
    model_configuration,
    model_verification,
)
from src.services.invocation_types import InvocationError
from src.services.model_capabilities import capabilities
from src.services.model_verification import (
    connection_ready,
    effective_roles,
    model_available,
)

ROLES = {"student": "s1-v1", "mentor": "s3-v1", "analysis": "s4-v2"}
VALID = {"max_output_tokens": 4096}


def evidence(role, **changes):
    return dict(
        status="succeeded",
        verified_at="2026-10-18T00:00:00Z",
        credential_revision=0,
        connection_version=1,
        capability_definition_version="demo-cap-v1",
        role_contract_version=ROLES[role],
        **changes,
    )


def demo_connection(**changes):
    values = dict(
        id=1,
        provider="demo",
        enabled=True,
        encrypted_key=None,
        nonce=None,
        encryption_key_version=None,
        masked_hint=None,
        credential_revision=0,
        connection_version=1,
        catalog_models_json=[],
    )
    return SimpleNamespace(**values | changes)


def demo_model(**changes):
    values = dict(
        id=1,
        provider_connection_id=1,
        model_id="demo-classroom-v1",
        display_name="준비된 수업 예시 v1",
        enabled=True,
        config_version=1,
        capability_definition_version="demo-cap-v1",
        default_options_json=dict(VALID),
        verification_state={role: evidence(role) for role in ROLES},
    )
    return SimpleNamespace(**values | changes)


def external():
    version = capabilities("openai", "gpt-5-mini")["definition_version"]
    connection = SimpleNamespace(
        id=2,
        provider="openai",
        enabled=True,
        credential_revision=1,
        connection_version=1,
        catalog_models_json=[],
    )
    model = SimpleNamespace(
        id=2,
        provider_connection_id=2,
        model_id="gpt-5-mini",
        display_name="External",
        enabled=True,
        config_version=1,
        capability_definition_version=version,
        default_options_json={},
        verification_state={
            role: dict(
                status="succeeded",
                credential_revision=1,
                connection_version=1,
                capability_definition_version=version,
                role_contract_version=contract,
            )
            for role, contract in ROLES.items()
        },
    )
    return model, connection


@pytest.fixture
def secrets(monkeypatch):
    calls = SimpleNamespace(decrypt=0, external=0)

    def decrypt(connection):
        calls.decrypt += 1
        return "external-key"

    def outbound(*args, **kwargs):
        calls.external += 1
        raise AssertionError("external call")

    monkeypatch.setattr(model_verification, "decrypt_key", decrypt)
    monkeypatch.setattr(call_execution, "execute_call", outbound)
    monkeypatch.setattr(call_execution, "sdk_events", outbound)
    return calls


class FakeDB:
    def __init__(self, model, connection, setting=None):
        self.rows = {
            "ModelConfig": model,
            "ProviderConnection": connection,
            "AppSetting": setting,
        }

    async def get(self, table, _id):
        return self.rows[table.__name__]

    async def scalars(self, _statement):
        return SimpleNamespace(all=lambda: [self.rows["ModelConfig"]])


def selection(**options):
    return ResolvedModelConfig(
        model_config_id=1,
        provider_connection_id=1,
        provider="demo",
        model_id="demo-classroom-v1",
        options=options,
    )


@pytest.mark.parametrize(
    "function",
    [
        connection_ready,
        effective_roles,
        model_available,
        model_capabilities.validate_model_and_options,
        model_configuration.public_model,
        model_configuration.configuration_state,
        lesson_connections.resolve_frozen_model,
    ],
)
def test_app_mode_is_required_keyword_only_without_default(function):
    parameter = inspect.signature(function).parameters["app_mode"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


def test_missing_app_mode_raises_type_error():
    with pytest.raises(TypeError):
        connection_ready(demo_connection())
    with pytest.raises(TypeError):
        connection_ready(demo_connection(), "demo")
    with pytest.raises(TypeError):
        model_capabilities.validate_model_and_options(
            "demo", "demo-classroom-v1", VALID
        )


def test_demo_capability_definition_is_exact():
    definition = capabilities("demo", "demo-classroom-v1")
    assert definition["definition_version"] == "demo-cap-v1"
    assert definition["text"] and definition["streaming"]
    assert definition["structured"]
    assert not (
        definition["tools"] or definition["image"] or definition["audio"]
    )
    assert definition["input_token_limit"] == 65536
    assert definition["max_output_tokens"] == 8192
    assert [
        (f["name"], f["type"], f["min"], f["max"], f["default"])
        for f in definition["fields"]
    ] == [("max_output_tokens", "integer", 4096, 8192, 4096)]
    assert model_capabilities.DEMO_MODEL_ID == "demo-classroom-v1"
    assert model_capabilities.DEMO_MODEL_TITLE == "준비된 수업 예시 v1"
    assert capabilities("demo", "demo-student-v1") is None
    assert capabilities("demo", "gpt-5-mini") is None
    assert capabilities("openai", "demo-classroom-v1") is None


@pytest.mark.parametrize("value", [4096, 8192])
def test_demo_output_bounds_accept(value):
    assert model_capabilities.validate_model_and_options(
        "demo",
        "demo-classroom-v1",
        {"max_output_tokens": value},
        app_mode="demo",
    ) == {"max_output_tokens": value}


@pytest.mark.parametrize(
    "options",
    [
        {"max_output_tokens": 4095},
        {"max_output_tokens": 8193},
        {"max_output_tokens": True},
        {"max_output_tokens": 4096.0},
        {},
        {"max_output_tokens": 4096, "temperature": 1},
        {"max_output_tokens": 4096, "reasoning": {"effort": "low"}},
        {"max_output_tokens": 4096, "thinking": {"type": "disabled"}},
        {"max_output_tokens": 4096, "output_config": {"effort": "low"}},
        {"max_output_tokens": 4096, "service_tier": "flex"},
        {"max_output_tokens": 4096, "unknown": 1},
        None,
    ],
)
def test_demo_options_reject_out_of_contract_values(options):
    with pytest.raises(ValueError):
        model_capabilities.validate_model_and_options(
            "demo", "demo-classroom-v1", options, app_mode="demo"
        )


def test_option_validation_separates_modes():
    with pytest.raises(ValueError):
        model_capabilities.validate_model_and_options(
            "demo", "demo-classroom-v1", VALID, app_mode="normal"
        )
    with pytest.raises(ValueError):
        model_capabilities.validate_model_and_options(
            "demo", "demo-student-v1", VALID, app_mode="demo"
        )
    for provider, model_id in (
        ("openai", "gpt-5-mini"),
        ("anthropic", "claude-sonnet-4-5"),
        ("google", "gemini-2.5-flash"),
    ):
        with pytest.raises(ValueError):
            model_capabilities.validate_model_and_options(
                provider, model_id, {}, app_mode="demo"
            )
    assert (
        model_capabilities.validate_model_and_options(
            "openai", "gpt-5-mini", {}, app_mode="normal"
        )
        == {}
    )


def test_demo_selection_snapshots_default_and_rejects_external_options():
    assert selection().model_dump()["options"] == {"max_output_tokens": 4096}
    assert selection(max_output_tokens=8192).model_dump()["options"] == {
        "max_output_tokens": 8192
    }
    for options in (
        {"temperature": 1.0},
        {"reasoning": {"effort": "low"}},
        {"thinking": {"type": "disabled"}},
        {"max_output_tokens": True},
        {"max_output_tokens": 4096.0},
    ):
        with pytest.raises(ValidationError):
            selection(**options)
    with pytest.raises(ValidationError):
        ResolvedModelConfig(
            model_config_id=1,
            provider_connection_id=1,
            provider="local",
            model_id="demo-classroom-v1",
            options={},
        )


LIMITS = dict(total=4, openai=2, anthropic=2, google=2, admin=1)


def test_call_limits_demo_defaults_to_two_and_is_strict():
    assert CallLimits.model_validate(LIMITS).demo == 2
    assert CallLimits.model_validate(LIMITS | {"demo": 2}).demo == 2
    assert CallLimits.model_validate(LIMITS | {"demo": 9}).demo == 9
    for value in (1, 0, True, 2.0, "2", None):
        with pytest.raises(ValidationError):
            CallLimits.model_validate(LIMITS | {"demo": value})
    with pytest.raises(ValidationError):
        CallLimits.model_validate(LIMITS | {"admin": 4, "total": 4})
    with pytest.raises(ValidationError):
        CallLimits.model_validate(LIMITS | {"total": 1})


def test_demo_ready_and_evidence_never_decrypt_or_call_out(secrets):
    connection, model = demo_connection(), demo_model()
    assert connection_ready(connection, app_mode="demo")
    for role in ROLES:
        assert (
            effective_roles(model, connection, app_mode="demo")[role]["status"]
            == "succeeded"
        )
        assert model_available(model, connection, role, app_mode="demo")
    assert (secrets.decrypt, secrets.external) == (0, 0)


def test_normal_rejects_demo_rows_even_when_present(secrets):
    connection, model = demo_connection(), demo_model()
    assert not connection_ready(connection, app_mode="normal")
    for role in ROLES:
        assert (
            effective_roles(model, connection, app_mode="normal")[role][
                "status"
            ]
            == "stale"
        )
        assert not model_available(model, connection, role, app_mode="normal")
    assert (secrets.decrypt, secrets.external) == (0, 0)


def test_demo_rejects_external_models_and_normal_keeps_them(secrets):
    model, connection = external()
    assert not connection_ready(connection, app_mode="demo")
    assert connection_ready(connection, app_mode="normal")
    for role in ROLES:
        assert not model_available(model, connection, role, app_mode="demo")
        assert model_available(model, connection, role, app_mode="normal")


def test_production_env_and_ambient_config_do_not_select_mode(
    monkeypatch, secrets
):
    monkeypatch.setattr(config, "ENV", "production", raising=False)
    connection, model = demo_connection(), demo_model()
    assert config.TESTING and config.APP_MODE == "normal"
    assert model_available(model, connection, "student", app_mode="demo")
    assert not model_available(model, connection, "student", app_mode="normal")


@pytest.mark.parametrize(
    "role, change",
    [
        (role, change)
        for role in ROLES
        for change in (
            dict(role_contract_version="s1-v0"),
            dict(capability_definition_version="demo-cap-v0"),
            dict(credential_revision=1),
            dict(connection_version=2),
            dict(verified_at="2026-10-19T00:00:00Z"),
            dict(probe_request_id="probe-1"),
        )
    ],
)
def test_evidence_tuple_mismatch_is_stale_version_mismatch(role, change):
    model = demo_model()
    model.verification_state[role] = evidence(role) | change
    for enabled in (False, True):
        connection = demo_connection(enabled=enabled)
        state = effective_roles(model, connection, app_mode="demo")[role]
        assert state["status"] == "stale"
        assert not model_available(model, connection, role, app_mode="demo")
    assert state["error_code"] == "VERSION_MISMATCH"


@pytest.mark.parametrize(
    "connection, model",
    [
        (demo_connection(enabled=False), demo_model()),
        (demo_connection(connection_version=2), demo_model()),
        (demo_connection(credential_revision=1), demo_model()),
        (demo_connection(), demo_model(capability_definition_version="x")),
        (demo_connection(), demo_model(model_id="demo-student-v1")),
        (demo_connection(), demo_model(enabled=False)),
        (demo_connection(), demo_model(default_options_json={})),
    ],
)
def test_connection_and_model_mismatch_is_unavailable(connection, model):
    for role in ROLES:
        assert not model_available(model, connection, role, app_mode="demo")


def test_public_model_reports_mode_specific_state(secrets):
    connection, model = demo_connection(), demo_model()
    shown = model_configuration.public_model(model, connection, app_mode="demo")
    assert shown["provider"] == "demo"
    assert shown["capabilities"]["definition_version"] == "demo-cap-v1"
    assert shown["probe_budgets"] == {}
    assert {v["status"] for v in shown["verification_state"].values()} == {
        "succeeded"
    }
    hidden = model_configuration.public_model(
        model, connection, app_mode="normal"
    )
    assert {v["status"] for v in hidden["verification_state"].values()} == {
        "stale"
    }
    assert (secrets.decrypt, secrets.external) == (0, 0)


async def test_configuration_state_defaults_follow_explicit_mode(secrets):
    setting = SimpleNamespace(
        limits_json=dict(LIMITS),
        timeouts_json=dict(
            connect=2,
            student_first_output=5,
            student_total=15,
            mentor_first_output=5,
            mentor_total=15,
            analysis_total=30,
            model_list_total=5,
        ),
        settings_version=1,
        student_model_config_id=1,
        mentor_model_config_id=1,
        analysis_model_config_id=1,
    )
    connection = demo_connection()
    db = FakeDB(demo_model(), connection, setting)
    demo = await model_configuration.configuration_state(
        db, {"demo": connection}, app_mode="demo"
    )
    normal = await model_configuration.configuration_state(
        db, {"demo": connection}, app_mode="normal"
    )
    assert {d["available"] for d in demo["settings"]["defaults"].values()} == {
        True
    }
    assert {
        d["available"] for d in normal["settings"]["defaults"].values()
    } == {False}
    assert demo["settings"]["limits"]["demo"] == 2
    # The normal administrator form has no demo limit field.
    assert "demo" not in normal["settings"]["limits"]
    assert (secrets.decrypt, secrets.external) == (0, 0)


async def test_frozen_demo_selection_keeps_snapshot_options(secrets):
    db = FakeDB(demo_model(), demo_connection())
    connection, model, options = await lesson_connections.resolve_frozen_model(
        db, selection(), "student", app_mode="demo"
    )
    assert (connection.provider, model.model_id) == (
        "demo",
        "demo-classroom-v1",
    )
    assert options == {"max_output_tokens": 4096}
    for role in ROLES:
        with pytest.raises(InvocationError) as error:
            await lesson_connections.resolve_frozen_model(
                db, selection(), role, app_mode="normal"
            )
        assert error.value.code == "configuration_unavailable"
    assert (secrets.decrypt, secrets.external) == (0, 0)


async def test_frozen_external_selection_rejected_in_demo(secrets):
    model, connection = external()
    db = FakeDB(model, connection)
    frozen = ResolvedModelConfig(
        model_config_id=2,
        provider_connection_id=2,
        provider="openai",
        model_id="gpt-5-mini",
        options={},
    )
    with pytest.raises(InvocationError):
        await lesson_connections.resolve_frozen_model(
            db, frozen, "student", app_mode="demo"
        )
    assert (
        await lesson_connections.resolve_frozen_model(
            db, frozen, "student", app_mode="normal"
        )
    )[2] == {}
