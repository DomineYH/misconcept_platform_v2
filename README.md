# misconcept_platform_v2
misconcept_platform_v2

## Local development

Requires Python 3.11+ and uv. Install the locked runtime and test dependencies:

```sh
uv sync --frozen --extra dev --python 3.12
cp .env.example .env
# Set OPENAI_API_KEY, SESSION_SECRET (openssl rand -hex 32),
# and ADMIN_DEFAULT_PASSWORD in .env before seeding a new development DB.
uv run --frozen python -m src.db.seed
uv run --frozen uvicorn src.main:app --reload
```

`CONTEXT_WINDOW_TURNS` defaults to 10 completed teacher–student pairs.
Student input includes those prior pairs and the current question once; mentor
input includes the prior pairs and its target pair, excluding later turns.
On upgrading to Phase A, review any explicitly configured value: it previously
counted individual messages (including mentor rows), and now counts complete
pairs. Existing values are preserved, so administrators should adjust them
before starting new sessions. Failed attempts and greetings consume no slots.

Tests need no API credentials or network. They override configuration before
imports, reject outbound sockets, and create databases only under pytest's
temporary directory. The repository's dialogue_sim.db and .env are never used:

```sh
uv run --frozen python -m pytest -q
```

The original GitHub commit remains an ancestor. The application baseline is the
commit titled `chore: preserve local application baseline for issue #1`; it
contains the original product code, without credentials, databases or caches.
See [refactoring checks](docs/refactoring.md) for subsequent verification.
