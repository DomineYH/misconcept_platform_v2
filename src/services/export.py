"""
CSV export service for session data (T062-T064).

Generates UTF-8 CSV files with anonymized student identifiers
and session summary rows.
"""

import csv
import hashlib
import io
import json
import logging
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.models import GenerationRun, SessionFeedbackReport
from src.models.message import Message
from src.models.question_analysis import QuestionAnalysis
from src.models.session import Session
from src.models.session_summary import SessionSummary
from src.models.user import User
from src.services.analysis_projection import analysis_status
from src.services.analysis_results import analysis_display
from src.services.session_history import session_display

ANALYSIS_COLUMNS = [
    "analysis_schema_version",
    "analysis_status",
    "analysis_coverage_json",
    "misconception_findings_json",
    "message_analysis_disposition",
]

HISTORY_COLUMNS = [
    "student_name",
    "snapshot_origin",
    "snapshot_created_at",
    "source_scenario_version",
    "config_hash_kind",
]


def _history_columns(display):
    return {
        "student_name": CSVExporter._sanitize_csv_value(
            display["student_name"]
        ),
        **{
            key: display["snapshot_provenance"][key]
            for key in HISTORY_COLUMNS[1:]
        },
    }


async def _analysis_exports(db, summaries):
    """Load accepted analysis metadata once per export, including bulk exports."""
    reports = {
        report.session_id: report
        for report in await db.scalars(
            select(SessionFeedbackReport).where(
                SessionFeedbackReport.session_id.in_(summaries)
            )
        )
    }
    runs = {}
    for run in await db.scalars(
        select(GenerationRun)
        .where(
            GenerationRun.session_id.in_(summaries),
            GenerationRun.operation == "analysis",
        )
        .order_by(GenerationRun.started_at.desc(), GenerationRun.id.desc())
    ):
        runs.setdefault(run.session_id, run)
    result = {}
    for sid, summary in summaries.items():
        report, run = reports.get(sid), runs.get(sid)
        payload = json.loads(report.payload_json) if report else {}
        status = analysis_status(summary, report, run, legacy=True)
        is_v2 = report is not None and report.version == 2
        common = dict(
            analysis_schema_version=report.version if report else "unknown",
            analysis_status=status,
        )
        coverage = (
            payload.get("metadata", {}).get("coverage") if is_v2 else None
        )
        summary_columns = dict(
            analysis_coverage_json=(
                json.dumps(coverage, ensure_ascii=False)
                if coverage is not None
                else ""
            ),
            misconception_findings_json=(
                json.dumps(
                    payload.get("misconception_findings", []),
                    ensure_ascii=False,
                )
                if is_v2
                else ""
            ),
        )
        dispositions = (
            {mid: "missing" for mid in coverage.get("missing_message_ids", [])}
            if coverage
            else {}
        )
        if is_v2:
            dispositions.update(
                {
                    item["message_id"]: item["disposition"]
                    for item in payload.get("message_classifications", [])
                }
            )
        result[sid] = common, summary_columns, dispositions
    return result


logger = logging.getLogger(__name__)


