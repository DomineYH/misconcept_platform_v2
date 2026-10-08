"""Encrypted, verified S1 lesson configurations in isolated test databases."""

import base64

from pydantic import SecretStr
from sqlalchemy import inspect, select

from src.config import config
from src.db.migrations import migrate
from src.models import AppSetting, ModelConfig, ProviderConnection
from src.services.model_capabilities import DEFINITION_VERSION
from src.services.provider_secrets import encrypt_key

LESSON_KEY = "sk-PRIVATE-DB-LESSON-KEY"


async def install_connection(data, monkeypatch):
    monkeypatch.setattr(
        config,
        "PROVIDER_SECRET_ENCRYPTION_KEY",
        SecretStr(base64.b64encode(b"k" * 32).decode()),
    )
    monkeypatch.setattr(
        config, "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION", "test-v1"
    )
    async with data.engine.connect() as conn:
        installed = await conn.run_sync(
            lambda sync: inspect(sync).has_table("model_config")
        )
    if not installed:
        monkeypatch.setattr(migrate, "engine", data.engine)
        await migrate.run_all_migrations()
    connection = await data.db.scalar(
        select(ProviderConnection).where(
            ProviderConnection.provider == "openai"
        )
    )
    if connection is None:
        connection = ProviderConnection(provider="openai")
        data.db.add(connection)
        await data.db.flush()
    connection.credential_revision = 1
    connection.encrypted_key, connection.nonce = encrypt_key(
        connection, LESSON_KEY, 1
    )
    connection.encryption_key_version = "test-v1"
    connection.masked_hint = "-KEY"
    connection.enabled = True
    model = ModelConfig(
        provider_connection_id=connection.id,
        model_id="gpt-5-mini",
        display_name="Lesson student",
        enabled=True,
        capability_definition_version=DEFINITION_VERSION,
        default_options_json={"max_output_tokens": 1024},
        verification_state={
            "student": {
                "status": "succeeded",
                "credential_revision": 1,
                "connection_version": connection.connection_version,
                "capability_definition_version": DEFINITION_VERSION,
                "role_contract_version": "s1-v1",
            }
        },
    )
    data.db.add(model)
    if await data.db.get(AppSetting, 1) is None:
        data.db.add(
            AppSetting(
                id=1,
                limits_json=dict(
                    total=8, openai=4, anthropic=4, google=4, admin=3
                ),
                timeouts_json=dict(
                    connect=5,
                    student_first_output=60,
                    student_total=180,
                    mentor_first_output=60,
                    mentor_total=180,
                    analysis_total=300,
                    model_list_total=30,
                ),
            )
        )
    await data.db.commit()
    return connection, model


async def install_mentor_model(data, connection):
    model = ModelConfig(
        provider_connection_id=connection.id,
        model_id="gpt-5.2",
        display_name="Lesson mentor",
        enabled=True,
        capability_definition_version=DEFINITION_VERSION,
        default_options_json={"max_output_tokens": 1024},
        verification_state={
            "mentor": {
                "status": "succeeded",
                "credential_revision": connection.credential_revision,
                "connection_version": connection.connection_version,
                "capability_definition_version": DEFINITION_VERSION,
                "role_contract_version": "s1-v1",
            }
        },
    )
    data.db.add(model)
    await data.db.commit()
    return model
