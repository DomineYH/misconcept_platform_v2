"""Admin presentation of persisted scenario notices, never their message text."""

import re

from src.i18n import display_language, translate

FIELD_KEYS = {
    "title": "title",
    "subject": "subject",
    "target_grade": "target_grade",
    "groups": "assigned_groups",
    "config": "configuration",
    "problem.public_text": "public_problem_situation",
    "problem.learning_objective": "learning_objective",
    "student.name": "student_name",
    "student.misconception": "target_misconception",
    "student.behavior_instruction": "student_behavior_instructions",
    "student.public_profile": "public_student_profile_optional",
    "student.internal_profile": "internal_student_profile_optional",
    "mentor.name": "mentor_name",
    "mentor.behavior_instruction": "mentor_behavior_instructions",
    "mentor.intervention_policy.condition": "automatic_intervention_condition",
    "mentor.intervention_policy.max_interventions": "maximum_interventions_in_the_recent_turn_window",
    "mentor.intervention_policy": "mentor_mode",
    "runtime.context_turn_limit": "recent_completed_turns_in_context",
    "analysis.context": "analysis_context",
    "analysis.expected_understanding": "correct_answer_and_expected_understanding",
    "analysis.instruction": "analysis_instructions_and_focus",
    "analysis.rubric_name": "rubric_name",
    "analysis.rubric": "classification",
    "acknowledge_review": "review_acknowledgement",
}
REASON_KEYS = {
    "required": "review_field_required",
    "invalid_source": "review_invalid_source",
    "privacy_change": "review_privacy",
    "missing_template": "review_missing_template",
    "unsupported_template": "review_unsupported_template",
    "bucket_to_rolling": "review_rolling",
    "sensitivity_removed": "review_sensitivity",
    "temperature_not_applied": "review_temperature",
    "missing_framework": "review_missing_rubric",
    "unresolved_model": "review_model",
    "subcall_settings_changed": "review_subcall",
    "oversize_target": "review_oversize",
    "invalid_rubric_id": "review_rubric_id",
    "duplicate_rubric_id": "review_duplicate_id",
    "duplicate_rubric_name": "review_duplicate_name",
    "common_model_required": "review_common_model",
    "model_unavailable": "review_model",
    "model_identity_mismatch": "review_model",
    "legacy_conversion_required": "review_conversion",
    "invalid_groups": "review_groups",
    "review_acknowledgement_required": "review_acknowledgement",
}


RUBRIC_FIELD_KEYS = {
    "id": "rubric_id",
    "name": "name",
    "criteria": "criteria",
    "level": "level",
}
ROLE_KEYS = {
    "student": "student",
    "mentor": "mentor",
    "analysis": "post_session_analysis",
}


def review_notice(reason, language):
    path = reason.get("path", "").removeprefix("config.")
    field_key = FIELD_KEYS.get(path)
    field = translate("admin." + field_key, language) if field_key else path
    row = re.fullmatch(
        r"analysis\.rubric\.(\d+)\.(id|name|criteria|level)", path
    )
    if row:
        label = translate("admin." + RUBRIC_FIELD_KEYS[row[2]], language)
        field = translate(
            "admin.rubric_field", language, index=int(row[1]) + 1, label=label
        )
    elif path.endswith(".resolved_model_config"):
        role = path.split(".")[0]
        if role in ROLE_KEYS:
            field = translate(
                "admin.role_model",
                language,
                role=translate(
                    "admin." + ROLE_KEYS[role],
                    language,
                ),
            )
    key = REASON_KEYS.get(reason.get("code"), "review_input")
    return {
        **reason,
        "message": translate("admin." + key, language, field=field),
    }


def scenario_view(view, request):
    return {
        **view,
        "review_reasons": [
            review_notice(reason, display_language(request))
            for reason in view["review_reasons"]
        ],
    }
