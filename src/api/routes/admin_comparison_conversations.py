"""Owner-isolated whole scenario comparisons; GET never starts a call."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_admin_user, get_db_session
from src.api.routes.admin_comparisons import (
    DemoComparisonRoute,
    PanelId,
    comparison_factory,
)
from src.api.routes.admin_providers import (
    demo_missing,
    demo_read_only,
    execution_registry,
    execution_scope,
)
from src.api.schemas.comparison_conversation import (
    ConversationAction,
    ConversationAdopt,
    ConversationCreate,
    ConversationMentor,
    ConversationRetry,
    ConversationStop,
    ConversationTurn,
)
from src.i18n import display_language
from src.models import User
from src.services.comparison_conversations import (
    conversation_snapshot,
    create_conversation,
    dialogue_action,
    owned_conversation,
)
from src.services.comparison_stream import ComparisonStreamingResponse

router = APIRouter(
    tags=["Admin Comparison Conversations"], route_class=DemoComparisonRoute
)
BASE = "/admin/comparison-conversations"


def json_result(snapshot, status_code=200):
    return JSONResponse(
        snapshot, status_code=status_code, headers={"Cache-Control": "no-store"}
    )


@router.post(BASE)
async def create(
    body: ConversationCreate,
    user: User = Depends(demo_read_only),
    db: AsyncSession = Depends(get_db_session),
):
    snapshot, created = await create_conversation(
        await comparison_factory(db), user, body
    )
    return json_result(snapshot, 201 if created else 200)


@router.get(BASE + "/{identifier}")
async def get(
    identifier: UUID,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    with demo_missing():
        parent = await owned_conversation(db, str(identifier), user.id)
    return json_result(await conversation_snapshot(db, parent))


async def execute(db, user, identifier, action, body):
    factory = await comparison_factory(db)
    snapshot, executions = await dialogue_action(
        factory, user, str(identifier), action, body
    )
    return (
        ComparisonStreamingResponse(factory, snapshot, executions)
        if executions
        else json_result(snapshot)
    )


@router.post(BASE + "/{identifier}/turn")
async def turn(
    identifier: UUID,
    body: ConversationTurn,
    user: User = Depends(demo_read_only),
    db: AsyncSession = Depends(get_db_session),
):
    return await execute(db, user, identifier, "turn", body)


@router.post(BASE + "/{identifier}/adopt")
async def adopt(
    identifier: UUID,
    body: ConversationAdopt,
    user: User = Depends(demo_read_only),
    db: AsyncSession = Depends(get_db_session),
):
    return await execute(db, user, identifier, "adopt", body)


@router.get(BASE + "/{identifier}/panels/{panel_id}/model")
async def model(
    identifier: UUID,
    panel_id: PanelId,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    from src.services.comparison_conversations import conversation_model

    with demo_missing():
        parent = await owned_conversation(db, str(identifier), user.id)
        return json_result(await conversation_model(db, parent, panel_id))


@router.post(BASE + "/{identifier}/mentor")
async def mentor(
    identifier: UUID,
    body: ConversationMentor,
    user: User = Depends(demo_read_only),
    db: AsyncSession = Depends(get_db_session),
):
    return await execute(db, user, identifier, "mentor", body)


@router.post(BASE + "/{identifier}/end")
async def end(
    identifier: UUID,
    body: ConversationAction,
    user: User = Depends(demo_read_only),
    db: AsyncSession = Depends(get_db_session),
):
    return await execute(db, user, identifier, "end", body)


@router.get(BASE)
async def history(
    cursor: Annotated[str | None, Query(max_length=100)] = None,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    from src.services.comparison_conversation_history import list_conversations

    return json_result(await list_conversations(db, user.id, cursor))


@router.delete(BASE + "/{identifier}", status_code=204)
async def delete(
    request: Request,
    identifier: UUID,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    from fastapi.responses import Response

    from src.services.comparison_conversation_history import delete_conversation

    with demo_missing():
        await delete_conversation(
            db,
            str(identifier),
            user.id,
            scope=execution_scope(request),
            registry=execution_registry(request),
        )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post(BASE + "/{identifier}/children/{child_id}/panels/{panel_id}/retry")
async def retry(
    identifier: UUID,
    child_id: UUID,
    panel_id: PanelId,
    body: ConversationRetry,
    user: User = Depends(demo_read_only),
    db: AsyncSession = Depends(get_db_session),
):
    from src.services.comparison_conversation_recovery import recover_action

    factory = await comparison_factory(db)
    snapshot, executions = await recover_action(
        factory, user, str(identifier), "retry", body, str(child_id), panel_id
    )
    return (
        ComparisonStreamingResponse(factory, snapshot, executions)
        if executions
        else json_result(snapshot)
    )


@router.post(BASE + "/{identifier}/stop")
async def stop(
    request: Request,
    identifier: UUID,
    body: ConversationStop,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    from src.services.comparison_conversation_recovery import recover_action

    factory = await comparison_factory(db)
    with demo_missing():
        snapshot, _ = await recover_action(
            factory,
            user,
            str(identifier),
            "stop",
            body,
            scope=execution_scope(request),
            registry=execution_registry(request),
        )
    return json_result(snapshot)


@router.get("/admin/model-comparison")
async def page(
    request: Request,
    scenario_id: int | None = None,
    conversation_id: UUID | None = None,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    from src.api.dependencies import templates
    from src.api.routes.admin_scenarios import editor_models
    from src.services.scenario_drafts import draft_view, native_scenario

    snapshot = None
    if conversation_id:
        with demo_missing():
            parent = await owned_conversation(db, str(conversation_id), user.id)
        snapshot = await conversation_snapshot(db, parent)
    source = (
        await draft_view(db, await native_scenario(db, scenario_id))
        if scenario_id is not None
        else None
    )
    _, choices = await editor_models(db, display_language(request))
    response = templates.TemplateResponse(
        "admin/comparison_conversation.html",
        dict(
            request=request,
            user=user,
            comparison=dict(
                source=source, model_choices=choices, snapshot=snapshot
            ),
        ),
    )
    response.headers["Cache-Control"] = "no-store"
    return response
