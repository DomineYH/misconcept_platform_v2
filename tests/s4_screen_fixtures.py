"""Public S4 screen contract; synthetic responses, no DB/provider execution."""

from copy import deepcopy


def analysis_fixture(state="ok", admin=False):
    messages = [
        dict(id=101, role="teacher", content="안녕하세요"),
        dict(id=102, role="teacher", content="왜 1/5이 더 크다고 생각하나요?"),
        dict(id=103, role="student", content="분모가 5라서 더 커요."),
        dict(
            id=104,
            role="teacher",
            content="같은 전체를 나누면 조각은 어떨까요?",
        ),
        dict(id=105, role="student", content="조각이 더 작아져요."),
        dict(id=106, role="teacher", content="다른 분수에서도 설명해 볼까요?"),
        dict(id=107, role="teacher", content="그림으로 비교해 볼까요?"),
    ]
    report = dict(
        schema_version=2,
        status="ok",
        created_at="2026-10-01 10:00",
        classification_enabled=True,
        brief_feedback=["학생의 생각을 원문으로 확인하며 질문했습니다."],
        message_classifications=[
            dict(
                message_id=101,
                disposition="non_analyzable",
                rubric_id=None,
                quote="안녕하세요",
                reason="인사",
            ),
            *[
                dict(
                    message_id=mid,
                    disposition="classified",
                    rubric_id="reasoning",
                    quote=messages[index]["content"],
                    reason="생각을 탐색하는 질문",
                )
                for mid, index in [(102, 1), (104, 3), (106, 5), (107, 6)]
            ],
        ],
        misconception_findings=[
            dict(
                kind="changed",
                claim="분수 조각의 크기에 관한 설명이 달라졌습니다.",
                evidence=[
                    dict(message_id=103, quote="분모가 5라서 더 커요."),
                    dict(message_id=105, quote="조각이 더 작아져요."),
                ],
            )
        ],
        strengths=[
            dict(
                message_id=102,
                quote=messages[1]["content"],
                reason="학생의 판단 이유를 물었습니다.",
            )
        ],
        improvements=[
            dict(
                message_id=105,
                quote=messages[4]["content"],
                missed_reason="같은 전체라는 조건을 확인할 수 있습니다.",
                alternative_question="전체 크기가 같아야 할까요?",
                alternative_reason="비교 조건을 설명하도록 돕습니다.",
            )
        ],
        dialogue_coaching=[
            dict(
                message_id=104,
                role="teacher",
                marker="good_moment",
                quote=messages[3]["content"],
                note="크기를 탐색하는 질문",
            )
        ],
        coverage=dict(
            input_message_ids=[101, 102, 103, 104, 105, 106, 107],
            reviewed_message_ids=[101, 102, 103, 104, 105, 106, 107],
            teacher_message_ids=[101, 102, 104, 106, 107],
            classified_message_ids=[102, 104, 106, 107],
            non_analyzable_message_ids=[101],
            unclassified_message_ids=[],
            missing_message_ids=[],
            invalid_message_ids=[],
            response_missing_message_ids=[101, 106, 107],
            chunks=[],
        ),
        distribution=[dict(name="추론 질문", count=4, percentage=100)],
    )
    data = dict(
        accepted_report=report,
        latest_run=dict(status="ok", preserved=False),
        messages=messages,
        plan=None,
        permissions=dict(
            can_analyze=False,
            can_retry=False,
            can_regenerate=admin,
            read_only=False,
        ),
        actions=dict(
            analyze=(
                "/admin/sessions/1/analyze_regenerate"
                if admin
                else "/sessions/1/analyze"
            )
        ),
    )
    if state == "partial":
        report["status"] = "degraded"
        coverage = report["coverage"]
        coverage.update(
            reviewed_message_ids=[101, 102, 103, 104, 105, 106],
            classified_message_ids=[102, 104],
            unclassified_message_ids=[106],
            missing_message_ids=[107],
            invalid_message_ids=[107],
            chunks=[
                dict(status="ok", message_ids=[101, 102, 103, 104, 105, 106]),
                dict(status="failed", message_ids=[107]),
            ],
        )
        report["message_classifications"] = report["message_classifications"][
            :3
        ]
        report["message_classifications"].append(
            dict(
                message_id=106,
                disposition="unclassified",
                rubric_id=None,
                quote=messages[5]["content"],
                reason="적합한 분류 항목이 없습니다.",
            )
        )
        report["distribution"] = [
            dict(name="추론 질문", count=2, percentage=100)
        ]
        data["latest_run"]["status"] = "degraded"
        data["permissions"]["can_retry"] = True
    if state == "chunked":
        report["coverage"]["chunks"] = [
            dict(status="ok", message_ids=[101, 102, 103]),
            dict(status="ok", message_ids=[104, 105, 106, 107]),
        ]
    if state in {
        "failed",
        "preserved",
        "preserved_partial",
        "cancelled",
        "interrupted",
    }:
        data["latest_run"].update(
            status=(
                "degraded"
                if state == "preserved_partial"
                else (
                    state if state in {"cancelled", "interrupted"} else "failed"
                )
            ),
            preserved=state != "failed",
            error_code="invalid_reference",
        )
        data["permissions"]["can_retry"] = True
        if state == "failed":
            data["accepted_report"] = None
    if state == "off":
        report["classification_enabled"] = False
        report["message_classifications"] = []
        report["distribution"] = []
        for field in ["classified_message_ids", "non_analyzable_message_ids"]:
            report["coverage"][field] = []
    if state in {"legacy", "summary_only"}:
        data["latest_run"] = None
        report.update(
            schema_version=1,
            status="legacy",
            classification_enabled=None,
            coverage=None,
            misconception_findings=[],
            message_classifications=[],
        )
        report["brief_feedback"] = [
            (
                "과거에 저장한 요약만 있는 결과"
                if state == "summary_only"
                else "과거에 저장한 총평 그대로"
            )
        ]
        report["strengths"] = (
            [
                dict(
                    message_id=None,
                    quote="과거 원문 인용",
                    reason="과거에 저장한 강점",
                )
            ]
            if state == "legacy"
            else []
        )
        report["improvements"] = (
            [
                dict(
                    student_message_id=None,
                    student_quote="과거 학생 인용",
                    missed_reason="과거 개선 이유",
                    alternative_question="과거 대안 질문",
                    alternative_reason="과거 대안 이유",
                )
            ]
            if state == "legacy"
            else []
        )
        report["dialogue_coaching"] = []
        report["distribution"] = [
            dict(name="Original label", count=1, percentage=100)
        ]
        data["questions"] = [
            dict(
                message_id=102,
                label="Original label",
                label_name="Original label",
                grade="우수",
                reasoning=dict(summary="Original reasoning"),
                created_at="2026-10-01T10:00:00",
            )
        ]
        messages[1]["created_at"] = "2026-10-01T10:00:00"
        messages[1]["label"] = "Original label"
        data["permissions"].update(read_only=True, can_regenerate=False)
    if state == "no_dialogue":
        data.update(
            accepted_report=None,
            messages=[],
            latest_run=dict(status="no_dialogue", preserved=False),
        )
    if state in {"plan", "blocked"}:
        data["latest_run"] = None
        if not admin:
            data["accepted_report"] = None
        data["permissions"]["can_analyze"] = True
        data["plan"] = dict(
            status="blocked" if state == "blocked" else "ready",
            mode="chunked",
            message_ids=[101, 102, 103, 104, 105, 106, 107],
            chunks=[
                dict(message_ids=[101, 102, 103]),
                dict(message_ids=[104, 105, 106, 107]),
            ],
            estimated_input_tokens=4200,
            estimated_output_tokens=2600,
            estimator_version="utf8-v1",
            generation_calls=3,
            retry_limit=2,
            plan_hash="plan-v1",
            blocked_code="unit_too_large",
        )
    if state == "zero":
        report["status"] = "degraded"
        data["latest_run"]["status"] = "degraded"
        report["coverage"].update(
            classified_message_ids=[],
            unclassified_message_ids=[102, 104, 106, 107],
        )
        report["distribution"] = []
        for item in report["message_classifications"]:
            if item["message_id"] != 101:
                item.update(
                    disposition="unclassified",
                    rubric_id=None,
                    reason="적합한 분류 항목이 없습니다.",
                )
        data["permissions"]["can_retry"] = True
    if state == "running":
        data["latest_run"] = dict(
            status="running", preserved=False, run_id="run-1"
        )
        data["permissions"].update(
            can_analyze=False, can_retry=False, can_regenerate=False
        )
        data["actions"].update(
            status="/sessions/1/analysis/runs/run-1",
            cancel="/sessions/1/analysis/runs/run-1/cancel",
        )
    return deepcopy(data)
