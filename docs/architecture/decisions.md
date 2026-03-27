# AgentCafe — Architectural Decisions Log

**Purpose:** Captures key decisions with rationale so future contributors (human or AI) understand *why*, not just *what*.  
**Last Updated:** March 14, 2026

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

---

### ADR-030: Consent ceremony, human notification, and implementation scoping decisions for Service Integration Standard

**Date:** March 14, 2026
**Context:** During Jeremy's review of the three Service Integration Standard specs (Artifact 0: Proxy Behavior, Artifact 1: Per-Request Artifact, Artifact 2: Service Contract & Identity Binding), a red-team analysis of rogue agent attack vectors surfaced design decisions about consent ceremony security, human notification channels, and implementation sequencing. The specs themselves (protocol between AC and services) are unchanged — these decisions govern how AC communicates with humans and how implementation is phased.

**Decisions:**

1. **Email-primary consent channel with risk-tier degradation.** AC sends a consent notification email directly to the human for ALL risk tiers — email is the ground truth channel that agents cannot intercept or frame. The agent's access to consent details degrades by risk tier:
   - **Low** (read-only): AC sends email + agent receives consent URL + `human_message`.
   - **Medium**: AC sends email + agent receives activation code (no URL) + `human_message`.
   - **High / Critical**: AC sends email only. Agent receives a poll endpoint (`/consents/{id}/status`) + `human_message`.
   All tiers include a `human_message` field in the consent initiation response — an AC-authored plain-language string the agent can relay to the human (e.g., `"Please check your email from AgentCafe to approve this request."`). This ensures the agent always knows what to tell the human regardless of modality, and AC controls the framing. The agent can relay it verbatim or rephrase, but the ground truth comes from AC, not the agent's own description of what's being requested.
   This solves the multi-modality problem (text chat, voice, background agents) and limits the agent's ability to misrepresent the consent scope to the human. The email is always primary; the agent relay is a convenience shortcut.

2. **PWA (Progressive Web App) for consent ceremonies.** AC needs a PWA to provide push notifications as a direct, unforgeable channel to the human. Ceremony flow: push notification → tap → AC consent page → passkey (biometric) → approved (~3 seconds). Email serves as universal fallback for humans who haven't installed the PWA. The PWA also provides persistent presence on the human's device for: consent notifications, revocation confirmations, human dashboard, and post-approval alerts. Built on the existing stack (Jinja2, FastAPI, same domain). Main addition: service worker for push notifications. No native app required.

3. **Plain-language scope descriptions in the OS-level passkey prompt.** The WebAuthn authenticator dialog is the last line of defense — it's the one piece of UI that no agent, no script, and no DOM injection can touch. AC must show plain-language scope descriptions (e.g., "Grant payments:charge to Helpful Assistant") in the OS-level prompt, not just the web UI. Short-term: maximize clarity in the web UI with risk-tier visual differentiation. Medium-term: use WebAuthn `extensions` or platform-specific APIs as they become available. Long-term: advocate for richer relying-party-supplied context in authenticator dialogs.

4. **Service Integration Developer Guide.** The three Service Integration Standard specs are internal architecture documents — they are the source of truth but are not suitable as service-facing documentation. Before third-party onboarding, AC must produce a separate developer-facing guide: step-by-step onboarding, what the SDK handles vs. what the service implements, code samples, and endpoint stubs. The SDK (Artifact 5) is the primary integration path — most protocol details (JWKS caching, `jti` tracking, fetch cooldowns, `kid` matching) should never require manual implementation.

5. **Two-phase audit log deferred for HM MVS.** The specs document the two-phase audit pattern (placeholder before proxy, finalize after) as the destination state. For HM MVS, a single-phase write after the proxy call is sufficient. HM is AC-owned and co-located — crashes are rare, and AC owns both sides of any dispute. The two-phase pattern becomes necessary when third-party services are onboarded and disputes have legal or financial consequences. The spec remains unchanged (it documents the correct end state); this is an implementation sequencing decision only.

6. **Eager binding resolution for Company Cards.** When a Company Card is issued, AC SHOULD resolve all service bindings within the card approval ceremony — not lazily at first proxy request. This prevents agents from hitting `ACCOUNT_LINK_REQUIRED` mid-task and eliminates the need for agents to have a synchronous human channel at proxy time. The human completes one session: approve card (passkey) + link service (if needed). For services using `broker_delegated` (like HM), linking is automatic and adds zero human touches. For services requiring OAuth linking, the redirect is chained into the card approval flow. Already-linked services reuse existing bindings. This is a UX design principle for the Company Cards phase (Phase 8.1); the protocol already supports both eager and lazy resolution.

