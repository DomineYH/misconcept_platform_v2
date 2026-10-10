"""Bound generation context by completed turn identity, not message order."""

from sqlalchemy import select
from sqlalchemy.orm import aliased

from src.config import config
from src.models import Message


async def load_completed_turns(
    db, session_id, *, before_turn_index=None, limit=None
):
    """Return the last N completed pairs, in teacher–student turn order."""
    students = select(
        Message.turn_id, Message.turn_index, Message.content
    ).where(
        Message.session_id == session_id,
        Message.role == "student",
        Message.turn_id.is_not(None),
        Message.turn_index.is_not(None),
    )
    if before_turn_index is not None:
        students = students.where(Message.turn_index < before_turn_index)
    recent = (
        students.order_by(Message.turn_index.desc())
        .limit(config.CONTEXT_WINDOW_TURNS if limit is None else limit)
        .subquery()
    )
    rows = await db.execute(
        select(Message.content, recent.c.content)
        .join(
            recent,
            (Message.session_id == session_id)
            & (Message.turn_id == recent.c.turn_id)
            & (Message.role == "teacher"),
        )
        .order_by(recent.c.turn_index)
    )
    return [
        {"role": role, "content": content}
        for teacher, student in rows
        for role, content in (("teacher", teacher), ("student", student))
    ]


async def load_mentor_context(db, session_id, turn_id, *, limit=None):
    """Return N preceding completed pairs plus the completed target pair.

    Pass the last pair as current teacher/student and the rest as history to
    TutorBot.generate_feedback. Missing/incomplete targets raise NoResultFound.
    """
    student = aliased(Message)
    teacher, answer, turn_index = (
        await db.execute(
            select(Message.content, student.content, Message.turn_index)
            .join(
                student,
                (student.session_id == Message.session_id)
                & (student.turn_id == Message.turn_id)
                & (student.role == "student"),
            )
            .where(
                Message.session_id == session_id,
                Message.turn_id == turn_id,
                Message.role == "teacher",
                Message.turn_index.is_not(None),
            )
        )
    ).one()
    history = await load_completed_turns(
        db, session_id, before_turn_index=turn_index, limit=limit
    )
    return history + [
        {"role": "teacher", "content": teacher},
        {"role": "student", "content": answer},
    ]
