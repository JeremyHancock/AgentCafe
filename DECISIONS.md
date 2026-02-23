# AgentCafe — Architectural Decisions Log

**Purpose:** Captures key decisions with rationale so future contributors (human or AI) understand *why*, not just *what*.  
**Last Updated:** February 21, 2026

---

### ADR-001: Docker Compose moved to Phase 2 (not Phase 6)

**Date:** February 20, 2026  
**Context:** Initially Docker was deferred to Phase 6 (Packaging & Release Prep) to keep iteration speed high.  
**Decision:** Move Docker Compose to early Phase 2.  
**Rationale:** The demo backends are meant to represent opaque third-party servers. Running everything in one process (`asyncio.gather` in `main.py`) hides real network boundaries — the Cafe and backends share memory, localhost, and the same process. This makes it too easy to accidentally couple them. Containerizing early forces the proxy to go through real HTTP, matching the production architecture. Phase 6 Docker is now specifically for *production-hardened* images (multi-stage builds, etc.).

---

### ADR-002: Menu entries loaded from Phase 0.2 JSON files (single source of truth)

**Date:** February 20, 2026  
**Context:** The original `seed.py` hardcoded ~350 lines of Menu entry dicts, duplicating what was already defined in `docs/design/services/*/menu-entry.json`.  
**Decision:** `seed.py` loads Menu entries from the JSON files at startup. Proxy configs remain hardcoded in `seed.py` (they're implementation details, not design artifacts).  
**Rationale:** Single source of truth. If someone edits a Menu entry, they edit the design file and it flows through automatically. No risk of the design files and the seeded database drifting apart. `config.py` has a `design_dir` setting (defaults to `docs/design` relative to project root, overridable via `CAFE_DESIGN_DIR` env var).

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
**Context:** The MVP "demo-passport" magic string provided no real security. Phase 2 required a cryptographically secure, scoped, revocable delegation system — designed collaboratively with Grok (advisor) and documented in `docs/passport/design.md`.
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
