"""Temporary S1 resolver: preserve legacy model IDs and per-call options."""

from sqlalchemy import select

from src.models import ModelConfig, ProviderConnection
from src.services.invocation_types import InvocationError
from src.services.model_capabilities import validate_model_and_options
from src.services.model_verification import model_available


async def resolve_lesson_model(db, model_id, role, options):
    connection = await db.scalar(
        select(ProviderConnection).where(
            ProviderConnection.provider == "openai"
        )
    )
    model = None
    if connection is not None:
        model = await db.scalar(
            select(ModelConfig).where(
                ModelConfig.provider_connection_id == connection.id,
                ModelConfig.model_id == model_id,
            )
        )
    if model is None or not model_available(model, connection, role):
        raise InvocationError("configuration_unavailable")
    try:
        validated = validate_model_and_options("openai", model_id, options)
    except ValueError:
        raise InvocationError("configuration_unavailable") from None
    return connection, model, validated


async def resolve_frozen_model(db, selection, role):
    """Current registration/role authorization using the saved effective options."""
    model = (
        await db.get(ModelConfig, selection.model_config_id)
        if selection is not None
        else None
    )
    connection = (
        await db.get(ProviderConnection, model.provider_connection_id)
        if model is not None
        else None
    )
    if (
        model is None
        or connection is None
        or selection.provider_connection_id != connection.id
        or selection.provider != connection.provider
        or selection.model_id != model.model_id
    ):
        raise InvocationError("configuration_unavailable")
    options = selection.options.model_dump(exclude_unset=True)
    if not model_available(model, connection, role, options=options):
        raise InvocationError("configuration_unavailable")
    return (
        connection,
        model,
        validate_model_and_options(
            connection.provider, model.model_id, options
        ),
    )
