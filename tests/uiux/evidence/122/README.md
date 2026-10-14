# #122 teacher practice evidence

Synthetic offline browser flows against real Jinja templates. No production DB,
real accounts or paid AI calls. #121 baseline captures remain unchanged in
`../121`; this directory records #122 layout and state changes.

Reproduce with the authorized dependency environment:

```sh
npm run test:browser -- uiux_chat uiux_analysis uiux_history uiux_practice mentor_stream
```

`browser_uiux_practice.mjs` records 6 screens × 7 widths × 2 themes = 84 cases
in `measurements.json`. Widths: 320/390/767/768/769/1024/1440 CSS px, height 900.
It measures settled rendered text with ancestor backgrounds and opacity,
secondary-button/input boundaries against both surfaces, action dimensions,
document overflow, and independent long-information/message scrolling. Color
pairs are deduplicated with a representative text. Measurements include rendered
controls below the initial viewport; hidden panes are excluded.

- Composed text contrast minimum: 4.8994989766:1; no failing pairs.
- Required boundaries minimum: 3.6894605419:1 against adjacent surfaces.
- Action targets: minimum 44 × 44 CSS px.
- Document overflow: 0/84 cases.
- Chat: 35% information at 769/1024/1440; stored 48px collapse; 768px mobile
  boundary; two independently scrolling regions and composer outside messages.
- `browser_uiux_chat` additionally checks mobile height changes, keyboard tabs,
  draft retention and editing shortcut exclusion, plus 512×450 and 720×450
  reflow. Those reduced viewports are not actual 200% browser zoom.

36 PNGs: six practice screens at 390/1440 in both themes (24), teacher analysis
modal preserved-report/load-failure states in both themes and widths (8), and
completed late coaching plus normal no-intervention with a preserved draft (4).
`modal-measurements.json` records the eight modal boxes: width ≤900px, height
≤90dvh, and ≥16px horizontal margins. Screenshots capture the current viewport,
not every part of the long transcript.

Behavior evidence additionally covers student pending/recovery/incomplete output,
zero token-driven live-status mutations, mentor target ownership/manual help/
policy waiting/normal no-intervention, analysis progress with an accepted report,
stable keyboard focus and nonrepeating status announcements, exact evidence links,
teacher modal focus trap/return and GET-only failure retry, and authentication
expiry returning to accessible login guidance. See the mandatory worker report
for exact commands, exits, historical failures and related HTTP integration tests.

Real 200% browser zoom, mobile virtual keyboard, screenreader speech and real
hardware have not been tested. The DOM/live-mutation evidence is not a claim of
screenreader verification. Full regression/live acceptance and independent review
remain assigned to #127/coordinator; admin modal lifecycle remains #126.
