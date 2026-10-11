"""Offline corpus verification; never provider execution or quality approval."""

import json
from pathlib import Path

from src.services.lesson_snapshots import LessonSnapshot, canonical_hash

FIXTURES = Path(__file__).parent / "fixtures"


def load_corpus(path=FIXTURES / "s4_quality_corpus.json"):
    corpus = json.loads(path.read_text(encoding="utf-8"))
    if (
        canonical_hash({k: v for k, v in corpus.items() if k != "corpus_hash"})
        != corpus["corpus_hash"]
    ):
        raise ValueError("corpus hash mismatch")
    for sample in corpus["samples"]:
        transcript, snapshot = sample["transcript"], sample["snapshot"]
        if (
            canonical_hash(transcript) != sample["transcript_hash"]
            or canonical_hash(snapshot) != sample["config_hash"]
            or canonical_hash(dict(transcript=transcript, snapshot=snapshot))
            != sample["sample_hash"]
        ):
            raise ValueError("sample hash mismatch")
        if LessonSnapshot.model_validate(snapshot).model_dump() != snapshot:
            raise ValueError("snapshot is not canonical")
        messages = {m["id"]: m for m in transcript}
        if len(messages) != len(transcript):
            raise ValueError("duplicate message ID")
        rubric = {r["id"] for r in snapshot["config"]["analysis"]["rubric"]}
        for evidence in sample["expected_evidence_draft"]["evidence"]:
            message = messages.get(evidence["message_id"])
            if (
                message is None
                or evidence["role"] != message["role"]
                or not evidence["quote"]
                or evidence["quote"] not in message["content"]
                or not set(evidence.get("allowed_rubric_ids", [])) <= rubric
            ):
                raise ValueError("invalid draft evidence")
    return corpus


def mock_comparison_record(corpus, sample_id, baseline, candidate):
    record = json.loads(
        (FIXTURES / "s4_quality_comparison_template.json").read_text(
            encoding="utf-8"
        )
    )
    if record["corpus_hash"] != corpus["corpus_hash"]:
        raise ValueError("comparison template corpus hash mismatch")
    record["mode"] = "mock_only_execution_rehearsal"
    sample = next(s for s in corpus["samples"] if s["id"] == sample_id)
    selection = sample["snapshot"]["config"]["analysis"][
        "resolved_model_config"
    ]
    row = next(r for r in record["samples"] if r["sample_id"] == sample_id)
    for side, result in (("baseline", baseline), ("candidate", candidate)):
        if (
            result["provider"] != selection["provider"]
            or result["exact_model_id"] != selection["model_id"]
            or result["options"] != selection["options"]
        ):
            raise ValueError("exact model/options mismatch")
        row[side].update(result)
    return record
