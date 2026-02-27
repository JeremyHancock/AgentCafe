# AgentCafe Development Plan

**Current Status:** Phase 3 complete. Company Onboarding Wizard fully implemented and tested.  
**Last Updated:** February 26, 2026

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
- ✅ Tests: 77 passing (Menu format, action correctness, auth requirements, input validation, happy-path proxy, JWT passport issuance/validation/revocation, rate limiting, type validation, wizard spec parsing, enrichment, full wizard API flow, draft ownership, dry-run, hotel spec)

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
- ✅ 29 wizard tests (spec parsing, enrichment, hotel spec with $ref, full API flow, draft ownership, missing auth, dry-run, out-of-order steps)

**Phase 3.1: Code Quality & Lint Cleanup** ✅
- ✅ Pylint 10.00/10 — zero warnings across all source and test files
- ✅ Replaced all `global` statements with `_State` class pattern (engine.py, passport.py, cafe/router.py, wizard/router.py)
- ✅ Proper exception chaining (`raise ... from exc`) on all re-raised HTTPExceptions
- ✅ Narrowed broad `except Exception` to specific types where possible
- ✅ Removed unused imports and variables in test files
- ✅ See `FIX_PROGRESS.md` for the complete 12-fix improvement tracker

**Phase 4: Security & Guardrails**
- ✅ Real double validation with JWT verification (done in Phase 2)
- ⬜ Tamper-evident audit logging
- ✅ Passport revocation (done in Phase 2)
- ⬜ Input injection protection
- ⬜ Backend credential encryption (AES-256 at rest)
- ⬜ Upgrade to RS256 with key management

**Phase 5: Testing & Polish**
- ⬜ End-to-end demo with a simple test agent
- ⬜ **Company Onboarding Wizard Dashboard** — web UI (React/Next.js) where companies log in, paste their OpenAPI spec, review the candidate Menu entry, configure policies, preview, and publish. Replaces the current REST-only workflow with a guided visual experience.
  - Known UX issue: review step (Step 3) replaces the AI-generated candidate entirely with company edits. If the company submits a review with no `actions` array, the preview shows empty actions. The dashboard must **merge** partial edits with the candidate — e.g., only overwrite fields the company actually changed, and pre-populate the review form with the AI-generated values so the company can edit in place.
  - Wizard-published services need Passport scopes that match the wizard-assigned scopes. In MVP passport mode (`demo-passport`), orders to wizard-published services are rejected because the hardcoded scope list doesn't include them. The dashboard should make this clear, or auto-issue a test passport.
  - The `ENRICHMENT_MODEL` (currently hardcoded to `gpt-4o-mini`) should be configurable via env var for the dashboard deployment.
- ⬜ Local admin dashboard for viewing Menu & logs

**Phase 6: Packaging & Release Prep**
- ⬜ Production Docker images (multi-stage builds, hardened base images)
- ⬜ Open-core split, launch assets

---

**Guiding Rules**
- AI agents write 90–95% of the code (human reviews/merges)
- Discovery effort = development effort
- Keep everything readable and human-friendly
- Moonshot vision stays alive: this becomes the Cafe everyone uses
- **Always read `AGENT_CONTEXT.md` first** — it has the codebase map and what's real vs. placeholder
