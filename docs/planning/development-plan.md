# AgentCafe Development Plan

**Current Status:** Phase 8.4 complete (Open Source Prep) — deployed to Fly.io, live at agentcafe.io, CI/CD green. 335 tests, pylint 10.00/10. Legacy Next.js dashboard removed. README rewritten, CONTRIBUTING.md added, documentation audit complete.
**Last Updated:** March 24, 2026

**MVP Success Criteria**
We can run end-to-end locally:
- ✅ Three demo services are registered (HotelBooking, LunchDelivery, HomeServiceAppointment)
- ✅ Company Onboarding Wizard works smoothly (Phase 3)
- ✅ Agent can browse the clean Menu
- ✅ Agent can successfully order using a valid Passport
- ✅ All calls go through the secure proxy with **real** double validation and full audit logs (`USE_REAL_PASSPORT=true` enables JWT)

**Tech Stack (locked)**
- Python 3.12 + FastAPI
- SQLite (MVP)
- LiteLLM for wizard
- Clean-slate architecture

---

**Full Ordered Phases**

**Phase 0: Discovery & Design** ✅
- 0.1 Competitive landscape (completed)
- 0.2 Design the three demo backend APIs + Company Onboarding Wizard (completed)
  - Deliverables: OpenAPI specs and locked Menu entries in `agentcafe/db/services/`. Wizard design docs removed (superseded by implementation).

**Phase 1: Project Bootstrap + Demo Backends** ✅
- ✅ Project structure: `pyproject.toml`, `agentcafe/` package, config, SQLite schema
- ✅ Three demo backends: `hotel.py` (port 8001), `lunch.py` (port 8002), `home_service.py` (port 8003)
- ✅ Menu discovery: `GET /cafe/menu` returns locked format from database
- ✅ Order proxy: `POST /cafe/order` with double validation (MVP passport) + audit logging
- ✅ Database seeding: 3 services, 12 proxy configs, auto-seeded on startup
- ✅ Tests: 85 passing (Menu format, action correctness, auth requirements, input validation, happy-path proxy, JWT passport issuance/validation/revocation, rate limiting, type validation, wizard spec parsing, enrichment, full wizard API flow, draft ownership, dry-run, hotel spec, post-publish management)

**Phase 2: Core Cafe Foundation** ✅
- ✅ **2.0 Passport System Design** — Grok + Claude collaboration. V1 design doc removed (superseded by V2 spec).
- ✅ 2.1 Docker Compose: Cafe + 3 demo backends in separate containers, real HTTP proxying, health checks, configurable backend hosts
- ✅ 2.2 Real Passport validation — JWT (HS256), scopes (`{service_id}:{action_id}`), wildcard (`{service_id}:*`), expiry, revocation via `revoked_jtis` table
- ✅ 2.3 Company Policy engine: sliding-window rate limiting (per passport+action from audit_log), input type validation (explicit `type` field per ADR-015, fallback to example inference)
- ✅ 2.4 Migration flag `USE_REAL_PASSPORT` routes between MVP and real JWT validation in `place_order`
- ✅ 2.5 Passport issuance (`POST /passport/issue`, API-key protected) + revocation (`POST /cafe/revoke`)

**Phase 3: Company Onboarding Wizard** ✅
- ✅ Spec parser (`wizard/spec_parser.py` — OpenAPI 3.0/3.1 ingestion, YAML+JSON, `$ref` resolution, operation extraction, read/write classification, required-only input filtering, x-agentcafe-* extensions)
- ✅ AI enricher (`wizard/ai_enricher.py` — LiteLLM generates Menu entries from specs, rule-based fallback when LiteLLM unavailable, no parameter truncation)
- ✅ Review engine (`wizard/review_engine.py` — draft management, company edits preserved separately in `company_edits_json`, preview generation)
- ✅ Publisher (`wizard/publisher.py` — atomic one-click publish to Menu + proxy configs)
- ✅ Wizard router (`wizard/router.py` — full API: companies, spec parsing, review, policy, preview, dry-run, publish)
- ✅ JWT session tokens — `Authorization: Bearer <token>` on all wizard endpoints, 8-hour expiry, ownership checks on all draft operations
- ✅ bcrypt password hashing (replaced SHA-256)
- ✅ Dry-run endpoint — single httpx client, HEAD requests against resolved backend action paths with example parameter substitution
- ✅ Pydantic input validation on company create (name length, email format, password minimum)
- ✅ Pydantic models (`wizard/models.py` — request/response models for all wizard endpoints)
- ✅ DB schema: `draft_services` table + `password_hash` column on `companies`
- ✅ 37 wizard tests (spec parsing, enrichment, hotel spec with $ref, full API flow, draft ownership, missing auth, dry-run, out-of-order steps, post-publish management)

