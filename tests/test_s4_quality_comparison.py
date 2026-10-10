"""Same frozen dialogue through real S2/S4 execution and mocked SDK calls."""

import copy
import hashlib
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter

import httpx2
import pytest
from analysis_fixtures import install_analysis_snapshot
from s2_analysis_baseline import run_llm_pipeline as s2_pipeline
from s4_quality_fixtures import load_corpus, mock_comparison_record
from test_analysis_invocations import analysis_transport
from test_analysis_invocations import api as analysis_api
from test_provider_connections import api as provider_api
from test_scenario_api import login
from test_student_probe import response_body

from src.models import GenerationRun, Message
from src.services.lesson_snapshots import canonical_hash

api = analysis_api
connection_api = provider_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@pytest.fixture
def quality_reply():
    """Both executors use the same SDK transport boundary."""

    def reply(sample, body, side):
        name = body["text"]["format"]["name"]
        if name in {"UnifiedAnalysisOutput", "MergeAnalysisOutput"}:
            from s4_analysis_fixtures import analysis_reply, prompt_inputs

            inputs = prompt_inputs(body)
            if name == "MergeAnalysisOutput":
                return inputs["chunk_feedback"][0]
            owned = inputs.get(
                "owned_message_ids", [m["id"] for m in inputs["messages"]]
            )
            messages = [m for m in inputs["messages"] if m["id"] in owned]
            value = analysis_reply(
                messages,
                enabled=inputs["analysis"]["classification_enabled"],
                label="open",
            )
            value["brief_feedback"] = [
                f"{side}: 실행 경계 리허설이며 교육 품질 판단이 아닙니다."
            ]
            if sample["boundary"] == "greeting_only":
                for item in value["message_classifications"]:
                    item.update(disposition="non_analyzable", rubric_id=None)
            else:
                teacher = next(m for m in messages if m["role"] == "teacher")
                value["strengths"] = [
                    dict(
                        message_id=teacher["id"],
                        quote=teacher["content"][:200],
                        reason="원문 참조 검사용 mock",
                    )
                ]
            return value
        if name == "RuntimeGreetings":
            return {
                "results": [
                    {
                        "index": i,
                        "is_greeting": sample["boundary"] == "greeting_only",
                    }
                    for i in range(
                        sum(
                            m["role"] == "teacher" for m in sample["transcript"]
                        )
                    )
                ]
            }
        if name == "RuntimeClassification":
            return {
                "label": "open",
                "confidence": 0.9,
                "reasoning": "절차 검사용 mock 분류",
            }
        assert name == "RuntimeSynthesis"
        teacher = sample["transcript"][0]
        return {
            "brief_feedback": [
                f"{side}: 실행 경계 리허설이며 교육 품질 판단이 아닙니다."
            ],
            "strengths": (
                []
                if sample["boundary"] == "greeting_only"
                else [
                    {
                        "message_id": teacher["id"],
                        "quote": teacher["content"],
                        "reason": "원문 참조 검사용 mock",
                    }
                ]
            ),
            "improvements": [],
            "dialogue_coaching": [],
        }

    return reply


