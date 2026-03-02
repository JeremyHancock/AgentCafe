# AgentCafe — Comprehensive Project Review

**Date:** February 27, 2026  
**Scope:** Full audit of code vs. stated goals, gap analysis, placeholder inventory, viability assessment  
**Method:** Read every source file, design doc, test file, and config; cross-referenced against DEVELOPMENT-PLAN.md, AGENT_CONTEXT.md, and docs/design/onboarding-wizard/ARCHITECTURE.md

---

## 1. Executive Summary

The project has a strong foundation. Phases 0–3 are genuinely complete: the core proxy architecture works end-to-end, the wizard API flow is functional, and the codebase is clean (85 tests, pylint 10.00/10). After the initial review identified critical design-vs-implementation mismatches (scopes, cost format, missing post-publish endpoints), all three were fixed in the same session. The remaining gaps are primarily absent schema validation in the spec parser, security placeholders (Phase 4), and the lack of any database migration story.

**Overall assessment:** The code achieves ~90% of the stated Phase 0–3 goals. The remaining 10% is security placeholders documented for Phase 4 and a few unimplemented design-doc features (file upload, email, formal schema validation).

---

## 2. What Works Well (Strengths)

### 2.1 Core Architecture — Solid
- **Menu → Order → Proxy** pipeline is real and functional
- Double validation (Passport + Policy) works correctly in both MVP and JWT modes
- Audit logging on every order with hashed PII
- Rate limiting (sliding window from audit_log) is real and tested
- Input type validation against Menu schema is real and tested
- Path parameter resolution in the proxy layer works

### 2.2 Wizard API — Complete Flow
- All 6 steps implemented: Create account → Parse spec → Review → Policy → Preview → Publish
- JWT session tokens with ownership enforcement on all draft endpoints
- bcrypt passwords, Pydantic validation on company create
- Spec parser handles YAML/JSON, $ref resolution, read/write classification
- Rule-based enricher fallback works without any LLM dependency
- Publisher does atomic writes to published_services + proxy_configs
- Dry-run endpoint tests backend reachability

### 2.3 Code Quality
- Pylint 10.00/10 with zero suppressed warnings on real issues
- Consistent `_State` pattern for module-level state
- Good test structure: fixtures in conftest.py, in-memory DBs, ASGI transport mocking
- 85 tests covering menu format, order flow, passport, policy, wizard, and post-publish management

### 2.4 Demo Backends — Realistic
- All 3 backends (hotel, lunch, home service) implement realistic business logic
- In-memory state management (bookings, orders, appointments) with proper error handling
- Backends match the proxy_configs in seed.py (paths, methods, request models all align)

---

## 3. Design-vs-Implementation Mismatches

### 3.1 Menu Entry Scopes — ✅ FIXED

**Was:** The design files used generic scopes (`hotel:search`, `food:browse`, `home:book`) that didn't match the runtime scopes in `seed.py` (`stayright-hotels:search-availability`, etc.). Agents saw wrong scope requirements on the Menu.

**Fix applied:** Updated `cost.required_scopes` in all 4 design files (`hotel-booking/menu-entry.json`, `lunch-delivery/menu-entry.json`, `home-service-appointment/menu-entry.json`, `menu/full-menu.json`) — 13 scope values total — to match `seed.py` proxy_configs exactly.

### 3.2 Menu Entry `cost.limits` Format — ✅ FIXED

**Was:** Design files used `"limits": "60 requests per minute"` (string) while the wizard generated `"limits": {"rate_limit": "60/minute"}` (object). Inconsistent locked Menu format.

**Fix applied:** Standardized all 4 design files to the object format `{"rate_limit": "60/minute"}` — 13 occurrences updated. Seeded and wizard-published services now use the same format.

### 3.3 Post-Publish Management Endpoints — ✅ FIXED

**Was:** ARCHITECTURE.md specified 5 post-publish management endpoints; none existed.

**Fix applied:** Implemented 5 new endpoints in `wizard/router.py`:
- `GET /wizard/services/{service_id}/dashboard` — service info + audit_log request counts
- `PUT /wizard/services/{service_id}/pause` — sets status to `paused` (hidden from Menu)
- `PUT /wizard/services/{service_id}/resume` — restores paused service to `live`
- `PUT /wizard/services/{service_id}/unpublish` — permanently removes from Menu
- `GET /wizard/services/{service_id}/logs` — anonymized audit log entries (capped at 200)