**Rationale:** The red-team analysis identified that the hardest attacks to defend against are not protocol-level (the crypto is solid) but social engineering: consent farming, misleading agent framing, and the irreducible "human approves without reading" problem. Decisions 1–3 address this by moving consent communication to channels the agent cannot control (email, push notifications, OS-level authenticator prompts). Decision 4 ensures third-party developers can integrate without reading 2,400 lines of architecture specs. Decisions 5–6 are implementation sequencing that keeps HM MVS lean while preserving the correct destination-state design in the specs.
**Status:** Approved. Decision 5 implemented (single-phase audit for HM MVS). Decision 6 implemented (eager binding in card approval via `_create_jv_grant_if_needed`). Decisions 1–4 are pre-third-party-onboarding deliverables (deferred past HM MVS).

---

### ADR-031: Service Integration Standard — ratification of jointly-verified mode

**Date:** March 14, 2026 (specs locked). Design work: March 8–14, 2026.
**Context:** AgentCafe's proxy (`router.py`) previously operated in a single mode: AC holds the backend credential, the service trusts AC implicitly, and the human's identity is invisible to the service. This works for simple APIs but fails for account-bearing services (banking, memory, payments) where the service needs to know *which human* is acting and must independently verify authorization. The Service Integration Standard introduces "jointly-verified mode" — a second integration path where AC and the service share responsibility for authorization, identity, and revocation.

**What it introduces:**

1. **Jointly-verified integration mode.** A new `integration_mode = 'jointly_verified'` flag on `proxy_configs` that activates an extended proxy path. Standard-mode services are completely unaffected — the flag defaults to `NULL` (standard).
2. **Per-request authorization artifact.** A signed JWT (RS256, 30s TTL) attached to every proxied request via `X-AgentCafe-Authorization` header. Contains: `sub` (service-side account ID), `action`, `scopes`, `consent_ref`, `ac_human_id_hash` (identity correlator), `request_hash` (integrity binding), `jti` (replay nonce + audit ID), `standard_version`. The artifact is not a session token — one artifact per request, non-reusable.
3. **Identity binding protocol.** A 6-endpoint contract (`account-check`, `account-create`, `link-complete`, `unlink`, `revoke`, `grant-status`) that services implement for AC to call during consent and revocation flows. Capability negotiation allows services to declare which endpoints they support.
4. **Binding vs. grant separation.** Two tables: `human_service_accounts` tracks identity ("who is this human on the service?"), `authorization_grants` tracks authorization ("what are they allowed to do?"). Bindings outlive grants — a human can revoke a policy without losing their service account link.
5. **State machine.** A 6-gate proxy path: Passport validation → scope check → binding + grant resolution → artifact signing → proxy call → audit finalization. Fail-closed at every gate.
6. **Revocation with defense in depth.** Four layers: AC stops issuing artifacts immediately, pushes revocation to service, 30s artifact TTL as backstop, periodic reconciliation for third-party services. Service stores revoked `consent_ref` values to close the in-flight artifact window.
7. **Separate artifact key infrastructure.** Artifact signing uses a dedicated key pair (`art_` prefix), independent from Passport keys (`psp_` prefix). Both served via JWKS with `kid`-based selection.
8. **`request_hash` integrity binding.** SHA-256 of HTTP method + normalized path + raw body bytes. Binds the artifact to the specific request, preventing endpoint-swapping and body-tampering attacks. Raw bytes (not JSON canonicalization) avoids cross-library serialization mismatches.

**Key architectural decisions (detail in the specs):**

- **Bearer artifact, not session token.** 30s TTL, one per request. No token refresh, no session management.
- **Binding outlives grants.** Revocation removes authorization, not identity. Re-consent creates a new grant without re-linking.
- **`ac_human_id_hash` full 64-char hex.** Defense-in-depth identity correlator. Full hash avoids v1→v2 truncation compatibility issues.
- **No path lowercasing in `request_hash`.** Third-party services may have case-sensitive paths. The path in `proxy_configs` is authoritative.
- **`UNIQUE(consent_ref, service_id)` on grants.** Supports Company Card fan-out (one card creates grants on multiple services).
- **Fail-closed deferred state.** If a service is unreachable during consent, the binding is `deferred` and proxy requests return `SERVICE_SETUP_PENDING` (503) until resolved.
- **Service-side revocation storage.** Services must store and check revoked `consent_ref` values — AC's "stop issuing" is necessary but not sufficient due to the 30s artifact TTL window.
- **Capability negotiation.** Services declare which endpoints they implement. AC adapts consent flows accordingly. Services that declare none of `account_check`, `account_create`, or `link_complete` are rejected at onboarding — there would be no way to establish a genuine `sub` claim.

