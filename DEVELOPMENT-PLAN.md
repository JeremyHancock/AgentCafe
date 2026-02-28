# AgentCafe Development Plan

**Current Status:** Phase 3.2 complete. Passport V2 design fully converged (8 locked positions, MVP scope defined). ADR-023 + ADR-024 recorded.  
**Last Updated:** February 27, 2026

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
  - Deliverables in `docs/design/`: OpenAPI specs, locked Menu entries, Onboarding Wizard design (FLOW.md, UI-SCREENS.md, ARCHITECTURE.md)

**Phase 1: Project Bootstrap + Demo Backends** ✅
- ✅ Project structure: `pyproject.toml`, `agentcafe/` package, config, SQLite schema
- ✅ Three demo backends: `hotel.py` (port 8001), `lunch.py` (port 8002), `home_service.py` (port 8003)
- ✅ Menu discovery: `GET /cafe/menu` returns locked format from database
- ✅ Order proxy: `POST /cafe/order` with double validation (MVP passport) + audit logging
- ✅ Database seeding: 3 services, 12 proxy configs, auto-seeded on startup
- ✅ Tests: 85 passing (Menu format, action correctness, auth requirements, input validation, happy-path proxy, JWT passport issuance/validation/revocation, rate limiting, type validation, wizard spec parsing, enrichment, full wizard API flow, draft ownership, dry-run, hotel spec, post-publish management)

**Phase 2: Core Cafe Foundation** ✅
- ✅ **2.0 Passport System Design** — locked in `docs/passport/design.md` (Grok + Claude collaboration)
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
- ✅ See `FIX_PROGRESS.md` for the complete 12-fix improvement tracker

**Phase 3.2: Project Review Fixes** ✅
- ✅ Fixed scope mismatch: updated `cost.required_scopes` in all 4 design files (13 values) to match `seed.py` proxy_configs (e.g., `hotel:search` → `stayright-hotels:search-availability`)
- ✅ Standardized `cost.limits` format: all design files now use object form `{"rate_limit": "60/minute"}` matching wizard output (13 occurrences across 4 files)
- ✅ Post-publish management endpoints: `GET .../dashboard`, `PUT .../pause`, `PUT .../resume`, `PUT .../unpublish`, `GET .../logs` — all with JWT auth + ownership enforcement
- ✅ 4 new Pydantic response models (`ServiceDashboardResponse`, `ServiceStatusResponse`, `AuditLogEntry`, `ServiceLogsResponse`)
- ✅ 8 new tests (dashboard, pause, pause-idempotency, resume, unpublish, logs, ownership 403, not-found 404)
- ✅ Comprehensive `PROJECT_REVIEW.md` documenting all findings and remaining gaps

