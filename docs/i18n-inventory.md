# 표시 언어 inventory and handoff (#134 → #135/#136)

This is an inventory of string groups and state/request boundaries, not a list
of user content to translate. #134 owns the shared contract; #135 and #136 add
translations and connect their own state. Parent acceptance is #133/#137.

## Shared ownership

| Screen / source | App strings (including errors and accessibility) | Input / task guard | Requests | Owner |
| --- | --- | --- | --- | --- |
| `layout.html`, `display-language.js` | product/footer, skip link, navigation/account, selector name/state, input/busy/cookie notices, toast close | registered page callbacks; opening a modal alone is allowed | HTMX config, logout POST, About fetch | #134 / `common` |
| `login.html`, `routes/auth.py` | title, labels, required/help/placeholder, submit, credential/ID errors | username/password, including failed authentication values | full form POST; full navigation follows cookie | #134 / `login` |
| `about_fragment.html`, layout About inline JS, `routes/about.py` | title, close, phone/email, loading/empty/load failure | no guard solely for open modal or read-only load | `/about/content` | #134 / `about` |
| `modal-lifecycle.js`, `analysis-toggle.js` | modal lifecycle is shared; analysis toggle wording belongs to analysis | modal itself is not dirty | consuming page owns request | #134 lifecycle; #135 analysis strings |

## Teacher screens and shared detail components (#135)

| Screen / sources | String groups | Input / task guard | Request / render boundaries |
| --- | --- | --- | --- |
| `scenarios.html`, `routes/scenarios.py` | page/title, scenario cards, start/disabled/empty/help, accessible names | any non-URL choices before starting | start session POST, scenario list/detail |
| `chat.html`, `chat.js`, `sessions.js` | title, controls, notices, alert/confirm, status/errors, labels, end/analysis actions | unsent message, ending/analyzing; failed input remains protected | message POST, end/close/analyze, HTMX fragments; preserve status/code |
| `student-stream.js`, `student-sse.js`, `routes/student_generation.py` | queued/streaming/completed/retry/recovery/auth/error notices | pending generation, lookup/retry/unknown terminal status | stream start/reconnect/recovery; capture start language |
| `mentor-stream.js` | checks/help/loading/retry/error/empty, per-turn accessible controls | all concurrent turn requests, manual question; one completion cannot release other work | mentor start/lookup/recovery, streamed events |
| `session_history.html`, `partials/session_row.html`, `routes/sessions.py` | history title/table/empty/status/actions | non-URL filter/search state if present | history navigation, partial refresh |
| `analysis.html`, `analysis-result.js`, `analysis-toggle.js`, `partials/analysis_{bar,result,modal,detail_tabs,detail_modal}.html`, `routes/session_analysis.py` | normal/degraded/failed/coverage, plan/chunk/run/cancel/retry, tabs, accessible names, errors/confirm | analysis plan selections, active analysis/cancellation, outstanding detail operations | analysis page/modal/detail, plan/run/cancel, HTMX, detail-open logging |
| `partials/message.html`, `session_detail.html`, `snapshot_provenance.html`, `routes/session_messages.py`, `session_helpers.py` | role/status/time/provenance, session-detail headings and empty/error/a11y groups | read-only modal alone allowed | direct message `.render(request=request, ...)`, shared detail/analysis fragments |

The last two rows are also consumed by administrators. **#135 owns their shared
strings and rendering**, #136 owns admin entry points, surrounding controls and
requests. Coordinate key additions in `teacher`, not duplicate admin translations.

## Administrator screens (#136)

