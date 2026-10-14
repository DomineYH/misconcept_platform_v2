# #126 administrator evidence

Synthetic fixtures only. Generate with:

```sh
UV_PROJECT_ENVIRONMENT=/tmp/misconcept-uiux-120/merge121-venv PYTHONPYCACHEPREFIX=/tmp/misconcept-uiux-120/uiux126/pycache npm run test:browser -- uiux_admin
```

`browser_uiux_admin_evidence.mjs` measures rendered text contrast, input/button boundaries, 44px actions, document overflow, dialog bounds, footer visibility and focus. It covers light/dark at 320/390/768/1024/1440 CSS px, with 390/1440 captures. The bulk result password column is masked in PNGs and redacted in measurement text. No operating data or credentials are used. The final measurements are in `measurements.json`; `contrast-failures.json` records the final contrast failures.

`browser_uiux_admin_keyboard` uses actual Tab/Shift+Tab/Escape in Chromium and a synthetic 320×360 viewport in both themes; this is not a real virtual keyboard. Table checks use actual ArrowRight and inspect internal scroll. Modal/CRUD checks cover groups, About admin, users/panel, bulk upload/result, session analysis, summary session lists, and HTMX analysis detail. Tests preserve rejected inputs, destructive confirmation, row replacement/selection/download, CSRF, caller return and removed-caller fallback. Dedicated HTTP tests cover authenticated groups/About CRUD, validation, member deletion refusal, CSRF and teacher denial.

## Scope and all 18 contract groups

| Contract group | Ownership / evidence for this change |
| --- | --- |
| 01 shell/navigation | About lifecycle and persistent failure, long nickname wrapping; entry/practice and affected shared browser consumers |
| 02 login | Existing login preserved; public About through login fixture and `uiux_entry` |
| 03 selection | #121 baseline reused; `uiux_practice` |
| 04 chat | #122 owns teacher modal; `uiux_chat`, `uiux_chat_modal`, shared modal CSS consumer check |
| 05 post-session/shared result | #122 renderer unchanged; `s4_`, `native_analysis`, `s5_results`, `s5_analysis` |
| 06 history/detail/provenance | #122 baseline; `uiux_history`, `s4_records`, legacy HTTP/CSV |
| 07 scenario administration | #123; `s6_`, shared table bounds and shell consumers |
| 08 five-step authoring | #123; `s2_authoring`, `s2_save`, `s2_privacy` |
| 09 new comparison | #124; `s5_results`, `conversation_models` |
| 10 previous comparison | #124; `s5_layout`, `s5_analysis` |
| 11 AI setup/models | #125 owns nonmodal forms; `ai_models`, `ai_connections` |
| 12 dashboard | Existing quick actions preserved; dashboard captures, no generated statistics |
| 13 users/bulk | Named tables, modal/panel, regional errors, password/credential close/pagehide clearing; CRUD/bulk/keyboard fixtures and `bulk_passwords`, `s6_users` |
| 14 groups | Create/edit/error/focus/empty and destructive confirmation; group captures, HTTP CRUD/permissions/member guard |
| 15 admin sessions | Filter wrapping, named scroll, selected CSV/HTMX replacement, analysis load/error/close; session captures, `uiux_admin_sessions`, S4/CSV/integration |
| 16 conversation summaries | Summary table and session modal normal/empty/error, safe CSV links, load invalidation; summary captures and focused load tests |
| 17 analysis management | Named table/filter labels and detail loading/local error/focus/close; analysis/detail captures, focused HTMX check; unknown confidence preserved |
| 18 usage/public/admin About | Existing unknown costs/tokens and exports preserved; usage/About captures, `api_usage`, HTTP permission/CRUD and public About tests |

No DOM IDs, hx targets/swaps, form names, password minima, API/models/permissions or AI execution policy changed. Scoped classes on About dialogs now use existing overlay/container/body/footer styling. Session analysis partials keep their existing IDs and renderer mount.

Final full regression, real 200% browser zoom, actual mobile devices/virtual keyboards, screenreader announcements and independent visual acceptance belong to #127. Headless bounds/focus/contrast measurements do not certify those. Earlier failed runs are retained in `/tmp/misconcept-uiux-120/uiux126/` and summarized in the mandatory worker report. #121 evidence is unchanged.
