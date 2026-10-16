"""Load generation context by completed turn identity, not message order."""

from sqlalchemy import select
from sqlalchemy.orm import aliased

from src.models import Message


async def load_completed_turns(db, session_id, *, before_turn_index=None):
    """Return all completed pairs, in teacher–student turn order."""
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
    completed = students.subquery()
    rows = await db.execute(
        select(Message.content, completed.c.content)
        .join(
            completed,
            (Message.session_id == session_id)
            & (Message.turn_id == completed.c.turn_id)
            & (Message.role == "teacher"),
        )
        .order_by(completed.c.turn_index)
    )
    return [
        {"role": role, "content": content}
        for teacher, student in rows
        for role, content in (("teacher", teacher), ("student", student))
    ]


async def load_mentor_context(db, session_id, turn_id):
    """Return all preceding completed pairs plus the completed target pair.

    Mentor budgeting protects the last pair and trims only preceding pairs.
    Missing/incomplete targets raise NoResultFound.
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
        db, session_id, before_turn_index=turn_index
    )
    return history + [
        {"role": "teacher", "content": teacher},
        {"role": "student", "content": answer},
    ]
