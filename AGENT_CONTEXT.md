# AGENT_CONTEXT.md — AgentCafe
**Project Bible for All AI Contributors — read this first before touching any code.**  
Last Updated: March 2, 2026 (Phase 7 in progress — deployed to agentcafe.io, 214 tests, pylint 10.00/10)

## 1. Project Vision & Origin
We are building **AgentCafe** — the friendly, trusted Cafe where AI agents discover and safely use services that companies have voluntarily registered.

Services put their offerings on the Menu.  
Agents browse freely.  
When they want to act, they present a valid Passport. The Cafe enforces safety as a mandatory proxy and forwards the request.

Key principles (locked):
- Zero mandatory pre-onboarding for humans (agent handles consent/pre-approvals)
- Company onboarding is the main product: ridiculously easy + completely free + insanely safe
- We are a full proxy — agents never touch backend URLs or long-lived tokens
- Double validation on every order: Human Passport + Company Policy
- The Menu is semantic, lightweight, and future-proof for 2026–2027 agents

## 2. Locked Menu Format (Feb 22 2026, extended ADR-023)
Agents receive a clean semantic menu (no HTTP methods, no paths, no full schemas).  
Schema policy: **no breaking changes**; additive fields allowed with an ADR (see `docs/architecture/decisions.md`).

**Services**:
- service_id (slug, format: `{brand}-{category}`, e.g. `stayright-hotels`)
- name
- category (string, e.g. `"hotels"`, `"food-delivery"`, `"home-services"`)
- capability_tags (array of strings for agent discovery)
- description

**Actions**:
- action_id (slug)
- description (what it accomplishes)
- example_response (JSON preview)
- cost (object: human_authorization_required, limits with rate_limit)
- required_inputs (array of {name, description, example})
- security_status (object: quarantine_until, suspended_at) — ADR-023 extension

When ordering: POST /cafe/order with service_id, action_id, passport, inputs.

**Passport V2 (Phase 4, locked):**
- Two-tier model: Tier-1 (read-only, agent self-requests) and Tier-2 (write-scope, requires human consent)
- The Passport is a HUMAN authorization document (ADR-024: "I authorize the bearer")
- Consent flow: agent initiates → human approves → Cafe issues short-lived JWT
- Token expiry enforced by risk-tier ceilings (low: 60m, medium: 15m, high: 5m, critical: single-use)
- See `docs/architecture/passport/v2-discussion.md` §13 for full convergence summary

## 3. Core Metaphor & Rules
- Central Cafe/Menu discovery is the core bet — inevitable and agent-first.
- Agents browse freely, order or leave.
- Company Onboarding Wizard is the product we polish until it feels magical.
- Human consent handling is explicitly an agent-side concern.
- Three mocked services for MVP to make the Cafe feel alive immediately.
- Security and company trust first.

## 4. Tech Stack (Locked)
- Python 3.12 + FastAPI
- SQLite (MVP)
- LiteLLM for wizard AI enrichment (optional — rule-based fallback when unavailable)
- Clean-slate (no forking existing gateways)

## 5. Current Status & What Exists

**Phase 0.2 — COMPLETE.** Service specs and Menu JSONs in `agentcafe/db/services/`.

**Phase 1 — COMPLETE.** Core Cafe: 3 demo backends, `GET /cafe/menu`, `POST /cafe/order` proxy, audit logging.

**Phase 3 — COMPLETE.** Company Onboarding Wizard: spec parser, AI enricher, review engine, publisher, full `/wizard/*` API.

**Phase 4 — COMPLETE.** Security & Guardrails (7 waves):
- Schema migration system (numbered SQL in `db/migrations/`)
- Passport V2: Tier-1 read tokens, Tier-2 write tokens via human consent
- Human accounts (`cafe/human.py`): register/login, session JWT (passkeys planned, currently email+password)
- Full consent flow (`cafe/consent.py`): initiate → approve → exchange → refresh
- Risk-tier token ceilings, policy revocation, max 20 active tokens per policy
- Identity verification (Gate 1b), ADR-023 Menu fields, implicit read
- Consent page UI (Jinja2 templates at `/authorize/`)
- Input injection protection, consent privacy enforcement (JWT audience separation)
- Backend credential encryption (AES-256-GCM, `crypto.py`)
- Tamper-evident audit logging (SHA-256 hash chain)
- Service onboarding security: quarantine mode (7-day default, configurable via `QUARANTINE_DAYS`), instant suspension

