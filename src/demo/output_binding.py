"""Bind prepared evidence only to completed turns in the current demo lease."""

from copy import deepcopy

from sqlalchemy import select

from src.demo.database import DemoError
from src.demo.visitors import DemoContext
from src.models import GenerationRun, Message, Session, User
from src.services.analysis_output_contract import MergeAnalysisOutput
from src.services.invocation_types import InvocationError


def _invalid():
    raise InvocationError("invalid_reference")


async def _history(db, context, session_id, selection):
    actor = (
        await db.execute(
            select(User.id, User.role).where(User.id == context.user_id)
        )
    ).first()
    session = (
        await db.execute(
            select(
                Session.teacher_id, Session.ended_at, Session.deleted_at
            ).where(Session.id == session_id)
        )
    ).first()
    if (
        actor is None
        or actor.role != context.role
        or actor.role not in {"admin", "teacher"}
        or session is None
        or session.deleted_at is not None
        or (actor.role == "teacher" and session.teacher_id != actor.id)
        or (selection.role == "analysis" and session.ended_at is None)
    ):
        _invalid()
    messages = list(
        (
            await db.execute(
                select(
                    Message.id,
                    Message.turn_id,
                    Message.turn_index,
                    Message.role,
                    Message.content,
                    Message.created_at,
                    Message.generation_run_id,
                ).where(Message.session_id == session_id)
            )
        ).mappings()
    )
    runs = (
        await db.execute(
            select(
                GenerationRun.id,
                GenerationRun.turn_id,
                GenerationRun.owner_id,
                GenerationRun.finished_at,
                GenerationRun.result_kind,
            ).where(
                GenerationRun.session_id == session_id,
                GenerationRun.operation == "student",
                GenerationRun.status == "completed",
            )
        )
    ).mappings()
    turns = []
    for run in runs:
        teachers = [
            m
            for m in messages
            if m["turn_id"] == run["turn_id"] and m["role"] == "teacher"
        ]
        students = [
            m
            for m in messages
            if m["generation_run_id"] == run["id"] and m["role"] == "student"
        ]
        if len(teachers) != 1 or len(students) != 1:
            _invalid()
        teacher, student = teachers[0], students[0]
        if (
            not run["turn_id"]
            or run["owner_id"] != session.teacher_id
            or run["finished_at"] is None
            or run["result_kind"] != "message"
            or teacher["generation_run_id"] is not None
            or student["turn_id"] != teacher["turn_id"]
            or type(teacher["turn_index"]) is not int
            or teacher["turn_index"] < 1
            or student["turn_index"] != teacher["turn_index"]
            or (teacher["created_at"], teacher["id"])
            >= (student["created_at"], student["id"])
        ):
            _invalid()
        turns.append((teacher, student))
    turns.sort(key=lambda pair: pair[0]["turn_index"])
    for previous, current in zip(turns, turns[1:]):
        if previous[0]["turn_index"] >= current[0]["turn_index"] or (
            previous[1]["created_at"],
            previous[1]["id"],
        ) >= (current[0]["created_at"], current[0]["id"]):
            _invalid()
    return turns


def _references(turns, inputs, selection):
    if (
        set(inputs) != {"A", "B", "C", "common"}
        or any(not isinstance(v, str) or not v.strip() for v in inputs.values())
        or len(set(inputs.values())) != 4
    ):
        raise DemoError("VERSION_MISMATCH")
    refs, unsupported, states = {}, [], {}
    branch, state = None, "initial"
    for teacher, student in turns:
        content = teacher["content"].strip()
        kind = "unsupported"
        if state == "initial":
            kind = next(
                (k for k in ("A", "B", "C") if content == inputs[k]),
                "unsupported",
            )
            if kind != "unsupported":
                branch, state = kind, "after_first"
                refs.update(
                    {"@teacher_first": teacher, "@student_first": student}
                )
        elif state == "after_first" and content == inputs["common"]:
            kind, state = "common", "after_compare"
            refs.update(
                {"@teacher_common": teacher, "@student_common": student}
            )
        if kind == "unsupported":
            unsupported.append((teacher, student))
            number = len(unsupported)
            refs[f"@unsupported_teacher:{number}"] = teacher
            refs[f"@unsupported_student:{number}"] = student
        states[teacher["turn_id"]] = (branch, state, kind)
    expected = (
        branch,
        state,
        "common" if state == "after_compare" else branch or "unsupported",
    )
    if selection.role == "mentor":
        expected = states.get(selection.target_turn_id)
    if expected is None or expected != (
        selection.branch,
        selection.content_state,
        selection.input_kind,
    ):
        _invalid()
    return refs, unsupported