All enforce JWT auth + ownership. Added 4 Pydantic response models to `wizard/models.py`. 8 new tests cover: dashboard, pause, pause-idempotency, resume, unpublish, logs, ownership (403), not-found (404). The `PUT .../edit` endpoint (re-enter wizard) is deferred to Phase 5 UI.

### 3.4 Schema Validation Not Implemented in Spec Parser

**The problem:** The ARCHITECTURE.md says the spec parser does "Schema validation: Validate against the official OpenAPI JSON Schema." The implementation in `spec_parser.py` does format detection, version validation, and operation extraction — but does **not** validate the spec against the official OpenAPI JSON Schema (despite `jsonschema` being in the `[wizard]` optional dependencies).

**Impact:** Invalid OpenAPI specs that happen to be valid YAML will pass parsing without error. The design promised formal validation.

**Fix:** Either implement JSON Schema validation against the official OpenAPI 3.x meta-schema, or update the design doc to clarify that validation is structural (version + paths + operations) rather than formal schema validation.

---

## 4. Placeholders and Incomplete Features

### 4.1 Backend Credential Encryption — NOT IMPLEMENTED (Phase 4)

**Status:** `backend_auth_header` is stored as plaintext in both `proxy_configs` and `draft_services`. The ARCHITECTURE.md says "Backend auth headers: Encrypted at rest in SQLite (AES-256, key from environment variable)." The design file structure even lists `db/encryption.py` as a target file — it doesn't exist.

**Risk:** Any service that provides a backend auth header has that credential stored in plaintext in SQLite. Moderate risk for MVP, critical for production.

### 4.2 LLM Enrichment — UNTESTED IN PRODUCTION

**Status:** The `ai_enricher.py` has a complete LiteLLM integration, but:
- `ENRICHMENT_MODEL = "gpt-4o-mini"` is hardcoded (not env-configurable)
- The `litellm` import is conditional — if not installed, rule-based fallback runs
- `litellm` is in `[wizard]` optional deps, not in main deps
- No test exercises the actual LLM path (all tests use rule-based fallback)
- The `x-agentcafe-*` extension merging described in ARCHITECTURE.md (preset values override AI-generated values) is **not implemented** — presets are parsed but never used in the enricher

**Impact:** The "AI-assisted" selling point of the wizard has never actually been exercised. The rule-based fallback works but produces generic slugified names rather than polished descriptions.

### 4.3 File Upload and URL Fetch for Specs — NOT IMPLEMENTED

**Status:** The ARCHITECTURE.md says the spec parser accepts "Raw OpenAPI spec (YAML or JSON string, file upload, or URL fetch)." The implementation only accepts raw string in `SpecParseRequest.raw_spec`. No multipart file upload, no URL fetch endpoint.

### 4.4 Confirmation Email on Publish — NOT IMPLEMENTED

**Status:** The FLOW.md and ARCHITECTURE.md both describe sending a confirmation email on publish. No email sending code exists anywhere.

### 4.5 "Amber" Confidence Highlighting — NOT IMPLEMENTED

**Status:** The FLOW.md describes highlighting low-confidence fields in amber for the company to review. The `CandidateMenuEntry` and `CandidateAction` models have `confidence: dict[str, float]` fields, and the rule-based enricher sets `confidence.description = 0.4` etc. — but nothing in the review or preview endpoints surfaces these confidence scores to the company. They exist in the data model but are invisible.

### 4.6 Input Injection Protection — NOT IMPLEMENTED (Phase 4)

**Status:** Listed as Phase 4 in DEVELOPMENT-PLAN.md but no code exists. The proxy forwards `req.inputs` directly to backends without sanitization. Path parameters are resolved via string replacement, which could be exploited:
```python
resolved_path = backend_path
for key, value in req.inputs.items():
    resolved_path = resolved_path.replace(f"{{{key}}}", str(value))
```
An agent could send `room_id = "../../../etc/passwd"` and the path would resolve without sanitization.

### 4.7 Tamper-Evident Audit Logging — NOT IMPLEMENTED (Phase 4)

**Status:** Audit logging exists and is real, but there's no tamper-evidence (hash chaining, HMAC signatures, etc.). Listed as Phase 4.