@pytest.mark.parametrize(
    "sample_id", [s["id"] for s in load_corpus()["samples"]]
)
async def test_mock_comparison_records_same_frozen_inputs_without_approval(
    data, api, monkeypatch, tmp_path, sample_id, quality_reply
):
    from src.services import analysis_pipeline, analysis_runs
    from src.services.analysis_chunks import run_chunk_pipeline

    s4_pipeline = analysis_pipeline.run_llm_pipeline

    async def baseline_chunks(*args, plan, **kwargs):
        # S2 has no chunk executor; replay its full dialogue after plan confirmation.
        return await s2_pipeline(*args, **kwargs)

    corpus = load_corpus()
    sample = next(s for s in corpus["samples"] if s["id"] == sample_id)
    await install_analysis_snapshot(data, monkeypatch)
    data.session.config_snapshot_json = copy.deepcopy(sample["snapshot"])
    data.session.config_hash = sample["config_hash"]
    for index, message in enumerate(sample["transcript"]):
        data.db.add(
            Message(
                session_id=data.session.id,
                created_at=datetime(2026, 1, 1) + timedelta(seconds=index),
                **message,
            )
        )
    await data.db.commit()
    results = {}
    code_revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
    prompt_versions = {
        name: hashlib.sha256(
            (Path("src/prompts") / name).read_bytes()
        ).hexdigest()
        for name in (
            "analysis_prompt.txt",
            "greeting_detection.txt",
            "session_synthesis_prompt.txt",
            "analysis_v2.txt",
            "analysis_merge_v2.txt",
        )
    }
    for side in ("baseline", "candidate"):
        monkeypatch.setattr(
            analysis_pipeline,
            "run_llm_pipeline",
            s2_pipeline if side == "baseline" else s4_pipeline,
        )
        monkeypatch.setattr(
            analysis_runs,
            "chunk_executor",
            baseline_chunks if side == "baseline" else run_chunk_pipeline,
        )
        raw = []
        plan = None

        async def upstream(request, body):
            value = quality_reply(sample, body, side)
            raw.append(value)
            return httpx2.Response(200, json=response_body(json.dumps(value)))

        clients, calls = analysis_transport(monkeypatch, upstream)
        if side == "candidate":
            login(api, data.admin)
            await api.get("/health")
        path = (
            f"/sessions/{data.session.id}/analyze"
            if side == "baseline"
            else f"/admin/sessions/{data.session.id}/analyze_regenerate"
        )
        started = perf_counter()
        response = await api.post(
            path, headers={"x-csrf-token": api.cookies["csrftoken"]}
        )
        if sample["length"] == "long":
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "plan_required"
            plan = response.json()["plan"]
            assert (
                len(plan["chunks"])
                == sample["planner_fixture"]["expected_chunks"]
            )
            assert calls == []  # No execution before explicit confirmation.
            response = await api.post(
                path,
                json={"plan_hash": plan["plan_hash"]},
                headers={"x-csrf-token": api.cookies["csrftoken"]},
            )
        assert response.status_code == 200, response.text
        elapsed = perf_counter() - started
        login(api, data.owner)
        reader = await api.get(f"/sessions/{data.session.id}/analysis")
        assert reader.status_code == 200, reader.text
        output = reader.json()
        assert output["feedback_status"] == (
            "degraded"
            if side == "baseline" and sample["boundary"] == "greeting_only"
            else "ok"
        )
        assert side in output["feedback"]
        assert [(m["role"], m["content"]) for m in output["messages"]] == [
            (m["role"], m["content"]) for m in sample["transcript"]
        ]
        enabled = sample["snapshot"]["config"]["analysis"][
            "classification_enabled"
        ]
        assert output["classification_enabled"] == enabled
        expected_calls = (
            1
            if not enabled
            else (
                2
                if sample["boundary"] == "greeting_only"
                else 2
                + sum(m["role"] == "teacher" for m in sample["transcript"])
            )
        )
        if side == "candidate":
            expected_calls = plan["generation_calls"] if plan else 1
        assert len(calls) == expected_calls
        assert all(c.is_closed() for c in clients)
        selection = sample["snapshot"]["config"]["analysis"][
            "resolved_model_config"
        ]
        for call in calls:
            assert call["model"] == selection["model_id"]
            assert (
                call["max_output_tokens"]
                == selection["options"]["max_output_tokens"]
            )
            assert call["reasoning"] == selection["options"]["reasoning"]
        synthesis = calls[-1]["input"][0]["content"]
        if side == "candidate" and plan:
            from s4_analysis_fixtures import prompt_inputs

            assert "messages" not in prompt_inputs(calls[-1])
            assert [
                mid
                for call in calls[:-1]
                for mid in prompt_inputs(call)["owned_message_ids"]
            ] == [m["id"] for m in sample["transcript"]]
        else:
            assert all(m["content"] in synthesis for m in sample["transcript"])
        if side == "candidate":
            async with data.factory() as db:
                run = await db.get(
                    GenerationRun, output["latest_run"]["run_id"]
                )
                plan = json.loads(run.plan_json)
        results[side] = dict(
            code_revision=code_revision,
            execution_contract=("s2-v1" if side == "baseline" else "s4-v2"),
            provider=selection["provider"],
            exact_model_id=selection["model_id"],
            options=selection["options"],
            prompt_versions=prompt_versions,
            rendered_prompt_hashes=[canonical_hash(c["input"]) for c in calls],
            schema_version="RuntimeSynthesis-v1" if side == "baseline" else 2,
            schema_hashes=[
                canonical_hash(c["text"]["format"]["schema"]) for c in calls
            ],
            estimator_version=(
                "not_used_by_s2_analysis"
                if side == "baseline"
                else plan["estimator_version"]
            ),
            chunk_policy_version=(
                "not_used_by_s2_analysis"
                if side == "baseline"
                else plan["chunk_policy_version"]
            ),
            plan_hash=(
                "not_used_by_s2_analysis"
                if side == "baseline"
                else plan["plan_hash"]
            ),
            output=output,
            raw_provider_outputs=raw,
            validation_status=response.json()["feedback_status"],
            call_count=len(calls),
            mock_elapsed_seconds=elapsed,
            measurement_source="mock_sdk_transport_only",
        )
    await data.db.refresh(
        data.session, attribute_names=["config_hash", "config_snapshot_json"]
    )
    assert data.session.config_hash == sample["config_hash"]
    assert data.session.config_snapshot_json == sample["snapshot"]
    record = mock_comparison_record(corpus, sample_id, **results)
    assert record["release_status"] == "release_blocked"
    assert record["education_approval"]["decision"] == "pending"
    row = next(r for r in record["samples"] if r["sample_id"] == sample_id)
    assert row["baseline"]["options"] == row["candidate"]["options"]
    assert row["candidate"]["elapsed_seconds"] == "unknown"
    assert row["candidate"]["total_tokens"] == "unknown"
    assert row["candidate"]["cost"] == "unknown"
    (tmp_path / f"comparison-{sample_id}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
