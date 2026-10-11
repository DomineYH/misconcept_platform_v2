"""Native analysis configuration for existing runtime and transaction checks."""

import copy
from datetime import datetime

from lesson_fixtures import install_connection, install_snapshot

from src.services.lesson_snapshots import canonical_hash


async def install_analysis_snapshot(data, monkeypatch):
    connection, model = await install_connection(data, monkeypatch)
    model.verification_state = {
        **model.verification_state,
        "analysis": {
            **model.verification_state["student"],
            "role_contract_version": "s4-v2",
        },
    }
    await install_snapshot(data, connection, model)
    envelope = copy.deepcopy(data.session.config_snapshot_json)
    envelope["config"]["analysis"].update(
        classification_enabled=True,
        rubric_name="Test",
        rubric=[
            dict(id="A", name="A", criteria="Explore", level=None),
            dict(id="B", name="B", criteria="Tell", level=None),
        ],
    )
    data.session.config_snapshot_json = envelope
    data.session.config_hash = canonical_hash(envelope)
    data.session.ended_at = datetime(2026, 1, 2)
    await data.db.commit()
    return connection, model