| Screen / sources | String groups (include inline JS, alert/confirm, errors and a11y) | Input / task guard | Requests |
| --- | --- | --- | --- |
| `admin/dashboard.html`, `routes/admin.py` | dashboard sections, counts, navigation | none on read-only dashboard | dashboard/AI page navigation |
| `admin/scenarios.html`, `admin-scenarios.js`, `routes/admin_scenarios.py` | list/card/filter/page/empty/status, publish/delete/assignment errors and confirmations | filters not represented in URL, assignment/modal input, bulk choices | list/CRUD/publish/assign/search; include #132 strings when integrated |
| `admin/scenario_editor.html`, `scenario-editor.js`, `scenario-models.js`, `scenario_input.py` | steps, validation paths, save/preview/model/rubric/help/conflict/status | dirty/busy/edits counter; edits during save and failed/conflicting save remain dirty | create/update/version conflict/model selection; preserve structured code/path |
| `scenario-comparison.js`, `comparison-results.js`, comparison UI in editor | role/models/options/question, submit/results/partial/error/confirm | unsaved settings/question/model selection; submitting/running/recovering | comparison create/read/stream/cancel; captured start language |
| `admin/comparison_conversation.html`, `comparison-conversation.js`, `comparison-history.js`, `routes/admin_comparison*.py`, `comparison_input.py` | conversation/history/panels/adoption/source/apply/provenance/errors/confirm | selected models/question/settings, busy/recovery and pending children; terminal confirmation required | turn/adopt/mentor/end/recovery/history/draft/apply, SSE; preserve run IDs |
| `admin/ai.html`, `ai-connections.js`, `ai-render.js`, `routes/admin_{ai_settings,providers,models,catalog,probes}.py` | provider/model/key/options/catalog/verification, safe errors/conflicts, accessible editor and status | editor fields/key/current password, options/selections; saving/testing/probing/refresh/cancel | settings/keys/models/catalog/probes; never expose secrets or change storage |
| `admin/api_usage.html`, `routes/admin_api_usage.py` | metrics/table/status/filter/empty/tooltips/labels | non-URL filters | usage query/navigation |
| `admin/users.html`, inline JS, `routes/admin_users.py` | CRUD/password/CSV/preview/pagination/empty/error/confirm | user/password forms, CSV file and unconfirmed parsed rows, selection/non-URL filters, submissions | user create/update/delete/bulk/search; include #131 strings when integrated |
| `admin/groups.html`, inline JS, `routes/admin_groups.py` | group/member/scenario tables, forms/modals/empty/error/confirm | group form, member/scenario selection and non-URL search; pending mutations | CRUD/member/scenario assignment fetch |
| `admin/about.html`, inline JS, `routes/admin_about.py` | contributor forms/order/contact/CRUD/confirm/error/a11y | all contributor edits, pending delete/save/reorder | contributor CRUD/reorder; public fragment wording remains #134 |
| `admin/sessions.html`, inline JS, `routes/admin_session*.py` | list/stats/filter/export/delete/analysis action/modals, validation/errors | non-URL search/filter/selection, pending action/analysis | JSON list/stats/export/delete/analyze/detail; shared detail strings #135 |
| `admin/user_conversations.html`, inline JS | user/history/status/filter/empty/actions/errors | non-URL query/selection, pending mutations | user session list/detail/analysis entry points; shared fragment strings #135 |
| `admin/analysis.html`, inline JS, `routes/admin_analysis.py` | admin analysis list/filter/status/empty/actions/errors | non-URL filters, selected actions and pending analysis | list/analysis/detail; shared analysis renderer #135 |

All inline `<script>` blocks in the above templates belong to their screen owner.
All app-provided accessible names, title/help text, toast and alert/confirm bodies,
server validation messages and SSE terminal notices belong to the same owner as
their consuming screen. Do not infer control flow by reverse-translating text:
keep code/path/status and response shapes. Unexpected exception text is not a
catalog key and must not be newly exposed. Native browser validation/dialog UI is
outside the app-provided string inventory.

## Content that stays unchanged

Nicknames and usernames, contributor names/affiliations/contact/bios (About body),
scenario titles/problems/role instructions, rubric content, group/user data,
teacher messages, student/mentor/analysis/comparison output, snapshots and adopted
responses retain their original text and meaning. Model/provider identifiers,
paths/status/code, CSV input headers, prompt language and stored data contracts
are not display translations. No DOM-wide Korean replacement is permitted.

## Integration responsibility

#135/#136 must cover every direct fetch in their listed JS and inline scripts,
and every direct render must pass its request. A request must use the language of
the document that started it even if another tab changes the cookie. The common
HTMX hook covers same-origin HTMX requests. #137 verifies the final combined
candidate, including #131/#132 changes merged after this inventory.

## Contract for #135 / #136