class CSVExporter:
    """
    Session data export service with anonymization.

    Generates CSV exports with columns:
    session_id, scenario_title, student_hash, timestamp,
    role, content, label, confidence, feedback
    """

    @staticmethod
    def _anonymize_student(username: str, session_salt: str) -> str:
        """
        Anonymize student identifier using SHA-256 hash.

        Args:
            username: Original student identifier
            session_salt: Session-specific salt

        Returns:
            Hexadecimal hash string (64 characters)
        """
        combined = f"{username}:{session_salt}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    @staticmethod
    def _sanitize_csv_value(value: str) -> str:
        """Sanitize value to prevent CSV formula injection.

        Prefixes cells starting with dangerous characters
        (=, +, -, @, \\t, \\r) with a single quote.
        """
        if not value:
            return value
        if value[0] in ("=", "+", "-", "@", "\t", "\r"):
            return f"'{value}"
        return value

    async def export_session(self, session_id: int, db: AsyncSession) -> str:
        """
        Export single session to CSV format.

        Args:
            session_id: Session identifier
            db: Database session

        Returns:
            CSV content as string

        Raises:
            ValueError: If session not found
        """
        # Load session with relationships
        result = await db.execute(
            select(Session)
            .where(Session.id == session_id)
            .options(
                # Eager load relationships
            )
        )
        session = result.scalar_one_or_none()
        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Load related data
        teacher_result = await db.execute(
            select(User).where(User.id == session.teacher_id)
        )
        teacher = teacher_result.scalar_one()

        display = session_display(session)
        history_columns = _history_columns(display)
        scenario_title = self._sanitize_csv_value(display["scenario_title"])
        classification_enabled, label_names = analysis_display(session)
        classification_status = {
            False: "classification_disabled",
            True: "enabled",
            None: "legacy",
        }[classification_enabled]

        messages_result = await db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at)
        )
        messages = messages_result.scalars().all()

        # Load question analyses
        analyses_result = await db.execute(
            select(QuestionAnalysis)
            .join(Message)
            .where(Message.session_id == session_id)
        )
        analyses = {a.message_id: a for a in analyses_result.scalars().all()}

        # Load session summary
        summary_result = await db.execute(
            select(SessionSummary).where(
                SessionSummary.session_id == session_id
            )
        )
        summary = summary_result.scalar_one_or_none()
        analysis_columns, analysis_summary_columns, dispositions = (
            await _analysis_exports(db, {session_id: summary})
        )[session_id]

        # Generate CSV
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "session_id",
                "scenario_title",
                "student_hash",
                "timestamp",
                "role",
                "content",
                "label",
                "confidence",
                "feedback",
                "classification_status",
                *HISTORY_COLUMNS,
                *ANALYSIS_COLUMNS,
            ],
        )
        writer.writeheader()

        # Anonymize student
        session_salt = str(session.started_at.timestamp())
        student_hash = self._anonymize_student(teacher.username, session_salt)

        # Write message rows
        for msg in messages:
            analysis = analyses.get(msg.id)
            writer.writerow(
                {
                    "session_id": session_id,
                    "scenario_title": scenario_title,
                    "student_hash": student_hash,
                    "timestamp": msg.created_at.isoformat(),
                    "role": msg.role,
                    "content": self._sanitize_csv_value(msg.content),
                    "label": (
                        self._sanitize_csv_value(
                            label_names.get(analysis.label, analysis.label)
                        )
                        if analysis
                        else ""
                    ),
                    "classification_status": classification_status,
                    **history_columns,
                    **analysis_columns,
                    "confidence": (
                        f"{analysis.confidence:.2f}"
                        if analysis and analysis.confidence
                        else ""
                    ),
                    "feedback": "",
                    "message_analysis_disposition": (
                        dispositions.get(msg.id, "unknown")
                        if msg.role == "teacher"
                        and classification_enabled is not False
                        else ""
                    ),
                }
            )

        # Write summary row
        if summary:
            writer.writerow(
                {
                    "session_id": session_id,
                    "scenario_title": scenario_title,
                    "student_hash": student_hash,
                    "timestamp": summary.created_at.isoformat(),
                    "role": "summary",
                    "content": "Session Summary",
                    "label": "",
                    "classification_status": classification_status,
                    **history_columns,
                    **analysis_columns,
                    "confidence": "",
                    **analysis_summary_columns,
                    "feedback": self._sanitize_csv_value(
                        summary.feedback or ""
                    ),
                }
            )

        return output.getvalue()

    async def export_multiple_sessions(
        self, session_ids: List[int], db: AsyncSession
    ) -> str:
        """
        Export multiple sessions to single CSV.

        Args:
            session_ids: List of session identifiers
            db: Database session

        Returns:
            Combined CSV content as string
        """
        all_rows = []

        for session_id in session_ids:
            try:
                csv_content = await self.export_session(session_id, db)
                # Skip header for subsequent sessions
                lines = csv_content.strip().split("\n")
                if not all_rows:
                    all_rows.extend(lines)  # Include header
                else:
                    all_rows.extend(lines[1:])  # Skip header
            except ValueError as e:
                logger.warning(f"Skipping session {session_id}: {e}")
                continue

        return "\n".join(all_rows)

    async def export_session_admin(
        self, session_id: int, db: AsyncSession
    ) -> str:
        """Admin export with raw teacher info and meta_json."""
        result = await db.execute(
            select(Session).where(Session.id == session_id)
        )
        session = result.scalar_one_or_none()
        if not session:
            raise ValueError(f"Session {session_id} not found")

        teacher_result = await db.execute(
            select(User).where(User.id == session.teacher_id)
        )
        teacher = teacher_result.scalar_one()

        display = session_display(session)
        history_columns = _history_columns(display)
        scenario_title = self._sanitize_csv_value(display["scenario_title"])
        classification_enabled, label_names = analysis_display(session)
        classification_status = {
            False: "classification_disabled",
            True: "enabled",
            None: "legacy",
        }[classification_enabled]

        messages_result = await db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at)
        )
        messages = messages_result.scalars().all()

        analyses_result = await db.execute(
            select(QuestionAnalysis)
            .join(Message)
            .where(Message.session_id == session_id)
        )
        analyses = {a.message_id: a for a in analyses_result.scalars().all()}

        summary_result = await db.execute(
            select(SessionSummary).where(
                SessionSummary.session_id == session_id
            )
        )
        summary = summary_result.scalar_one_or_none()
        analysis_columns, analysis_summary_columns, dispositions = (
            await _analysis_exports(db, {session_id: summary})
        )[session_id]

        output = io.StringIO()
        fieldnames = [
            "session_id",
            "scenario_id",
            "scenario_title",
            "teacher_id",
            "teacher_username",
            "teacher_nickname",
            "session_started_at",
            "session_ended_at",
            "message_id",
            "message_created_at",
            "role",
            "content",
            "label",
            "confidence",
            "meta_json",
            "feedback",
            "classification_status",
            *HISTORY_COLUMNS,
            *ANALYSIS_COLUMNS,
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for msg in messages:
            analysis = analyses.get(msg.id)
            writer.writerow(
                {
                    "session_id": session_id,
                    "scenario_id": session.scenario_id,
                    "scenario_title": scenario_title,
                    "teacher_id": teacher.id,
                    "teacher_username": self._sanitize_csv_value(
                        teacher.username
                    ),
                    "teacher_nickname": self._sanitize_csv_value(
                        teacher.nickname
                    ),
                    "session_started_at": session.started_at.isoformat(),
                    "session_ended_at": (
                        session.ended_at.isoformat() if session.ended_at else ""
                    ),
                    "message_id": msg.id,
                    "message_created_at": msg.created_at.isoformat(),
                    "role": msg.role,
                    "content": self._sanitize_csv_value(msg.content),
                    "label": (
                        self._sanitize_csv_value(
                            label_names.get(analysis.label, analysis.label)
                        )
                        if analysis
                        else ""
                    ),
                    "classification_status": classification_status,
                    **history_columns,
                    **analysis_columns,
                    "confidence": (
                        f"{analysis.confidence:.2f}"
                        if analysis and analysis.confidence
                        else ""
                    ),
                    "meta_json": analysis.meta_json if analysis else "",
                    "feedback": "",
                    "message_analysis_disposition": (
                        dispositions.get(msg.id, "unknown")
                        if msg.role == "teacher"
                        and classification_enabled is not False
                        else ""
                    ),
                }
            )

        if summary:
            writer.writerow(
                {
                    "session_id": session_id,
                    "scenario_id": session.scenario_id,
                    "scenario_title": scenario_title,
                    "teacher_id": teacher.id,
                    "teacher_username": self._sanitize_csv_value(
                        teacher.username
                    ),
                    "teacher_nickname": self._sanitize_csv_value(
                        teacher.nickname
                    ),
                    "session_started_at": session.started_at.isoformat(),
                    "session_ended_at": (
                        session.ended_at.isoformat() if session.ended_at else ""
                    ),
                    "message_id": "",
                    "message_created_at": summary.created_at.isoformat(),
                    "role": "summary",
                    "content": "Session Summary",
                    "label": "",
                    "classification_status": classification_status,
                    **history_columns,
                    **analysis_columns,
                    "confidence": "",
                    "meta_json": "",
                    **analysis_summary_columns,
                    "feedback": self._sanitize_csv_value(
                        summary.feedback or ""
                    ),
                }
            )

        return output.getvalue()

    async def export_multiple_sessions_admin(
        self, session_ids: List[int], db: AsyncSession
    ) -> str:
        """Admin bulk export with raw teacher info and meta_json.

        Optimized to batch-load all data in minimal queries to avoid N+1.
        """
        if not session_ids:
            return ""

        # Batch load all sessions with relationships (1 query)
        sessions_result = await db.execute(
            select(Session)
            .where(Session.id.in_(session_ids))
            .options(
                joinedload(Session.teacher),
            )
        )
        sessions = {s.id: s for s in sessions_result.scalars().unique().all()}

        if not sessions:
            return ""

        # Batch load all messages for these sessions (1 query)
        messages_result = await db.execute(
            select(Message)
            .where(Message.session_id.in_(session_ids))
            .order_by(Message.session_id, Message.created_at)
        )
        all_messages = messages_result.scalars().all()

        # Group messages by session
        messages_by_session: dict[int, List[Message]] = {}
        for msg in all_messages:
            messages_by_session.setdefault(msg.session_id, []).append(msg)

        # Batch load all analyses (1 query)
        message_ids = [m.id for m in all_messages]
        if message_ids:
            analyses_result = await db.execute(
                select(QuestionAnalysis).where(
                    QuestionAnalysis.message_id.in_(message_ids)
                )
            )
            analyses = {
                a.message_id: a for a in analyses_result.scalars().all()
            }
        else:
            analyses = {}

        # Batch load all summaries (1 query)
        summaries_result = await db.execute(
            select(SessionSummary).where(
                SessionSummary.session_id.in_(session_ids)
            )
        )
        summaries = {s.session_id: s for s in summaries_result.scalars().all()}
        analysis_exports = await _analysis_exports(
            db, {sid: summaries.get(sid) for sid in sessions}
        )

        # Generate CSV with all pre-loaded data
        output = io.StringIO()
        fieldnames = [
            "session_id",
            "scenario_id",
            "scenario_title",
            "teacher_id",
            "teacher_username",
            "teacher_nickname",
            "session_started_at",
            "session_ended_at",
            "message_id",
            "message_created_at",
            "role",
            "content",
            "label",
            "confidence",
            "meta_json",
            "feedback",
            "classification_status",
            *HISTORY_COLUMNS,
            *ANALYSIS_COLUMNS,
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        # Write rows for each session in order
        for session_id in session_ids:
            session = sessions.get(session_id)
            if not session:
                logger.warning(f"Skipping session {session_id}: not found")
                continue

            teacher = session.teacher
            display = session_display(session)
            history_columns = _history_columns(display)
            scenario_title = self._sanitize_csv_value(display["scenario_title"])
            classification_enabled, label_names = analysis_display(session)
            classification_status = {
                False: "classification_disabled",
                True: "enabled",
                None: "legacy",
            }[classification_enabled]
            session_messages = messages_by_session.get(session_id, [])
            analysis_columns, analysis_summary_columns, dispositions = (
                analysis_exports[session_id]
            )

            for msg in session_messages:
                analysis = analyses.get(msg.id)
                writer.writerow(
                    {
                        "session_id": session_id,
                        "scenario_id": session.scenario_id,
                        "scenario_title": scenario_title,
                        "teacher_id": teacher.id,
                        "teacher_username": self._sanitize_csv_value(
                            teacher.username
                        ),
                        "teacher_nickname": self._sanitize_csv_value(
                            teacher.nickname
                        ),
                        "session_started_at": (session.started_at.isoformat()),
                        "session_ended_at": (
                            session.ended_at.isoformat()
                            if session.ended_at
                            else ""
                        ),
                        "message_id": msg.id,
                        "message_created_at": (msg.created_at.isoformat()),
                        "role": msg.role,
                        "content": self._sanitize_csv_value(msg.content),
                        "label": (
                            self._sanitize_csv_value(
                                label_names.get(analysis.label, analysis.label)
                            )
                            if analysis
                            else ""
                        ),
                        "classification_status": classification_status,
                        **history_columns,
                        **analysis_columns,
                        "confidence": (
                            f"{analysis.confidence:.2f}"
                            if analysis and analysis.confidence
                            else ""
                        ),
                        "meta_json": (analysis.meta_json if analysis else ""),
                        "feedback": "",
                        "message_analysis_disposition": (
                            dispositions.get(msg.id, "unknown")
                            if msg.role == "teacher"
                            and classification_enabled is not False
                            else ""
                        ),
                    }
                )

            # Write summary row if exists
            summary = summaries.get(session_id)
            if summary:
                writer.writerow(
                    {
                        "session_id": session_id,
                        "scenario_id": session.scenario_id,
                        "scenario_title": scenario_title,
                        "teacher_id": teacher.id,
                        "teacher_username": self._sanitize_csv_value(
                            teacher.username
                        ),
                        "teacher_nickname": self._sanitize_csv_value(
                            teacher.nickname
                        ),
                        "session_started_at": (session.started_at.isoformat()),
                        "session_ended_at": (
                            session.ended_at.isoformat()
                            if session.ended_at
                            else ""
                        ),
                        "message_id": "",
                        "message_created_at": (summary.created_at.isoformat()),
                        "role": "summary",
                        "content": "Session Summary",
                        "label": "",
                        "classification_status": classification_status,
                        **history_columns,
                        **analysis_columns,
                        "confidence": "",
                        "meta_json": "",
                        **analysis_summary_columns,
                        "feedback": self._sanitize_csv_value(
                            summary.feedback or ""
                        ),
                    }
                )

        return output.getvalue()
