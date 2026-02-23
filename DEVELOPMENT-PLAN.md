# AgentCafe Development Plan

**Current Status:** Phase 2.0–2.2 complete — Passport system designed and implemented (JWT, scopes, revocation). MVP flag for safe rollout.  
**Last Updated:** February 22, 2026

**MVP Success Criteria**
We can run end-to-end locally:
- ✅ Three demo services are registered (HotelBooking, LunchDelivery, HomeServiceAppointment)
- ⬜ Company Onboarding Wizard works smoothly (Phase 3)
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
- ✅ Tests: 27 passing (Menu format, action correctness, auth requirements, input validation, happy-path proxy, JWT passport issuance/validation/revocation)

**Phase 2: Core Cafe Foundation** ← IN PROGRESS
- ✅ **2.0 Passport System Design** — locked in `docs/passport/design.md` (Grok + Claude collaboration)
- ⬜ 2.1 Docker Compose: separate Cafe and demo backends into independent containers
- ✅ 2.2 Real Passport validation — JWT (HS256), scopes (`{service_id}:{action_id}`), wildcard (`{service_id}:*`), expiry, revocation via `revoked_jtis` table
- ⬜ 2.3 Company Policy engine (enforce rate limits from proxy_configs, type validation)
- ✅ 2.4 Migration flag `USE_REAL_PASSPORT` routes between MVP and real JWT validation in `place_order`
- ✅ 2.5 Passport issuance (`POST /passport/issue`, API-key protected) + revocation (`POST /cafe/revoke`)

**Phase 3: Company Onboarding Wizard**
- ⬜ Full wizard implementation (the main product we obsess over)
- ⬜ Spec parser (`wizard/spec_parser.py` — OpenAPI ingestion + validation)
- ⬜ AI enricher (`wizard/ai_enricher.py` — LiteLLM generates Menu entries from specs)
- ⬜ Review engine (`wizard/review_engine.py` — draft management, editing)
- ⬜ Publisher (`wizard/publisher.py` — one-click publish to Menu + proxy config)
- ⬜ Full design already exists in `docs/design/onboarding-wizard/`

**Phase 4: Security & Guardrails**
- ✅ Real double validation with JWT verification (done in Phase 2)
- ⬜ Tamper-evident audit logging
- ✅ Passport revocation (done in Phase 2)
- ⬜ Input injection protection
- ⬜ Backend credential encryption (AES-256 at rest)
- ⬜ Upgrade to RS256 with key management

**Phase 5: Testing & Polish**
- ⬜ End-to-end demo with a simple test agent
- ⬜ Local dashboard for viewing Menu & logs

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
