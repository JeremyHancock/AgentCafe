# AgentCafe End-to-End Test Plan

**Status:** Draft — Feb 28, 2026
**Scope:** Full-stack scenarios spanning company onboarding, agent lifecycle, security enforcement, and cross-cutting integration.

> Existing unit/integration tests (166) cover individual endpoints and modules.
> The demo agent (`python -m agentcafe.demo_agent`) covers a single happy-path lifecycle.
> This document defines **multi-step scenarios** that cross module boundaries and exercise the system as a real user would.

---

## 1. Company Onboarding (Wizard → Menu)

These scenarios test the company-side flow: register, upload spec, review, configure, publish, and verify the result appears on the agent-facing Menu.

### E2E-ONB-01: Happy path — paste spec, review, publish

| Step | Action | Expected |
|------|--------|----------|
| 1 | `POST /wizard/companies` — register new company | 200, `session_token` returned |
| 2 | `POST /wizard/specs/parse` — paste valid OpenAPI JSON | 200, `draft_id` + `candidate_menu` with actions and confidence scores |
| 3 | `PUT /wizard/drafts/{id}/review` — accept AI candidate as-is | 200 |
| 4 | `PUT /wizard/drafts/{id}/policy` — set backend URL + per-action policies | 200 |
| 5 | `GET /wizard/drafts/{id}/preview` — generate preview | 200, `final_menu_entry` has all actions with confidence dicts |
| 6 | `POST /wizard/drafts/{id}/publish` — publish | 200, `service_id` returned |
| 7 | `GET /cafe/menu` — browse as agent | Published service appears with correct name, actions, and `quarantine_until` set ~30 days out |

**Coverage gap:** No existing test goes from wizard publish → agent-visible Menu in a single flow.

### E2E-ONB-02: Upload spec file (multipart)

| Step | Action | Expected |
|------|--------|----------|
| 1 | Register company | 200 |
| 2 | `POST /wizard/specs/upload` — upload .json file (< 2 MB) | 200, same response shape as paste |
| 3 | Verify `candidate_menu.actions` match the uploaded spec's operations | Actions present |

### E2E-ONB-03: Fetch spec from URL

| Step | Action | Expected |
|------|--------|----------|
| 1 | Register company | 200 |
| 2 | `POST /wizard/specs/fetch` — provide URL to a valid spec | 200 (or error if URL unreachable in test env) |

### E2E-ONB-04: Review with partial edits preserves AI confidence

| Step | Action | Expected |
|------|--------|----------|
| 1 | Parse spec → get candidate with confidence scores | Confidence present on actions |
| 2 | Submit review editing only `description` (leave actions untouched) | 200 |
| 3 | Generate preview | `final_menu_entry` actions still have original confidence scores |

**Coverage gap:** `test_wizard_preview_includes_confidence` exists but doesn't test partial edit preservation.

### E2E-ONB-05: Review excluding actions

| Step | Action | Expected |
|------|--------|----------|
| 1 | Parse spec with 3+ operations | Candidate has 3+ actions |
| 2 | Submit review with `excluded_actions: ["action-2"]` | 200 |
| 3 | Preview | Only non-excluded actions appear in `final_menu_entry` |

### E2E-ONB-06: Publish duplicate service_id rejected

| Step | Action | Expected |
|------|--------|----------|
| 1 | Company A publishes service `my-api` | 200 |
| 2 | Company B tries to publish service `my-api` | 409 Conflict |

**Existing:** `test_wizard_publish_duplicate_service_id_rejected` covers this.

### E2E-ONB-07: Draft ownership isolation

| Step | Action | Expected |
|------|--------|----------|
| 1 | Company A creates a draft | Gets `draft_id` |
| 2 | Company B tries `PUT /wizard/drafts/{A's id}/review` | 403 |
| 3 | Company B tries `GET /wizard/drafts/{A's id}/preview` | 403 |

**Existing:** `test_wizard_draft_ownership_403` covers this.

---

## 2. Agent Lifecycle (Passport → Order → Audit)

These scenarios test the agent-side flow: discover services, authenticate, place orders, manage tokens.

### E2E-AGT-01: Full read → consent → write lifecycle

