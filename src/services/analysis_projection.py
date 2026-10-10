"""Public v2 report fields for the shared S4 result renderer."""

import json


def public_analysis(
    session, summary, report, messages, labels, enabled, latest, *, admin=False
):
    payload = json.loads(report.payload_json)
    outcome = payload.get("metadata", {}).get("outcome", report.status)
    accepted = None
    if report.status != "failed" and outcome != "no_dialogue":
        total = sum(summary.distribution.values())
        accepted = {
            key: payload[key]
            for key in (
                "schema_version",
                "message_classifications",
                "misconception_findings",
                "brief_feedback",
                "strengths",
                "improvements",
                "dialogue_coaching",
            )
        }
        accepted.update(
            status=report.status,
            created_at=report.created_at.isoformat(),
            classification_enabled=enabled,
            coverage=payload.get("metadata", {}).get("coverage"),
            distribution=(
                [
                    dict(
                        name=labels.get(label, label),
                        count=count,
                        percentage=100 * count / total if total else None,
                    )
                    for label, count in summary.distribution.items()
                ]
                if enabled
                else []
            ),
        )
    latest_run = dict(status=outcome, preserved=False)
    if code := payload.get("metadata", {}).get("error_code"):
        latest_run["error_code"] = code
    if latest is not None and latest.request_id != payload.get(
        "metadata", {}
    ).get("request_id"):
        latest_run.update(
            status="failed",
            preserved=accepted is not None,
            error_code=latest.error_code,
        )
    elif latest is not None and latest.error_code:
        latest_run["error_code"] = latest.error_code
    native = session.snapshot_origin == "native"
    return dict(
        accepted_report=accepted,
        latest_run=latest_run,
        messages=messages,
        plan=None,
        permissions=dict(
            can_analyze=native and report.status == "failed",
            can_retry=native and report.status == "failed",
            can_regenerate=native and admin,
            read_only=not native,
        ),
        actions=dict(
            analyze=(
                f"/admin/sessions/{session.id}/analyze_regenerate"
                if admin
                else f"/sessions/{session.id}/analyze"
            )
        ),
    )