**Review process:** 10 rounds across 4 reviewers. 65 findings total: 55 fixed, 5 already addressed, 5 rebutted (stale context).

| Reviewer | Rounds | Role |
|----------|--------|------|
| Self (Claude) | 2 | Initial consistency + cross-reference checks |
| ChatGPT | 3 | Adversarial architectural review |
| Grok | 3 | Adversarial security + codebase alignment review |
| Cascade (Claude) | 2 | Adversarial consistency + logic review |

Key findings fixed: missing grant-not-found check in Gate 3, `UNIQUE` constraint too narrow for card fan-out, grant query missing `service_id` filter, path lowercasing causing replay mismatches, `GRANT_REVOKED` missing from error code table, stale cross-references after section renumbering, duplicate code block with stale `.lower()`.

**Canonical files (locked):**

- `docs/architecture/service-integration/proxy-behavior-state-machine-spec.md` — Artifact 0. How `router.py` orchestrates the jointly-verified proxy path.
- `docs/architecture/service-integration/per-request-artifact-spec.md` — Artifact 1. The authorization artifact format, signing, validation, and key infrastructure.
- `docs/architecture/service-integration/service-contract-identity-binding-protocol.md` — Artifact 2. The service contract: identity binding, revocation, and capability negotiation.

**Supersedes:** `docs/strategy/service-integration-standard-briefing.md` — the original briefing document that preceded these specs. The briefing remains for historical context but diverges from the canonical specs on: `correlation_id` (removed), grant state storage (moved to `authorization_grants`), `request_hash` algorithm (method + path added), and path normalization (lowercasing removed).

**MVS scope (Human Memory):** HM is the first jointly-verified service. The MVS implements the full artifact (no claim subsetting) with a reduced surface: `account-check` + `account-create` + `revoke` only. Deferred past HM: linking flow, unlinking, reconciliation, capability wizard, `service_integration_configs` table, deferred binding background resolver, Company Card fan-out. See §12 in Artifact 0, §12 in Artifact 1, §7 in Artifact 2 for detailed MVS scoping.

**Rationale:** Standard-mode proxying works for stateless APIs but creates an untenable trust model for account-bearing services: the service has no idea who is acting, no independent way to verify authorization, and no mechanism to deny a revoked grant. Jointly-verified mode closes these gaps while preserving standard-mode for services that don't need it. The protocol is validated against both HM (simplest case: AC-owned, `broker_delegated`, no existing users) and Stripe (complex case: third-party, OAuth linking, existing users) with no modifications needed. Related decisions: ADR-024 (bearer model), ADR-027 (service-integration definition), ADR-028 (Company Cards), ADR-030 (consent ceremony and implementation scoping).
**Status:** Locked and implemented (March 25, 2026). AC-side infrastructure complete across two PRs:
- PR 1: `cafe/artifact.py`, `cafe/binding.py`, `cafe/router.py` (Gates 3-4), `cafe/consent.py`, `cafe/cards.py`, `keys.py`, migration 0012. 45 tests.
- PR 2: `cafe/integration.py`, `cafe/pages.py`, `cafe/cards.py`, `main.py` (background retry), migration 0013. 25 tests.
- HM onboarding: AC ready. Awaiting HM-side artifact validation and integration endpoints.

---

### ADR-032: Account creation strategy for jointly-verified services — hybrid model

**Date:** March 27, 2026
**Context:** During HM's Phase 2 integration, HM raised a fragmentation concern: a human who signs up directly at a service AND later connects through AC ends up with two accounts and siloed data. In `opaque_id` mode, AC never shares the human's email, so the service cannot match the brokered account to the direct one. The accounts are permanently split.

Internal review escalated this to an existential concern: **if using AgentCafe makes a human's experience worse than using the service directly, AC is creating a problem, not a solution.** A bank customer who accidentally gets a second account through AC, or an HM user whose agent-stored memories land in an inaccessible namespace, are unacceptable outcomes.

The initial proposal — eliminate brokered accounts entirely for jointly-verified services and require redirect-based linking for all account creation — was pressure-tested by two adversarial reviewers (architectural + security/UX). Both found it too broad: it breaks AC-owned services (HM has no login UI), headless/API-only services, autonomous agents with no human present, and voice-only agents that can't complete browser redirects.