| Step | Action | Expected |
|------|--------|----------|
| 1 | `GET /cafe/menu` — browse | Services listed |
| 2 | `POST /passport/register` — get Tier-1 token | 200, JWT with `tier: read` |
| 3 | `POST /cafe/order` — read action with Tier-1 token | 200, result returned |
| 4 | `POST /consents/initiate` — request write access | 200, `consent_id` + `consent_url` |
| 5 | Human registers + approves consent | 200 |
| 6 | `POST /tokens/exchange` — get Tier-2 token | 200, `token` + `policy_limits` |
| 7 | `POST /cafe/order` — write action with Tier-2 token | 200 |
| 8 | `POST /tokens/refresh` — refresh Tier-2 token | 200, new token ≠ old |
| 9 | Verify audit log has entries for steps 3 + 7 with valid hash chain | Hash chain intact |

**Existing:** `test_full_consent_flow_end_to_end` + demo agent cover most of this. Missing: audit chain verification in same flow.

### E2E-AGT-02: Tier-1 token rejected for write action

| Step | Action | Expected |
|------|--------|----------|
| 1 | Register Tier-1 token | 200 |
| 2 | `POST /cafe/order` — write action with Tier-1 | 403, `authorization_required` |

**Existing:** `test_tier1_rejected_for_write_action` covers this.

### E2E-AGT-03: Token expiry and rejection

| Step | Action | Expected |
|------|--------|----------|
| 1 | Issue Tier-2 token with very short lifetime | Token received |
| 2 | Wait for expiry or mock time | — |
| 3 | `POST /cafe/order` with expired token | 401 or 403 |
| 4 | `POST /tokens/refresh` with expired token | 401 or 403 |

### E2E-AGT-04: Policy revocation kills active tokens

| Step | Action | Expected |
|------|--------|----------|
| 1 | Get Tier-2 token via consent flow | Token works |
| 2 | `POST /cafe/revoke` — revoke the policy | 200 |
| 3 | `POST /cafe/order` with revoked token | 403, `token_revoked` or `policy_revoked` |
| 4 | `POST /tokens/refresh` with revoked token | 403 |

**Existing:** `test_policy_revoked_after_token_issued` covers partial. Missing: explicit refresh rejection after revocation.

### E2E-AGT-05: Rate limiting (V2 429)

| Step | Action | Expected |
|------|--------|----------|
| 1 | Get Tier-1 token | 200 |
| 2 | Send N+1 orders exceeding rate limit | 429 with `retry_after_seconds`, `policy_id` |
| 3 | Wait `retry_after_seconds` | — |
| 4 | Send order again | 200 |

**Coverage gap:** No existing test verifies the full exceed → wait → succeed cycle.

---

## 3. Security Gates

### E2E-SEC-01: Quarantine forces Tier-2 on new services

| Step | Action | Expected |
|------|--------|----------|
| 1 | Company publishes new service via wizard | Service has `quarantine_until` ~30 days out |
| 2 | Agent registers Tier-1 token | 200 |
| 3 | Agent orders a **read** action on quarantined service | 403, `quarantine_human_auth_required` |
| 4 | Agent goes through full consent flow → Tier-2 | Token received |
| 5 | Agent orders same read action with Tier-2 | 200 |

**Existing:** `test_quarantine_forces_human_auth` covers steps 2-3. Missing: full cycle including wizard publish as the source of quarantine.

### E2E-SEC-02: Suspended service returns 503

| Step | Action | Expected |
|------|--------|----------|
| 1 | Admin suspends a service via `POST /cafe/services/{id}/suspend` | 200 |
| 2 | Agent orders any action on that service | 503, `service_suspended` |
| 3 | Service appears on Menu with `security_status.suspended_at` set | Field present |

**Existing:** `test_suspended_service_returns_503` + `test_suspend_endpoint_success` cover pieces.

### E2E-SEC-03: Identity verification (Gate 1b) — write without prior read

| Step | Action | Expected |
|------|--------|----------|
| 1 | Get Tier-2 token for a medium+ risk action | Token received |
| 2 | Attempt write order **without** prior read on same service | 403, identity verification failure |
| 3 | Place read order first | 200 |
| 4 | Re-attempt write order | 200 |

**Existing:** `test_write_without_prior_read_rejected` + `test_write_after_read_succeeds` cover this.

### E2E-SEC-04: Input injection blocked

