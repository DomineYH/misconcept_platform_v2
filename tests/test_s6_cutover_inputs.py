"""S6 refuses unsafe paths, unsupported states and unfinished work."""

import os
import sqlite3

import pytest
from fastapi import HTTPException
from test_s6_cutover import current_source, legacy_source

from src.db.s2_verification import database_state, readonly
from src.db.s6_verification import upgrade

__all__ = ["current_source", "legacy_source"]


@pytest.mark.parametrize(
    "kind",
    [
        "symlink",
        "parent_symlink",
        "hardlink",
        "public_source",
        "unreadable_source",
        "default_name",
        "inside_workspace",
        "public_workspace",
        "workspace_symlink",
        "existing_file",
        "artifact_symlink",
        "artifact_hardlink",
        "public_artifact",
    ],
)
async def test_unsafe_paths_and_permissions_are_refused_without_overwrite(
    tmp_path, kind
):
    from src.db.s6_cutover import rehearse

    source = tmp_path / "source.db"
    with sqlite3.connect(source):
        pass
    source.chmod(0o600)
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    original = source.read_bytes()
    if kind == "symlink":
        alias = tmp_path / "alias.db"
        alias.symlink_to(source)
        source = alias
    elif kind == "parent_symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        source = alias / "source.db"
    elif kind == "hardlink":
        os.link(source, tmp_path / "alias.db")
    elif kind == "public_source":
        source.chmod(0o644)
    elif kind == "unreadable_source":
        source.chmod(0o200)
    elif kind == "default_name":
        source = source.rename(tmp_path / "dialogue_sim.db")
    elif kind == "inside_workspace":
        source = source.rename(workspace / "source.db")
    elif kind == "public_workspace":
        workspace.chmod(0o755)
    elif kind == "workspace_symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(workspace, target_is_directory=True)
        workspace = alias
    else:
        artifact = workspace / "backup.db"
        if kind == "artifact_symlink":
            artifact.symlink_to(source)
        elif kind == "artifact_hardlink":
            os.link(source, artifact)
        else:
            artifact.write_bytes(b"existing private bytes")
            artifact.chmod(0o644 if kind == "public_artifact" else 0o600)
    with pytest.raises(ValueError, match="isolated|private"):
        await rehearse(source, workspace, apply=True)
    source.chmod(0o600)
    assert source.read_bytes() == original
    if kind in {"existing_file", "public_artifact"}:
        assert (
            workspace / "backup.db"
        ).read_bytes() == b"existing private bytes"


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
@pytest.mark.parametrize(
    "kind",
    [
        "session",
        "student",
        "mentor",
        "analysis",
        "usage",
        "comparison",
        "probe",
    ],
)
async def test_unfinished_work_is_refused_including_deleted_sessions(
    current_source, tmp_path, kind
):
    from src.db.s6_cutover import rehearse

    with sqlite3.connect(current_source) as db:
        if kind == "session":
            db.execute(
                "UPDATE session SET ended_at=NULL,deleted_at='2026-01-02'"
            )
        elif kind in {"student", "mentor", "analysis"}:
            db.execute(
                "UPDATE generation_run SET operation=?,status='running',finished_at=NULL",
                (kind,),
            )
        elif kind == "usage":
            db.execute(
                "UPDATE api_usage_log SET status='completed',started_at='2026-01-01',finished_at=NULL"
            )
        elif kind == "probe":
            db.execute(
                "INSERT INTO model_config(id,provider_connection_id,model_id,display_name) VALUES(1,1,'synthetic','Synthetic')"
            )
            db.execute(
                "INSERT INTO model_probe(id,owner_id,model_config_id,role,request_id,fingerprint,credential_revision,connection_version,config_version,capability_definition_version,role_contract_version,options_json,status,started_at) VALUES(1,1,1,'student','probe','hash',0,1,1,'synthetic','synthetic','{}','verifying','2026-01-01')"
            )
        else:
            db.execute(
                "INSERT INTO comparison_run(id,admin_id,target_role,mode,source_snapshot,source_hash,canonical_context,context_hash,panel_configs,request_id,input_hash,created_at,expires_at,status) VALUES('pending',3,'student','same_context','{}','hash','[]','context','[]','request','input','2026-01-01','2026-01-31','running')"
            )
        db.commit()
    with readonly(current_source) as db:
        before = database_state(db)
    with pytest.raises(ValueError, match="active_sessions|unfinished_runs"):
        await rehearse(current_source, tmp_path / "s6", apply=True)
    with readonly(current_source) as db:
        assert database_state(db) == before


@pytest.mark.parametrize("data", ["baseline"], indirect=True)
@pytest.mark.parametrize(
    "kind",
    [
        "snapshot",
        "config",
        "unknown_history",
        "gapped_history",
        "custom_table",
        "custom_index",
        "foreign_key",
    ],
)
async def test_invalid_current_state_is_refused_without_source_changes(
    current_source, tmp_path, kind
):
    from src.db.s6_cutover import rehearse

    with sqlite3.connect(current_source) as db:
        if kind == "snapshot":
            db.execute("UPDATE session SET config_hash='corrupted'")
        elif kind == "config":
            db.execute("UPDATE scenario SET config_json='{}'")
        elif kind == "unknown_history":
            db.execute(
                "INSERT INTO _migrations(filename) VALUES('999_private_unknown.sql')"
            )
        elif kind == "gapped_history":
            db.execute(
                "DELETE FROM _migrations WHERE filename='034_analysis_run.sql'"
            )
        elif kind == "custom_table":
            db.execute("CREATE TABLE private_custom(value TEXT)")
        elif kind == "custom_index":
            db.execute("CREATE INDEX private_custom ON message(content)")
        else:
            db.execute("UPDATE session SET teacher_id=999")
        db.commit()
    with readonly(current_source) as db:
        before = database_state(db)
    with pytest.raises((ValueError, HTTPException)):
        await rehearse(current_source, tmp_path / "s6", apply=True)
    with readonly(current_source) as db:
        assert database_state(db) == before


@pytest.mark.parametrize("revision", range(23, 31))
async def test_legacy_sources_require_the_separate_legacy_path(
    tmp_path, revision
):
    from src.db.s6_cutover import rehearse

    source = tmp_path / "source.db"
    await upgrade(source, revision)
    source.chmod(0o600)
    with readonly(source) as db:
        before = database_state(db)
    with pytest.raises(ValueError, match="legacy_path_required"):
        await rehearse(source, tmp_path / "s6", apply=True)
    with readonly(source) as db:
        assert database_state(db) == before
