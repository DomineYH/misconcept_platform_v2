"""S2 screen inputs; only the isolated browser server imports these fixtures."""

from copy import deepcopy

from src.services.model_capabilities import (
    capabilities,
    validate_model_and_options,
)


def model_choices():
    models = []
    for pk, provider, model_id, options in [
        (
            1,
            "openai",
            "gpt-5-mini",
            dict(max_output_tokens=1500, reasoning=dict(effort="medium")),
        ),
        (
            2,
            "anthropic",
            "claude-sonnet-4-6",
            dict(max_output_tokens=2048, temperature=1),
        ),
        (
            3,
            "google",
            "gemini-2.5-flash",
            dict(max_output_tokens=2048, thinking=dict(budget=0)),
        ),
    ]:
        models.append(
            dict(
                id=pk,
                provider=provider,
                provider_connection_id=pk,
                model_id=model_id,
                display_name=model_id,
                enabled=True,
                connection_available=True,
                capabilities=capabilities(provider, model_id),
                default_options=validate_model_and_options(
                    provider, model_id, options
                ),
                verification_state={
                    role: dict(status="succeeded")
                    for role in ("student", "mentor", "analysis")
                },
            )
        )
    for pk, status in [
        (4, "unverified"),
        (5, "verifying"),
        (6, "failed"),
        (7, "stale"),
    ]:
        model = deepcopy(models[0])
        model.update(
            id=pk,
            display_name=status,
            verification_state={
                role: dict(status=status)
                for role in ("student", "mentor", "analysis")
            },
        )
        models.append(model)
    for pk, name in [
        (8, "기능 정의 필요"),
        (9, "연결 사용 불가"),
        (10, "비활성"),
    ]:
        model = deepcopy(models[0])
        model.update(id=pk, display_name=name)
        if pk == 8:
            model.update(
                model_id="custom-model", capabilities=None, default_options={}
            )
        if pk == 9:
            model["connection_available"] = False
        if pk == 10:
            model["enabled"] = False
        models.append(model)
    return models


def selection(model, options=None):
    return dict(
        model_config_id=model["id"],
        provider_connection_id=model["provider_connection_id"],
        provider=model["provider"],
        model_id=model["model_id"],
        options=deepcopy(
            options if options is not None else model["default_options"]
        ),
    )


def editor_fixture(*, new=False, published=False, legacy=False):
    models = model_choices()
    data = dict(
        id=1,
        title="분수의 크기 비교",
        subject="수학",
        target_grade="초등 4학년",
        status="published" if published else "draft",
        config_version=1,
        groups=[],
        config_schema_version=1,
        available_groups=[
            dict(id=1, name="예비교사 A반"),
            dict(id=2, name="현직교사 연수"),
        ],
        model_choices=models,
        role_defaults=dict(
            student=dict(model_config_id=2, available=True),
            mentor=dict(model_config_id=7, available=False),
            analysis=None,
        ),
        config=dict(
            problem=dict(
                public_text="분수 1/3과 1/5을 비교해 보세요.",
                learning_objective="같은 전체에서 분수를 비교한다.",
            ),
            student=dict(
                name="민수",
                public_profile="분수 비교를 연습하는 학생입니다.",
                internal_profile="INTERNAL_STUDENT_SENTINEL",
                misconception="분모가 클수록 분수가 크다고 생각한다.",
                behavior_instruction="학생의 생각을 2~3문장으로 말한다.",
                resolved_model_config=None,
            ),
            mentor=dict(
                mode="manual",
                name="멘토",
                welcome_message="함께 질문을 살펴봅시다.",
                behavior_instruction="INTERNAL_MENTOR_SENTINEL",
                intervention_policy=dict(
                    condition="정답을 바로 알려 줄 때",
                    start_turn=3,
                    min_interval_turns=2,
                    window_turns=10,
                    max_interventions=3,
                ),
                resolved_model_config=None,
            ),
            analysis=dict(
                context="초등 분수 수업",
                expected_understanding="같은 전체에서 1/3이 1/5보다 크다.",
                instruction="총평·강점·개선점·대안 질문을 제시한다.",
                rubric_name="교사 질문",
                rubric_description="질문의 의미",
                category_name="질문 유형",
                classification_enabled=True,
                rubric=[
                    dict(
                        id="reasoning",
                        name="추론 질문",
                        criteria="INTERNAL_RUBRIC_SENTINEL",
                        level="high",
                    )
                ],
                resolved_model_config=None,
            ),
            runtime=dict(context_turn_limit=10),
        ),
    )
    data["config"]["student"]["resolved_model_config"] = selection(
        models[0], dict(max_output_tokens=900, reasoning=dict(effort="low"))
    )
    data["config"]["mentor"]["resolved_model_config"] = selection(models[0])
    data["config"]["analysis"]["resolved_model_config"] = selection(models[0])
    if new:
        data["id"] = None
        for role in ("student", "mentor", "analysis"):
            data["config"][role]["resolved_model_config"] = None
    if legacy:
        data["config"]["student"]["public_profile"] = ""
        data["review_required"] = True
        data["conversion_provenance"] = [
            dict(
                field="학생 프로필",
                source="INTERNAL_STUDENT_SENTINEL",
                target="내부 학생 프로필로 보존 · 공개 학생 소개는 비어 있음",
            ),
            dict(
                field="멘토 정책",
                source="고정 주기·민감도 사용",
                target="완료 턴 rolling window·자연어 조건 사용",
            ),
            dict(
                field="하위 호출 옵션",
                source="판단 200 토큰 · 코칭 1500 토큰",
                target="멘토 역할의 동일한 모델·옵션 사용",
            ),
        ]
        data["review_reasons"] = [
            dict(
                path="student.public_profile",
                code="privacy_change",
                message="공개 학생 소개는 관리자가 선정합니다.",
                blocking=False,
            ),
            dict(
                path="mentor.intervention_policy.condition",
                code="policy_change",
                message="개입 정책·하위 호출 옵션 변경을 검토하세요.",
                blocking=False,
            ),
        ]
        if legacy == "blocked":
            data["config"]["problem"]["public_text"] = ""
            data["review_reasons"].append(
                dict(
                    path="problem.public_text",
                    code="required",
                    message="공개 문제 상황 보완 필요",
                    blocking=True,
                )
            )
    return data


def lesson_fixture(query):
    source = editor_fixture()["config"]
    mode = query.get("mode", ["manual"])[0]
    legacy = "legacy" in query
    public = dict(
        title="분수의 크기 비교",
        subject="수학",
        target_grade="초등 4학년",
        problem=source["problem"],
        student=dict(
            name=source["student"]["name"],
            public_profile=source["student"]["public_profile"],
        ),
        mentor=dict(
            mode=mode,
            name="멘토" if mode != "off" else "",
            welcome_message=(
                source["mentor"]["welcome_message"] if mode != "off" else ""
            ),
        ),
    )
    return dict(
        public=public,
        snapshot_origin="legacy_reconstructed" if legacy else "native",
        snapshot_created_at="2026-10-09 09:00",
        unknown_fields=["당시 모델", "당시 역할 지시"] if legacy else [],
        classification_enabled=query.get("classification", ["on"])[0] != "off",
        help=dict(status=query.get("help", ["ready"])[0], message=None),
        completed_turn_id="turn-1",
        session_id=1,
        auto_eligible=mode == "auto" and not legacy,
        feedback=dict(
            overall="학생의 생각을 확인했습니다.",
            strengths="그림으로 이유를 물었습니다.",
            improvements="같은 전체인지 먼저 확인하세요.",
            alternative="같은 크기의 종이를 나누면 어떨까요?",
        ),
    )