| Step | Action | Expected |
|------|--------|----------|
| 1 | Send order with path traversal in `service_id` (`../../etc/passwd`) | 400, `invalid_input` |
| 2 | Send order with SQL injection in inputs | 400 |
| 3 | Send order with newline injection in inputs | 400 |
| 4 | Send order with safe but unusual characters | 200 (allowed) |

**Existing:** `test_path_traversal_blocked`, `test_query_injection_blocked`, `test_newline_injection_blocked`, `test_safe_path_param_allowed` cover this well.

### E2E-SEC-05: Audit chain tamper detection

| Step | Action | Expected |
|------|--------|----------|
| 1 | Place several orders (creating audit entries) | Entries created |
| 2 | Read audit log and verify hash chain | Every `entry_hash` = SHA-256(prev_hash + data), chain is valid |
| 3 | Tamper with one entry's `outcome` field | — |
| 4 | Re-verify hash chain | Tamper detected at the modified entry |

**Existing:** `test_audit_hash_chain_valid` + `test_audit_hash_chain_detects_tamper` cover this.

### E2E-SEC-06: Consent privacy — no enumeration

| Step | Action | Expected |
|------|--------|----------|
| 1 | Agent A initiates consent | Gets `consent_id` |
| 2 | Agent B tries to check status of Agent A's consent | 404 or 403 (no leakage) |
| 3 | No endpoint exists to list all consents | Verified by absence |

**Existing:** `test_no_consent_enumeration_endpoint` covers this.

---

## 4. Cross-Cutting Integration

These are the highest-value scenarios — they span the company wizard, agent lifecycle, and security in a single flow.

### E2E-INT-01: Company publishes → agent discovers → full order cycle

| Step | Action | Expected |
|------|--------|----------|
| 1 | New company registers via wizard | 200 |
| 2 | Parses spec, reviews, configures policy (human_auth=true on write) | Draft ready |
| 3 | Publishes service | Service live with quarantine |
| 4 | Agent browses Menu → finds new service | Service present |
| 5 | Agent registers Tier-1 | 200 |
| 6 | Agent attempts read on quarantined service with Tier-1 | 403 (quarantine) |
| 7 | Agent initiates consent → human approves → Tier-2 exchange | Token received |
| 8 | Agent places read order | 200 |
| 9 | Agent places write order | 200 |
| 10 | Verify audit log has both orders with valid hash chain | Chain valid |

**This is the single most important scenario. No existing test covers it.**

### E2E-INT-02: Company pauses service → agent gets rejected → company resumes

| Step | Action | Expected |
|------|--------|----------|
| 1 | Company publishes service | Live |
| 2 | Agent successfully places an order | 200 |
| 3 | Company pauses service via `POST /wizard/services/{id}/pause` | 200 |
| 4 | Agent orders again | 503 or appropriate error |
| 5 | Company resumes service | 200 |
| 6 | Agent orders again | 200 |

**Existing:** `test_pause_service` + `test_resume_paused_service` cover wizard-side. Missing: agent-side rejection during pause.

### E2E-INT-03: Company unpublishes → service removed from Menu

| Step | Action | Expected |
|------|--------|----------|
| 1 | Company publishes service | Appears on Menu |
| 2 | Company unpublishes via `DELETE /wizard/services/{id}` | 200 |
| 3 | Agent browses Menu | Service no longer listed |
| 4 | Agent orders on unpublished service | 404 or error |

### E2E-INT-04: Multiple companies, multiple agents, no cross-contamination

| Step | Action | Expected |
|------|--------|----------|
| 1 | Company A publishes service A | — |
| 2 | Company B publishes service B | — |
| 3 | Agent 1 gets consent for service A | — |
| 4 | Agent 1 uses service A token to order on service B | 403 (scope mismatch) |
| 5 | Agent 2 cannot see or use Agent 1's consent | 404 |

### E2E-INT-05: Risk-tier ceiling enforcement end-to-end

| Step | Action | Expected |
|------|--------|----------|
| 1 | Company publishes service with `x-agentcafe-risk-tier: high` on a write action | Risk tier persisted |
| 2 | Human approves consent requesting 1-hour lifetime | — |
| 3 | Token exchange | Token lifetime capped at 5 minutes (high-risk ceiling), not 1 hour |

---

## 5. Dashboard-Specific Scenarios

These validate the Next.js dashboard's interaction with the backend.

### E2E-DASH-01: Login flow

