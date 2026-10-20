"""Synthetic D0-02 seed DB, build receipt and tour content (contract §5/§17).

Build: APP_MODE=demo python -m src.demo.seed build --out DIR
"""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from src.demo.database import DatabaseRoot

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")
MANIFEST = FIXTURES / "manifest.json"
MIGRATIONS = ROOT / "src/db/migrations"
SEED_NAME = "demo-seed-v1.db"
RECEIPT_NAME = "receipt.json"
DB_SCHEMA_VERSION = "037_demo_provider.sql"
# Stored the way SQLAlchemy DateTime keeps UTC values: naive.
SEED_TIME = datetime(2026, 10, 18)
SEED_TIMESTAMP = "2026-10-18T00:00:00Z"
# D0-02 §4 recommended inputs; the selector matches them exactly (§16).
INPUTS = dict(
    A="1/8이 1/4보다 크다고 생각한 이유를 말해 줄래?",
    B="같은 크기의 종이띠에서는 1/4이 1/8보다 커. 이 답을 기억해 봐.",
    C="같은 크기의 종이띠를 4조각과 8조각으로 똑같이 나누면 한 조각은 어느 쪽이 더 클까? 지금 생각을 말해 줄래?",
    common="같은 크기의 종이띠 두 개를 각각 똑같이 4조각과 8조각으로 나누어 한 조각씩 겹쳐 보자. 어느 쪽 조각이 더 크고, 조각 수가 늘면 한 조각의 크기는 어떻게 될까?",
)
# Volatile by design (§17): only the bcrypt salt.
VOLATILE = {"user": {"password_hash"}}
# The builder's exact logical rows; startup rejects any other seed content.
SEED_LOGICAL_SHA256 = (
    "93808c612ae6496a31f100dde1ce5c6780a3a6fd8cd136b82bc871eb6d746141"
)
# Contract §8: what the demo seed stores in AppSetting.
DEMO_LIMITS = dict(total=2, openai=2, anthropic=2, google=2, admin=1, demo=2)
DEMO_TIMEOUTS = dict(
    connect=2,
    student_first_output=5,
    student_total=15,
    mentor_first_output=5,
    mentor_total=15,
    analysis_total=30,
    model_list_total=5,
)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _at(seconds=0):
    return SEED_TIME + timedelta(seconds=seconds)


def _id(name):
    return str(uuid5(NAMESPACE_URL, f"demo-seed-v1/{name}"))


def migration_filenames():
    return ("023_schema_baseline",) + tuple(
        p.name for p in _migration_paths() if p.name[0].isdigit()
    )


def _migration_paths():
    # Mirrors run_all_migrations: baseline plus forward files after 023.
    return sorted(
        [MIGRATIONS / "baseline.sql"]
        + [
            path
            for path in MIGRATIONS.glob("[0-9]*.sql")
            if int(path.name.split("_")[0]) > 23
            and not path.name.endswith("_down.sql")
        ]
    )


def migration_set_sha256():
    """Sorted POSIX path + NUL + bytes + NUL over what the installer applies."""
    digest = hashlib.sha256()
    for path in sorted(
        _migration_paths(), key=lambda p: p.relative_to(ROOT).as_posix()
    ):
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def _manifest():
    raw = MANIFEST.read_bytes()
    return json.loads(raw), _sha(raw)


def _envelope(manifest, key):
    path = FIXTURES / manifest["responses"][f"normal/{key}"]
    return json.loads(path.read_text())


def _bind(template, refs):
    """Seed-only binding: every message_id ref names a row created here."""
    if isinstance(template, list):
        return [_bind(item, refs) for item in template]
    if not isinstance(template, dict):
        return template
    return {
        key: (refs[value].id if key == "message_id" else _bind(value, refs))
        for key, value in template.items()
    }


