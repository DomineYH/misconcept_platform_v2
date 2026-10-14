# #123 scenario list and five-step editor evidence

Synthetic screens through the existing isolated browser server and production
Jinja templates. No production database, paid provider, or real account was used.
Reuse the shared theme/entry baseline in `../121`; those artifacts were not regenerated.

Reproduce with the already installed dependencies:

```sh
npm run test:browser -- uiux_scenario
```

`browser_uiux_scenario_layout.mjs` checks light/dark at
320/390/767/768/769/800/801/1024/1279/1280/1440 CSS pixels, height 900px.
`measurements.json` contains 132 observations: all five editor steps and the
scenario list at each theme/width. It measures composed rendered text colors,
required input/secondary-button boundaries and button targets after finite
animations settle. Assertions require text 4.5:1 (large text 3:1), boundaries
3:1, button targets 44×44px, and no document overflow. Native checkbox target
size and their focus contrast are not included in this numeric measurement.

The editor also checks the exact 800px breakpoint, 320px preview, 24px gap,
76px sticky reference and scrolling the end of a long preview into the viewport.
Long Korean titles, public problem paragraphs, model display names and list
publication/activation/review/conversion states are synthetic fixture inputs.
The four editor and four list PNGs use 390/1440px in both themes, with captures scrolled to the document top. Editor images show the full document;
list images show the initial 900px viewport so headings and first-card actions
remain legible. Measurements include the entire rendered list.

`browser_uiux_scenario_errors.mjs` tests response loss → 422 → 409 at
390/1440px in both themes. `error-measurements.json` and twelve additional PNGs
cover uncertain save, field validation and conflict. Tests assert summary and
conflict focus, per-step errors, common-model and hidden-field navigation,
retained title/options/expected_version/publication/classification state,
no automatic save, and no request or navigation when reload confirmation is
cancelled. Hiding classification stays meaningful: error navigation focuses its
checkbox without enabling classification. Existing `browser_s2_save` covers
visible rubric errors, CSRF, draft success, publication status and 403 handling;
other s2/s6 checks cover remaining authoring/privacy/conversion/list contracts.

Keyboard evidence is automated Playwright Enter/Space/focus behavior in these and
existing user-flow checks. It is not real hardware, 200% browser zoom, mobile
virtual keyboard or screen-reader evidence. Those checks and the final combined
full/live regression remain #127/coordinator work. This is bounded #123 evidence,
not full application acceptance. Exact commands, intermediate failures and final
results are in `/tmp/misconcept-uiux-120/report-uiux123.md`.

Final measured minima:

| Matrix | Cases | Text ratio | Boundary ratio | Button target | Overflow |
| --- | ---: | ---: | ---: | --- | ---: |
| Five steps + list | 132 | 4.859845689:1 | 3.689460542:1 | 44×44px | 0 |
| Uncertain / 422 / 409 | 12 | 5.640820808:1 | 3.652153664:1 | 44×44px | 0 |

Error-summary links are activated with keyboard Enter. The hidden classification
control receives keyboard focus with a computed outline at least 2px wide.
The list title owns a row before actions, and the search wrapper adds no empty
height at mobile widths. In desktop rows a wrapper can stretch to the height of
wrapped filters; that does not add height beyond the filter group itself.
