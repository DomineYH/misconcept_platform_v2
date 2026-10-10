"""Budget and complete-turn plans at the pure planning boundary."""

import copy
import json
from pathlib import Path

import pytest

from src.services.lesson_snapshots import LessonSnapshot


def snapshot(options=None):
    value = json.loads(Path("tests/fixtures/s2_draft.json").read_text())
    value = dict(
        schema_version=1,
        scenario_context=dict(title="Test", subject="", target_grade=""),
        config=value["config"],
    )
    value["config"]["analysis"]["resolved_model_config"] = dict(
        model_config_id=1,
        provider_connection_id=1,
        provider="openai",
        model_id="gpt-5.2",
        options=options
        or dict(max_output_tokens=8192, reasoning=dict(effort="none")),
    )
    return LessonSnapshot.model_validate(value)


def test_single_plan_records_frozen_cap_formula_and_version():
    from src.services.analysis_plan import build_plan

    lesson = snapshot()
    messages = [
        dict(id=1, role="teacher", content='한글 "\\\n🙂 {x}'),
        dict(id=2, role="student", content="답변"),
    ]
    plan = build_plan(lesson, messages)
    assert plan["mode"] == "single"
    assert plan["estimated_output_tokens"] == 1480
    assert plan["reserved_thinking_tokens"] == 0
    assert plan["input_budget_tokens"] == 391808
    assert plan["estimator_version"] == "utf8-v1-s4-20pct"
    assert (
        plan["formula"]
        == "ceil(serialized_utf8_bytes * 1.2); B = min(I, C - R); output = 1200 + 160*T + 120*S + thinking"
    )
    assert plan["generation_calls"] == 1
    assert plan["message_ids"] == [1, 2]
    assert plan["model_options"] == dict(
        max_output_tokens=8192, reasoning=dict(effort="none")
    )
    assert len(plan["plan_hash"]) == 64


def test_chunked_plan_keeps_whole_turns_unique_ownership_and_one_turn_overlap():
    from src.services.analysis_plan import build_plan

    messages = [
        dict(id=i, role="teacher" if i % 2 else "student", content="x")
        for i in range(1, 10)
    ]
    plan = build_plan(
        snapshot(dict(max_output_tokens=2000, reasoning=dict(effort="none"))),
        messages,
    )
    assert plan["mode"] == "chunked"
    assert [c["message_ids"] for c in plan["chunks"]] == [
        [1, 2, 3, 4],
        [5, 6, 7, 8, 9],
    ]
    assert [c["reference_message_ids"] for c in plan["chunks"]] == [[], [3, 4]]
    assert plan["generation_calls"] == 3
    assert (
        plan["merge"]["estimated_input_tokens"] <= plan["input_budget_tokens"]
    )
    assert plan["merge"]["estimated_output_tokens"] == 1200


def test_exact_input_budget_fits_and_overflow_is_never_truncated(monkeypatch):
    from src.services import analysis_plan
    from src.services.model_capabilities import capabilities

    messages = [
        dict(id=1, role="teacher", content='한글 "\\\n🙂' * 100),
        dict(id=2, role="student", content="answer"),
    ]
    lesson = snapshot()
    exact = analysis_plan.build_plan(lesson, messages)["estimated_input_tokens"]
    definition = capabilities("openai", "gpt-5.2")
    definition.pop("combined_context_tokens")
    definition["input_token_limit"] = exact
    monkeypatch.setattr(analysis_plan, "capabilities", lambda *_: definition)
    assert analysis_plan.build_plan(lesson, messages)["mode"] == "single"
    definition["input_token_limit"] = exact - 1
    plan = analysis_plan.build_plan(lesson, messages)
    assert plan["mode"] == "blocked"
    assert plan["blocked_code"] == "unit_too_large"
    assert plan["message_ids"] == [1, 2]
    assert plan["generation_calls"] == 0


