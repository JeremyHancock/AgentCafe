# Contributing to AgentCafe

Thanks for your interest in contributing! AgentCafe is an open-source marketplace where AI agents discover and use services on behalf of humans.

## Getting Started

```bash
git clone https://github.com/JeremyHancock/AgentCafe.git
cd AgentCafe
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,wizard]"
```

Start the Cafe locally:

```bash
PASSPORT_SIGNING_SECRET=dev-secret-minimum-32-bytes!! \
ISSUER_API_KEY=admin123 \
python -m agentcafe.main
```

## Running Tests

```bash
pytest tests/ -v
```

All tests must pass before submitting a PR. The CI pipeline runs lint and tests automatically on every push and PR to `main`.

## Linting

```bash
python -m pylint agentcafe/ tests/ --disable=C,R
```

We maintain a **10.00/10** pylint score. PRs that lower the score will not be merged.

## Code Style

- **Python 3.12** — use modern syntax (type unions with `|`, etc.)
- **No comments unless they add real value** — code should be self-documenting
- **Imports at the top** — never import in the middle of a file
- **Pydantic models** for all request/response schemas
- **Module-level `_State` class pattern** — modules expose a `configure_*()` function called at startup; tests monkeypatch `_state` attributes

## Project Structure

Read `AGENT_CONTEXT.md` first — it's the project bible. It covers the full architecture, module map, and phase history.

Key directories:

- `agentcafe/cafe/` — Core Cafe logic (menu, passport, consent, cards, MCP adapter, pages)
- `agentcafe/wizard/` — Company onboarding wizard (spec parser, AI enricher, publisher)
- `agentcafe/db/` — SQLite schema, migrations, seed data
- `agentcafe/demo_backends/` — Three demo services for local testing
- `tests/` — Pytest suite (async tests use `pytest-asyncio`)

## Database Migrations

Migrations live in `agentcafe/db/migrations/` as numbered SQL files (e.g., `0011_mcp_request_log.sql`). They run automatically on startup via `run_migrations()`.

To add a migration:
1. Create `agentcafe/db/migrations/NNNN_description.sql`
2. Use `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`
3. The migration runner applies them in order and tracks versions in `schema_version`

## Architecture Decisions

Significant design choices are recorded as ADRs in `docs/architecture/decisions.md`. If your change introduces a new pattern or alters existing behavior, consider adding an ADR entry.

## What to Work On

- Check the [issues](https://github.com/JeremyHancock/AgentCafe/issues) for open tasks
- See `docs/planning/development-plan.md` for the roadmap
- Bug fixes and test improvements are always welcome

## Pull Request Process

1. Fork the repo and create a feature branch
2. Make your changes — keep PRs focused and small
3. Ensure all tests pass and pylint stays at 10.00/10
4. Write tests for new functionality
5. Update `AGENT_CONTEXT.md` if you add new modules or change architecture
6. Open a PR against `main` with a clear description

## Security

If you discover a security vulnerability, please report it privately rather than opening a public issue. Contact the maintainers directly.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
