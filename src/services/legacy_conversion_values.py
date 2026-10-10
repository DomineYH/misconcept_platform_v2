"""Conservative field mapping and literal-only legacy template rendering."""

import json
import re

from pydantic import Field, StrictInt, model_validator

from src.api.schemas.scenario_config import ConfigValue, ScenarioConfig
from src.services.model_capabilities import validate_model_and_options
from src.services.scenario_publication import publication_errors


class EffectiveOperation(ConfigValue):
    model_id: str
    options: dict


class EffectiveSettings(ConfigValue):
    source: str = Field(min_length=1)
    captured_at: str = Field(min_length=1)
    student_base_rules: str
    student: EffectiveOperation | None
    mentor_coaching: EffectiveOperation | None
    mentor_judgment: EffectiveOperation | None
    analysis_synthesis: EffectiveOperation | None
    analysis_greeting: EffectiveOperation | None
    analysis_classification: EffectiveOperation | None
    tutor_intervention_threshold: StrictInt | None
    context_turn_limit: StrictInt | None

    @model_validator(mode="before")
    @classmethod
    def reject_secrets(cls, value):
        def check(item):
            if isinstance(item, dict):
                for key, child in item.items():
                    name = key.lower()
                    if (
                        name.endswith(("_key", "_token"))
                        or "password" in name
                        or "secret" in name
                        or name
                        in {
                            "key",
                            "token",
                            "authorization",
                            "credentials",
                            "nonce",
                            "ciphertext",
                        }
                    ):
                        raise ValueError("secret_field_forbidden")
                    check(child)
            elif isinstance(item, list):
                for child in item:
                    check(child)

        check(value)
        return value


def render_legacy(template, values):
    parts, start = [], 0
    for match in re.finditer(
        r"{{|}}|{(?:scenario_title|student_profile|prompt)}|[{}]", template
    ):
        parts.append(template[start : match.start()])
        token = match.group()
        if token in ("{", "}"):
            raise ValueError("unsupported_template")
        parts.append(token[0] if token in ("{{", "}}") else values[token[1:-1]])
        start = match.end()
    parts.append(template[start:])
    return "".join(parts)