@pytest.mark.parametrize(
    "provider,model,options,reserve",
    [
        (
            "openai",
            "gpt-5.2",
            dict(max_output_tokens=8192, reasoning=dict(effort="none")),
            0,
        ),
        (
            "openai",
            "gpt-5.2",
            dict(max_output_tokens=8192, reasoning=dict(effort="high")),
            4096,
        ),
        ("openai", "gpt-5-mini", dict(max_output_tokens=8192), 4096),
        (
            "anthropic",
            "claude-sonnet-4-6",
            dict(
                max_output_tokens=8192,
                thinking=dict(type="enabled", budget_tokens=1024),
            ),
            1024,
        ),
        (
            "anthropic",
            "claude-sonnet-4-6",
            dict(max_output_tokens=8192, thinking=dict(type="adaptive")),
            4096,
        ),
        (
            "anthropic",
            "claude-sonnet-4-6",
            dict(max_output_tokens=8192, thinking=dict(type="disabled")),
            0,
        ),
        (
            "google",
            "gemini-2.5-flash",
            dict(max_output_tokens=8192, thinking=dict(budget=0)),
            0,
        ),
        (
            "google",
            "gemini-2.5-flash",
            dict(max_output_tokens=8192, thinking=dict(budget=-1)),
            4096,
        ),
        (
            "google",
            "gemini-2.5-flash",
            dict(max_output_tokens=8192, thinking=dict(budget=1024)),
            1024,
        ),
    ],
)
def test_native_thinking_reservation_and_frozen_options(
    provider, model, options, reserve
):
    from src.services.analysis_plan import build_plan

    lesson = snapshot().model_dump()
    lesson["config"]["analysis"]["resolved_model_config"].update(
        provider=provider, model_id=model, options=options
    )
    before = copy.deepcopy(lesson)
    plan = build_plan(
        LessonSnapshot.model_validate(lesson),
        [dict(id=1, role="teacher", content="질문")],
    )
    assert plan["reserved_thinking_tokens"] == reserve
    assert plan["estimated_output_tokens"] == 1360 + reserve
    assert plan["model_options"] == options
    assert lesson == before
    assert plan["input_budget_tokens"] == (
        1048576
        if provider == "google"
        else 1000000 - 8192 if provider == "anthropic" else 400000 - 8192
    )


@pytest.mark.parametrize(
    "boundary,code",
    [
        ("missing_input", "unknown_limits"),
        ("missing_output", "output_limit_missing"),
        ("small_catalog", "catalog_limit_conflict"),
        ("oversized_output_cap", "output_limit_exceeded"),
        ("teacher_only", "unit_too_large"),
    ],
)
def test_unknown_and_oversized_units_are_blocked_with_zero_calls(
    monkeypatch, boundary, code
):
    from src.services import analysis_plan
    from src.services.model_capabilities import capabilities

    lesson = snapshot()
    messages = [
        dict(id=i, role="teacher", content="미응답")
        for i in range(1, 60 if boundary == "teacher_only" else 2)
    ]
    catalog = None
    if boundary == "missing_input":
        definition = capabilities("openai", "gpt-5.2")
        definition.pop("combined_context_tokens")
        monkeypatch.setattr(
            analysis_plan, "capabilities", lambda *_: definition
        )
    elif boundary == "missing_output":
        lesson = snapshot(dict(reasoning=dict(effort="none")))
    elif boundary == "small_catalog":
        catalog = [dict(model_id="gpt-5.2", combined_context_tokens=100)]
    elif boundary == "oversized_output_cap":
        lesson = snapshot(
            dict(max_output_tokens=128001, reasoning=dict(effort="none"))
        )
    plan = analysis_plan.build_plan(lesson, messages, catalog=catalog)
    assert plan["mode"] == "blocked" and plan["blocked_code"] == code
    assert plan["generation_calls"] == 0
    assert plan["message_ids"] == [m["id"] for m in messages]