**Phase 5 — COMPLETE.** Testing & Polish:
- E2E demo agent CLI (`python -m agentcafe.demo_agent`)
- `ENRICHMENT_MODEL` configurable via env var, `x-agentcafe-*` extension merging
- Confidence scores in review/preview, spec upload (multipart) + URL fetch
- Company Onboarding Wizard Dashboard (Next.js 15 / React 19 / Tailwind 4)
  - Pages: `/login`, `/register`, `/onboard` (4-step wizard), `/services` (management), `/admin` (platform admin)
- 11 E2E integration tests

**Phase 6 — COMPLETE.** Packaging & Release Prep:
- RS256 passport signing (`keys.py`), JWKS endpoint, dual-key rotation, HS256 legacy fallback
- Production Docker (multi-stage, non-root), Docker Compose hardening
- Edit-after-publish (`POST /wizard/services/{id}/edit`)
- MIT LICENSE, README updated for v0.1.0

**Phase 6.1 — COMPLETE.** Security Review (ADR-026):
- Sprint 1: Revoke endpoint auth, CSRF tokens, bcrypt for human passwords, registration rate limiting, CORS hardening, FK enforcement, admin API key in header
- Sprint 2: Multi-action consent, audit hash chain concurrency fix (seq column + asyncio.Lock), configurable quarantine
- Sprint 3: Human dashboard (policy management + one-click revoke), consent webhook/callback
- 214 tests passing, pylint 10.00/10

**Phase 7 — IN PROGRESS.** Deployment & Real-Agent Beta:
- Deployed to Fly.io (app: agentcafe, region: iad). Live at **agentcafe.io** (Cloudflare DNS + Let’s Encrypt TLS)
- CI/CD via GitHub Actions: lint → test → deploy on push to main
- Landing page with beta banner, light/dark mode, live service cards
- Demo agent tested against production (headless + interactive human consent approval)
- Integration examples: `examples/openai_agent.py` (GPT function calling), `examples/claude_agent.py` (Claude tool_use)
- Human-facing UX uses cafe metaphor ("Tab" not "Passport"), ☕ emoji branding
- HIGH PRIORITY pending: WebAuthn passkeys, production UX flows (company wizard, admin dashboard)

## 6. Codebase Map

```
AgentCafe/
├── agentcafe/                      # Python package
│   ├── main.py                     # Entry point — starts Cafe (port 8000) + 3 demo backends (8001-8003)
│   ├── config.py                   # Env-based config (CafeConfig dataclass)
│   ├── crypto.py                   # AES-256-GCM encrypt/decrypt for backend credentials
│   ├── db/
│   │   ├── models.py               # SQLite schema (all tables)
│   │   ├── engine.py               # DB connection singleton (aiosqlite)
│   │   ├── seed.py                 # Seeds demo data on startup
│   │   ├── migrate.py              # Numbered SQL migration runner
│   │   ├── migrations/             # 0001–0007 SQL migration files
│   │   └── services/               # Demo service Menu JSONs + OpenAPI specs (loaded by seed.py)
│   ├── cafe/
│   │   ├── menu.py                 # Assembles locked Menu (incl. security_status)
│   │   ├── passport.py             # Passport V2: Tier-1 register, JWT validation, revocation
│   │   ├── policy.py               # Rate limiting + input type validation
│   │   ├── router.py               # GET /cafe/menu, POST /cafe/order, GET /cafe/admin/overview
│   │   ├── human.py                # Human accounts: register/login, session JWT
│   │   ├── consent.py              # Consent flow: initiate/approve, token exchange/refresh
│   │   └── pages.py                # Jinja2 server-rendered pages (login, register, /authorize/)
│   ├── wizard/                     # Company Onboarding Wizard
│   │   ├── models.py               # Pydantic models for all wizard + service management data
│   │   ├── spec_parser.py          # OpenAPI 3.x parsing + validation + operation extraction
│   │   ├── ai_enricher.py          # LiteLLM enrichment with rule-based fallback
│   │   ├── review_engine.py        # Draft management, edits, preview generation
│   │   ├── publisher.py            # Atomic publish to Menu + proxy configs (sets quarantine)
│   │   └── router.py               # /wizard/* endpoints incl. service management (pause/resume/unpublish/logs)
│   ├── demo_agent/
│   │   └── __main__.py             # E2E demo agent CLI (--headless for CI, 9 steps incl. read-before-write)
│   ├── demo_backends/
│   │   ├── hotel.py                # StayRight Hotels — 4 endpoints
│   │   ├── lunch.py                # QuickBite Delivery — 4 endpoints
│   │   └── home_service.py         # FixRight Home — 4 endpoints
│   └── templates/                  # Jinja2 HTML templates (landing, login, register, consent, dashboard)
├── dashboard/                      # Next.js 15 Company Dashboard
│   ├── src/app/
│   │   ├── login/page.tsx          # Company login
│   │   ├── register/page.tsx       # Company registration
│   │   ├── onboard/page.tsx        # 4-step onboarding wizard
│   │   ├── services/page.tsx       # Service management (pause/resume/unpublish/logs)
│   │   └── admin/page.tsx          # Platform admin dashboard (ISSUER_API_KEY gated)
│   ├── src/components/             # Wizard step components (spec-input, review, policy, preview)
│   ├── src/lib/                    # API client (api.ts) + auth helpers (auth.ts)
│   └── next.config.ts              # API proxy to FastAPI backend
├── examples/                      # Integration snippets
│   ├── openai_agent.py            # GPT function-calling agent for AgentCafe
│   └── claude_agent.py            # Claude tool_use agent for AgentCafe
├── tests/
│   ├── conftest.py                 # Shared fixtures: in-memory DB, ASGI test client
│   ├── test_menu.py                # Menu format compliance tests
│   ├── test_order.py               # Order proxy + input validation + audit tests
│   ├── test_passport.py            # JWT issuance, scope validation, revocation, Tier-1/Tier-2 tests
│   ├── test_policy.py              # Rate limiting + input type validation tests
│   ├── test_consent.py             # Full consent flow + human account tests
│   ├── test_wizard.py              # Spec parsing, enrichment, full wizard flow tests
│   ├── test_crypto.py              # AES-256-GCM encrypt/decrypt tests
│   └── test_e2e.py                 # 11 cross-cutting E2E integration tests
├── docs/
│   ├── architecture/
│   │   ├── decisions.md            # ADR-001 through ADR-026
│   │   └── passport/               # Passport V2: threat-model, v2-discussion, v2-spec
│   ├── planning/
│   │   └── development-plan.md     # Ordered phases with completion status
│   └── reviews/                    # Project reviews (review-1, review-2, wizard-fix-progress)
├── Dockerfile, docker-compose.yml  # Container setup
├── pyproject.toml                  # Dependencies and build config
└── AGENT_CONTEXT.md                # This file
```