def convert_values(source, settings, models):
    config = ScenarioConfig(
        problem={}, student={}, mentor={}, analysis={}, runtime={}
    ).model_dump()
    reasons, evidence = [], []

    def reason(path, code, message, blocking=True):
        reasons.append(
            dict(path=path, code=code, message=message, blocking=blocking)
        )

    def copy(path, value, limit=50000):
        value = "" if value is None else value
        if not isinstance(value, str) or len(value) > limit:
            reason(
                path,
                "invalid_source",
                "지원되지 않거나 너무 긴 원문을 보완하세요.",
            )
            result = ""
        else:
            result = value
        excerpt = (
            value
            if isinstance(value, str) and len(value) <= 1000
            else "[발췌 · 전체 원문은 archive 참조] " + str(value)[:1000]
        )
        evidence.append(dict(field=path, source=excerpt, target=result))
        return result

    title = copy("title", source["title"], 200)
    if not title.strip():
        reason("title", "invalid_source", "기존 제목을 보완하세요.")
        title = f"Legacy scenario {source['id']}"
    subject = copy("subject", source["subject"], 100)
    config["problem"]["public_text"] = copy(
        "problem.public_text", source["problem_situation"]
    )
    for field, column, limit in (
        ("name", "student_name", 50),
        ("internal_profile", "student_profile", 50000),
        ("misconception", "prompt", 50000),
    ):
        config["student"][field] = copy(
            "student." + field, source[column], limit
        )
    reason(
        "student.public_profile",
        "privacy_change",
        "공개 학생 소개는 비워 두었습니다. 내부 원문과 공개 범위를 검토하세요.",
        False,
    )
    values = dict(
        scenario_title=source["title"],
        student_profile=source["student_profile"] or "Grade 5 student",
        prompt=source["prompt"] or "",
    )
    if not source["student_profile"]:
        evidence.append(
            dict(
                field="student_profile fallback",
                source="이전 실행의 누락 프로필 대체값",
                target="Grade 5 student (실제 학년 아님)",
            )
        )
    for role in ("student", "mentor"):
        template = source[role + "_template"]
        path = role + ".behavior_instruction"
        if role == "mentor" and source["tutor_template_id"] is None:
            continue
        if template is None or template["bot_type"] != (
            "student" if role == "student" else "tutor"
        ):
            reason(
                path,
                "missing_template",
                "참조 템플릿이 없습니다. 역할 지시를 보완하세요.",
            )
            continue
        try:
            rendered = render_legacy(template["template_text"], values)
            if role == "student":
                if not settings.student_base_rules.strip():
                    raise ValueError("missing_base_rules")
                rendered = settings.student_base_rules + "\n\n" + rendered
            config[role]["behavior_instruction"] = copy(path, rendered)
        except (ValueError, TypeError):
            reason(
                path,
                "unsupported_template",
                "알 수 없거나 손상된 템플릿을 원문 archive와 비교하여 보완하세요.",
            )
        evidence.append(
            dict(
                field=role + " template",
                source=(
                    str(template["template_text"])[:1000]
                    if len(str(template["template_text"])) <= 1000
                    else "[발췌 · 전체 원문은 archive 참조] "
                    + str(template["template_text"])[:1000]
                ),
                target=config[role]["behavior_instruction"],
                source_id=template["id"],
                source_version=template["version"],
            )
        )
    config["mentor"]["mode"] = (
        "auto" if source["tutor_template_id"] is not None else "off"
    )
    config["mentor"]["welcome_message"] = copy(
        "mentor.welcome_message", source["greeting_message"]
    )
    threshold = (
        source["tutor_intervention_threshold"]
        if source["tutor_intervention_threshold"] is not None
        else settings.tutor_intervention_threshold
    )
    if type(threshold) is int and 1 <= threshold <= 10:
        config["mentor"]["intervention_policy"]["max_interventions"] = threshold
    else:
        reason(
            "mentor.intervention_policy.max_interventions",
            "invalid_source",
            "배포 환경의 실제 개입 상한 N을 확인하세요.",
            config["mentor"]["mode"] != "off",
        )
    reason(
        "mentor.intervention_policy",
        "bucket_to_rolling",
        "기존 고정 주기 제한에서 최근 완료 10턴의 rolling 제한으로 바뀝니다.",
        False,
    )
    evidence.append(
        dict(
            field="mentor.intervention_policy",
            source=dict(
                effective_threshold=threshold,
                sensitivity=source["tutor_sensitivity"],
                counting="fixed bucket",
            ),
            target=config["mentor"]["intervention_policy"],
        )
    )
    reason(
        "mentor.intervention_policy.condition",
        "sensitivity_removed",
        "기존 민감도는 실행에서 제거됩니다. 자동 개입 조건을 직접 작성하세요.",
        False,
    )
    if source["chat_temperature"] is not None:
        evidence.append(
            dict(
                field="chat_temperature (미적용)",
                source=source["chat_temperature"],
                target="실행 옵션에 추가하지 않음",
            )
        )
        reason(
            "student.resolved_model_config",
            "temperature_not_applied",
            "기존 chat_temperature는 적용되지 않았으므로 새 옵션에 넣지 않았습니다.",
            False,
        )
    if (
        type(settings.context_turn_limit) is int
        and 1 <= settings.context_turn_limit <= 100
    ):
        config["runtime"]["context_turn_limit"] = settings.context_turn_limit
    else:
        reason(
            "runtime.context_turn_limit",
            "invalid_source",
            "배포 문맥 상한을 확인하여 1–100 범위로 보완하세요.",
        )
    framework = source["framework"]
    if framework is None:
        reason(
            "analysis.rubric",
            "missing_framework",
            "이전 분석 틀이 없습니다. 분류 기준을 보완하세요.",
        )
    else:
        for field, column in (
            ("rubric_name", "name"),
            ("rubric_description", "description"),
            ("category_name", "category_name"),
        ):
            config["analysis"][field] = copy(
                "analysis." + field, framework[column]
            )
        try:
            labels = json.loads(framework["labels_json"])
            if not isinstance(labels, list) or len(labels) > 20:
                raise ValueError("invalid_rubric")
            for index, label in enumerate(labels):
                if isinstance(label, str):
                    label = dict(name=label, criteria="", level=None)
                if not isinstance(label, dict) or set(label) - {
                    "name",
                    "criteria",
                    "level",
                }:
                    raise ValueError("invalid_rubric")
                config["analysis"]["rubric"].append(
                    dict(
                        id=f"legacy_{index + 1:03d}",
                        name=label.get("name", ""),
                        criteria=label.get("criteria", ""),
                        level=label.get("level"),
                    )
                )
            ScenarioConfig.model_validate(config)
        except (ValueError, TypeError):
            config["analysis"]["rubric"] = []
            reason(
                "analysis.rubric",
                "invalid_source",
                "손상되거나 지원되지 않는 분류 기준 원문을 보완하세요.",
            )
    for role, operation in (
        ("student", settings.student),
        ("mentor", settings.mentor_coaching),
        ("analysis", settings.analysis_synthesis),
    ):
        path = role + ".resolved_model_config"
        model_id = (
            source["chat_model"] or (operation.model_id if operation else "")
            if role == "student"
            else (operation.model_id if operation else "")
        )
        model = next((m for m in models if m["model_id"] == model_id), None)
        try:
            if (
                operation is None
                or model is None
                or "max_output_tokens" not in operation.options
            ):
                raise ValueError("missing_effective_model")
            options = validate_model_and_options(
                "openai", model_id, operation.options
            )
            config[role]["resolved_model_config"] = dict(
                model_config_id=model["id"],
                provider_connection_id=model["provider_connection_id"],
                provider="openai",
                model_id=model_id,
                options=options,
            )
        except ValueError:
            reason(
                path,
                "unresolved_model",
                "실제 유효 모델·옵션과 exact OpenAI 등록을 확인하여 모델을 선택하세요.",
                role != "mentor" or config["mentor"]["mode"] != "off",
            )
        subcalls = (
            (settings.mentor_judgment,)
            if role == "mentor"
            else (
                (settings.analysis_greeting, settings.analysis_classification)
                if role == "analysis"
                else ()
            )
        )
        if any(subcall != operation for subcall in subcalls):
            reason(
                path,
                "subcall_settings_changed",
                "이전 하위 호출과 제안 역할의 모델·옵션이 다릅니다. 하나의 역할 설정으로 통합됩니다.",
                False,
            )
        evidence.append(
            dict(
                field=path,
                source=(
                    dict(operation.model_dump(), model_id=model_id)
                    if operation
                    else None
                ),
                target=config[role]["resolved_model_config"],
                subcall_settings=[
                    s.model_dump() if s else None for s in subcalls
                ],
                scenario_model_override=(
                    source["chat_model"] if role == "student" else None
                ),
            )
        )
    target = dict(title=title, subject=subject, target_grade="", config=config)
    if (
        len(
            json.dumps(
                dict(
                    target,
                    config_schema_version=1,
                    groups=source["groups"],
                    action="save_draft",
                    expected_version=source["config_version"] + 1,
                ),
                ensure_ascii=False,
            ).encode("utf-8")
        )
        > 1048576
    ):
        policy = config["mentor"]["intervention_policy"]
        target["config"] = config = ScenarioConfig(
            problem={}, student={}, mentor={}, analysis={}, runtime={}
        ).model_dump()
        config["mentor"]["mode"] = (
            "auto" if source["tutor_template_id"] is not None else "off"
        )
        config["mentor"]["intervention_policy"] = policy
        for entry in evidence:
            entry["target"] = (
                "입력 한도 초과: 전체 원문 archive를 확인하여 실행 설정을 보완하세요."
            )
        reason(
            "config",
            "oversize_target",
            "변환 설정이 UTF-8 입력 한도를 넘었습니다. 전체 원문 archive를 확인하여 초안을 보완하세요.",
        )
    reasons.extend(
        dict(error, blocking=True)
        for error in publication_errors(ScenarioConfig.model_validate(config))
    )
    return target, reasons, evidence
