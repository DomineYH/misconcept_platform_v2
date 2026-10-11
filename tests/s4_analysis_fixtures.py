"""Strict S4 replies at the existing provider transport seam."""


def analysis_reply(messages, *, enabled=True, label="A"):
    return dict(
        schema_version=2,
        message_classifications=[
            dict(
                message_id=m["id"],
                disposition="classified",
                rubric_id=label,
                quote=m["content"][:200],
                reason="생각을 탐색했습니다.",
            )
            for m in messages
            if enabled and m["role"] == "teacher"
        ],
        misconception_findings=[],
        brief_feedback=["Good question"],
        strengths=[],
        improvements=[],
        dialogue_coaching=[],
    )


def prompt_inputs(body):
    import json

    if "input" in body:
        text = body["input"][0]["content"]
    elif "messages" in body:
        text = body["messages"][0]["content"]
    else:
        text = body["contents"][0]["parts"][0]["text"]
    return json.loads(text.split("입력 JSON\n", 1)[1])
