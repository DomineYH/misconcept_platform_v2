"""Throwaway responsive decision fixtures; no provider or database access."""

TITLE = "같은 크기의 전체를 서로 다르게 나눈 분수의 크기 — 그림과 말로 설명하는 수학 대화"
PROFILE = (
    "민수는 초등학교 4학년 가상 학생입니다. 그림으로 생각을 설명하는 것을 좋아합니다. "
    * 4
)
PROBLEM = (
    "크기가 같은 두 피자를 각각 3조각과 5조각으로 나누었습니다. 한 조각씩 먹으면 누가 더 많이 먹었을까요?\n\n"
    * 4
)
RESPONSE = (
    "분모가 5이면 3보다 큰 수니까 더 큰 조각이라고 생각했어요.\n\n그림을 그려 보니 전체의 크기가 같아야 한다는 점이 헷갈려요. 제 생각을 어떻게 확인하면 좋을까요?\n\n"
    * 3
)


def enrich_editor(editor):
    editor["title"] = TITLE
    editor["config"]["student"]["public_profile"] = PROFILE
    editor["config"]["problem"]["public_text"] = PROBLEM
    for model in editor["model_choices"]:
        model[
            "display_name"
        ] += " · 분수 수업에서 여러 단락의 설명을 검토하는 합성 모델"


def conversation_fixture(count):
    return dict(
        source=None,
        snapshot=dict(
            conversation_id="prototype",
            source_snapshot=dict(title=TITLE),
            version=1,
            phase="active",
            expires_at="합성 자료 · 저장하지 않음",
            pairs=[dict(turn_id="1", teacher=PROBLEM, student=RESPONSE)],
            allowed_actions=dict(turn=True, mentor=True, end=True, adopt=False),
            mentor_actions=[],
            pending_student_child_id=None,
            children=[
                dict(
                    comparison_run_id="round-1",
                    target_role="student",
                    panels=[
                        dict(
                            panel_id=str(i),
                            status="completed",
                            text=RESPONSE,
                            model_config=dict(
                                model_id=f"합성-모델-{i}-긴한국어모델이름으로줄바꿈을확인합니다"
                            ),
                        )
                        for i in range(1, count + 1)
                    ],
                )
            ],
        ),
    )


def inject(body):
    # Load the request guard before product scripts; the query is fixture-only opt-in.
    body = body.replace(
        b"<head>",
        b'<head><script src="/fixtures/responsive/guard.js"></script>',
    )
    return body.replace(
        b"</body>",
        b'<link rel="stylesheet" href="/fixtures/responsive/prototype.css">'
        b'<script type="module" src="/fixtures/responsive/prototype.js"></script></body>',
    )
