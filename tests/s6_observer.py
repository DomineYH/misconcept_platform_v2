"""Test-only monotonic SDK observations and allowlisted ledger projection."""

import asyncio
import hashlib
import json
from time import monotonic_ns


def milliseconds(start, end):
    return (
        (end - start) / 1_000_000
        if start is not None and end is not None
        else None
    )


def input_metadata(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return dict(
        input_sha256=hashlib.sha256(encoded).hexdigest(),
        input_bytes=len(encoded),
    )


class Observation:
    def __init__(self, workload, repeat, fixture, *, limits=None):
        self.conditions = dict(
            **input_metadata(fixture),
            limits=limits,
            provider="openai",
            model="gpt-5-mini",
            runtime="native",
            automatic_retries=0,
            split=False,
            mock_body_delay_ms=10,
            tokens_source="synthetic_mock",
            price_source="unknown",
        )
        self.workload, self.repeat = workload, repeat
        self.calls, self.rows = [], []

    def stream(self, events, *, barrier=None, options=None):
        call = dict(start=monotonic_ns(), first_body=None, end=None)
        if options:
            call.update(input_metadata(options))
            call["model_options"] = {
                key: options[key]
                for key in ("model", "max_output_tokens", "reasoning")
                if key in options
            }
        self.calls.append(call)

        class Stream:
            async def __aiter__(self):
                if barrier is not None:
                    await barrier.wait()
                await asyncio.sleep(0.01)
                for item in events:
                    if isinstance(item, Exception):
                        raise item
                    if (
                        item.type == "response.output_text.delta"
                        and item.delta.strip()
                    ):
                        if call["first_body"] is None:
                            call["first_body"] = monotonic_ns()
                    if item.type in {
                        "response.completed",
                        "response.failed",
                        "response.incomplete",
                        "error",
                    }:
                        call["end"] = monotonic_ns()
                    yield item
                if call["end"] is None:
                    call["end"] = monotonic_ns()

            async def close(self):
                pass

        return Stream()

    def record(self, status, start, end, *, ledger=(), calls=None, final=None):
        calls = self.calls if calls is None else calls
        first = min(
            (c["first_body"] for c in calls if c["first_body"] is not None),
            default=None,
        )
        last = max(
            (c["end"] for c in calls if c["end"] is not None), default=None
        )
        starts = [c["start"] for c in calls]
        provider_start = min(starts) if starts else None
        row = dict(
            workload=self.workload,
            repeat=self.repeat,
            status=status,
            conditions={
                **self.conditions,
                "model": ledger[0].model if ledger else None,
                "model_options": [call.get("model_options") for call in calls],
            },
            clocks=dict(
                server="python.monotonic_ns",
                browser=None,
                ledger="UTC wall clock; raw only",
            ),
            raw_ns=dict(
                request_start=start, response_end=end, provider_calls=calls
            ),
            durations_ms=dict(
                request=milliseconds(start, end),
                provider_first_body=milliseconds(provider_start, first),
                provider_end=milliseconds(provider_start, last),
                request_to_provider_first_body=milliseconds(start, first),
                first_visible_body=None,
                save_complete=None,
                save_to_completion_event=None,
                final_validated_result=(
                    milliseconds(start, end) if final else None
                ),
                comparison_start_skew=(
                    milliseconds(min(starts), max(starts)) if starts else None
                ),
            ),
            unknown_reason="ASGI response buffered; browser visibility and exact save boundary unobserved",
            ledger=[
                dict(
                    id=entry.id,
                    role=entry.role,
                    operation=entry.operation,
                    status=entry.status,
                    attempt_no=entry.attempt_no,
                    run_id=entry.run_id,
                    started_at=(
                        entry.started_at.isoformat()
                        if entry.started_at
                        else None
                    ),
                    first_output_at=(
                        entry.first_output_at.isoformat()
                        if entry.first_output_at
                        else None
                    ),
                    finished_at=(
                        entry.finished_at.isoformat()
                        if entry.finished_at
                        else None
                    ),
                    input_tokens=entry.input_tokens,
                    output_tokens=entry.output_tokens,
                    estimated_cost_usd=entry.estimated_cost_usd,
                )
                for entry in ledger
            ],
        )
        self.rows.append(row)
        return row