- Add meaningful keys **only** inside `teacher` (#135) or `admin` (#136) in
  `src/i18n/catalog.json`: `"save": {"ko": "저장", "en": "Save"}`. Shared
  `common`/`about`/`login` keys are owned by #134; coordinate shared additions.
- Jinja uses `{{ t('teacher.save') }}` and `t('teacher.greeting', name=value)`.
  Values are plain strings and autoescaped. Do not apply `safe`. Missing English
  uses Korean; unknown keys show `표시할 수 없는 안내입니다.` rather than a key.
- Set `{% set i18n_areas = ['teacher'] %}` after `extends` in a full page (or pass
  the equivalent context). The common layout injects `#ui-strings` exactly once,
  containing common/About plus the requested sections in the request language.
  An admin page consuming shared teacher analysis/detail JS requests both sections:
  `{% set i18n_areas = ['admin', 'teacher'] %}`. Fragments reuse that page payload.
  JS uses `uiLanguage.t('teacher.save', {name: value})`; insert values with
  `textContent`, not `innerHTML`. Never render a second catalog in a fragment.
- Register `const unregister = uiLanguage.registerGuard(() => ({dirty, busy}));`
  after page state exists. The reader is evaluated at switch time; return current
  booleans, not an initial snapshot. Busy takes priority over input notices.
  Combine all active tasks (e.g. `pendingRequests.size > 0`) rather than releasing
  protection when one request finishes. Include failed-submit values, unsaved
  selections, unresolved recovery and edits made while a save is running. Call
  `unregister()` when a fragment/component is removed. Do not register modal-open
  state alone. Current-language notices use the common live status region.
- Direct fetches use `fetch(url, {headers: uiLanguage.headers(url, existingHeaders),
  ...otherOptions})`. Keep existing method/body/credentials/CSRF and abort signals.
  The helper returns `Headers`, so use `.set()` for subsequent additions. It sets
  `X-UI-Language` from `document.documentElement.lang` on same-origin URLs and
  removes it on external URLs. It does not wrap fetch, retry or change responses.
  HTMX is already covered by the common hook. Stream initiators must capture and
  retain the document language for reconnects, without consulting a changed cookie.
- Server errors use `translate('teacher.some_error', display_language(request))`
  from `src.i18n`; keep existing code/path/status. Direct template `.render()`
  must include `request=request` (the message renderer already does). Both that
  route and `TemplateResponse` use the same request-aware Jinja translator. No
  locale setter, account setting or additional browser store exists.

## Server-generated error / status groups
+
+| Boundary sources | Owner / treatment |
+| --- | --- |
+| `services/{student_stream,generation_runs,mentor_stream,mentor_generation,session_mgr,session_history}.py` and corresponding student/mentor/session routes | #135: safe retry/recovery/admission/auth/terminal display messages; preserve protocol state, IDs and retry semantics |
+| `services/{analysis_plan,analysis_chunks,analysis_pipeline,analysis_results,analysis_statistics,analysis_merge_budget}.py`, analysis routes | #135: plan/budget/coverage/partial/failure labels and safe notices; keep AI narratives, rubric values and stored report meaning unchanged |
+| `services/{scenario_drafts,scenario_publication,scenario_conversion,legacy_conversion_values,lesson_snapshots}.py`, scenario routes | #136: validation, publication, legacy conversion and provenance notices; shared session provenance rendering stays #135; stored snapshots and editable content are unchanged |
+| `services/{comparison_state,comparison_preparation}.py`, comparison routes/input schemas | #136: safe conflict/recovery/terminal/configuration errors; keep state/status/code and adopted responses unchanged |
+| `services/{model_configuration,model_capabilities,anthropic_capabilities,google_capabilities,student_probe_contract,role_probe_contract}.py`, provider/model/catalog/probe routes | #136: user-facing model/options/capability/probe notices; coordinate errors consumed by #135 through explicit keys/codes; synthetic probe prompts and provider/AI output are not display translations |
+| `services/admin_user_bulk.py`, user/group/About/admin filter/export routes | #136: CSV preview/validation/conflict/permission/delete notices; keep CSV headers/data and export schemas unchanged |
+
+Translate only strings that cross into UI or documented safe error fields. Korean
+comments, logs, AI instructions and data literals in these files are not a batch
+translation target. FastAPI/browser-provided validation mechanics retain their
+existing contracts; app-owned login credential/ID errors are covered by #134.