---

## 5. Scope Gaps to Viable Product

### 5.1 No Way to Issue Passports for Wizard-Published Services

**The problem:** When a company publishes via the wizard, their service gets scopes like `stayright-hotels-wizard:search-availability`. But there's no mechanism for an agent to get a Passport with those scopes.

In MVP mode, `demo-passport` has hardcoded scopes for the 3 seeded services only. In real JWT mode, `POST /passport/issue` requires an `ISSUER_API_KEY` and the caller must know the exact scope strings.

**Impact:** Wizard-published services are discoverable on the Menu but **unusable by any agent** unless someone manually issues a JWT with the right scopes. This is a fundamental product gap.

**Fix needed:** Either:
- Auto-register wizard-published scopes with the passport system so newly issued passports can include them
- Provide a wizard endpoint that returns the required scopes for a published service
- Add a test passport generation feature for companies to verify their service works

### 5.2 No Company Dashboard

**Impact:** Companies can publish but cannot manage, monitor, or unpublish their services. There's no visibility into how their service is being used.

### 5.3 No Agent SDK or Example

**Impact:** The README and architecture describe an agent-first marketplace, but there's no example agent, SDK, or tutorial showing how an agent would actually discover and use services. Phase 5 lists "End-to-end demo with a simple test agent" but this is critical for product viability — without it, there's no proof the agent experience works.

### 5.4 Single-Process SQLite Limitation

**Impact:** The architecture uses a single `aiosqlite` connection stored in `_state.db`. This works for local development but won't scale to multiple workers or containers. Docker Compose runs the Cafe as a single process, but production would need a real database or at least WAL mode + connection pooling.

### 5.5 No CORS Configuration

**Impact:** If anyone builds a web frontend (the Phase 5 Wizard Dashboard), the FastAPI app has no CORS middleware configured. API calls from a browser-based dashboard will be blocked.

---

## 6. Test Coverage Assessment

### 6.1 What's Well-Tested
- Menu format compliance (7 tests)
- Order rejection paths: missing action, missing inputs, invalid types, invalid passport, scope missing, human auth (8 tests)
- Full JWT lifecycle: issuance, scope validation, wildcard, authorization, expiry, revocation (12 tests)
- Rate limiting: unit parsing + integration with audit_log (21 tests)
- Wizard: spec parsing, enrichment, hotel spec with $ref, full API flow, ownership, auth, dry-run, post-publish management (37 tests)

