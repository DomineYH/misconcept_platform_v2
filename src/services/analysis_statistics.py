"""Server-derived coverage, distribution and frozen rubric grades for S4."""

import json

from src.models import QuestionAnalysis


def analysis_statistics(
    all_messages, payload, analysis, context, status, error_code
):
    classified = {
        item["message_id"]: item for item in payload["message_classifications"]
    }
    teacher_ids = [m.id for m in all_messages if m.role == "teacher"]
    missing = (
        [mid for mid in teacher_ids if mid not in classified]
        if analysis.classification_enabled
        else []
    )
    errors = context.get("analysis_errors", [])
    if errors:
        error_code = (
            "invalid_reference"
            if any(e["code"] == "invalid_reference" for e in errors)
            else "invalid_output"
        )
    if status != "failed" and (
        errors
        or missing
        or any(
            item["disposition"] == "unclassified"
            for item in classified.values()
        )
    ):
        status = "degraded"
    answered_turns = {
        m.turn_id
        for m in all_messages
        if m.role == "student" and m.turn_id is not None
    }
    response_missing = [
        m.id
        for i, m in enumerate(all_messages)
        if m.role == "teacher"
        and (
            m.turn_id not in answered_turns
            if m.turn_id is not None
            else not (
                i + 1 < len(all_messages)
                and all_messages[i + 1].role == "student"
                and all_messages[i + 1].turn_id is None
            )
        )
    ]
    coverage = dict(
        input_message_ids=[m.id for m in all_messages],
        reviewed_message_ids=(
            [m.id for m in all_messages] if status != "failed" else []
        ),
        teacher_message_ids=teacher_ids,
        **{
            name
            + "_message_ids": [
                mid
                for mid in teacher_ids
                if mid in classified and classified[mid]["disposition"] == name
            ]
            for name in ("classified", "non_analyzable", "unclassified")
        },
        missing_message_ids=missing,
        invalid_message_ids=[
            mid
            for mid in teacher_ids
            if mid in context.get("invalid_message_ids", [])
        ],
        response_missing_message_ids=response_missing,
        chunks=[],
    )
    distribution = (
        {r.id: 0 for r in analysis.rubric}
        if analysis.classification_enabled
        else {}
    )
    questions = []
    for item in payload["message_classifications"]:
        if item["disposition"] != "classified":
            continue
        distribution[item["rubric_id"]] += 1
        questions.append(
            QuestionAnalysis(
                message_id=item["message_id"],
                label=item["rubric_id"],
                confidence=None,
                meta_json=json.dumps(
                    {"summary": item["reason"], "improved_sentence": None},
                    ensure_ascii=False,
                ),
                grade={"high": "우수", "low": "개선"}.get(
                    context["labels"][item["rubric_id"]]
                ),
            )
        )
    return distribution, questions, coverage, status, error_code