**Decision: Hybrid model based on service-declared `has_direct_signup`.**

Services declare during onboarding whether humans can create accounts directly (outside AC). This single flag determines the consent-time account creation strategy:

1. **`has_direct_signup: false`** (AC-owned services, API-only services, new services with no existing users).
   - Consent flow uses `account-create` as today. Brokered accounts are the correct mechanism — there are no existing accounts to fragment against.
   - No linking flow needed.
   - Example: Human Memory (AC-owned, no independent signup today).

2. **`has_direct_signup: true`** (services with existing user bases — banks, SaaS tools, any service with its own registration).
   - During consent, AC asks the human: **"Do you already have a {Service} account?"**
   - **If yes** → service-initiated linking flow (Service Contract §A.4). The human authenticates on the service's site, the service confirms the match, AC binds the existing account. No brokered account created.
   - **If no** → `account-create` proceeds normally. The human is genuinely new to the service; no fragmentation risk.
   - **If the human answers wrong** (says "no" but has an existing account) → AC provides a post-hoc "Link existing account" flow accessible from the human's dashboard. This is the recovery path, not the happy path.

3. **Agent fragmentation signal.** For `has_direct_signup: true` services, when the human's account was created via `account-create` (not linked), AC includes `X-AgentCafe-Account-Status: unlinked` on proxied requests. Agents can use this signal to surface a linking prompt to the human. This is advisory — agents are not required to act on it, but well-designed agents should.

4. **`has_direct_signup` is declared at onboarding.** It is recorded in `service_integration_configs` (or the MVS equivalent) and cannot be changed without re-onboarding. It appears in the configuration agreement template.

5. **Schema migration guidance is concept-level only.** AC does not prescribe specific SQL or migration strategies. Services run diverse database engines (PostgreSQL, MySQL, SQLite, others) and have varying schema complexity. AC's guidance states *what* the schema must support (nullable identity fields, account type distinction), not *how* to implement the migration. Services plan their own migration strategy.

**Key invariants:**

- Brokered accounts (`account-create`) remain a first-class mechanism. They are correct for `has_direct_signup: false` services and for genuinely new users on `has_direct_signup: true` services.
- The consent ceremony is the primary fragmentation prevention point. By asking "do you already have an account?" at consent time, AC prevents the most common fragmentation scenario.
- The linking flow (Service Contract §A.4–A.5) is the mechanism for connecting existing accounts. It is already specified and requires no protocol changes — only the consent UI and the `has_direct_signup` declaration are new.
- The dashboard "Link existing account" flow is the recovery path for humans who answered incorrectly. It uses the same linking protocol.
- PAT issuance policy on brokered accounts is a **service-level decision**, not an AC-mandated constraint. Services should enforce it via route-level policy (not schema constraints) so it can evolve with account linking.

**Security mitigations for the linking flow:**

- `linking_url` domain must match the service's declared domain (no open redirect).
- CSRF `state` parameter round-tripped through AC (already in §A.4).
- Linking codes are time-bound (5 min max), single-use, and bound to `consent_ref`.
- Service authentication quality during linking is the service's responsibility — AC validates the binding, not the service's login strength.

**What this means for HM specifically:**

- HM has `has_direct_signup: false` today. Brokered `account-create` remains correct.
- When/if HM adds direct signup (their Phase 3 roadmap), they would update to `has_direct_signup: true` and implement the linking flow.
- HM's proposed route-level PAT restriction for brokered accounts is the correct pattern — AC endorses policy-level enforcement over schema constraints.
- The namespace fragmentation HM identified is real but deferred until HM has direct signup.

**Review process:** 2 adversarial reviewers. Key findings that shaped this decision:
- Eliminating brokered accounts universally breaks AC-owned services, headless APIs, autonomous agents, and voice-only agents.
- Redirect-based linking requires a human in the loop — cannot be the only account creation path.
- The fragmentation problem is service-type-dependent, not universal — the fix should be too.
- `linking_url` needs domain validation to prevent open redirect attacks.

**Rationale:** The hybrid model satisfies both constraints: (1) AC must not silently fragment a human's data across duplicate accounts, and (2) AC must support autonomous agents, headless services, and services where brokered accounts are the only correct mechanism. The `has_direct_signup` flag makes this a service-level declaration rather than a protocol-level restriction, and the consent-time question makes the system correct by default rather than relying on post-hoc human action.
**Status:** Approved. Design only — implementation deferred to Phase 3 (account linking). Onboarding guide and configuration template updated to reflect this decision. HM notified.