@pytest.mark.parametrize(
    "turns,mode,code", [(8, "chunked", None), (9, "blocked", "too_many_chunks")]
)
def test_eight_chunk_limit_has_no_gaps_or_reowned_overlap(
    monkeypatch, turns, mode, code
):
    from src.services import analysis_plan
    from src.services.model_capabilities import capabilities

    definition = capabilities("openai", "gpt-5.2")
    definition["combined_context_tokens"] = 2000000
    monkeypatch.setattr(analysis_plan, "capabilities", lambda *_: definition)
    messages = [
        dict(
            id=i,
            role="teacher" if i % 2 else "student",
            content="x",
            turn_id=str((i - 1) // 2),
        )
        for i in range(1, 2 * turns + 1)
    ]
    plan = analysis_plan.build_plan(
        snapshot(dict(max_output_tokens=1700, reasoning=dict(effort="none"))),
        messages,
    )
    assert plan["mode"] == mode and plan["blocked_code"] == code
    if mode == "chunked":
        assert len(plan["chunks"]) == 8 and plan["generation_calls"] == 9
        assert [
            mid for c in plan["chunks"] for mid in c["message_ids"]
        ] == list(range(1, 17))
        assert [c["reference_message_ids"] for c in plan["chunks"]] == [
            [],
            [1, 2],
            [3, 4],
            [5, 6],
            [7, 8],
            [9, 10],
            [11, 12],
            [13, 14],
        ]
    else:
        assert plan["generation_calls"] == 0


def test_large_merge_is_blocked_before_any_chunk_call():
    from src.services.analysis_plan import build_plan

    messages = [
        dict(id=i, role="teacher" if i % 2 else "student", content="한" * 200)
        for i in range(1, 10)
    ]
    plan = build_plan(
        snapshot(dict(max_output_tokens=2000, reasoning=dict(effort="none"))),
        messages,
    )
    assert (
        plan["mode"] == "blocked" and plan["blocked_code"] == "merge_too_large"
    )
    assert plan["generation_calls"] == 0
    assert plan["merge"]["estimated_input_tokens"] > plan["input_budget_tokens"]


def test_unmatched_messages_attach_without_inventing_turn_links():
    from src.services.analysis_plan import complete_units

    messages = [
        dict(id=1, role="teacher", content="prefix", turn_id="failed"),
        dict(id=2, role="teacher", content="question", turn_id="a"),
        dict(id=3, role="student", content="answer", turn_id="a"),
        dict(id=4, role="teacher", content="unanswered", turn_id="b"),
        dict(id=5, role="teacher", content="question", turn_id="c"),
        dict(id=6, role="student", content="answer", turn_id="c"),
        dict(id=7, role="teacher", content="tail", turn_id="d"),
    ]
    units = complete_units(messages)
    assert [[m["id"] for m in u["messages"]] for u in units] == [
        [1, 2, 3, 4],
        [5, 6, 7],
    ]
    assert [[m["id"] for m in u["pair"]] for u in units] == [[2, 3], [5, 6]]


def test_reference_overlap_is_included_in_the_input_estimate():
    from src.services.analysis_plan import (
        analysis_prompt,
        analysis_request,
        estimate_request,
    )

    lesson = snapshot()
    owned = [
        dict(id=3, role="teacher", content="question"),
        dict(id=4, role="student", content="answer"),
    ]
    reference = [
        dict(id=1, role="teacher", content="한글" * 1000),
        dict(id=2, role="student", content="참고" * 1000),
    ]
    alone = estimate_request(
        analysis_request(
            lesson, analysis_prompt(lesson, owned, owned_ids=[3, 4])
        )
    )
    overlap = estimate_request(
        analysis_request(
            lesson,
            analysis_prompt(
                lesson,
                reference + owned,
                owned_ids=[3, 4],
                reference_ids=[1, 2],
            ),
        )
    )
    assert overlap > alone + 14000