**Phase 3.1: Code Quality & Lint Cleanup** ✅
- ✅ Pylint 10.00/10 — zero warnings across all source and test files
- ✅ Replaced all `global` statements with `_State` class pattern (engine.py, passport.py, cafe/router.py, wizard/router.py)
- ✅ Proper exception chaining (`raise ... from exc`) on all re-raised HTTPExceptions
- ✅ Narrowed broad `except Exception` to specific types where possible
- ✅ Removed unused imports and variables in test files
- ✅ See `docs/reviews/wizard-fix-progress.md` for the complete 12-fix improvement tracker

**Phase 3.2: Project Review Fixes** ✅
- ✅ Fixed scope mismatch: updated `cost.required_scopes` in all 4 design files (13 values) to match `seed.py` proxy_configs (e.g., `hotel:search` → `stayright-hotels:search-availability`)
- ✅ Standardized `cost.limits` format: all design files now use object form `{"rate_limit": "60/minute"}` matching wizard output (13 occurrences across 4 files)
- ✅ Post-publish management endpoints: `GET .../dashboard`, `PUT .../pause`, `PUT .../resume`, `PUT .../unpublish`, `GET .../logs` — all with JWT auth + ownership enforcement
- ✅ 4 new Pydantic response models (`ServiceDashboardResponse`, `ServiceStatusResponse`, `AuditLogEntry`, `ServiceLogsResponse`)
- ✅ 8 new tests (dashboard, pause, pause-idempotency, resume, unpublish, logs, ownership 403, not-found 404)
- ✅ Comprehensive `PROJECT_REVIEW.md` documenting all findings and remaining gaps