**Phase 4: Passport V2 & Security** (design converged — see `docs/passport/v2-design-discussion.md` §13)
- ✅ Real double validation with JWT verification (done in Phase 2)
- ✅ Passport revocation (done in Phase 2)
- ✅ Passport V2 design: 8 locked positions via three-way review (ADR-024)
- ✅ Menu schema extension for consent flow (ADR-023, ADR-009 amendment)
- ✅ **Passport V2 implementation — Tier-1 read Passports**: `POST /passport/register` returns Tier-1 JWT (`tier: "read"`, `granted_by: "self"`). Tier-1 tokens access read actions, rejected for writes with `tier_insufficient`. 5 new tests.
- ⬜ **Passport V2 implementation — human accounts + consent flow**: human Cafe accounts (passkey/WebAuthn), `POST /consents/initiate`, consent URL, `POST /tokens/exchange`, short-lived write tokens under long-lived policies
- ⬜ **Passport V2 implementation — Cafe-side identity verification**: layered by risk tier (agent-supplied for low, +read-before-write for medium+, mandatory read for high/critical). `human_identifier_field` in Menu drives both paths.
- ⬜ **Passport V2 implementation — risk-tier token ceilings**: per-policy human-chosen expiry with Cafe-enforced ceilings (60m/15m/5m/single-use). Asymmetric ceremony.
- ⬜ **Populate ADR-023 Menu schema fields** — update AI enricher + review engine to generate `risk_tier`, `human_identifier_field`, `constraints_schema`, `account_linking_required`, `self_only`, `concurrency_guidance`, and `cost.limits.rate_limit_scope` during onboarding. Backfill at least one seeded demo service (hotel booking) with these fields so the consent flow has real data to work with.
- ⬜ **Rate-limit 429 response** — machine-readable error body with `error`, `detail` (explaining per-policy shared budget), `retry_after_seconds`, and `policy_id`. Required for MVP per rate-limit communication principle (§2.2).
- ⬜ **Consent page UI** — first Cafe-owned web frontend. Cafe-authored plain-language consent text. Passkey confirmation for high-value actions.
- ⬜ **Passport signing key management** — migrate from single HS256 secret → RS256 asymmetric with cloud KMS (private key never leaves KMS, JWKS endpoint for public keys, `kid` in JWT header, dual-key rotation). Addresses single-key compromise risk identified in §3.
- ⬜ **Consent privacy enforcement** — strict JWT audience separation between agent Passports and human sessions. No agent-accessible consent discovery surface. See v2-design-discussion.md §2.3.
- ✅ **Policy revocation — instant for all tiers** — `revoked_at` column in policies table (migration 0001) + `iat < revoked_at` check in `validate_passport_jwt`. Returns `401 policy_revoked`. 3 new tests.
- ⬜ Input injection protection — path parameters resolved via string replacement without sanitization; an agent could send malicious values
- ⬜ Backend credential encryption (AES-256 at rest) — `backend_auth_header` currently stored as plaintext in `proxy_configs` and `draft_services`
- ⬜ Tamper-evident audit logging — hash chaining or HMAC signatures
- ✅ Schema migration system — lightweight numbered SQL migrations in `agentcafe/db/migrations/`, version tracking via `schema_version` table, auto-applied on startup. Migration 0001 adds `policies` table with `revoked_at`.
- ⬜ **Token response `policy_limits` snapshot** — optional `remaining_requests_in_window`, `active_tokens_under_policy`, `max_active_tokens` in token exchange/refresh responses. Not MVP; convenience for sophisticated agent platforms.
- ⬜ **"Building Agents for AgentCafe" developer guide** — one-page doc in `docs/` explaining per-policy rate limits, multi-agent coordination, consent flow, and token lifecycle. Essential long-term, low cost.

**Phase 5: Testing & Polish**
- ⬜ End-to-end demo with a simple test agent
- ✅ ~~Add `pyyaml` to main dependencies~~ (moved from `[wizard]` to base deps in `pyproject.toml`, Feb 27)
- ⬜ Add CORS middleware to FastAPI app — required before any web frontend (consent page, wizard dashboard) can call the API
- ⬜ Make `ENRICHMENT_MODEL` configurable via env var (currently hardcoded to `gpt-4o-mini` in `ai_enricher.py`)
- ⬜ Implement `x-agentcafe-*` extension merging in the AI enricher — presets are parsed by the spec parser but never used during enrichment
- ⬜ Expose confidence scores in review/preview responses — the data model has them but they're invisible to the company
- ⬜ Spec file upload (multipart) and URL fetch endpoints — currently only raw string accepted
- ⬜ **Company Onboarding Wizard Dashboard** — web UI (React/Next.js) where companies log in, paste their OpenAPI spec, review the candidate Menu entry, configure policies, preview, and publish. Replaces the current REST-only workflow with a guided visual experience.
  - Known UX issue: review step (Step 3) replaces the AI-generated candidate entirely with company edits. If the company submits a review with no `actions` array, the preview shows empty actions. The dashboard must **merge** partial edits with the candidate — e.g., only overwrite fields the company actually changed, and pre-populate the review form with the AI-generated values so the company can edit in place.
- ⬜ Local admin dashboard for viewing Menu & logs

**Phase 6: Packaging & Release Prep**
- ⬜ Production Docker images (multi-stage builds, hardened base images)
- ⬜ Docker Compose hardening: set `PASSPORT_SIGNING_SECRET` env var, add SQLite volume for persistence, install `[wizard]` deps in image, add `OPENAI_API_KEY` for LiteLLM
- ⬜ Open-core split, launch assets

---

**Guiding Rules**
- AI agents write 90–95% of the code (human reviews/merges)
- Discovery effort = development effort
- Keep everything readable and human-friendly
- Moonshot vision stays alive: this becomes the Cafe everyone uses
- **Always read `AGENT_CONTEXT.md` first** — it has the codebase map and what's real vs. placeholder