def logical_data_sha256(path):
    """Row content hash that ignores SQLite pages and VOLATILE columns."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return _logical_digest(db)
    finally:
        db.close()


def _logical_digest(db):
    digest = hashlib.sha256()
    tables = db.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' "
        "AND name != 'sqlite_sequence' ORDER BY name"
    ).fetchall()
    for name, sql in tables:
        columns = [
            r[1]
            for r in db.execute(f'PRAGMA table_info("{name}")')
            if r[1] not in VOLATILE.get(name, ())
        ]
        selected = ",".join(f'"{c}"' for c in columns)
        rows = db.execute(
            f'SELECT {selected} FROM "{name}" ORDER BY rowid'
        ).fetchall()
        digest.update(
            json.dumps(
                [name, sql, columns, [tuple(r) for r in rows]],
                ensure_ascii=False,
            ).encode()
            + b"\n"
        )
    return digest.hexdigest()


async def _populate(db, manifest, manifest_sha256):
    # Imported here: src.config is loaded only after the CLI's mode check.
    import bcrypt
    from sqlalchemy import select, text

    from src.api.schemas.ai_settings import CallLimits, CallTimeouts
    from src.api.schemas.scenario_config import ScenarioConfig
    from src.demo.session import Provenance
    from src.models import (
        ApiUsageLog,
        AppSetting,
        GenerationRun,
        Message,
        ModelConfig,
        ProviderAuditLog,
        ProviderConnection,
        Scenario,
        ScenarioGroup,
        Session,
        SessionFeedbackReport,
        SessionSummary,
        User,
        UserGroup,
    )
    from src.models.demo_metadata import DemoMetadata
    from src.services.analysis_output_contract import UnifiedAnalysisOutput
    from src.services.analysis_statistics import analysis_statistics
    from src.services.generation_runs import digest
    from src.services.invocation_types import empty_usage
    from src.services.lesson_snapshots import (
        canonical_hash,
        read_lesson_snapshot,
        scenario_snapshot,
    )
    from src.services.model_capabilities import (
        DEMO_MODEL_ID,
        DEMO_MODEL_TITLE,
        capabilities,
    )
    from src.services.model_verification import ROLE_CONTRACT_VERSIONS
    from src.services.scenario_publication import publication_errors
    from src.utils.cache import load_prompt_template

    # Installer rows carry their apply time; the seed fixes it (§17).
    await db.execute(
        text("UPDATE _migrations SET applied_at=:at"),
        dict(at=_at().isoformat(sep=" ")),
    )
    for row in await db.scalars(select(ProviderConnection)):
        row.created_at = row.updated_at = _at()
    group = UserGroup(
        name="예시 그룹",
        description="데모 교사에게 배정된 허구의 예시 그룹",
        created_at=_at(),
    )
    db.add(group)
    await db.flush()
    # A random secret nobody keeps: the stored hash never verifies a login.
    unusable = bcrypt.hashpw(
        secrets.token_hex(32).encode(), bcrypt.gensalt()
    ).decode()
    admin, teacher = (
        User(
            username=username,
            nickname=nickname,
            password_hash=unusable,
            role=role,
            group_id=group_id,
            created_at=_at(),
        )
        for username, nickname, role, group_id in (
            ("demo-admin", "데모 관리자", "admin", None),
            ("demo-teacher", "데모 교사", "teacher", group.id),
        )
    )
    db.add_all([admin, teacher])
    connection = ProviderConnection(
        provider="demo",
        credential_revision=0,
        connection_version=1,
        enabled=True,
        created_at=_at(),
        updated_at=_at(),
    )
    db.add(connection)
    await db.flush()
    db.add(
        ProviderAuditLog(
            actor_id=None,
            provider_connection_id=connection.id,
            provider="demo",
            change_kind="demo_seeded",
            previous_credential_revision=0,
            credential_revision=0,
            previous_connection_version=1,
            connection_version=1,
            created_at=_at(),
        )
    )
    definition = capabilities("demo", DEMO_MODEL_ID)
    model = ModelConfig(
        provider_connection_id=connection.id,
        model_id=DEMO_MODEL_ID,
        display_name=DEMO_MODEL_TITLE,
        enabled=True,
        capabilities_json=definition,
        capability_definition_version=definition["definition_version"],
        default_options_json=dict(max_output_tokens=4096),
        verification_state={
            role: dict(
                status="succeeded",
                verified_at=SEED_TIMESTAMP,
                credential_revision=0,
                connection_version=1,
                capability_definition_version=definition["definition_version"],
                role_contract_version=version,
            )
            for role, version in ROLE_CONTRACT_VERSIONS.items()
        },
        config_version=1,
        created_at=_at(),
        updated_at=_at(),
    )
    db.add(model)
    await db.flush()
    setting = await db.get(AppSetting, 1)
    setting.limits_json = CallLimits(**DEMO_LIMITS).model_dump()
    setting.timeouts_json = CallTimeouts(**DEMO_TIMEOUTS).model_dump()
    setting.student_model_config_id = model.id
    setting.mentor_model_config_id = model.id
    setting.analysis_model_config_id = model.id
    setting.updated_at = _at()

    source = json.loads((FIXTURES / "seed/scenario.json").read_text())
    selection = dict(
        model_config_id=model.id,
        provider_connection_id=connection.id,
        provider="demo",
        model_id=DEMO_MODEL_ID,
        options=dict(max_output_tokens=4096),
    )
    config = {
        section: (
            dict(value, resolved_model_config=selection)
            if section in ("student", "mentor", "analysis")
            else value
        )
        for section, value in source["config"].items()
    }
    config = ScenarioConfig.model_validate(config).model_dump()
    scenario = Scenario(
        title=source["title"],
        subject=source["subject"],
        target_grade=source["target_grade"],
        is_active=1,
        status="published",
        config_schema_version=source["config_schema_version"],
        config_version=1,
        config_json=config,
        review_required=False,
        review_reasons=[],
        created_by=admin.id,
        created_at=_at(),
    )
    db.add(scenario)
    await db.flush()
    db.add(
        ScenarioGroup(
            scenario_id=scenario.id, group_id=group.id, created_at=_at()
        )
    )
    snapshot = scenario_snapshot(scenario)
    if publication_errors(snapshot.config):
        raise ValueError("seed scenario is not publishable")
    envelope = snapshot.model_dump()
    session = Session(
        scenario_id=scenario.id,
        teacher_id=teacher.id,
        started_at=_at(60),
        ended_at=_at(300),
        tutor_intervention_count=0,
        tutor_question_count=0,
        config_snapshot_json=envelope,
        config_hash=canonical_hash(envelope),
        source_scenario_version=1,
        snapshot_origin="native",
        snapshot_created_at=_at(60),
    )
    db.add(session)
    await db.flush()

    def usage(run, operation, role, start, finish, **more):
        db.add(
            ApiUsageLog(
                session_id=session.id,
                model=DEMO_MODEL_ID,
                timestamp=_at(start),
                operation=operation,
                invocation_id=_id(f"{run.id}/invocation"),
                request_id=run.request_id,
                run_id=run.id,
                owner_id=teacher.id,
                attempt_no=1,
                provider="demo",
                role=role,
                credential_revision=0,
                status="completed",
                started_at=_at(start),
                first_output_at=_at(finish),
                finished_at=_at(finish),
                **empty_usage(),
                **more,
            )
        )

    refs = {}
    # Default B path, then the common turn (D0-02 §4/§8).
    for index, (kind, state, start) in enumerate(
        (("B", "initial/B/B", 120), ("common", "after_first/B/common", 180)),
        1,
    ):
        turn = _id(f"turn/{index}")
        teacher_message = Message(
            session_id=session.id,
            role="teacher",
            content=INPUTS[kind],
            turn_id=turn,
            turn_index=index,
            created_at=_at(start),
        )
        db.add(teacher_message)
        await db.flush()
        run = GenerationRun(
            id=_id(f"student/{index}"),
            owner_id=teacher.id,
            session_id=session.id,
            turn_id=turn,
            operation="student",
            request_id=_id(f"student/{index}/request"),
            input_hash=digest(
                dict(
                    owner=teacher.id,
                    session=session.id,
                    operation="student",
                    turn_id=None,
                    content=INPUTS[kind],
                )
            ),
            config_hash=session.config_hash,
            provider="demo",
            model=DEMO_MODEL_ID,
            status="completed",
            result_kind="message",
            started_at=_at(start),
            first_output_at=_at(start + 1),
            finished_at=_at(start + 2),
        )
        db.add(run)
        await db.flush()
        usage(run, "student", "student", start, start + 2)
        student_message = Message(
            session_id=session.id,
            role="student",
            content=_envelope(manifest, f"student/{state}/none/none")["text"],
            turn_id=turn,
            turn_index=index,
            generation_run_id=run.id,
            created_at=_at(start + 2),
        )
        db.add(student_message)
        name = "first" if kind == "B" else "common"
        refs[f"@teacher_{name}"] = teacher_message
        refs[f"@student_{name}"] = student_message
        mentor_state = (
            "after_first/B/B" if kind == "B" else "after_compare/B/common"
        )
        coaching = _envelope(manifest, f"mentor/{mentor_state}/auto/none")[
            "structured"
        ]
        mentor = GenerationRun(
            id=_id(f"mentor/{index}"),
            owner_id=teacher.id,
            session_id=session.id,
            turn_id=turn,
            operation="mentor",
            mentor_trigger="auto",
            request_id=_id(f"mentor/{index}/request"),
            input_hash=digest(
                dict(
                    owner=teacher.id,
                    session=session.id,
                    operation="mentor",
                    turn_id=turn,
                    trigger="auto",
                )
            ),
            config_hash=session.config_hash,
            provider="demo",
            model=DEMO_MODEL_ID,
            status="completed",
            result_kind=(
                "message" if coaching["feedback"] else "no_intervention"
            ),
            mentor_reason_summary=coaching["reason_summary"],
            started_at=_at(start + 3),
            first_output_at=_at(start + 4) if coaching["feedback"] else None,
            finished_at=_at(start + 4),
        )
        db.add(mentor)
        await db.flush()
        usage(mentor, "mentor", "mentor", start + 3, start + 4)
        session.tutor_question_count += 1
        if coaching["feedback"]:
            db.add(
                Message(
                    session_id=session.id,
                    role="tutor",
                    content=coaching["feedback"],
                    turn_id=turn,
                    turn_index=index,
                    generation_run_id=mentor.id,
                    created_at=_at(start + 4),
                )
            )
            session.tutor_intervention_count += 1
    await db.flush()

    # Analysis exactly as the pipeline persists an adopted single result.
    lesson = read_lesson_snapshot(session)
    analysis = lesson.config.analysis
    dialogue = list(
        await db.scalars(
            select(Message)
            .where(
                Message.session_id == session.id,
                Message.role.in_(["teacher", "student"]),
            )
            .order_by(Message.created_at, Message.id)
        )
    )
    messages = [dict(id=m.id, role=m.role, content=m.content) for m in dialogue]
    context = dict(
        messages=messages,
        labels={r.id: r.level for r in analysis.rubric},
        classification_enabled=analysis.classification_enabled,
    )
    template = _envelope(
        manifest, "analysis/after_compare/B/common/none/single"
    )
    payload = UnifiedAnalysisOutput.model_validate(
        _bind(template["structured"], refs), context=context
    ).model_dump()
    distribution, questions, coverage, status, error_code = analysis_statistics(
        dialogue, payload, analysis, context, "ok", None
    )
    if status != "ok" or context["analysis_errors"]:
        raise ValueError("seed analysis is not an adopted ok result")
    run_id = _id("analysis")
    request_id = _id("analysis/request")
    prompt_version = _sha(load_prompt_template("analysis_v2.txt").encode())
    # ponytail: build_plan cannot estimate demo requests until #198 registers
    # the demo adapter; plan fields stay null rather than invented.
    payload["metadata"] = dict(
        coverage=coverage,
        mode="single",
        estimator_version=None,
        formula=None,
        plan_hash=None,
        estimates=None,
        request_id=request_id,
        run_id=run_id,
        config_hash=canonical_hash(lesson.model_dump()),
        prompt_version=prompt_version,
        schema_version=2,
        generated_at=_at(401).isoformat(),
        error_code=None,
        outcome="ok",
        # §19: what a visitor run stores from the adapter, fixed as seed.
        provenance=Provenance(record_origin="seed_prepared").model_dump(),
    )
    run = GenerationRun(
        id=run_id,
        owner_id=teacher.id,
        session_id=session.id,
        turn_id=run_id,
        operation="analysis",
        request_id=request_id,
        input_hash=digest(
            dict(
                session_id=session.id,
                actor_id=teacher.id,
                messages=messages,
                snapshot=lesson.model_dump(),
                plan=None,
                regenerate=False,
            )
        ),
        config_hash=digest(lesson.model_dump()),
        provider="demo",
        model=DEMO_MODEL_ID,
        status="ok",
        outcome_json=json.dumps(
            dict(status="ok", error_code=None, coverage=coverage, outcome="ok")
        ),
        started_at=_at(400),
        finished_at=_at(401),
    )
    db.add(run)
    await db.flush()
    usage(run, "analysis_unified", "analysis", 400, 401)
    report = SessionFeedbackReport(
        session_id=session.id,
        version=2,
        model=DEMO_MODEL_ID,
        prompt_hash=prompt_version,
        status="ok",
        payload_json=json.dumps(payload, ensure_ascii=False),
        created_at=_at(401),
    )
    db.add_all(
        [
            *questions,
            report,
            SessionSummary(
                session_id=session.id,
                distribution_json=json.dumps(distribution),
                feedback=payload["brief_feedback"][0],
                created_at=_at(401),
            ),
        ]
    )
    await db.flush()
    run.accepted_report_id = report.id
    run.accepted_report_version = report.version
    db.add(
        DemoMetadata(
            id=1,
            kind="demo_seed",
            contract_version=manifest["contract_version"],
            content_version=manifest["content_version"],
            fixture_version=manifest["fixture_version"],
            db_schema_version=manifest["db_schema_version"],
            manifest_sha256=manifest_sha256,
        )
    )
    await db.flush()
    refs = dict(
        admin=admin.id,
        teacher=teacher.id,
        group=group.id,
        scenario=scenario.id,
        completed_session=session.id,
        model=model.id,
        connection=connection.id,
    )
    if refs != manifest["seed_refs"]:
        raise ValueError("seed rows differ from manifest seed_refs")


async def build(out, *, command=None):
    """Install and fill a new staging DB, then publish it with a receipt."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from src.db.migrations.migrate import run_all_migrations

    manifest, manifest_sha256 = _manifest()
    if (
        manifest["db_schema_version"] != DB_SCHEMA_VERSION
        or manifest["migration_set_sha256"] != migration_set_sha256()
    ):
        raise ValueError("manifest does not match the installed migrations")
    # #191 accepts only absolute paths.
    out = Path(out).absolute()
    out.mkdir(parents=True, exist_ok=True)
    final, staging = out / SEED_NAME, out / f"{SEED_NAME}.staging"
    staging.unlink(missing_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{staging}")
    try:
        await run_all_migrations(db_engine=engine)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            await _populate(db, manifest, manifest_sha256)
            await db.commit()
    finally:
        await engine.dispose()
    db = sqlite3.connect(staging)
    try:
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("VACUUM")
    finally:
        db.close()
    # Publish only what startup accepts: manifest, fixture bytes and rows.
    try:
        # A throwaway root outside out: #191 refuses roots inside the repo.
        with tempfile.TemporaryDirectory() as scratch:
            root = DatabaseRoot(Path(scratch) / "root", initialize=True).open()
            try:
                root.validate_seed(replace(load_seed(out)[0], path=staging))
            finally:
                root.close()
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    os.replace(staging, final)
    receipt = dict(
        source_commit=subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        manifest_sha256=manifest_sha256,
        db_schema_version=DB_SCHEMA_VERSION,
        seed_sha256=_sha(final.read_bytes()),
        logical_data_sha256=logical_data_sha256(final),
        command=command or f"python -m src.demo.seed build --out {out}",
    )
    (out / RECEIPT_NAME).write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    )
    return receipt