### 6.2 What's NOT Tested
- **Happy-path order through ALL 3 services** — only hotel `search-availability` is tested with a real backend round-trip. Lunch and home service backends are never exercised in tests.
- **Wizard publish → order flow** — no test verifies that a wizard-published service can actually be ordered through `POST /cafe/order`
- **Docker Compose** — no integration test verifies the 4-container setup works
- **LLM enrichment path** — only rule-based fallback is tested
- ~~**Company login** — the `/wizard/companies/login` endpoint has no dedicated test~~ (login is now tested: `test_wizard_login` + `test_wizard_login_bad_password`)
- **Concurrent access** — no tests for race conditions on shared state (rate limiting, draft edits)
- **Spec parser edge cases** — Swagger 2.0 rejection is tested, but things like circular $refs, specs with only unsupported methods (HEAD, OPTIONS), or extremely large specs are not
- **Backend error propagation** — the `httpx.RequestError` catch in `place_order` is not tested
- **Audit log correctness** — no test verifies that audit_log entries are written with correct values
- **Seeding idempotency** — no test verifies that `seed_demo_data` is actually idempotent (it's supposed to skip if ≥3 services exist)

---

## 7. Docker/Deployment Assessment

### 7.1 Dockerfile
- Uses `python:3.12-slim` — appropriate
- Layer caching is reasonable (deps first, code second)
- Does NOT install `[wizard]` optional deps — `litellm`, `pyyaml`, and `jsonschema` won't be available in the Docker image. YAML spec parsing will fail.
- Does NOT install `[dev]` deps — expected, but means no testing in container

### 7.2 Docker Compose
- 4 containers with health checks — well structured
- Backend containers use the same image with different commands — efficient
- **Missing:** No volume mount for SQLite persistence — DB is ephemeral per container restart
- **Missing:** No `PASSPORT_SIGNING_SECRET` or `ISSUER_API_KEY` env vars — signing secret will be randomly generated each restart (breaking all existing JWTs)
- **Missing:** No wizard-related env vars (`OPENAI_API_KEY` for LiteLLM)

### 7.3 PyYAML Not in Main Dependencies
`pyyaml` is only in `[wizard]` optional deps, but it's imported at runtime in `spec_parser.py`. If someone installs just `pip install agentcafe` (without `[wizard]`), the import fails at spec parse time with a handled error — but this means the wizard's primary function (parsing YAML specs) won't work with a basic install.

---

## 8. "What's Real vs. Placeholder" Table Accuracy Check

| Claim in AGENT_CONTEXT.md | Actual Status | Accurate? |
|---|---|---|
| Menu format — LOCKED & REAL | Real — scopes and limits now consistent across design files and runtime | ✅ |
| Menu discovery — Real | Yes | ✅ |
| Proxy — Real | Yes | ✅ |
| Demo backends — Real mock data | Yes | ✅ |
| Passport validation — Real (behind flag) | Yes, fully functional in both modes | ✅ |
| Human authorization check — Real (behind flag) | Yes | ✅ |
| Rate limiting — Real | Yes | ✅ |
| Company Onboarding Wizard — Real | Real for the 6-step flow + post-publish management (pause/unpublish/resume/dashboard/logs) | ✅ |
| Audit log — Real | Real but not tamper-evident | ⚠️ Partially |
| DB encryption for backend creds — Not implemented | Correct — plaintext | ✅ (accurately reported) |

**Missing from table:** The table doesn't mention that LLM enrichment is untested/optional or that file upload/URL fetch for specs isn't implemented.

---

## 9. Priority-Ordered Gap List (for project completion)

### P0 — Blocks basic product viability
1. ~~**Fix scope mismatch in design file menu-entry.json files**~~ — ✅ FIXED
2. ~~**Standardize `cost.limits` format**~~ — ✅ FIXED
3. **Install `pyyaml` in main dependencies** (or at minimum in the Dockerfile) — YAML spec parsing is the wizard's core function

### P1 — Blocks real-world usage
4. **Passport issuance for wizard-published services** — without this, wizard-published services are unusable
5. ~~**Post-publish endpoints: pause, unpublish**~~ — ✅ FIXED (pause, resume, unpublish, dashboard, logs all implemented)
6. **Add CORS middleware** — required for any web frontend
7. **Fix Docker Compose**: add `pyyaml` to image, set `PASSPORT_SIGNING_SECRET` env var, add SQLite volume

### P2 — Gaps in stated design
8. **Implement x-agentcafe-* extension merging** in the AI enricher (presets parsed but never used)
9. **Expose confidence scores** in review/preview responses
10. **Add input sanitization** for path parameter resolution (injection risk)
11. **Implement spec file upload** (multipart) and URL fetch endpoints
12. **Make `ENRICHMENT_MODEL` env-configurable**

### P3 — Testing gaps
13. **Test wizard publish → order round-trip**
14. **Test company login endpoint**
15. **Test happy-path orders for lunch and home service backends**
16. **Test audit log correctness**
17. **Test backend error handling in place_order**

### P4 — Phase 4+ (already planned)
18. Backend credential encryption (AES-256)
19. Tamper-evident audit logging
20. RS256 key management
21. Schema migration system (replace `rm -f agentcafe.db`)
22. End-to-end agent demo
23. Wizard Dashboard UI

---

## 10. Conclusion

The project has genuine substance — the core proxy architecture, double validation, and wizard flow all work. The code quality is high and the test suite is meaningful. The main risks are:

1. ~~**The design files disagree with the implementation**~~ — ✅ FIXED (scopes and cost format now consistent)
2. **Wizard-published services are discoverable but unusable** without a way to issue passports with the right scopes
3. **The Docker image can't parse YAML specs** because `pyyaml` isn't installed in the main deps
4. ~~**Post-publish management doesn't exist**~~ — ✅ FIXED (pause, resume, unpublish, dashboard, logs implemented with 8 tests)

Items #1 and #4 are resolved. Fixing #2 and #3 would bring the project to a genuinely functional MVP. The remaining gaps (encryption, tamper-evident audit, RS256, dashboard UI) are correctly identified in the development plan as future phases.
