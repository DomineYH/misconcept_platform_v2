# #121 shared themes and entry flow evidence

Synthetic, offline browser screens; no production DB, paid AI, or real accounts.
The source fixes are in `59728ea` and the following #121 consumer/focus repair commit.
This evidence covers #121 and global token impact. It is not #127 final acceptance.

## Reproduce

```sh
npm run test:browser -- uiux_entry uiux_visual
uv run --frozen python -m pytest -q tests/test_uiux_entry.py
```

The visual check writes this directory. It reuses real Jinja screens and existing
screen fixtures; the comparison snapshot is synthetic completed/failed output,
not a generated production comparison. Measurements wait for finite animations
to settle, composite ancestor backgrounds and text alpha, and inspect rendered
text, input boundaries, and button/tab bounds. Repeated color pairs are grouped
in JSON with a sample text and count. Screenshots show the initial 900px-high
viewport; measurements include rendered controls below that viewport as well.
Hidden panels and unopened modals are not represented by those measurements.

## Measured results

`measurements.json`: 13 screens × 11 widths × 2 themes = 286 cases.
Widths: 320, 390, 767, 768, 769, 800, 801, 1024, 1279, 1280, 1440 CSS px.
Each screen has light/dark 390/1440 PNGs: 52 captures in this directory.

| Check | Observed result |
| --- | --- |
| Composed text contrast | minimum 4.859845689:1; no failing pairs |
| Required input / secondary-button boundary, both adjacent surfaces | minimum 3.689460542:1 |
| Measured button, button-link, tab targets | minimum width 44px, height 44px |
| Document horizontal overflow | 0 of 286 cases |
| Failing text pairs | `contrast-failures.json` is empty |

All widths passed those assertions for login, normal/empty/unavailable selection,
chat, preserved analysis with latest failure, scenario administration, authoring,
new/legacy comparison, AI settings, users, and usage. This is a rendered sample
matrix, not an assertion that every state of every screen was tested in both themes.

`browser_uiux_entry.mjs` additionally checks keyboard skip navigation, native
required-field focus, login errors/help relationships, current navigation,
1-column cards through 768px and auto-fit thereafter, start-link keyboard order,
2px focus/offset/surface gap, opaque disabled controls and public-only information.
The HTTP tests check real login failure/success, POST logout with explicit CSRF
middleware, signed session removal, teacher/admin navigation and authorization.
Existing lesson-snapshot tests cover assignment/publication/activation, public
projection and start/reuse without provider calls.

## Global consumer map

| Contract group | Evaluated impact / evidence |
| --- | --- |
| 01 Shell | shared CSS; entry keyboard/current-menu checks and all browser families |
| 02 Login | entry captures/keyboard; HTTP error/auth/CSRF/logout checks |
| 03 Selection | normal/empty/unavailable; long titles; card breakpoints; public profile |
| 04 Chat | rendered roles in both themes; existing chat/student/mentor/S3 checks |
| 05 Analysis | composed native report colors; existing S4/native analysis checks |
| 06 History | existing phase-A/history checks; neutral chart token consumer smoke |
| 07 Scenario list | rendered colors/targets; scenario filters/conflict and S6 checks |
| 08 Authoring | rendered controls and role/error fallback; existing S2 checks |
| 09 New comparison | synthetic completed/failed snapshot captures; saved-conversation checks |
| 10 Previous comparison | real existing editor comparison captures; S5 checks |
| 11 AI | rendered state fixture; existing AI connection/model/probe checks |
| 12 Dashboard | shared cards/navigation; existing S5 entry check |
| 13 Users/bulk | direct inline colors corrected; existing S6 and bulk credential checks |
| 14 Groups | shared shell/form/table/button consumers inspected; dedicated CRUD/keyboard evidence deferred to #126/#127 |
| 15 Sessions/user conversations | shared table/modals and existing record/history checks; full modal lifecycle deferred |
| 16 Analysis management | shared forms/tables inspected; dedicated management evidence deferred to #126/#127 |
| 17 Usage | white hardcoded summary surface repaired; captures and existing usage check |
| 18 Public/admin About | shared shell/modal/form colors inspected; focus/load/failure workflow remains #126 |

## Repairs and limits

The initial entry check failed on login width before source changes. A rendered
contrast run exposed the hardcoded white usage summary in dark mode (1.16:1);
it now uses the theme surface. The first measurement run also sampled message
fade-in, so the measurement now waits for finite animations. Actual 320px
pagination links were under 44px and their owning rule was corrected.

A broad browser run completed with 65 passing checks and 3 failures, not a full
pass: the new fixture request lacked `query_params` (S5 layout/scenario list),
and the new immediate keyboard focus assertion caught the button's `all`
transition. Corrected fixture/focus checks passed on targeted reruns. The final
matrix passed separately. Existing chart fill/track colors were checked on the
existing fixture after neutral token mapping: text contrast 6.263:1 light and
7.406:1 dark at 390/1440; no history-flow feature or new fixture was added.

No full pytest or final standalone live regression was run; those remain #127.
CI requires those plus ruff, black and the whole browser command. Ruff/black
passed locally. The broader browser failure record must remain visible alongside
the corrected targeted results. No independent reviewer sign-off is claimed.
Real 200% zoom, mobile keyboard, screenreader announcements, all modal states,
and exhaustive 18-group state combinations were not measured. Later-ticket
layout/state workflows, About focus/error lifecycle and management CRUD were
not added here.