def _check(db, manifest):
    """DatabaseRoot verify_content hook: the seed must be the builder's rows."""
    (kind,) = db.execute("SELECT kind FROM demo_metadata WHERE id=1").fetchone()
    # Restart recovery re-verifies retired visitor DBs, which visitors edit;
    # #191 already checks their marker, history and key material.
    if kind != "demo_seed":
        return
    actual = _logical_digest(db)
    if actual != SEED_LOGICAL_SHA256:
        raise ValueError(f"seed content digest {actual}")


@dataclass(frozen=True)
class Tour:
    """TourContent (session.py) for the verified manifest."""

    seed_refs: dict
    first_inputs: frozenset
    common_input: str
    supported_config_sha256: str

    def supported_snapshot(self, row):
        from src.services.lesson_snapshots import canonical_hash
        from src.services.model_configuration import supported_config_sha256

        try:
            envelope = row["config_snapshot_json"]
            if isinstance(envelope, str):
                envelope = json.loads(envelope)
            return bool(
                row["snapshot_origin"] == "native"
                and canonical_hash(envelope) == row["config_hash"]
                and envelope["schema_version"] == 1
                and supported_config_sha256(1, envelope["config"])
                == self.supported_config_sha256
            )
        except (KeyError, TypeError, ValueError):
            return False


def load_seed(seed_dir):
    """SeedSpec plus TourContent for a directory written by build()."""
    from src.demo.database import SeedSpec

    manifest, _ = _manifest()
    spec = SeedSpec(
        path=Path(seed_dir) / SEED_NAME,
        manifest_path=MANIFEST,
        db_schema_version=DB_SCHEMA_VERSION,
        migration_set_sha256=migration_set_sha256(),
        migration_filenames=migration_filenames(),
        verify_content=_check,
    )
    tour = Tour(
        seed_refs=dict(manifest["seed_refs"]),
        first_inputs=frozenset(INPUTS[k] for k in "ABC"),
        common_input=INPUTS["common"],
        supported_config_sha256=manifest["supported_config_sha256"],
    )
    return spec, tour


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # Demo config never absorbs .env and rejects external key/DB env (§4/§14).
    if os.environ.get("APP_MODE") != "demo":
        print(
            json.dumps(dict(code="DEMO_CONFIGURATION_INVALID", exit_status=1))
        )
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build").add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    from src.config import config  # noqa: F401  validates demo settings first

    receipt = asyncio.run(
        build(
            args.out,
            command=" ".join(["python", "-m", "src.demo.seed", *argv]),
        )
    )
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
