"""Public saved report fields for the shared S4 result renderer."""

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
    plan=None,
):
    payload = (
        json.loads(report.payload_json)
        if report
        else dict(brief_feedback=[summary.feedback] if summary else [])
    )
    outcome = payload.get("metadata", {}).get(
        "outcome",
        (
            report.status
            if report
            else (
                "failed"
                if summary and summary.feedback == FALLBACK_FEEDBACK
                else "legacy"
            )
        ),
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
            schema_version=report.version if report else 1,
            run_id=payload.get("metadata", {}).get("run_id"),
            status=(
                report.status if report and report.version == 2 else "legacy"
            ),
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
                if enabled is not False
                else []
            ),
        )
        if accepted["schema_version"] == 1:
            accepted.update(
                coverage=None,
                message_classifications=[],
                misconception_findings=[],
            )
        if isinstance(accepted["brief_feedback"], str):
            accepted["brief_feedback"] = [accepted["brief_feedback"]]
    latest_run = dict(
        status=(
            ("plan_required" if plan and plan["mode"] == "chunked" else "ready")
            if summary is None and run is None
            else outcome
        ),
        preserved=False,
    )
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
    elif (
        latest is None
        and (report is None or report.version == 1)
        and accepted is not None
    ):
        latest_run = None
    running = latest_run is not None and latest_run["status"] == "running"
    status = latest_run["status"] if latest_run else "legacy"
    native = session.snapshot_origin == "native"
    return dict(
        accepted_report=accepted,
        latest_run=latest_run,
        messages=messages,
        plan=plan,
        permissions=dict(
            can_analyze=native
            and accepted is None
            and status
            in {"failed", "cancelled", "interrupted", "ready", "plan_required"},
            can_retry=native
            and not running
            and (
                report.status
                if report
                else (accepted["status"] if accepted else status)
            )
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
