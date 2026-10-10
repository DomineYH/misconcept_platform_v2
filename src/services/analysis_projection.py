"""Public v2 report fields for the shared S4 result renderer."""

import json

from src.utils.session_feedback import FALLBACK_FEEDBACK


def public_analysis(
    session,
    summary,
    report,
    messages,
    labels,
    enabled,
    latest,
    *,
    admin=False,
    run=None,
):
    payload = (
        json.loads(report.payload_json)
        if report
        else dict(brief_feedback=[summary.feedback] if summary else [])
    )
    outcome = payload.get("metadata", {}).get(
        "outcome", report.status if report else "running"
    )
    accepted = None
    if (
        summary is not None
        and outcome != "no_dialogue"
        and (
            (report is not None and report.status != "failed")
            or (report is None and summary.feedback != FALLBACK_FEEDBACK)
        )
    ):
        total = sum(summary.distribution.values())
        accepted = {
            key: payload.get(
                key,
                (
                    []
                    if key != "schema_version"
                    else (report.version if report else 1)
                ),
            )
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
            run_id=payload.get("metadata", {}).get("run_id"),
            status=report.status if report else "legacy",
            created_at=(
                report.created_at if report else summary.created_at
            ).isoformat(),
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
    if run is not None:
        from src.services.analysis_runs import run_state

        latest_run = run_state(run, report)
        latest_run["preserved"] = (
            accepted is not None and accepted.get("run_id") != run.id
        )
    running = latest_run["status"] == "running"
    native = session.snapshot_origin == "native"
    return dict(
        accepted_report=accepted,
        latest_run=latest_run,
        messages=messages,
        plan=None,
        permissions=dict(
            can_analyze=native
            and accepted is None
            and latest_run["status"] in {"failed", "cancelled", "interrupted"},
            can_retry=native
            and not running
            and (accepted["status"] if accepted else latest_run["status"])
            in {"failed", "degraded"},
            can_regenerate=native and admin and not running,
            read_only=not native,
        ),
        actions=dict(
            status=(
                f"/admin/sessions/{session.id}/analysis"
                if admin
                else f"/sessions/{session.id}/analysis"
            ),
            cancel=(
                f"{'/admin' if admin else ''}/sessions/{session.id}/analysis/runs/{run.id}/cancel"
                if run and running
                else None
            ),
            analyze=(
                f"/admin/sessions/{session.id}/analyze_regenerate"
                if admin
                else f"/sessions/{session.id}/analyze"
            ),
        ),
    )