| Step | Action | Expected |
|------|--------|----------|
| 1 | Load `/login` page | 200, form renders |
| 2 | Submit wrong password | Error message displayed, no redirect |
| 3 | Submit correct credentials | Redirect to `/onboard`, token in localStorage |

### E2E-DASH-02: Registration flow

| Step | Action | Expected |
|------|--------|----------|
| 1 | Load `/register` page | 200, form renders |
| 2 | Submit with duplicate email | Error message displayed |
| 3 | Submit with valid data | Redirect to `/onboard`, token in localStorage |

### E2E-DASH-03: Auth guard — unauthenticated redirect

| Step | Action | Expected |
|------|--------|----------|
| 1 | Navigate to `/onboard` without token | Redirect to `/login` |
| 2 | Navigate to `/` without token | Redirect to `/login` |

### E2E-DASH-04: Full wizard flow in browser

| Step | Action | Expected |
|------|--------|----------|
| 1 | Login → land on spec input | Step indicator shows step 1 |
| 2 | Paste valid JSON → parse | Step advances to Review (step 2) |
| 3 | Review AI candidate → edit description → save | Step advances to Policy (step 3) |
| 4 | Enter backend URL → configure rate limits → save | Step advances to Preview (step 4) |
| 5 | Verify preview shows all data correctly | All fields present |
| 6 | Click publish | Success screen with quarantine notice |

---

## 6. Error Path Scenarios

### E2E-ERR-01: Invalid spec formats

| Input | Expected |
|-------|----------|
| Empty string | 400, parse error |
| Valid JSON but not OpenAPI | 400, missing `openapi` field |
| Swagger 2.0 | 400, unsupported version |
| YAML with syntax errors | 400, parse error |
| Spec > 2 MB (upload) | 413 or 400, size limit |
| Non-UTF-8 file (upload) | 400, encoding error |

**Existing:** Most covered by `test_wizard_parse_invalid_spec`, `test_parse_empty_spec_raises`, `test_parse_swagger_2_raises`.

### E2E-ERR-02: Auth failures

| Action | Expected |
|--------|----------|
| Wizard endpoints without `X-Company-Token` | 401 |
| Wizard endpoints with expired/invalid token | 401 |
| Order with garbage JWT | 401, `invalid_passport` |
| Order with valid JWT but wrong scope | 403, `insufficient_scope` |
| Consent initiate without Bearer token | 401 |

### E2E-ERR-03: Wizard state machine violations

| Action | Expected |
|--------|----------|
| Publish without preview | 400 |
| Preview without review | 400 (or uses AI candidate directly) |
| Review on non-existent draft | 404 |
| Double publish same draft | 409 or 400 |

---

## Priority for Implementation

When converting scenarios to automated tests:

| Priority | Scenarios | Rationale |
|----------|-----------|-----------|
| **P0** | E2E-INT-01 | Only scenario spanning wizard → Menu → agent → audit |
| **P0** | E2E-SEC-01 | Quarantine is a core security property |
| **P1** | E2E-ONB-01 | Happy path through entire wizard |
| **P1** | E2E-AGT-01 | Full agent lifecycle with audit verification |
| **P1** | E2E-INT-02, E2E-INT-03 | Service lifecycle management |
| **P2** | E2E-AGT-05 | Rate limit full cycle |
| **P2** | E2E-INT-04 | Multi-tenant isolation |
| **P2** | E2E-INT-05 | Risk-tier ceiling enforcement |
| **P3** | E2E-DASH-* | Browser-based tests (require Playwright or similar) |
| **P3** | E2E-ERR-* | Error paths (many already have unit test coverage) |

---

## Test Infrastructure Notes

- **Backend tests** (E2E-ONB, E2E-AGT, E2E-SEC, E2E-INT): Can use existing `pytest-asyncio` + `httpx.AsyncClient` pattern from `tests/`. Both the Cafe app and Wizard app need to be mounted together.
- **Dashboard tests** (E2E-DASH): Require browser automation (Playwright recommended). The Next.js dev server must be running alongside the FastAPI backend.
- **Demo agent**: `python -m agentcafe.demo_agent --headless` already covers E2E-AGT-01 minus audit verification. Could be extended or used as a smoke test.
- **CI integration**: Backend E2E tests should run in the same `pytest` suite. Dashboard tests should be a separate `npm test` step.