def _message_context(turns, context, validation, phase):
    actual = [
        dict(id=m["id"], role=m["role"], content=m["content"])
        for pair in turns
        for m in pair
    ]
    by_id = {m["id"]: m for m in actual}
    supplied = validation.get("messages")
    if validation.get("actor_id") != context.user_id or not isinstance(
        supplied, list
    ):
        _invalid()
    ids = []
    for message in supplied:
        if (
            not isinstance(message, dict)
            or type(message.get("id")) is not int
            or message != by_id.get(message["id"])
        ):
            _invalid()
        ids.append(message["id"])
    order = {m["id"]: i for i, m in enumerate(actual)}
    if (
        len(set(ids)) != len(ids)
        or ids != sorted(ids, key=order.get)
        or (phase == "single" and supplied != actual)
    ):
        _invalid()
    if phase == "chunk":
        owned = validation.get("owned_message_ids")
        if (
            not isinstance(owned, list)
            or any(type(mid) is not int or mid not in ids for mid in owned)
            or len(set(owned)) != len(owned)
        ):
            _invalid()
    if phase == "merge":
        allowed = validation.get("allowed_evidence")
        if not isinstance(allowed, list):
            _invalid()
        for evidence in allowed:
            if (
                not isinstance(evidence, dict)
                or set(evidence) != {"message_id", "quote"}
                or type(evidence["message_id"]) is not int
                or evidence["message_id"] not in ids
                or not isinstance(evidence["quote"], str)
                or not evidence["quote"]
                or evidence["quote"]
                not in by_id[evidence["message_id"]]["content"]
            ):
                _invalid()
    return set(ids), order


def _resolve(raw, refs):
    if not isinstance(raw, str):
        _invalid()
    if raw in refs:
        return refs[raw]
    if raw in {
        "@teacher_first",
        "@student_first",
        "@teacher_common",
        "@student_common",
    }:
        return None
    if raw.startswith(("@unsupported_teacher:", "@unsupported_student:")):
        number = raw.rsplit(":", 1)[1]
        if (
            number.isascii()
            and number.isdecimal()
            and str(int(number)) == number
            and int(number) > 0
        ):
            return None
    _invalid()