**Phase 4: Passport V2 & Security** (design converged — see `docs/architecture/passport/v2-discussion.md` §13)
- ✅ Real double validation with JWT verification (done in Phase 2)
- ✅ Passport revocation (done in Phase 2)
- ✅ Passport V2 design: 8 locked positions via three-way review (ADR-024)
- ✅ Menu schema extension for consent flow (ADR-023, ADR-009 amendment)
- ✅ **Passport V2 implementation — Tier-1 read Passports**: `POST /passport/register` returns Tier-1 JWT (`tier: "read"`, `granted_by: "self"`). Tier-1 tokens access read actions, rejected for writes with `tier_insufficient`. 5 new tests.
- ✅ **Passport V2 implementation — human accounts + consent flow**: `cafe/human.py` (register/login with session JWT, aud: human-dashboard), `cafe/consent.py` (POST /consents/initiate, GET /consents/<id>/status, POST /consents/<id>/approve, POST /tokens/exchange, POST /tokens/refresh). Migration 0002 adds cafe_users, consents, active_tokens tables. 20 new tests.
- ✅ **Passport V2 implementation — Cafe-side identity verification**: Gate 1b in router.py. Read-before-write enforced for medium+ risk actions with `human_identifier_field`. Missing identifier returns 422 `identity_field_missing`. No prior read returns 403 `read_before_write_required`. Low risk skips check. Implicit read access for Tier-2 tokens on same service. 4 new tests.
- ✅ **Passport V2 implementation — risk-tier token ceilings**: risk_tier column on proxy_configs (migration 0003), Cafe-enforced ceilings (low:60m, medium:15m, high:5m, critical:single-use). Human-chosen lifetime capped at ceiling. 2 new tests.
- ✅ **Populate ADR-023 Menu schema fields** — All 3 demo Menu JSONs updated with `risk_tier`, `human_identifier_field`, `rate_limit_scope`, `account_linking_required`, `concurrency_guidance` per action. Migration 0004 adds `human_identifier_field` to proxy_configs. Seed data updated.
- ✅ **Rate-limit 429 response** — V2-compliant error body with `error`, `detail` (per-policy shared budget explanation), `retry_after_seconds`, `policy_id`, plus `Retry-After` HTTP header. Computed from sliding window oldest entry.
- ✅ **Consent page UI** — Server-rendered Jinja2 pages via `cafe/pages.py`. Login (`/login`), register (`/register`), consent approval (`/consent/<id>`), decline (`/consent/<id>/decline`). Session cookies (httponly, samesite=lax). Cafe-authored plain-language consent text. Risk-tier badge, duration selector with ceiling enforcement. 5 templates, 8 new tests. Passkey confirmation deferred to post-MVP.
- ✅ **Consent privacy enforcement** — JWT audience separation (`aud: human-dashboard` vs `aud: agentcafe`) enforced on approve endpoint. No consent enumeration endpoint exists. Agent tokens rejected for human-session actions. 1 new test.
- ✅ **Policy revocation — instant for all tiers** — `revoked_at` column in policies table (migration 0001) + `iat < revoked_at` check in `validate_passport_jwt`. Returns `401 policy_revoked`. 3 new tests.
- ✅ **Input injection protection** — path parameter values validated against `_SAFE_PATH_VALUE` regex (alphanumeric, hyphens, underscores, dots, @, ~). Blocks traversal (`../../`), query injection (`?`), newlines, spaces. Unresolved placeholders rejected. Logged as `input_injection_blocked`. 5 new tests.
- ✅ **Backend credential encryption** (AES-256-GCM at rest) — `agentcafe/crypto.py` module. `backend_auth_header` encrypted on write (publisher, review_engine), decrypted on read (router proxy). Format: `enc::base64(nonce||ciphertext||tag)`. Graceful legacy plaintext passthrough. Key via `CAFE_ENCRYPTION_KEY` env var (64 hex chars). Disabled in dev (passthrough mode). 9 new tests.
- ✅ **Tamper-evident audit logging** — SHA-256 hash chaining on audit_log entries. Each entry stores `prev_hash` (previous entry's hash) and `entry_hash` (SHA-256 of all fields + prev_hash). Migration 0005 adds columns. `verify_audit_chain()` walks the chain to detect tampering. Graceful skip for legacy entries. 3 new tests.
- ✅ Schema migration system — lightweight numbered SQL migrations in `agentcafe/db/migrations/`, version tracking via `schema_version` table, auto-applied on startup. Migration 0001 adds `policies` table with `revoked_at`.
- ✅ **Token response `policy_limits` snapshot** — `active_tokens` and `max_active_tokens` (20) included in `/tokens/exchange` and `/tokens/refresh` responses via `PolicyLimits` model. Rate limit info remains per-action (returned reactively via 429). 2 new tests.
- ✅ **Service onboarding security** — ADR-025. Quarantine mode (`quarantine_until` on proxy_configs, 7-day default for new services, configurable via `QUARANTINE_DAYS` env var, forces Tier-2 consent for all actions). Instant suspension (`suspended_at`, returns 503 `service_suspended`). `POST /cafe/services/{id}/suspend` admin endpoint. Cafe-owned consent text (already enforced). Migration 0006. Publisher sets quarantine on publish. Demo data pre-lifted. 5 new tests.
- ✅ ~~**"Building Agents for AgentCafe" developer guide**~~ — removed during doc cleanup (March 2, 2026). Agent-facing info belongs in `AGENT_CONTEXT.md` and API auto-docs, not a human-written integration guide.

**Phase 5: Testing & Polish**
- ✅ **End-to-end demo agent** — `python -m agentcafe.demo_agent` CLI. Full lifecycle: browse menu → Tier-1 register → read order → consent initiate → human approve → token exchange → write order → token refresh. `--headless` flag auto-approves for CI. `--service`, `--read-action`, `--write-action` args. Colored terminal output.
- ✅ ~~Add `pyyaml` to main dependencies~~ (moved from `[wizard]` to base deps in `pyproject.toml`, Feb 27)
- ✅ Add CORS middleware to FastAPI app — added in Phase 4 Wave 1 via `CORSMiddleware` in `create_cafe_app()`. Configurable via `CORS_ALLOWED_ORIGINS` env var.
- ✅ Make `ENRICHMENT_MODEL` configurable via env var — reads from `ENRICHMENT_MODEL` env var with `gpt-4o-mini` default.
- ✅ Implement `x-agentcafe-*` extension merging — `x-agentcafe-risk-tier` and `x-agentcafe-human-identifier-field` (ADR-023) now extracted by spec parser and wired through both rule-based and LLM enricher paths. All 5 extension fields flow end-to-end. 2 new tests.
- ✅ Expose confidence scores in review/preview responses — per-action and top-level confidence dicts now included in preview `final_menu_entry`. Already visible in `SpecParseResponse` via `CandidateMenuEntry`. 1 new test.
- ✅ **Spec file upload (multipart) and URL fetch endpoints** — `POST /wizard/specs/upload` (multipart, 2 MB limit, UTF-8 validation) and `POST /wizard/specs/fetch` (URL fetch with httpx, follow redirects, 2 MB limit). Both return same `SpecParseResponse` as `/specs/parse`. `SpecFetchRequest` model in models.py. 3 new tests. Also fixed confidence merge bug: company edits no longer lose original AI confidence scores in preview.
- ✅ **Wizard Dashboard quarantine UI & security indicators** — Post-publish success screen with quarantine badge, service info card, and link to services page. Company services management page (`/services`) with status badges (live/paused/unpublished), pause/resume/unpublish controls, expandable audit logs per service. `GET /wizard/services` list endpoint with per-service request stats. Admin dashboard (`/admin`) with global stats, quarantine/suspended badges, per-action security table.
- ✅ **E2E integration tests** — 11 cross-cutting tests in `tests/test_e2e.py`. Covers: golden smoke test (wizard → Menu → consent → order → audit chain), quarantine enforcement, full agent lifecycle with token refresh, token revocation, pause/resume, unpublish, duplicate service_id, draft ownership isolation, auth failures. Combined fixture wires all modules with real JWT validation + mock backend. 177 total tests passing.
- ✅ **Company Onboarding Wizard Dashboard** — `dashboard/` Next.js 15 app (React 19, TypeScript, Tailwind 4). 4-step wizard: spec input (paste/upload/URL) → review (editable AI candidate with confidence badges) → policy config (scope, rate limits, human_auth, risk tiers) → preview & publish (quarantine notice, raw JSON). Pages: `/login`, `/register`, `/onboard`. API proxy to FastAPI backend. Typed fetch wrapper with auth token management.
  - Known UX issue: review step (Step 3) replaces the AI-generated candidate entirely with company edits. If the company submits a review with no `actions` array, the preview shows empty actions. The dashboard must **merge** partial edits with the candidate — e.g., only overwrite fields the company actually changed, and pre-populate the review form with the AI-generated values so the company can edit in place.
- ✅ **Local admin dashboard** — `/admin` page with ISSUER_API_KEY login gate. Calls `GET /cafe/admin/overview` (protected endpoint returning full Menu + audit stats). Shows 6 stat cards (services, actions, total requests, 24h requests, quarantine count, suspended count), expandable service cards with quarantine/suspended badges, per-service request stats, per-action security table, and recent audit log (last 50 entries).
- ✅ **Dashboard UX polish** — Confidence badges simplified to single "Review suggested" / "Needs review" hints (hidden when high confidence). Removed confidence from preview step (agent-facing view). Unpublish requires confirmation dialog with permanent-action warning. Spec input text persists on back-navigation. `example_response` model accepts any JSON type (dict, list, string, etc.). Error handler properly stringifies object error details. Sample spec at `dashboard/sample-spec.yaml`.

**Phase 6: Packaging & Release Prep** (finish first — blocking for any public release)
- ✅ **Passport signing key management** — RS256 asymmetric signing via `agentcafe/keys.py`. JWKS endpoint at `/.well-known/jwks.json`, `kid` in JWT header, dual-key rotation support. HS256 legacy fallback for migration window. Config: `PASSPORT_RSA_PRIVATE_KEY` (PEM env var) or `PASSPORT_RSA_KEY_FILE` (file path), auto-generates in dev. 12 new tests in `test_keys.py`.
- ✅ **Production Docker images** — multi-stage build (builder + runtime), `python:3.12-slim` base, non-root `cafe` user, `[wizard]` deps (LiteLLM) included, templates and design files copied.
- ✅ **Docker Compose hardening** — SQLite persistent volume (`cafe_data`), `USE_REAL_PASSPORT=true`, `OPENAI_API_KEY` passthrough, `restart: unless-stopped`, DB path in `/app/data/`, production notes in header.
- ✅ **Edit published service** — `POST /wizard/services/{id}/edit` creates a pre-populated draft (wizard_step=3) from the live Menu entry + proxy configs. Re-publishing via the normal wizard flow updates the existing service in-place (publisher detects same company + service_id → UPDATE instead of INSERT). Cross-company duplicate service_id still rejected. 6 new tests (edit creates draft, full edit→republish flow, unpublished rejection, cross-company rejection, same-company re-publish, cross-company duplicate). `EditServiceResponse` model added.
- ✅ **Open-core split, launch assets** — MIT LICENSE file, README updated for v0.1.0 (RS256 architecture, edit-after-publish, production Docker), project layout reflects current state.

**Phase 6.1: Security Review (ADR-026)** ✅
- ✅ **Sprint 1 — Security hygiene (7 fixes):** Revoke endpoint auth, consent decline→POST, bcrypt password hashing, registration rate limiting, CORS hardening, foreign key enforcement, admin API key in header.
- ✅ **Sprint 2 — Structural fixes (3 fixes):** Multi-action consent, audit hash chain concurrency (asyncio.Lock + seq column, migration 0007), configurable quarantine (QUARANTINE_DAYS env var, default 7).
- ✅ **Sprint 3 — UX (2 fixes):** Human dashboard (active/revoked policies, one-click revoke with CSRF), consent webhook/callback (`_fire_consent_callback`, POST to `callback_url` on approve/decline).
- 214 tests passing (up from 194 pre-review). See `docs/reviews/review-2.md` and ADR-026 in `docs/architecture/decisions.md`.

**Phase 7: Deployment & Real-Agent Beta** (starts immediately after Phase 6)

**Deployment Pipeline**
- ✅ Cloud platform deploy — Fly.io (app: agentcafe, region: iad, shared-cpu-1x, 512MB, 1GB encrypted volume)
- ✅ TLS + custom domain — `agentcafe.io` via Cloudflare DNS + Let's Encrypt (RSA+ECDSA)
- ✅ CI/CD via GitHub Actions — lint → test → deploy on push to main (`.github/workflows/deploy.yml`)
- ⬜ Structured JSON logs with request IDs + platform-native metrics
- SQLite remains for beta (handles expected load; Postgres migration deferred to Phase 8)

**Real-Agent Testing & Dogfooding**
- ✅ Public Menu endpoint live — `GET https://agentcafe.io/cafe/menu` returns 3 services
- ✅ Integration examples — `examples/openai_agent.py` (GPT function calling), `examples/claude_agent.py` (Claude tool_use)
- ✅ E2E demo agent against production — headless (all 9 steps pass) + interactive (human browser approval)
- ✅ Read-before-write identity verification confirmed working in production
- ⬜ Test with 3–5 real external agents (GPT-4o, Claude Sonnet, Grok-3, plus 1–2 custom agents)
- ⬜ Immediate dogfooding: connect our own agents to the live instance
- ⬜ Feedback capture: log what agents struggle with (Menu clarity, consent flow, error messages, rate limits) and feed into v2

**✅ WebAuthn Passkeys — COMPLETE (Sprints 1–4)**
- ✅ **Sprint 1: Server-side WebAuthn** — Migration 0008 (webauthn_credentials + webauthn_challenges tables), passkey register/login endpoints in human.py, ALLOW_PASSWORD_AUTH feature flag, 15 tests in test_webauthn.py
- ✅ **Sprint 2: Browser-side integration** — webauthn.js helper, passkey-primary UI (register.html, login.html, consent.html), SEC-2/3/4 resolved (consent approval requires passkey assertion)
- ✅ **Sprint 3: Activation code flow** — Migration 0009 (activation_code on consents), /activate routes (GET, POST, /complete, /decline), activate.html template, complete_passkey_registration() extracted as reusable helper, rate limiting
- ✅ **Sprint 4: Migration & hardening** — _check_passkey_enrollment() helper, 7-day grace period (configurable), password login returns passkey_enrolled flag, page login redirects to /enroll-passkey, enroll/begin + enroll/complete endpoints, enroll_passkey.html template, activation code expiry tests
- See `docs/planning/webauthn-passkeys-plan.md` and `docs/security/SECURITY-DEBT.md` for full details
- 253 tests passing, pylint 10.00/10

**⚠️ HIGH PRIORITY — Production UX Flows**
- ✅ **Company onboarding wizard UI** — Rebuilt as server-rendered Jinja2 pages (`cafe/wizard_pages.py`). Covers: company registration, login, spec upload, AI review, policy config, preview, publish, post-publish service management. 18 tests.
- 🔜 **Human consent flow polish** — Deferred to pre-launch. Company Cards (Phase 8.1) will change consent UI; polishing now means rework.
- 🔜 **Human policy dashboard improvements** — Deferred to pre-launch. The "Tab" view from Company Cards replaces the current dashboard concept.
- ✅ **Admin dashboard** — Rebuilt as Jinja2 page at `/admin` (API key gated, stats, services list, audit log).
- 🔜 **Company wizard page documentation & helper content** — Deferred to pre-launch. Wizard works; help content is polish.
- 🔜 **Landing page → sign-up funnel** — Deferred to pre-launch (intentionally absent during beta).

**Observability (beta level)** — Deferred to pre-launch. Structured logs, dashboards, and alerting become important when real traffic arrives post-MCP adapter.
- 🔜 Structured JSON logs with request IDs
- 🔜 Platform-provided health, latency, and error-rate dashboard
- 🔜 Alerting on 5xx spikes or service suspensions (email/Slack)

**Beta Success Criteria**
- At least 1 real service onboarded through the wizard (Agent Memory — see Phase 8)
- At least 3 external AI agents successfully complete a write action using only the public Menu + consent flow
- Zero security incidents in first 48 hours
- <5 % of agent requests rejected for unexpected reasons
- Real usage visible in logs + quarantine/suspension features exercised
- Passkey authentication enforced for all Tier-2 consent approvals before public beta launch


**Phase 8: Strategic Evolution** (informed by strategic review — see `docs/strategy/strategic-review-briefing.md` §8–9)

The strategic review (Grok + ChatGPT adversarial reviews, March 6 2026) identified three high-priority items that precede infrastructure scaling. These address the existential risks flagged by both reviewers: consent fatigue, distribution, and bootstrapping real traffic.

- ✅ **8.1 Company Cards on the Tab** — Multi-action, company-scoped policies with human-set constraints (budget, duration, scope, excluded actions). Solves consent fatigue (ranked existential by both reviewers). Cards replace per-action consent for low/medium-risk actions; high/critical actions still require per-action approval. First-use confirmation for new cards.
  - Migration 0010: `company_cards` table with budget, scope, status, activation code, policy_id FK
  - `cafe/cards.py`: POST /cards/request, GET /cards/{id}/status, POST /cards/{id}/approve, POST /cards/{id}/token, GET /cards, POST /cards/{id}/revoke, POST /cards/{id}/confirm-first-use, PATCH /cards/{id}, POST /cards/{id}/report-spend
  - Tab dashboard: `/tab` page (view/manage cards), `/tab/approve/{id}` (approval page), `/tab/{id}/revoke`, `/tab/{id}/confirm`
  - Templates: `tab.html`, `card_approve.html`
  - Order integration: 403 responses include `card_suggestion` field guiding agents to request cards
  - Budget tracking: `report-spend` endpoint, budget enforcement on token issuance, period reset
  - 40 tests covering card lifecycle, edit constraints, Tab pages, card suggestions, budget tracking
  - See `docs/planning/company-cards-plan.md` for full design
- ⬜ **8.2 First Real Service: Agent Memory** — The Cafe's first real onboarded service. Human-owned persistent state for agents (key-value + document storage). Developed as a **separate project/repo** with its own API and deployment. Onboards to the Cafe through the standard company wizard — dogfooding the onboarding flow. Creates the bootstrap traffic wedge. See §8.3 of strategic briefing.
- ✅ **8.3 MCP Server Adapter** — 4-tool LLM-native discovery pattern (`cafe.search`, `cafe.get_details`, `cafe.request_card`, `cafe.invoke`) via remote Streamable HTTP at `/mcp`. Stateless transport via official `mcp` SDK. MCP adapts to the Cafe — not the other way around (ADR-029).
  - `cafe/mcp_adapter.py`: FastMCP server with 4 tools, mounted at `/mcp` in main.py
  - `cafe.search`: keyword search across Menu, returns summaries only (service_id, action_id, name, short_description, risk_tier, relevance)
  - `cafe.get_details`: full Menu entry for a service, optionally filtered to single action
  - `cafe.request_card`: initiates Company Card flow (validates Passport, returns card_id + consent_url)
  - `cafe.invoke`: routes to POST /cafe/order, returns structured `HUMAN_AUTH_REQUIRED` error for unauthorized writes
  - 18 tests in `test_mcp_adapter.py`
  - See `docs/strategy/strategic-review-briefing.md` §8.2 + ADR-029
- ✅ **8.4 Open Source Prep** — README rewritten for public launch (MCP endpoint, Company Cards, env vars table, updated project layout). CONTRIBUTING.md added. Legacy Next.js `dashboard/` removed from repo (sample-spec.yaml preserved in `examples/`). AGENT_CONTEXT.md updated (removed dashboard, added MCP, fixed test counts). No hardcoded secrets or local paths found. Documentation audit complete.

**Phase 9: Scale & Harden** (deferred — post-beta, when real traffic justifies it)
- ⬜ SQLite → PostgreSQL migration (Alembic, connection string swap, test all migrations)
- ⬜ Agent SDK (`agentcafe-py` client library) — only after API surface stabilizes
- ⬜ OpenTelemetry distributed tracing
- ⬜ Prometheus + Grafana observability stack
- ⬜ Secrets manager integration (Doppler / 1Password)
- ⬜ Audit-log web viewer (tamper-evident chain browser for humans)

---

**Guiding Rules**
- AI agents write 90–95% of the code (human reviews/merges)
- Keep everything readable and human-friendly
- The consent/authorization layer is the core product. Everything else (Menu, discovery, proxy) serves it.
- Services on the Cafe are a bootstrap strategy, not the product. The Cafe itself — proxy, safety, audit, human authorization — is the product.
- Build for autonomous agents. Non-autonomous agents are served incidentally (via MCP adapter) but are not the target.
- **Always read `AGENT_CONTEXT.md` first** — it has the codebase map and what's real vs. placeholder
- **Strategic context in `docs/strategy/strategic-review-briefing.md`** — read before making architectural or product decisions
