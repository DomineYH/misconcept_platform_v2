"""Measurement report contract: fixed examples, mixed outcomes, no payloads."""

from s6_measurement import summarize


def test_load_and_fault_workloads_are_required_for_complete_report():
    from s6_measurement import expected_samples

    expected = expected_samples()
    assert expected["slow_mentor_student"] == 12
    assert expected["slow_mentor_final"] == 6
    assert expected["panel_provider"] == 18
    assert expected["panel_429"] == 18
    assert expected["student_storage_failure"] == 6
    assert expected["student_disconnect"] == 6
    assert expected["analysis_invalid_evidence"] == 6
    assert expected["capacity_24_classroom_first"] == 138


def test_nearest_rank_and_all_outcome_denominators():
    rows = [
        {"status": "completed", "durations_ms": {"visible": value}}
        for value in [1, 2, 3, 4, 100]
    ] + [
        {"status": "failed", "durations_ms": {"visible": None}},
        {"status": "refused", "durations_ms": {"visible": 7}},
        {"status": "cancelled", "durations_ms": {"visible": None}},
    ]
    result = summarize(rows)
    assert result["counts"] == {
        "attempted": 8,
        "admitted": 7,
        "completed": 5,
        "failed": 1,
        "refused": 1,
        "cancelled": 1,
    }
    assert result["distributions"]["completed"]["visible"] == {
        "known": 5,
        "unknown": 0,
        "median_ms": 3,
        "p95_ms": 100,
    }
    assert result["distributions"]["failed"]["visible"]["p95_ms"] is None
    assert result["distributions"]["refused"]["visible"]["median_ms"] == 7


async def test_only_nonempty_provider_body_is_first_output():
    from types import SimpleNamespace

    from s6_observer import Observation

    observation = Observation("filter", 0, {"fixture": "synthetic"})
    stream = observation.stream(
        [
            SimpleNamespace(
                type="response.reasoning_text.delta", delta="reasoning"
            ),
            SimpleNamespace(type="response.output_text.delta", delta=""),
            SimpleNamespace(type="heartbeat", delta=""),
            SimpleNamespace(type="response.output_text.delta", delta="body"),
        ]
    )
    count = 0
    async for item in stream:
        count += 1
        assert (observation.calls[0]["first_body"] is not None) == (count == 4)
    assert observation.calls[0]["end"] >= observation.calls[0]["first_body"]


def test_provider_durations_start_at_sdk_boundary():
    from s6_observer import Observation

    row = Observation("clock", 1, {}).record(
        "completed",
        100_000_000,
        900_000_000,
        calls=[
            {
                "start": 200_000_000,
                "first_body": 500_000_000,
                "end": 800_000_000,
            }
        ],
    )
    assert row["durations_ms"]["provider_first_body"] == 300
    assert row["durations_ms"]["provider_end"] == 600