## 7. What's Real vs. MVP Placeholder

| Component | Status | Notes |
|-----------|--------|-------|
| Menu format | **LOCKED & REAL** | No breaking changes. ADR-023 added security_status fields. |
| Menu discovery (`GET /cafe/menu`) | **Real** | Includes quarantine_until, suspended_at per action |
| Proxy (`POST /cafe/order`) | **Real** | Full Passport V2 validation, scope check, rate limit, audit |
| Demo backends (hotel, lunch, home) | **Real mock data** | In-memory, no persistence across restarts |
| Passport V2 (Tier-1 + Tier-2) | **Real** | RS256 JWT signing + JWKS endpoint, scopes, expiry, risk-tier ceilings, revocation |
| Human consent flow | **Real** | Initiate → approve → exchange → refresh. Full lifecycle. |
| Human accounts | **Real** | Register/login with bcrypt, session JWT, passkey enforcement |
| Rate limiting | **Real** | Sliding-window per passport+action, V2 429 response with retry_after |
| Company Onboarding Wizard | **Real** | Full API + Next.js dashboard. Spec parse/upload/fetch → review → policy → publish |
| Audit log | **Real** | SHA-256 hash chain, tamper detection via `verify_audit_chain()` |
| Backend credential encryption | **Real** | AES-256-GCM at rest (`crypto.py`), `CAFE_ENCRYPTION_KEY` env var |
| Quarantine / suspension | **Real** | 7-day quarantine on new services (configurable via `QUARANTINE_DAYS`), instant suspension via admin endpoint |
| Dashboard (Next.js) | **Real** | Login, register, 4-step wizard, service management, platform admin |
| Passport signing keys | **Real** | RS256 asymmetric signing, JWKS endpoint, dual-key rotation, HS256 legacy fallback |

## 8. How to Run

**Docker (recommended):**
```bash
docker compose up --build          # Builds image, starts 4 containers
# Menu:  http://localhost:8000/cafe/menu
# Order: POST http://localhost:8000/cafe/order
docker compose down                # Stop and remove containers
```

