"""Fixed synthetic corpus and offline comparison preparation, not approval."""

import copy
import json

import pytest
from s4_quality_fixtures import FIXTURES, load_corpus, mock_comparison_record

from src.services.analysis_plan import build_plan
from src.services.lesson_snapshots import LessonSnapshot, canonical_hash


def test_fixed_corpus_covers_d10_and_has_no_human_approval():
    corpus = load_corpus()
    assert corpus["synthetic"] is True
    assert (
        corpus["review"]["status"]
        == "agent_draft_requires_education_lead_review"
    )
    assert corpus["review"]["reviewer"] is None
    assert corpus["review"]["reviewed_at"] is None
    for subject in ("수학", "과학"):
        samples = [s for s in corpus["samples"] if s["subject"] == subject]
        assert len(samples) == 6
        assert sorted(s["length"] for s in samples) == [
            "long",
            "long",
            "ordinary",
            "ordinary",
            "short",
            "short",
        ]
        assert any(
            not s["snapshot"]["config"]["analysis"]["classification_enabled"]
            for s in samples
        )
        for sample in samples:
            turns = sum(m["role"] == "student" for m in sample["transcript"])
            if sample["length"] == "short":
                assert 1 <= turns <= 3
            elif sample["length"] == "ordinary":
                assert 8 <= turns <= 12
            else:
                assert turns >= 20
    assert {s["boundary"] for s in corpus["samples"]} >= {
        "greeting_only",
        "unanswered_tail",
    }
    assert {
        s["expected_evidence_draft"]["finding"] for s in corpus["samples"]
    } >= {"maintained", "changed", "deviation", "insufficient"}


def test_mock_record_keeps_quality_scores_and_approval_pending():
    corpus = load_corpus()
    sample = corpus["samples"][0]
    selection = sample["snapshot"]["config"]["analysis"][
        "resolved_model_config"
    ]
    result = dict(
        provider=selection["provider"],
        exact_model_id=selection["model_id"],
        options=selection["options"],
        output={"feedback_status": "ok"},
        call_count=1,
    )
    record = mock_comparison_record(corpus, sample["id"], result, result)
    assert record["mode"] == "mock_only_execution_rehearsal"
    assert record["release_status"] == "release_blocked"
    assert record["education_approval"]["decision"] == "pending"
    assert record["education_approval"]["approved_by"] is None
    assert record["expected_evidence_review"]["reviewed_at"] is None
    assert len(record["samples"]) == 12
    row = record["samples"][0]
    assert row["candidate"]["output"] == {"feedback_status": "ok"}
    assert (
        row["candidate"]["dimensions"]["evidence_fidelity"]["score"]
        == "unknown"
    )
    assert row["candidate"]["cost"] == "unknown"


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "google"),
        ("exact_model_id", "other-model"),
        ("options", {"max_output_tokens": 500}),
    ],
)
def test_mock_comparison_rejects_model_or_option_substitution(field, value):
    corpus = load_corpus()
    sample = corpus["samples"][0]
    selection = sample["snapshot"]["config"]["analysis"][
        "resolved_model_config"
    ]
    baseline = dict(
        provider=selection["provider"],
        exact_model_id=selection["model_id"],
        options=selection["options"],
    )
    candidate = {**baseline, field: value}
    with pytest.raises(ValueError, match="exact model/options mismatch"):
        mock_comparison_record(corpus, sample["id"], baseline, candidate)


def test_long_corpus_has_real_bounded_chunk_plans_without_padding():
    corpus = load_corpus()
    for sample in corpus["samples"]:
        if sample["length"] != "long":
            continue
        plan = build_plan(
            LessonSnapshot.model_validate(sample["snapshot"]),
            sample["transcript"],
        )
        fixture = sample["planner_fixture"]
        assert plan["mode"] == fixture["expected_mode"] == "chunked"
        assert plan["input_budget_tokens"] == fixture["input_budget_tokens"]
        assert len(plan["chunks"]) == fixture["expected_chunks"] <= 8
        assert plan["generation_calls"] == len(plan["chunks"]) + 1
        assert (
            plan["merge"]["estimated_input_tokens"]
            <= plan["input_budget_tokens"]
        )
        assert [
            mid for chunk in plan["chunks"] for mid in chunk["message_ids"]
        ] == [m["id"] for m in sample["transcript"]]
        assert all(
            c["estimated_input_tokens"] <= plan["input_budget_tokens"]
            and c["estimated_output_tokens"] <= plan["frozen_output_cap"]
            for c in plan["chunks"]
        )
    boundaries = {b["id"]: b for b in corpus["boundary_fixtures"]}
    assert boundaries["input-exact"]["expected"] == "single"
    assert boundaries["input-over"]["expected"] == "chunked"
    assert boundaries["eight-chunks"]["required_chunks"] == 8
    assert boundaries["nine-chunks"]["required_chunks"] == 9
    assert all(
        boundaries[k]["expected"] == "blocked_zero_calls"
        for k in ("nine-chunks", "unit-too-large", "merge-too-large")
    )


def test_changed_transcript_is_rejected_before_execution(tmp_path):
    corpus = json.loads((FIXTURES / "s4_quality_corpus.json").read_text())
    corpus["samples"][0]["transcript"][0]["content"] = "바뀐 대화"
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus))
    with pytest.raises(ValueError, match="corpus hash mismatch"):
        load_corpus(path)


@pytest.mark.parametrize(
    "field,value",
    [("message_id", 999), ("role", "teacher"), ("quote", "없는 인용")],
)
def test_invalid_draft_evidence_is_rejected_even_with_updated_hash(
    tmp_path, field, value
):
    corpus = copy.deepcopy(load_corpus())
    corpus["samples"][2]["expected_evidence_draft"]["evidence"][-1][
        field
    ] = value
    corpus["corpus_hash"] = canonical_hash(
        {k: v for k, v in corpus.items() if k != "corpus_hash"}
    )
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus))
    with pytest.raises(ValueError, match="invalid draft evidence"):
        load_corpus(path)
