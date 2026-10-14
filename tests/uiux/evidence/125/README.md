# #125 AI connections and role verification evidence

Synthetic, offline Jinja/Playwright screens. No production DB, real credentials,
provider error bodies, paid AI or dependency changes. The fixed checked revision
and exact normal exits are recorded in `/tmp/misconcept-uiux-120/report-uiux125.md`.
Existing #121 evidence was not regenerated.

Reproduce with the authorized dependencies:

```sh
npm run test:browser -- uiux_ai_evidence uiux_ai_states uiux_ai_layout
```

`measurements.json`: 5 states × 7 widths × 2 themes = 70 cases.
Widths: 320, 390, 767, 768, 769, 1024, 1440 CSS px. Themes follow the existing
system light/dark preference. Twenty full-page PNGs show 390/1440px in each theme:

| File prefix | State |
| --- | --- |
| roles | Ready provider + student success, mentor error, analysis revalidation, unknown capability + ongoing verification, unavailable default; long Korean name and unbroken model ID |
| blocked | Capability/catalog conflict + absent provider key; explicit trial-blocking reasons |
| master | Missing master key; existing masked hints and role evidence retained |
| editor | Nonmodal defaults/limits/timeouts editor with a keyboard-focused input |
| conflict | Synthetic 409; entered name retained, safe alert beside the form |

Provider cards and role groups use one column at 768px and below; the 769px
boundary is checked. Single forms remain at most 600px. Measurements reuse the
#121 color-composition method in a scoped check, wait for finite animations,
include rendered controls below the viewport and record text contrast, required
input boundaries, action sizes, overflow and focus outline/shadow metadata.

The browser check actually presses Enter, Shift+Tab and Escape at every theme/
width: first-field focus, focus outside the nonmodal editor, and caller return.
`uiux_ai_states` additionally verifies loading with retained prior state, safe
read failure, local catalog/form failures, input retention and eight distinct
trial blockers. Existing AI checks cover secret clearing, reauthentication,
CSRF, zero/omission/nested options, immutable IDs, unknown saved options,
role-specific defaults and explicit paid-trial confirmation/reconnect/cancel.

Observed measurements: 0 horizontal overflows; minimum text contrast 4.947845:1;
minimum measured input boundary contrast 4.153557:1; minimum action width/height
44/44px. Focus outline/shadow is recorded, rather than claiming screenreader
or real-device acceptance from headless checks. Full-page captures reset scroll
to the document top to avoid placing the sticky navigation over an editor crop.

Unverified here: real 200% browser zoom, mobile virtual keyboard, physical-device
interaction, actual screenreader speech, exhaustive focus contrast and independent
visual review. These remain with the coordinator/#127 final acceptance. No full
suite or complete CI pass is implied by these bounded artifacts.
