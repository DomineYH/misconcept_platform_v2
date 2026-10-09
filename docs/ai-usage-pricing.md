# API usage estimate rates

Checked **2026-10-09**. USD per million tokens. This is the check date,
not a claim about when the provider first made a rate effective. New priced
attempts store this date and their source; reading the dashboard never reprices
existing rows. Estimates use public paid list rates, before free allowances,
credits, negotiated discounts or taxes, and are not invoices.

| Exact model IDs | Billing scope | Uncached input | Cache read | Cache write | Output including reasoning/thinking | Official source |
| --- | --- | ---: | ---: | --- | ---: | --- |
| `gpt-5-mini`, `gpt-5-mini-2025-08-07` | Responses, observed `default` tier | 0.25 | 0.025 | No separate write item | 2.00 | [GPT-5 mini](https://developers.openai.com/api/docs/models/gpt-5-mini) |
| `gpt-5.2`, `gpt-5.2-2025-12-11` | Responses, observed `default` tier | 1.75 | 0.175 | No separate write item | 14.00 | [GPT-5.2](https://developers.openai.com/api/docs/models/gpt-5.2) |
| `claude-sonnet-4-6` | Messages, observed `standard` tier and `global` geography | 3.00 | 0.30 | 5 minutes: 3.75; 1 hour: 6.00 | 15.00 | [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| `gemini-2.5-flash` | Native generateContent, text only, default standard tier | 0.30 | 0.03 | No cache creation/storage calls in this adapter | 2.50 | [Gemini pricing, Japanese edition](https://ai.google.dev/gemini-api/docs/pricing?hl=ja) |

The Japanese official Gemini page retains the exact 2.5 Flash table; the English
page did not expose that section on the check date. No rate from Flash-Lite,
Image, preview or another model is substituted.

OpenAI input already includes cached input: subtract cache reads before applying
the uncached rate. Output already includes reasoning: charge output once.
The response's actual tier is authoritative; missing tier, `auto`, Flex and
Priority have no verified price entry here. See [Responses service tiers](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
and [reasoning billing](https://developers.openai.com/api/docs/guides/reasoning).

Claude input is the sum of uncached input, cache reads and cache writes. Each
component gets its own rate; 5-minute and 1-hour write counts must add to the
observed write total. Thinking is included in output, never added again. Missing
thinking detail alone does not prevent pricing an observed output total. Zero
observed cache writes imply zero duration counts; missing nonzero write durations,
other tiers or geography stay unpriced. See [cache billing](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

Gemini prompt includes cached content. Output is candidates plus thoughts;
missing thoughts remains unknown even when the requested thinking budget is 0.
The current native adapter sends text, has no tools, explicit cached-content
storage, Batch or service-tier override. Its omitted tier is documented as
standard in [generateContent](https://ai.google.dev/api/generate-content#ServiceTier).
The pinned SDK does not expose a generateContent usage tier, so this rule is
limited to that known native request path; changing the endpoint, tier, modality,
tools or cache storage requires reviewing its billing contract first. Paid versus
free account eligibility is not observable and is not inferred.

All providers require the exact observed response model, consistent observed
input/output/total and cache-read billing counts. An unknown model, missing
required component or inconsistent total yields NULL cost/date/source, even
when another registered model has a known rate. Cumulative stream counts replace
earlier observations within one attempt; retries remain distinct rows. Model-list
calls have no token estimate and are excluded from generation totals and
unpriced generation-attempt counts.

The old `calculate_cost(model, prompt, completion)` helper is removed: it had no
callers and could neither identify a service tier nor split cache billing. Legacy
stored estimates, including unknown pricing dates, are preserved unchanged.
