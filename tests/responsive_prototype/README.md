# Throwaway responsive decision prototype

Three structural variants on real fixture templates, gated by `?variant=A|B|C`.
Only this prototype branch contains the experiment. No product integration.

From this worktree, start with the already installed base Python:

```sh
/mnt/d/dev/misconcept_platform_v2/.venv/bin/python tests/browser_server.py
```

Open localhost port 8765:

- `/chat?variant=A` — long student/teacher/mentor messages, public information tabs.
- `/fixtures/s2/editor?variant=A` — five steps and public preview.
- `/fixtures/s2/editor?variant=A&comparison=1` — old role comparison with synthetic results.
- `/fixtures/responsive/conversation?variant=A&panels=3` — actual new comparison template and renderer (1–3 panels).

Use the floating previous/next buttons or Left/Right outside text/select/contenteditable
and existing comparison tabs. Switching preserves DOM and input; reloading preserves
the variant, not unsaved editor/comparison input. Chat keeps its existing draft and
collapse storage. System light/dark preference applies; no new theme toggle.
All writes are simulated rejection, legacy result actions are no-ops. This host has
no database/provider transport. Native save/adoption/streaming are not demonstrated.
Ordinary fixture URLs and production templates have no switcher or injected styles.

## Decision: A, existing contracts first

- Chat: <=768 mobile context/chat buttons, >768 side-by-side with 35% information
  and remaining chat space; existing collapse rail, localStorage and Ctrl/Cmd+B
  (excluding text entry) remain. Each context/message pane scrolls internally;
  composer stays outside message scroll. Long headings/actions wrap. B stacks a
  340px-max context above 600px chat on desktop; C overlays a nonmodal context
  drawer using the existing collapse control. Both keep mobile tabs.
- Editor: <=800 one column, >800 form + 320px public preview, five steps preserved.
  Document owns scroll. B always stacks preview after form; C uses an explicit
  disclosure before form. A shows the preview without an extra disclosure.
- New comparison: <=800 one column, >800 auto-fit with 320px minimum and 16px gaps.
  At 1024 three models use 2+1; at 1280/1440 all three fit. One/two panels use available
  width. Document scroll reaches every result and action; no hidden mobile results.
  B always stacks; C collapses canonical history and uses two result columns >800.
- Legacy comparison: unchanged in all variants: <768 tabs with Left/Right/Home/End;
  768–1279 grid owns horizontal overflow when needed, each panel at least 320px;
  >=1280 equal columns. A one-panel case need not overflow. Three 320px panels
  can also fit toward the upper end of the intermediate range.

A wins because simultaneous context and work remain discoverable on desktop and
existing narrow-screen habits survive. New comparison's former 260px auto-fit is
made deliberately wider for long model names/results; explicit <=800 stacking is
independent of the legacy comparison's historical 768/1280 contract. B lengthens
travel between context and actions; C trades visibility for space and adds toggles.
The 35% chat allocation is a prototype recommendation, not a new data contract.

## Verification

For checks, temporarily link the existing base dependencies (remove these links afterward):

```sh
ln -s /mnt/d/dev/misconcept_platform_v2/.venv .venv
ln -s /mnt/d/dev/misconcept_platform_v2/node_modules node_modules
```

```sh
npm run test:browser -- responsive_prototype
npm run test:browser -- browser_chat.mjs s2_authoring s5_layout conversation_models
.venv/bin/python -m ruff check tests/browser_server.py tests/responsive_prototype/fixtures.py
.venv/bin/python -m black --check tests/browser_server.py tests/responsive_prototype/fixtures.py
```

A: 320/390/767/768/769/800/801/1024/1279/1280/1440, both themes, all four surfaces.
B/C: 390/1024/1440 representative checks. 1/2/3-panel fixtures at 390/1024/1440.
512x450 and 720x450 test half-viewport reflow equivalent to 1024/1440 at 200%.
This is not real browser zoom, OS text scaling, virtual keyboard, screen reader,
or a complete accessibility/contrast audit. Fixture switcher can overlay document
content during scroll; inputs can be brought above it. Native production tests
run on ordinary fixture URLs. Full product regression/live/provider tests are out
of this planning-only scope. Status hierarchy and final handoff remain separate
open decisions. No glossary/ADR change is needed.

Evidence/report: `/tmp/uiux-responsive-d1yRlZ/report.md` and neighboring PNGs.
