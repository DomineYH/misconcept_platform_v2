# Responsive decision evidence

Synthetic fixture screenshots for the A/B/C comparison. These capture prototype layouts, not production integration. The floating selector is test UI.

- [A: phone chat](a-chat-phone.png)
- [A: desktop chat](a-chat-desktop.png)
- [B: stacked desktop context](b-chat-desktop.png)
- [C: overlay desktop context](c-chat-desktop.png)
- [A: three-panel comparison](a-new-desktop.png)

The coordinator inspected these captures and selected A under the user's explicit approval of recommended choices. The decision itself is recorded in the [responsive layout decision](https://github.com/DomineYH/misconcept_platform_v2/issues/117).

## Verification on 2026-10-14

Prototype source: `6589f746c4576eaa7d1c99dc829529a520a99105`.

The coordinator started `tests/browser_server.py` using the existing base Python environment, waited for `/chat` to return HTTP 200, then ran the exact exported browser check functions with a new page per check and the same page-error assertions used by `tests/run_browser_checks.mjs`:

```text
PASS browser_responsive_prototype.mjs
PASS browser_chat.mjs
PASS browser_s2_authoring.mjs
PASS browser_s5_layout.mjs
PASS browser_conversation_models.mjs
PASS: browser closed; all 5 checks completed
Verification process exit 0; owned fixture server stopped.
```

The committed prototype test contains the coverage matrix: A on four surfaces at eleven widths in both themes; representative B/C layouts; one/two/three comparison panels; input preservation, tab/collapse/variant keyboard behavior, no mutation HTTP requests, and half-viewport reflow checks. Python ruff/black checks and `git diff --check` passed. The existing four checks also passed through the normal npm runner earlier.

A later combined npm invocation failed at the runner's 30-second server-readiness deadline before assertions. Sequential startup diagnosis served `/chat` in 22.28 seconds on unchanged base and 28.00 seconds on the prototype. The coordinator's completed check against a ready server resolves the later wrapper exit-143 uncertainty; it does not establish that the runner's startup timing is stable. No timeout or production code was changed.

Not checked: real browser zoom, virtual keyboard/touch hardware, screen reader, complete accessibility/contrast audit, product mutations/SSE/auth/database/provider integration, full regression suite. Half-viewport checks are reflow evidence, not true 200% browser zoom. Layout is selected; actual product integration remains future work.
