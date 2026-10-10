"""Publication completeness; disabled areas retain structurally safe values."""

import re

REVALIDATED_REASONS = {
    "required",
    "model_unavailable",
    "invalid_rubric_id",
    "duplicate_rubric_id",
    "duplicate_rubric_name",
}
MISSING_FIELD = object()


def field_value(config, path):
    try:
        for part in path.split("."):
            config = (
                config[int(part)] if isinstance(config, list) else config[part]
            )
        return config
    except (KeyError, IndexError, ValueError, TypeError):
        return MISSING_FIELD


def review_reasons(scenario, data, errors):
    previous = dict(
        config=scenario.config_json,
        title=scenario.title,
        subject=scenario.subject or "",
        target_grade=scenario.target_grade or "",
    )
    retained = []
    for reason in scenario.review_reasons:
        if reason["blocking"]:
            path = reason["path"]
            if path.split(".")[0] in data["config"]:
                path = "config." + path
            replacement = field_value(data, path)
            if reason["code"] in REVALIDATED_REASONS and (
                replacement is not MISSING_FIELD
                or re.fullmatch(
                    r"config\.analysis\.rubric\.\d+\.(id|name|criteria)",
                    path,
                )
            ):
                continue
            # Conversion fields need an actual replacement, never just acknowledgement.
            if (
                replacement is not MISSING_FIELD
                and replacement is not None
                and (not isinstance(replacement, str) or replacement.strip())
                and replacement != field_value(previous, path)
                and not any(
                    e["path"] == path or e["path"].startswith(path + ".")
                    for e in errors
                )
            ):
                continue
        retained.append(reason)
    return retained + [dict(error, blocking=True) for error in errors]


def publication_errors(config):
    errors = []

    def required(path, value, label):
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(
                dict(
                    path=f"config.{path}",
                    code="required",
                    message=f"{label} 보완 필요",
                )
            )

    for section, names in (
        (
            "problem",
            {"public_text": "공개 문제 상황", "learning_objective": "학습목표"},
        ),
        (
            "student",
            {
                "name": "학생봇 이름",
                "misconception": "목표 오개념",
                "behavior_instruction": "학생봇 행동 지시",
                "resolved_model_config": "학생봇 모델",
            },
        ),
        (
            "analysis",
            {
                "context": "분석 맥락",
                "expected_understanding": "정답·기대 이해",
                "instruction": "분석 지시",
                "resolved_model_config": "사후 분석 모델",
            },
        ),
    ):
        for name, label in names.items():
            required(
                f"{section}.{name}",
                getattr(getattr(config, section), name),
                label,
            )
    if config.mentor.mode != "off":
        for name, label in {
            "name": "멘토 이름",
            "behavior_instruction": "멘토 행동 지시",
            "resolved_model_config": "멘토 모델",
        }.items():
            required(f"mentor.{name}", getattr(config.mentor, name), label)
        if config.mentor.mode == "auto":
            required(
                "mentor.intervention_policy.condition",
                config.mentor.intervention_policy.condition,
                "자동 개입 조건",
            )
    if config.analysis.classification_enabled:
        required(
            "analysis.rubric_name",
            config.analysis.rubric_name,
            "분류 기준 이름",
        )
        if not config.analysis.rubric:
            errors.append(
                dict(
                    path="config.analysis.rubric",
                    code="required",
                    message="분류 기준을 하나 이상 작성하세요.",
                )
            )
        ids, names = set(), set()
        for index, row in enumerate(config.analysis.rubric):
            for name, label in {
                "id": "ID",
                "name": "이름",
                "criteria": "판정 기준",
            }.items():
                required(
                    f"analysis.rubric.{index}.{name}",
                    getattr(row, name),
                    f"분류 {index + 1} {label}",
                )
            for name, seen in (("id", ids), ("name", names)):
                value = row.id if name == "id" else row.name.strip()
                if value and value in seen:
                    errors.append(
                        dict(
                            path=f"config.analysis.rubric.{index}.{name}",
                            code=f"duplicate_rubric_{name}",
                            message=f"분류 {index + 1} {name}은 중복 없이 작성하세요.",
                        )
                    )
                seen.add(value)
            if row.id.strip() and not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", row.id
            ):
                errors.append(
                    dict(
                        path=f"config.analysis.rubric.{index}.id",
                        code="invalid_rubric_id",
                        message=f"분류 {index + 1} ID 형식을 확인하세요.",
                    )
                )
    return errors