**Local (all-in-one process):**
```bash
source .venv/bin/activate
PASSPORT_SIGNING_SECRET=your-secret-min-32-bytes!! \
ISSUER_API_KEY=your-admin-key \
python -m agentcafe.main           # Starts Cafe (8000) + 3 demo backends (8001-8003)
# Menu:  http://127.0.0.1:8000/cafe/menu
# Order: POST http://127.0.0.1:8000/cafe/order
# API docs: http://127.0.0.1:8000/docs
python -m pytest tests/ -v         # 214 tests passing
python -m pylint agentcafe/ tests/ --disable=C,R  # 10.00/10
```

**Dashboard (Next.js):**
```bash
cd dashboard && npm run dev        # http://localhost:3000
# Proxies /api/* to localhost:8000 (backend must be running)
# Pages: /login, /register, /onboard, /services, /admin
```

**Demo agent:**
```bash
python -m agentcafe.demo_agent --headless  # 9-step lifecycle, auto-approves consent
python -m agentcafe.demo_agent --base-url https://agentcafe.io  # Against live site (interactive)
```

**⚠️ Stale DB caveat:** SQLite uses `CREATE TABLE IF NOT EXISTS`. Always `rm -f agentcafe.db` after schema changes. The migration system handles incremental changes, but if the base schema changed you need a fresh DB. Tests use in-memory DBs and are unaffected.

**Key env vars:**
- `PASSPORT_SIGNING_SECRET` — JWT signing key (random if unset, tokens invalidate on restart)
- `ISSUER_API_KEY` — Admin key for suspend endpoint and `/admin` dashboard
- `CAFE_ENCRYPTION_KEY` — 64 hex chars for AES-256-GCM backend credential encryption (disabled if unset)
- `OPENAI_API_KEY` — Enables LLM enrichment in wizard (rule-based fallback if unset)
- `ENRICHMENT_MODEL` — LiteLLM model name (default: `gpt-4o-mini`)

## 9. Architecture Notes

- **`_State` class pattern**: All module-level mutable state uses a `_State` class. Tests monkeypatch via `monkeypatch.setattr(module._state, "attr", value)`. Applied across all modules.
- **`configure_*()` functions**: Each module (`passport`, `consent`, `human`, `wizard`, `pages`) has a `configure_*()` function called at startup in `main.py` to inject shared secrets.
- **Shared httpx.AsyncClient**: `cafe/router.py` uses `_state.http_client` for proxying — reuses TCP connections.
- **Named row access**: All `aiosqlite.Row` results use `row["column_name"]`. `row_factory = aiosqlite.Row` set in `engine.py`.
- **Audit hash chain**: Each `audit_log` entry stores `prev_hash` and `entry_hash` (SHA-256 of all fields + prev_hash). `verify_audit_chain()` walks the chain to detect tampering.
- **Wizard auth**: JWT session tokens signed with `PASSPORT_SIGNING_SECRET`, 8-hour expiry, `iss=agentcafe-wizard`. Draft ownership enforced on all endpoints.
- **Passport V2 validation chain**: JWT decode → JTI revoked check → policy revoked_at check → tier check → scope check → authorization check.
- **Test patterns**: `_MultiBackendTransport` for backend mocking, DB save/restore in E2E tests to avoid session-scoped interference.
- **Decisions log**: See `docs/architecture/decisions.md` (ADR-001 through ADR-026).

## 9.1 Known Limitations

- **Review replaces, doesn't merge**: `PUT /wizard/drafts/{id}/review` stores company edits as a complete replacement. The dashboard pre-populates forms with AI values, but the API itself does full replacement.
- **In-memory DB by default**: SQLite on disk (`agentcafe.db`) or `:memory:` for tests. No replication or backup.
- **Rule-based enricher confidence is static**: Hardcoded at 60%/80%/40% (description/inputs/example_response). LLM path returns genuinely calibrated scores.
- **Read-keyword regex in spec parser**: `_classify_write()` uses substring matching for read keywords (`search`, `list`, `get`, etc.). This can misclassify operations whose IDs contain these as substrings (e.g., `widget` contains `get`). Use operationIds that avoid these substrings, or override `is_write` in the review step.

## 10. Rules for All AI Contributors
- Always respect the locked Menu format and proxy architecture.
- **Menu entries in `agentcafe/db/services/*-menu.json` are the single source of truth.** Edit those files to change what agents see; `seed.py` loads them at startup.
- Read the codebase map (Section 6) before making changes — know what exists.
- Check Section 7 to understand what's real vs. placeholder before building on top of it.
- Produce clean, readable, well-commented code.
- Include tests with meaningful changes.
- Security first — every call must go through double validation.
- Never expose backend URLs, auth headers, or internal paths to agents.
- When in doubt, ask rather than assume.