def _project_item(
    item, section, refs, ids, order, validation, phase, unsupported, partial
):
    if not isinstance(item, dict):
        return item
    evidence = (
        item.get("evidence") if section == "misconception_findings" else [item]
    )
    if (
        not isinstance(evidence, list)
        or not evidence
        or any(
            not isinstance(e, dict) or "message_id" not in e for e in evidence
        )
    ):
        return item
    messages = [_resolve(e["message_id"], refs) for e in evidence]
    if any(m is None or m["id"] not in ids for m in messages):
        if phase in {"chunk", "merge"}:
            return None
        _invalid()
    mids = [m["id"] for m in messages]
    if phase == "merge" and any(
        dict(message_id=m["id"], quote=e.get("quote"))
        not in validation["allowed_evidence"]
        for e, m in zip(evidence, messages)
    ):
        return None
    if phase == "chunk":
        owned = set(validation["owned_message_ids"])
        if (
            not owned.intersection(mids)
            or (
                section != "misconception_findings"
                or item.get("kind") != "changed"
            )
            and not set(mids).issubset(owned)
        ):
            return None
        if (
            section == "misconception_findings"
            and item.get("kind") == "changed"
            and not partial
            and (
                len(set(mids)) < 2
                or len(set(mids)) != len(mids)
                or mids != sorted(mids, key=order.get)
            )
        ):
            return None
    for raw, message in zip(evidence, messages):
        role = {
            "misconception_findings": "student",
            "strengths": "teacher",
            "message_classifications": "teacher",
        }.get(section)
        if section == "dialogue_coaching":
            role = item.get("role")
        if section == "misconception_findings" and message["id"] in unsupported:
            _invalid()
        if (
            (role is not None and message["role"] != role)
            or not isinstance(raw.get("quote"), str)
            or not raw["quote"]
            or raw["quote"] not in message["content"]
        ) and not partial:
            _invalid()
    if (
        section == "message_classifications"
        and item["message_id"].startswith("@unsupported_teacher:")
        and (
            item.get("disposition") != "non_analyzable"
            or item.get("rubric_id") is not None
        )
    ):
        _invalid()
    for raw, mid in zip(evidence, mids):
        raw["message_id"] = mid
    if (
        section == "misconception_findings"
        and item.get("kind") == "changed"
        and not partial
    ):
        if phase == "merge":
            evidence.sort(key=lambda e: order[e["message_id"]])
            mids.sort(key=order.get)
        if (
            len(set(mids)) < 2
            or len(set(mids)) != len(mids)
            or mids != sorted(mids, key=order.get)
        ):
            _invalid()
    return item


async def bind_output(
    template,
    *,
    context,
    visitors,
    session_id,
    selection,
    validation_context,
    inputs,
):
    """Project a symbolic template, retaining strict downstream schema validation."""
    if not isinstance(context, DemoContext):
        raise DemoError("SESSION_REQUIRED")
    await visitors.check(context)
    try:
        if (
            type(session_id) is not int
            or session_id < 1
            or selection.role not in {"mentor", "analysis"}
        ):
            _invalid()
        async with context.factory() as db:
            turns = await _history(db, context, session_id, selection)
        refs, unsupported = _references(turns, inputs, selection)
        result = deepcopy(template)
        if selection.role == "mentor":
            return result
        phase = selection.analysis_phase
        if phase not in {"single", "chunk", "merge"}:
            raise DemoError("VERSION_MISMATCH")
        if phase == "merge" and (
            not isinstance(result, dict)
            or result.keys() != MergeAnalysisOutput.model_fields.keys()
        ):
            raise InvocationError("invalid_output")
        ids, order = _message_context(turns, context, validation_context, phase)
        if not isinstance(result, dict):
            return result
        unsupported_students = {student["id"] for _, student in unsupported}
        for section in (
            "message_classifications",
            "misconception_findings",
            "strengths",
            "improvements",
            "dialogue_coaching",
        ):
            if not isinstance(result.get(section), list):
                continue
            result[section] = [
                projected
                for item in result[section]
                if (
                    projected := _project_item(
                        item,
                        section,
                        refs,
                        ids,
                        order,
                        validation_context,
                        phase,
                        unsupported_students,
                        selection.fixture_case == "analysis_partial",
                    )
                )
                is not None
            ]
        if (
            selection.fixture_case == "normal"
            and phase != "merge"
            and validation_context.get("classification_enabled", True)
            and isinstance(result.get("message_classifications"), list)
        ):
            classifications = result["message_classifications"]
            for teacher, _ in unsupported:
                mid = teacher["id"]
                if (
                    mid in ids
                    and mid in validation_context.get("owned_message_ids", ids)
                    and not any(
                        c.get("message_id") == mid
                        for c in classifications
                        if isinstance(c, dict)
                    )
                ):
                    classifications.append(
                        dict(
                            message_id=mid,
                            quote=teacher["content"][:200],
                            disposition="non_analyzable",
                            rubric_id=None,
                            reason="지원하지 않는 교사 입력입니다.",
                        )
                    )
            classifications.sort(
                key=lambda c: (
                    order.get(c.get("message_id"), -1)
                    if isinstance(c, dict)
                    else -1
                )
            )
        return result
    finally:
        await visitors.check(context)
