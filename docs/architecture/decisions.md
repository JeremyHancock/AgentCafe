# AgentCafe — Architectural Decisions Log

**Purpose:** Captures key decisions with rationale so future contributors (human or AI) understand *why*, not just *what*.  
**Last Updated:** March 8, 2026

---

### ADR-001: Docker Compose moved to Phase 2 (not Phase 6)

**Date:** February 20, 2026  
**Context:** Initially Docker was deferred to Phase 6 (Packaging & Release Prep) to keep iteration speed high.  
**Decision:** Move Docker Compose to early Phase 2.  
**Rationale:** The demo backends are meant to represent opaque third-party servers. Running everything in one process (`asyncio.gather` in `main.py`) hides real network boundaries — the Cafe and backends share memory, localhost, and the same process. This makes it too easy to accidentally couple them. Containerizing early forces the proxy to go through real HTTP, matching the production architecture. Phase 6 Docker is now specifically for *production-hardened* images (multi-stage builds, etc.).

---

### ADR-002: Menu entries loaded from Phase 0.2 JSON files (single source of truth)

**Date:** February 20, 2026  
**Context:** The original `seed.py` hardcoded ~350 lines of Menu entry dicts, duplicating what was already defined in the service Menu JSON files (now in `agentcafe/db/services/`).  
**Decision:** `seed.py` loads Menu entries from the JSON files at startup. Proxy configs remain hardcoded in `seed.py` (they're implementation details, not design artifacts).  
**Rationale:** Single source of truth. If someone edits a Menu entry, they edit the design file and it flows through automatically. No risk of the design files and the seeded database drifting apart. `config.py` has a `design_dir` setting (overridable via `CAFE_DESIGN_DIR` env var).

---

### ADR-003: Shared httpx.AsyncClient for proxy requests

**Date:** February 20, 2026  
**Context:** The initial proxy implementation created a new `httpx.AsyncClient` per order request.  
**Decision:** Use a module-level shared client in `router.py` (`get_http_client()` / `close_http_client()`).  
**Rationale:** Creating a client per request means a new TCP connection per request — no connection reuse, extra handshake overhead. The shared client pools connections across requests. `close_http_client()` is called during shutdown in `main.py`'s `finally` block alongside `close_db()`.

---

### ADR-004: Named row access for aiosqlite queries

**Date:** February 20, 2026  
**Context:** Query results were accessed by positional index (`row[0]`, `row[1]`, etc.) despite `row_factory = aiosqlite.Row` being set.  
**Decision:** Use named access (`row["backend_url"]`, `row["status"]`, etc.) everywhere.  
**Rationale:** Positional indexing is fragile — reordering columns in a SELECT silently breaks everything. Named access is self-documenting and resilient to column order changes.

---

### ADR-005: Audit log indexes added at schema creation

**Date:** February 20, 2026  
**Context:** The `audit_log` table had no indexes beyond the primary key.  
**Decision:** Added three indexes: `timestamp`, `(service_id, action_id)`, `passport_hash`.  
**Rationale:** The audit log is append-only and grows with every order. Querying by time range, by service, or by passport will be common operations for dashboards, debugging, and rate limiting. Adding indexes now is free — adding them later on a large table is expensive.

---

### ADR-006: PyYAML removed from base dependencies

**Date:** February 20, 2026  
**Context:** `pyyaml` was listed in `pyproject.toml` base dependencies but nothing in the codebase imports it.  
**Decision:** Removed from base deps. Will be added to the `wizard` optional dependency group when Phase 3 needs it.  
**Rationale:** Base dependencies should reflect what the code actually uses. Phantom deps create confusion about what the project actually needs to run.

---

### ADR-007: In-process ASGI transport for happy-path order tests

**Date:** February 20, 2026  
**Context:** Order tests only covered rejection paths (invalid passport, unknown service/action). Testing the happy path requires the proxy to actually reach a backend.  
**Decision:** Created `_MultiBackendTransport` in `test_order.py` — a custom httpx transport that routes requests to the correct demo backend's FastAPI app via `ASGITransport`. Monkeypatches the shared `_http_client` in `router.py`.  
**Rationale:** This tests the full proxy flow (passport → policy → proxy → backend → response) without running any servers. Tests complete in ~0.06s total. The pattern is reusable for any future integration tests.

---

### ADR-008: MVP passport accepts only "demo-passport" (Phase 2 replaces)

> **SUPERSEDED** by ADR-011 (JWT Passport) and ADR-024 (V2 bearer model). The `_validate_passport_mvp()` code has been removed. Kept here for historical context.

**Date:** February 20, 2026  
**Context:** Real JWT-based Passport validation is complex (key management, scope parsing, expiry, human authorization claims).  
**Decision:** `_validate_passport_mvp()` accepts `"demo-passport"` with all scopes. `_check_human_authorization_mvp()` always passes for demo-passport. Both are clearly marked as MVP placeholders in `router.py`.  
**Rationale:** Gets the proxy architecture working end-to-end without blocking on JWT infrastructure. The functions are isolated and named with `_mvp` suffix so Phase 2 knows exactly what to replace.

---

### ADR-009: service_id naming convention + additive Menu schema policy

**Date:** February 22, 2026  
**Context:** External review questioned service_id length and suggested shorter slugs. Separate discussion identified the need for structured discovery fields (`category`, `capability_tags`) to help agents filter services programmatically.  
**Decision:**
1. **service_id format**: `{brand}-{category}` — shorter than the original `{brand}-{service-type}` while keeping global uniqueness. Renamed: `stayright-hotel-booking` → `stayright-hotels`, `quickbite-lunch-delivery` → `quickbite-delivery`, `fixright-home-services` → `fixright-home`.
2. **Additive Menu schema policy**: The locked Menu format may grow with new *additive* fields (no breaking changes, no removing/renaming existing fields). Each addition requires an ADR.
3. **New fields added**: `category` (string, e.g. `"hotels"`) and `capability_tags` (array, e.g. `["travel", "booking", "accommodation"]`) added to every service in the Menu. These enable structured agent discovery without relying on description parsing.

**Rationale:** In a multi-vendor marketplace, service_ids must be globally unique — `hotels` alone would collide when multiple hotel platforms join. The brand prefix solves this. Shortening from `stayright-hotel-booking` to `stayright-hotels` improves readability without losing uniqueness. The `category` and `capability_tags` fields give agents first-class discovery primitives, aligning with how modern agent registries (A2A, MCP catalogs) are evolving in 2026.

### ADR-010: Required inputs presence validation in the proxy

**Date:** February 22, 2026
**Context:** External review (Grok) identified that `POST /cafe/order` was forwarding requests to backends without checking whether the agent provided the required inputs declared in the Menu. This meant backends could receive malformed requests.
**Decision:** Added a presence check in `router.py` Gate 2: before proxying, the Cafe loads the action's `required_inputs` from the stored `menu_entry_json` and rejects with HTTP 422 + a `missing_inputs` error listing the missing field names. This is name-presence only — full type/schema validation is deferred to the Phase 2 Company Policy engine.
**Rationale:** The Menu already tells agents which inputs are required. Enforcing this at the proxy is basic correctness and protects backends from garbage. Keeping it to presence-only avoids over-engineering before the Policy engine exists.

### ADR-011: JWT Passport system (Phase 2.0)

**Date:** February 22, 2026
**Context:** The MVP "demo-passport" magic string provided no real security. Phase 2 required a cryptographically secure, scoped, revocable delegation system — designed collaboratively with Grok (advisor). Original V1 design doc removed (superseded by V2 spec).
**Decision:**
1. **JWT (HS256)** signed with `PASSPORT_SIGNING_SECRET` env var. Upgrade path to RS256 later.
2. **Two-layer auth**: `scopes` gate all actions; `authorizations` array gates human-auth-required actions.
3. **Wildcard**: Only `{service_id}:*` supported for MVP.
4. **Revocation**: Short expiry (≤24h) + `revoked_jtis` SQLite table + `POST /cafe/revoke`.
5. **Issuance**: `POST /passport/issue` protected by `ISSUER_API_KEY`.
6. **Migration flag**: `USE_REAL_PASSPORT` (default false) routes between MVP and real validation. Both paths coexist.
7. **Library**: PyJWT with explicit `algorithms=["HS256"]` whitelist.

**Rationale:** The design keeps the agent-facing API unchanged (`passport` stays a single string), provides real cryptographic safety, and has a safe migration path. The two-layer model (scopes for access, authorizations for human consent) matches the "double validation" principle. `human_consent` claim is kept for forward-compatibility but not validated.

### ADR-012: Scope format change to {service_id}:{action_id}

**Date:** February 22, 2026
**Context:** The original scope format (`hotel:search`, `food:browse`) was category-scoped. This would collide when multiple services in the same category join the marketplace (e.g., two hotel providers both needing `hotel:search`).
**Decision:** Scopes now use `{service_id}:{action_id}` format (e.g., `stayright-hotels:search-availability`). Updated in `seed.py` proxy configs and MVP passport scope list. During Company Onboarding (Phase 3), scopes will be auto-generated from the service_id + action_id.
**Rationale:** Per-service scoping is globally unique by construction (service_ids are already unique). It also makes Passport tokens more precise — a token scoped to `stayright-hotels:*` can't accidentally grant access to a different hotel service.

### ADR-013: Docker Compose with single image, multiple entrypoints

**Date:** February 22, 2026
**Context:** ADR-001 identified that running all four servers in one process hides real network boundaries. Phase 2.1 delivers on containerizing early.
**Decision:**
1. **Single Dockerfile** (`python:3.12-slim`) builds one image shared by all four services. Each `docker-compose.yml` service overrides the `command` to run its specific uvicorn entrypoint.
2. **FastAPI lifespan handler** (`_cafe_lifespan`) in `main.py` handles DB init, seeding, and passport config for standalone Cafe mode. Tests use `create_cafe_app()` without lifespan (they manage DB separately).
3. **Module-level `app`** exposed in `main.py` for `uvicorn agentcafe.main:app` — used by Docker and any production deployment.
4. **Configurable backend hosts** via env vars (`HOTEL_BACKEND_HOST`, etc.) — Docker sets these to service names (`hotel`, `lunch`, `home-service`); local defaults to `127.0.0.1`.
5. **Health checks** on all four containers using `urllib.request` (no extra dependencies). Cafe waits for all three backends to be healthy before starting (`depends_on: condition: service_healthy`).
6. **Local `python -m agentcafe.main`** still works unchanged — runs all four servers in one process for quick development.
**Rationale:** Single image avoids maintaining four Dockerfiles for identical code. The lifespan pattern is idiomatic FastAPI and cleanly separates "standalone server" from "test harness" initialization. Backend host env vars are the minimal change needed — no config file refactoring, no service discovery overhead.

### ADR-014: Company Policy engine — rate limiting via audit_log + type inference from Menu examples

**Date:** February 22, 2026
**Context:** `proxy_configs` has a `rate_limit` column (e.g., `"60/minute"`) that was seeded but never enforced. Input validation only checked presence of required fields, not their types. The onboarding wizard architecture anticipated a `policy.py` module.
**Decision:**
1. **Rate limiting**: Sliding-window counter using the existing `audit_log` table. Counts entries matching `(passport_hash, service_id, action_id)` within the window. No new tables — the audit log already indexes `passport_hash` and `(service_id, action_id)`. Returns HTTP 429 when exceeded.
2. **Input type validation**: Infers expected types from the `example` field in each `required_input` in the Menu schema. String examples expect strings, numeric examples accept int or float (not bool), bool examples require bools, etc. Returns HTTP 422 with per-field error details.
3. **Module**: `agentcafe/cafe/policy.py` — pure functions for type validation, async function for rate limiting. Imported by `router.py` Gate 2.
4. **Rate limit parsing**: Supports `N/minute`, `N/hour`, `N/day` formats. Invalid formats log a warning and skip enforcement (fail-open for malformed config).
**Rationale:** Reusing the audit_log avoids a separate rate-limit counter table and keeps the schema simple. The audit_log already has the right indexes. Fail-open on malformed rate limits prevents a config typo from blocking all traffic.

### ADR-015: Explicit `type` field in Menu `required_inputs`

**Date:** February 22, 2026
**Context:** ADR-014 introduced input type validation by inferring types from `example` values. This is ambiguous — e.g., `"2"` could be a string or a number that happens to be quoted. The backend APIs expect specific types, and guessing from examples is unnecessary when we can ask companies to declare the type at onboarding.
**Decision:**
1. Add an explicit `"type"` key to every `required_input` in the Menu schema. Values use JSON Schema type names: `string`, `integer`, `number`, `boolean`, `array`, `object`.
2. `policy.py` uses the `type` field for validation when present. Falls back to inferring from `example` only when `type` is absent (backward compatibility for any legacy entries).
3. This is an **additive** schema change per ADR-009 — no breaking changes to existing consumers. Agents and tools that ignore the field continue to work.
4. The Onboarding Wizard (Phase 3) will collect `type` as a required field during service registration, so all new entries will always have it.
**Rationale:** Explicit types eliminate ambiguity, produce clearer error messages, and align with what the backend API actually expects. Collecting type at onboarding is trivial (a dropdown) and far more reliable than heuristic inference.

### ADR-016: Company Onboarding Wizard — implementation architecture (Phase 3)

**Date:** February 26, 2026
**Context:** The wizard design (Phase 0.2) specified four components: Spec Parser, AI Enricher, Review Engine, and Publisher. Phase 3 implements all four plus the full API surface.
**Decision:**
1. **Spec Parser** (`wizard/spec_parser.py`): Pure Python, no LLM. Parses OpenAPI 3.0/3.1 in YAML or JSON. Auto-detects format. Extracts operations with read/write classification (POST with "search" in operationId is read). Extracts `x-agentcafe-*` extensions for preset scopes, human_auth, and rate limits. PyYAML is a wizard optional dependency (not in base deps, per ADR-006).
2. **AI Enricher** (`wizard/ai_enricher.py`): Tries LiteLLM first (single prompt per service, JSON mode). Falls back to rule-based generation when LiteLLM is not installed or fails. Rule-based generates action slugs from operationIds (camelCase → kebab-case), extracts inputs from request bodies, infers types from schemas. Both paths produce the same `CandidateMenuEntry` model. Broad exception catches on LLM calls are intentional — graceful fallback over hard failure.
3. **Review Engine** (`wizard/review_engine.py`): Manages `draft_services` table with progressive wizard steps (2–6). Each step saves state to the draft. Preview generation (Step 5) combines the candidate menu with policy settings to produce the final locked Menu format.
4. **Publisher** (`wizard/publisher.py`): Atomic transaction — inserts `published_services` + all `proxy_configs` in one commit. Checks service_id uniqueness. On failure, rolls back.
5. **DB schema**: New `draft_services` table for wizard state. `password_hash` column added to `companies` (bcrypt, upgraded from SHA-256 per ADR-018).
6. **Router**: All wizard endpoints under `/wizard/*` prefix. Company auth via JWT session tokens in `Authorization: Bearer <token>` header (see ADR-017). All draft endpoints enforce ownership.
**Rationale:** The four-component split from the design doc maps cleanly to four Python modules with clear responsibilities. Rule-based fallback ensures the wizard works even without an LLM API key configured — critical for local development and testing. Draft-based state management lets companies leave and resume the wizard. The atomic publisher prevents half-published services from appearing on the Menu.

---

### ADR-017: JWT session tokens for wizard authentication (Phase 3 hardening)

**Date:** February 26, 2026
**Context:** The initial Phase 3 implementation passed `company_id` in the request body for authentication. This was insecure — any caller could impersonate any company by guessing or enumerating UUIDs.
**Decision:**
1. `POST /wizard/companies` and `POST /wizard/companies/login` return a `session_token` (JWT, HS256, 8-hour expiry, `iss=agentcafe-wizard`).
2. All authenticated wizard endpoints require `Authorization: Bearer <token>` header. The `company_id` is extracted from the token's `sub` claim.
3. All draft endpoints (review, policy, preview, dry-run, publish) verify `draft.company_id == token.sub` and return 403 if mismatched.
4. `configure_wizard(signing_secret)` is called at startup in `main.py`, reusing `PASSPORT_SIGNING_SECRET`.
5. `SpecParseRequest` no longer includes `company_id` — it comes from the token.
**Rationale:** JWT tokens are stateless, don't require server-side session storage, and the 8-hour expiry limits exposure. Reusing the passport signing secret avoids a second secret to manage. Ownership checks on every draft endpoint prevent cross-company data access.

---

### ADR-018: bcrypt for company password hashing

**Date:** February 26, 2026
**Context:** The initial implementation used `hashlib.sha256` for password hashing — fast and unsalted, vulnerable to rainbow table attacks.
**Decision:** Replaced with `bcrypt.hashpw()` + `bcrypt.gensalt()` for hashing, `bcrypt.checkpw()` for verification. `bcrypt>=4.0.0` added to base dependencies.
**Rationale:** bcrypt is the industry standard for password hashing — it's intentionally slow (resistant to brute force), automatically salted (resistant to rainbow tables), and has an adaptive cost factor for future-proofing.

---

### ADR-019: `_State` class pattern replacing `global` statements

**Date:** February 26, 2026
**Context:** Multiple modules used `global` statements for module-level mutable state (`_db`, `_signing_secret`, `_http_client`, etc.). Pylint flags these as W0603.
**Decision:** Replace all `global` statements with a module-level `_State` class:
```python
class _State:
    signing_secret: str = ""
_state = _State()
```
Access via `_state.attribute` instead of bare module-level variables. Tests monkeypatch via `monkeypatch.setattr(module._state, "attr", value)`. Applied in `db/engine.py`, `cafe/passport.py`, `cafe/router.py`, `wizard/router.py`.
**Rationale:** Avoids `global` keyword entirely while preserving the same mutability semantics. The class provides a natural namespace, is type-annotatable, and keeps the monkeypatch pattern simple (setattr on an object). Consistent across all modules.

---

### ADR-020: `$ref` resolution and required-only input filtering in spec parser

**Date:** February 26, 2026
**Context:** The spec parser was passing `$ref` references through unresolved, causing downstream enrichment to miss schema details. Additionally, all body properties were treated as required inputs, even optional ones.
**Decision:**
1. **`$ref` resolution** (`_resolve_refs`): Recursive depth-limited (15 levels) resolution of `$ref` pointers within the parsed spec. Runs immediately after initial parsing, before any operation extraction.
2. **Required-only filtering** (`_extract_required_inputs`): Body properties are only included as required inputs if they appear in the schema's `required` array. Path parameters are always included.
**Rationale:** Real-world OpenAPI specs (like the hotel-booking spec) use `$ref` extensively — without resolution, the enricher sees empty schemas. Filtering to required-only inputs prevents agents from being asked for optional parameters, reducing friction and errors.

---

### ADR-021: Review step replaces candidate (known limitation for Phase 5)

**Date:** February 26, 2026
**Context:** During live testing of the wizard, submitting a review (`PUT /wizard/drafts/{id}/review`) without an `actions` array caused the preview to show an empty service — no actions, no proxy configs. This is because `save_review` stores company edits in `company_edits_json`, and `generate_preview` prefers company edits over the AI-generated `candidate_menu_json`. If the edits contain no actions, the preview is empty.
**Decision:** Document as a known limitation. The current API treats the review as a **complete replacement** of the candidate. The Phase 5 Wizard Dashboard must:
1. Pre-populate the review form with AI-generated values from the candidate menu.
2. Merge partial edits — only overwrite fields the company actually changed.
3. Always include the full `actions` array when submitting the review.
**Rationale:** Fixing this at the API level (merging edits with the candidate) would add complexity to `save_review` and create ambiguity about what "empty" means (did the company intentionally remove all actions, or did they just not include the field?). The dashboard is the right layer to handle this — it always has the full candidate context and can send complete review payloads.

---

### ADR-022: Stale SQLite DB requires manual deletion after schema changes

**Date:** February 26, 2026
**Context:** During live testing, `POST /wizard/companies` returned `sqlite3.OperationalError: table companies has no column named password_hash`. The `agentcafe.db` file on disk was created by a previous run before the `password_hash` column was added. `CREATE TABLE IF NOT EXISTS` does not alter existing tables.
**Decision:** Document the caveat prominently in all "How to Run" docs. The local startup instructions now include `rm -f agentcafe.db` before `python -m agentcafe.main`. Tests are unaffected because they use in-memory databases (`":memory:"`).
**Rationale:** SQLite has no built-in migration system. For MVP, deleting the DB is acceptable since it only contains seeded demo data. Phase 4 added a numbered SQL migration system (`db/migrate.py` + `db/migrations/0001–0006`) that handles incremental schema changes. The `rm -f` caveat remains relevant if the base schema (in `models.py`) changes, since migrations only handle additive changes.

---

### ADR-023: Menu schema extension for Passport V2 consent flow (ADR-009 amendment)

**Date:** February 27, 2026
**Context:** The Passport V2 design discussion (see `docs/passport/v2-design-discussion.md`) established that agents need richer metadata in the Menu to request human consent properly. Specifically: risk tier (for token lifetime ceilings), identity fields (for Cafe-side ownership verification), constraints schema (for the consent UI), and account-linking requirements. A three-way review (Jeremy + Claude + Grok) converged on these additions and agreed the ADR should be made before any dashboard UI work begins.
**Decision:** Extend the Menu Action object with five new optional fields per ADR-009's additive schema policy:
1. `risk_tier` (`"low"` | `"medium"` | `"high"` | `"critical"`, default `"medium"`) — determines token lifetime ceiling and verification depth.
2. `human_identifier_field` (`string | null`, default `null`) — field name in action inputs/responses containing human identity (e.g., `"customer_email"`). Serves double duty: fast-path input matching and read-before-write field targeting.
3. `constraints_schema` (`object | null`, default `null`) — JSON Schema the consent UI renders for human-settable limits (e.g., max price, time window).
4. `account_linking_required` (`boolean`, default `false`) — whether the human must link their service account before using this action.
5. `self_only` (`boolean`, default `true`) — actions scoped to human's own resources. Future `on_behalf_of` support will use `false`.
**Rationale:** All fields are optional, so this is 100% backward compatible — existing Menu entries work unchanged. Old seeded services get sensible defaults. Wizard-published services will populate these fields during the review step (AI enricher pre-fills from field names, company confirms). Making the ADR now ensures the Phase 5 dashboard and Phase 4 consent flow build against the correct schema from day one.

---

### ADR-024: Passport V2 — bearer authorization model (core reframing)

**Date:** February 27, 2026
**Context:** The V1 Passport design framed the Passport as a "digital Power of Attorney" — "this agent represents this human." Discussion revealed the POA analogy breaks down: POA names a specific, verifiable second party, but agents are ephemeral software with no persistent, verifiable identity. The V2 design discussion (`docs/architecture/passport/v2-discussion.md`) established a new framing through three-way review (Jeremy + Claude + Grok).
**Decision:**
1. **The Passport is a human-issued bearer authorization**, not an agent identity document. "I authorize the bearer" not "I authorize Agent X."
2. **Agent identity is intentionally out of scope.** Design Principle One is strengthened: `agent_id` is not just untrusted but irrelevant to the security model. The Passport works regardless of whether agents develop stable identities.
3. **Two-tier model:** Tier-1 (read-only, agent self-requests, no human) and Tier-2 (write-scope, requires human consent via Cafe-owned flow with passkey).
4. **The Cafe is the sole trusted issuer and consent broker.** Non-negotiable for product value and liability clarity.
5. **Cafe-side identity verification** (Proposed Principle Two): the Cafe enforces human-scoping by inspecting proxied data — no backend changes, no human identity broadcast. Layered by risk tier: agent-supplied identifier match for low-risk, +read-before-write for medium+, mandatory read for high/critical.
6. **Token expiry:** per-policy human-chosen with Cafe-enforced risk-tier ceilings. Single-use tokens for critical operations. Asymmetric ceremony preserved (easy to tighten, hard to loosen).
7. **Rolling proof deferred** to Phase 4. Short-lived tokens + rule-based anomaly detection + human audit dashboard sufficient for Phase 3.
**Rationale:** This reframing honestly acknowledges a new paradigm — a verified human delegating authority to an unverifiable autonomous entity — with no clean real-world analog. Anchoring trust exclusively to human identity (the only thing we can verify) produces a system that is robust regardless of how agent identity evolves. Design Principles Zero and One are preserved and strengthened. See `docs/architecture/passport/v2-discussion.md` §13 for the full convergence summary.

---

### ADR-025: Service Provider Zero-Trust Onboarding & Menu Integrity

**Date:** February 28, 2026
**Context:** Company onboarding (Phase 3 Wizard) is the sole entry point for every Menu item agents will ever discover. A malicious, compromised, or sloppy service can publish actions that appear harmless in the Menu but perform dangerous operations (e.g., "search-availability" that silently charges a card, or "book-room" that actually cancels all bookings). This bypasses all downstream Passport, consent, and audit controls because those controls trust the Menu description as ground truth.
**Decision:**
1. **Quarantine mode**: All new services start in quarantine (`proxy_configs.quarantine_until` timestamp, default 7 days — configurable via `QUARANTINE_DAYS` env var). Every action under quarantine forces full Tier-2 human consent (even low-risk read actions) plus Cafe admin notification. Quarantine auto-lifts after the period expires or via manual admin approval.
2. **Cafe owns all human-facing text**: Final consent text, `risk_tier`, `human_identifier_field`, `constraints_schema`, etc. are authored by the Cafe (AI enricher + review_engine + manual override). Service suggestions are advisory only and never reach the human or agent directly. (Already enforced — consent text generated by `_consent_text()` in `cafe/pages.py`.)
3. **Service suspension**: Any detected abuse triggers instant suspension (`proxy_configs.suspended_at` timestamp). Suspended services return `503 service_suspended` on all orders. Suspension is logged in the audit trail.
4. **Onboarding gates** (future): domain ownership verification (DNS TXT), contactable legal contact, optional manual review for actions flagged "high" or "critical" by static analysis. Deferred to Phase 6 — requires production infrastructure.
5. **Signed action catalog** (future): Services submit a cryptographically signed manifest (ECDSA or HMAC) of their action definitions. Deferred to Phase 6.
**Rationale:** The Menu is the root of discovery trust. Poisoned entries undermine Design Principle Zero and the entire layered model. By making the Cafe the single source of truth for semantics presented to humans, we eliminate misrepresentation at source while keeping onboarding frictionless for honest companies. Quarantine adds near-zero friction for good actors but blocks the attack entirely. Complements existing risk_tier (ADR-023), consent flow (ADR-024), and audit chain.
**Implementation:** Migration 0006 adds `quarantine_until` and `suspended_at` columns to `proxy_configs`. Router enforces quarantine (forces Tier-2) and suspension (blocks all). Publisher sets `quarantine_until` on publish. New tests for quarantine enforcement, suspension flow. No breaking changes to existing demo services (demo data sets `quarantine_until` to epoch past = already lifted).
**Status:** Approved. Cross-references: ADR-023 (Menu schema), ADR-024 (bearer model).

---

### ADR-026: Security Review Sprint 1–3 fixes (PROJECT_REVIEW_2 remediation)

**Date:** March 1–2, 2026
**Context:** A comprehensive security audit (`docs/reviews/review-2.md`) identified 12 items across three priority tiers: critical security hygiene (Sprint 1), structural fixes (Sprint 2), and UX improvements (Sprint 3). All 12 were implemented and verified with 214 tests passing.
**Decisions:**

**Sprint 1 — Security hygiene (7 fixes):**
1. **Revoke endpoint auth** (`cafe/passport.py`): `POST /cafe/revoke` now requires either `X-Api-Key` (admin) or valid token signature (self-revoke). Unauthenticated requests get 401/403.
2. **Consent decline → POST** (`cafe/pages.py`): Decline changed from GET to POST. CSRF tokens (HMAC-SHA256, 1-hour TTL, session-bound) added to all form endpoints (login, register, approve, decline).
3. **Human password hashing → bcrypt** (`cafe/human.py`): Replaced SHA-256 with bcrypt. Legacy SHA-256 hashes auto-upgrade to bcrypt on next login via `_rehash_if_legacy()`.
4. **Registration rate limiting** (`cafe/passport.py`): IP-based sliding-window rate limit (10/hour) on `POST /passport/register`. `agent_tag` required.
5. **CORS hardening** (`main.py`): `CORS_ALLOWED_ORIGINS` env var replaces wildcard. Credentials mode disabled when origin is `*`.
6. **Foreign keys enforced** (`db/engine.py`): `PRAGMA foreign_keys = ON` set on every connection.
7. **Admin API key in header** (`cafe/passport.py`, `wizard/router.py`): Moved from query parameter to `X-Api-Key` header. Added `POST /admin/services/{id}/suspend` endpoint.

**Sprint 2 — Structural fixes (3 fixes):**
8. **Multi-action consent** (`cafe/consent.py`): `InitiateRequest` accepts `action_ids: list[str]` with single `action_id` backward compat. Risk tier = highest among requested actions.
9. **Audit hash chain concurrency** (`cafe/router.py`): `asyncio.Lock` serializes SELECT+INSERT. Monotonic `seq INTEGER` column (migration 0007) replaces timestamp ordering.
10. **Configurable quarantine** (`config.py`, `wizard/publisher.py`): `QUARANTINE_DAYS` env var (default 7, was hardcoded 30).

**Sprint 3 — UX (2 fixes):**
11. **Human dashboard** (`cafe/pages.py`, `templates/dashboard.html`): `GET /dashboard` lists active/revoked policies. `POST /dashboard/revoke/{id}` one-click revoke with CSRF + ownership check. `GET /logout` added.
12. **Consent webhook** (`cafe/consent.py`, `cafe/pages.py`): `_fire_consent_callback()` POSTs `{consent_id, status, policy_id}` to `callback_url` on approve/decline. Best-effort, 10s timeout.

**Rationale:** The audit identified real attack surfaces (unauthenticated revoke, CSRF-less state changes, weak hashing) and structural gaps (timestamp-only audit ordering, hardcoded quarantine). Each fix was minimal and targeted — no over-engineering. All changes are backward compatible. See `docs/reviews/review-2.md` §12 for the full sprint log.
**Status:** Complete. 214 tests passing (up from 194 pre-review).

---

### ADR-027: Strategic positioning — authorization as core product

**Date:** March 6, 2026
**Context:** A strategic review involving two independent adversarial AI reviews (Grok, ChatGPT) examined AgentCafe's market positioning, assumptions, and architectural direction. Both reviewers independently converged on the same conclusion: the consent/authorization layer is the defensible moat, not the Menu or marketplace discovery. ChatGPT framed the core primitive as "human-authorized delegation to software agents" — comparable to OAuth or Stripe, not to MCP registries or API marketplaces. See `docs/strategy/strategic-review-briefing.md` §9 for the full convergence analysis.
**Decision:**
1. **The consent/authorization layer is the core product.** The Menu, proxy, discovery, and services all serve the authorization primitive. Architectural decisions should prioritize the delegation model's robustness and extensibility over marketplace features.
2. **Services on the Cafe are a bootstrap strategy, not the product.** Real services (starting with Agent Memory — a separate project/repo) attract agent traffic and prove demand. But the product companies are paying for is the safety infrastructure: consent flows, human authorization, audit trails, credential isolation, rate limiting.
3. **Build for autonomous agents.** Non-autonomous agents (developer-configured toolboxes) are served incidentally via MCP adapter (ADR-029) but are not the target audience. The architecture assumes agents that discover and choose tools at runtime.
4. **The browser is the real competitor for commodity actions.** Computer-use agents can navigate websites directly. AgentCafe wins only where the browser fails: authorization, payments, persistent state, cross-service coordination. Future service onboarding and Cafe-built services should focus on these defensible zones.
**Rationale:** Two independent reviewers arrived at this framing separately, which is strong signal. The existing architecture already reflects this positioning (bearer model, sole issuer, consent broker) — this ADR makes the positioning explicit so future decisions align. The marketplace metaphor (Cafe, Menu) remains as brand and UX framing, but the architectural center of gravity is the delegation primitive.
**Status:** Approved. Informs all Phase 8+ decisions.

---

### ADR-028: Company Cards — company-scoped consent policies

**Date:** March 6, 2026
**Context:** Both adversarial reviewers identified per-action consent fatigue as the #1 existential risk to AgentCafe. With 5–15 minute token lifetimes and per-action approval ceremonies, a human using multiple services would face dozens of consent requests per day — leading to either rubber-stamping (destroying safety) or platform abandonment. Grok rated this "existential." ChatGPT said "without this the system dies instantly."
**Decision:**
1. **Company Cards** replace per-action consent with **company-level relationships.** The human establishes a "card on the tab" with a specific company, setting constraints: budget cap, scope (included/excluded actions), duration, and first-use confirmation preference.
2. **Card ceremony:** One consent ceremony per company (passkey required). The human reviews the company, sets constraints, approves. This creates a multi-action policy with company-scoped constraints.
3. **Agent interaction:** Agent calls `POST /cafe/order` as usual. The Cafe checks for an applicable card policy. If the action is within the card's scope and budget → token issued automatically, no human ceremony. If the action is excluded or over budget → falls back to per-action consent.
4. **Risk-tier override:** Companies retain control. Low/medium actions are covered by cards automatically. High-risk actions can require per-action consent even if a card exists. Critical actions always require per-action, single-use tokens. No card bypass for critical.
5. **First-use confirmation:** When a card is first added, the very first real action triggers a lightweight confirmation to catch misaligned intent. The human can toggle this per card.
6. **Constraint enforcement:** The Cafe enforces budget caps, spending limits, excluded actions, and duration at order time. Violations are rejected — the agent must request a per-action approval or ask the human to raise the limit.
7. **Revocation:** Pulling a card is one click — instant, kills all active tokens issued under that card's policy.
**Implementation notes:**
- A Company Card is technically a multi-action policy with company-scoped constraints. The existing `policies` table, token exchange, and validation chain work with minimal changes.
- The consent ceremony scope changes: currently per-task, now also per-company.
- Token issuance changes: with a valid card, the agent can request tokens directly without a per-use consent ceremony.
- New dashboard view: the human's "Tab" — a list of company cards with activity summaries, constraint editing, and one-click revocation.
- Budget tracking requires a price field or company-tagged price field (consistent with `human_identifier_field` pattern from ADR-023).
**Rationale:** The card abstraction maps to how humans already think about service relationships (credit cards, Uber accounts, Amazon purchases). It preserves explicit human authorization (the card ceremony is still a deliberate act with passkey) while eliminating per-transaction friction for trusted, constrained relationships. The risk-tier override ensures companies retain control over dangerous actions. This directly addresses the existential risk identified by both reviewers without weakening the security model.
**Status:** Implemented (Phase 8.1). Migration 0010, `cafe/cards.py`, Tab dashboard (`/tab`), card approval page, 403 `card_suggestion` in order responses, budget tracking via `report-spend`. 40 tests. See `docs/planning/company-cards-plan.md` for implementation details.

---

### ADR-029: MCP Server Adapter — 4-tool LLM-native discovery pattern via remote Streamable HTTP

**Date:** March 6, 2026 (original). **Updated:** March 8, 2026 (four-review convergence: Grok, ChatGPT, cross-validated by Claude, filtered by Jeremy).
**Context:** The MCP ecosystem is the de-facto tool discovery standard for both autonomous and non-autonomous agents (March 2026). Three public registries exist (official MCP Registry, GitHub mirror, Microsoft Azure MCP Hub). Autonomous agents use `registry.discover` at runtime for tool discovery without developer pre-configuration. AgentCafe needs to be discoverable in these registries. However, naively exposing the entire Menu as MCP tools creates three problems: (1) transport ambiguity (stdio vs remote), (2) no MCP standard for human-in-the-loop consent, (3) tool lists degrade agent accuracy past ~30-50 tools. A fourth strategic question — who is the first real traffic source — was identified by ChatGPT as the most important blindspot in the original design.
**Decision:**
1. **Transport: Remote Streamable HTTP only** at `https://agentcafe.io/mcp`. This is the recommended remote transport in the current MCP spec (replaced HTTP+SSE in June 2025). Implementation: one FastAPI endpoint using Starlette `StreamingResponse` + JSON-RPC 2.0 routing (~200 LOC). Stdio deferred — we do not own or maintain a local package.
2. **4-tool LLM-native discovery pattern.** MCP `tools/list` returns exactly 4 permanent tools (~1000 tokens, never grows). Tool names are verb-first and LLM-aligned (`search`/`get`/`invoke` match model priors) rather than leaking internal Cafe metaphors (`menu`/`order`/`company_card`). Internal code keeps the original metaphors; the MCP layer translates at the boundary only.
   - `cafe.search` (Tier-1 read) — semantic search across the entire Menu. Accepts `query`, `category`, `capability_tags`, `max_results`. Returns **summaries only** (`service_id`, `action_id`, `name`, `short_description`, `risk_tier`, `relevance`). No inline schemas — keeps context stable across repeated searches. Implementation: structured filter via `menu.py` + keyword boost + LiteLLM rerank. No vector DB for MVP.
   - `cafe.get_details` (Tier-1 read) — returns the full semantic Menu entry for a specific `service_id` on demand (`required_inputs`, `constraints_schema`, `risk_tier`, `human_identifier_field`, etc.). One service at a time = no context explosion. Agent calls this after `cafe.search`.
   - `cafe.request_card` (Tier-1) — initiates the Company Card flow (ADR-028). Non-blocking: returns `card_request_id`, `consent_url`, `activation_code`, `status: "pending"`. Human approves asynchronously. This tool teaches agents the Cafe's core authorization pattern.
   - `cafe.invoke` (universal execution) — generic `service_id` + `action_id` + `inputs`. Routes to existing `POST /cafe/order`. All consent/card/policy logic stays in the proxy.
3. **Tier-2 actions fail fast.** All writes go through `cafe.invoke`. If a Tier-2 action is attempted without authorization, the adapter returns a structured JSON-RPC error: `{ "error": "HUMAN_AUTH_REQUIRED", "consent_id": "...", "consent_url": "...", "activation_code": "...", "card_suggestion": true }`. The adapter does NOT orchestrate consent — no polling, no URL surfacing, no workflow engine. Post-Company Cards, most writes auto-approve under card policy.
4. **Architectural guardrail: MCP adapts to the Cafe — not the other way around.** If the adapter ever begins dictating consent flow, tool structure, discovery mechanism, or token behavior, it has become the product and must be killed or rolled back. The Cafe's own protocol (`GET /cafe/menu`, `POST /cafe/order`) remains primary.
5. **Sequencing: ships after Company Cards and Agent Memory (Phase 8.3).** The `cafe.request_card` tool and `HUMAN_AUTH_REQUIRED` error both require the card policy engine. Agent Memory auto-appears in `cafe.search` once published to the Menu.
6. **Registry listing is self-service:** `npx @modelcontextprotocol/registry publish --url https://agentcafe.io/mcp --name AgentCafe`. No approval gate, no paid tier. Indexed instantly.
7. **Traffic source hierarchy:** (a) Short-term: framework/IDE developers adding the MCP server. (b) Medium-term: Agent Memory creating stickiness. (c) Long-term: autonomous registry discovery. We do NOT rely on autonomous discovery as the first source.
**Rationale:** MCP is non-negotiable distribution infrastructure. Autonomous agents discover tools via MCP registries at runtime — if the Cafe isn't registered, it doesn't exist for those agents. The 4-tool LLM-native pattern solves the context window problem (summaries from search + schema on demand; scales to 10,000+ services), the consent gap (fail-fast error + card flow), the transport question (remote Streamable HTTP, no shipped artifacts), and tool-selection accuracy (verb-aligned names match model priors). MCP has no authorization standard — that gap is the Cafe's product. For companies: "don't build an MCP server — publish through the Cafe wizard and you're available to every MCP-compatible agent, with safety included."
**Status:** Approved (implementation-ready). Ships as Phase 8.3, after Company Cards (8.1) and Agent Memory (8.2). See `docs/strategy/strategic-review-briefing.md` §8.2 + §8.2.2 for the full design and four-review convergence spec.
