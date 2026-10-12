# Approved synthetic provider smoke (S6 #109)

This operator CLI defaults to planning with zero provider calls. It uses the
existing encrypted DB credentials, current role verification, invocation,
output validation and usage ledger. It never registers/probes models or retries,
splits or resumes calls. A selected provider/model/role is called once.

Use a separately provisioned, disposable synthetic SQLite DB at schema 035,
containing an administrator, settings and already registered/verified models.
Do not copy a classroom DB. The CLI rejects classroom records and the conventional
production filename. `TESTING=true` is required before Python starts: this
existing configuration switch disables `.env` loading, but does not mock SDKs.
Supply the synthetic DB encryption key/version through the process environment;
provider API-key environment variables are never used by the invocation path.
Do not give this process access to production keys or DBs. Model verification's
paid preparation requires separate approval; this command does not perform it.

Prepare `selection.json` as an array, for example:

```json
[{"model_config_id":1,"role":"student","options":{"max_output_tokens":1024}}]
```

Choose student, mentor, or short unified analysis. Options are explicit and
validated by the existing exact-model definitions. The output limit is required;
no input text is accepted: the existing fixed synthetic role fixtures are used.

```sh
TESTING=true uv run --frozen python -m src.cli.provider_smoke \
  --database synthetic.db --selection selection.json --owner-id 1 > plan.json
```

The output includes a canonical `plan_sha256`, exact provider/model/options,
role, input/schema hash, credential/config/definition versions, all call bounds,
price sources/check dates, and the summed USD reservation. An administrator
must review those details and provide a separate approval JSON record:

```json
{"plan_sha256":"COPY_FROM_PLAN","approver":"responsible administrator","approved_at":"2026-10-12T00:00:00+00:00","max_usd":"3"}
```

The approval hash binds every listed plan detail. The flag itself is not approval.
Execute with the same selection and a new output path:

```sh
TESTING=true uv run --frozen python -m src.cli.provider_smoke \
  --database synthetic.db --selection selection.json --owner-id 1 \
  --execute --approval approval.json --output smoke.json
```

The full maximum is reserved durably before the first SDK call. Approval is
consumed in the isolated ledger; repeating an approved plan is refused, including
after cancellation or interruption. A new run needs a fresh `--plan-id` and a new
human approval. No reservation is refunded or reused for subsequent calls.
The output path must be new and is written before execution. Keep this artifact
and the isolated DB together. A process crash may leave a running artifact and
unknown final usage; never interpret this as zero cost or automatically resume.

Input reservations use documented entire model input/context limits, including
system/schema framing; local context estimates are not billing guarantees.
Uncached input is the maximum OpenAI cache rate. Output includes reasoning under
`max_output_tokens`. Gemini requires explicit thinking budget 0 (disabled);
other thinking settings have no accepted enforced billing bound here. Claude
uses the worst documented cache-write rate across its full context, and output
includes thinking. These conservative reservations can refuse Claude or other
plans exceeding USD 3 even when a short prompt might cost much less. Unknown
models, missing limits/prices and aggregate budget excess refuse all calls.
No caller-supplied cheaper rate or estimated token count overrides these bounds.

Observed usage is priced by the existing provider normalizers and recorded
separately from the reserved maximum. Unknown cost, output/usage bounds violations,
refusal, timeout, cancellation or invalid output stop remaining calls. Cancellation
does not guarantee provider billing stops. Safe samples contain model/options,
hashes, token counts, pricing provenance, status and monotonic durations; no
prompts, responses, credential material or internal mentor reasons are included.
The `schema_version=1` samples use S6-06's workload/repeat/status/conditions,
`raw_ns`, `raw_browser_ms`, `durations_ms`, and allowlisted `ledger` fields and
can be summarized with `tests/s6_measurement.py::summarize`. Browser TTFT and
unobservable provider subintervals remain null. A smoke is a single sample,
not the five-repeat measurement workload.

This is not a provider billing control, a price-change guarantee, or a guarantee
that every model can be tested under USD 3. It does not replace S4 HG's twelve
baseline/candidate education-quality samples or that evaluation's separate
cost approval. Implementation tests mock SDK transport only; no real paid calls
are executed by CI or as part of this ticket.
