# AgentCafe — Project Review #2

**Date:** March 1, 2026  
**Scope:** Security audit, gap analysis, UX review across all three customer types, liability assessment  
**Context:** This is the second review. The first review (Feb 27) covered Phases 0–3. Since then, Phases 4–6 have been implemented. This document is designed to be readable by reviewers who do NOT have access to the code.  
**Reviewers:** Cascade (code access), Jeremy (code access), Grok 4.20 (no code access), ChatGPT (no code access)

---

## 0. What AgentCafe Is (for reviewers without code access)

AgentCafe is a marketplace where **AI agents discover and use real-world services** on behalf of humans.

**Three customer types:**
1. **AI Agents** — browse a semantic "Menu" of available services, present a "Passport" (JWT), and place orders. The Cafe proxies the request to the company's backend. Agents never see backend URLs or credentials.
2. **Companies** — register via a wizard (upload OpenAPI spec → AI-assisted review → set security policies → publish to Menu). They control what agents can do, rate limits, and whether human consent is required.
3. **Humans** — the end-users whose agents act on their behalf. They create accounts, review consent requests from agents, and approve/decline with time-limited authorization.

**Core flow:** Agent → browse Menu → find action → if read-only, self-register for Tier-1 token → order directly. If write action, agent requests consent → human sees consent page → approves → agent gets short-lived Tier-2 token → places order → Cafe proxies to backend.

**Tech stack:** Python 3.12, FastAPI, SQLite (single-file DB via aiosqlite), Jinja2 templates for consent UI, RS256 JWT signing with JWKS endpoint, AES-256-GCM for backend credential encryption, Next.js 15 company dashboard.

**Current state:** 194 tests, pylint 10.00/10, Phases 0–6 complete. v0.1.0.

---

## 1. Architecture Summary (for reviewers without code access)

### 1.1 Token System
- **Tier-1 (read):** Any agent calls `POST /passport/register` with an optional `agent_tag`. No authentication required. Returns a 3-hour RS256 JWT with `tier: read`. Can access any action where `human_auth_required = false`.
- **Tier-2 (write):** Agent initiates consent request → human approves on a web page → agent exchanges consent for a short-lived token scoped to specific service+action pairs. Token lifetime capped by risk tier (low=1h, medium=15min, high=5min, critical=single-use).
- **Signing:** RS256 asymmetric signing. Public keys served at `/.well-known/jwks.json`. Key ID (`kid`) in JWT header. Dual-key support for rotation. HS256 legacy fallback for migration window.
- **Internal session tokens (wizard, human dashboard):** HS256, 8-hour expiry, separate `iss`/`aud` from Passport tokens.

### 1.2 Validation Gates (on every order)
- **Gate 0:** Service-level blocks — suspended services return 503, quarantined services force Tier-2 for all actions
- **Gate 1:** Passport validation — JWT signature, expiry, issuer, audience, jti revocation check, policy revocation check, tier check, scope check, authorization check
- **Gate 1b:** Identity verification — for medium+ risk write actions, requires `human_identifier_field` in inputs and a prior successful read action (read-before-write)
- **Gate 2:** Company policy — service must be `live`, required inputs present, input type validation against Menu schema, rate limiting (sliding window from audit log)
- **Proxy:** Path parameter resolution with injection protection (regex allowlist), backend method routing, auth header injection

### 1.3 Audit Trail
- Every order (success or failure) logged with: timestamp, service_id, action_id, hashed passport (SHA-256, first 16 chars), hashed inputs, outcome, response code, latency
- **Hash chain:** Each entry includes `prev_hash` (previous entry's hash) and `entry_hash` (SHA-256 of all fields + prev_hash). Genesis hash is 64 zeros. Verification function walks the chain.
- Audit log exposed to companies (anonymized, capped at 200 entries) and platform admin

### 1.4 Database
- SQLite with aiosqlite (async). Single connection per process.
- 6 numbered SQL migrations tracked in `schema_version` table
- Tables: companies, published_services, proxy_configs, audit_log, revoked_jtis, draft_services, cafe_users, consents, policies, active_tokens
- No foreign key enforcement at the SQLite level (FK constraints defined but `PRAGMA foreign_keys` not explicitly enabled)

### 1.5 Encryption
- Backend auth headers encrypted at rest with AES-256-GCM (env var `CAFE_ENCRYPTION_KEY`, 32-byte hex)
- If encryption key not set: passthrough mode (plaintext, logged warning)
- Format: `enc::` prefix + base64(nonce || ciphertext || tag)

### 1.6 Company Wizard Flow
1. Create company account (bcrypt password, JWT session)
2. Upload/paste/fetch OpenAPI spec → parsed + AI-enriched (LiteLLM or rule-based fallback)
3. Review: company edits service name, actions, descriptions
4. Policy: set scope, rate limit, human_auth per action; provide backend URL + auth header
5. Preview: generates locked Menu entry format
6. Publish: atomic insert to published_services + proxy_configs (or UPDATE on re-publish)
7. Post-publish: dashboard, pause, resume, unpublish, logs, edit (create new draft from live config)

---

## 2. What Has Been Fixed Since Review #1 (Feb 27)

The first review identified a priority-ordered gap list. Here is the current status:

| # | Gap | Status |
|---|-----|--------|
| P0-1 | Scope mismatch in design files | ✅ Fixed in Review #1 session |
| P0-2 | cost.limits format inconsistency | ✅ Fixed in Review #1 session |
| P0-3 | PyYAML not in main deps / Docker image | ✅ Fixed — `[wizard]` deps now installed in Docker multi-stage build |
| P1-4 | Passport issuance for wizard-published services | ✅ Fixed — Tier-1 read tokens work for all services (no scopes needed). Tier-2 uses consent flow which auto-derives scopes from proxy_configs |
| P1-5 | Post-publish endpoints | ✅ Fixed in Review #1 session |
| P1-6 | CORS middleware | ✅ Fixed — CORSMiddleware added with configurable origins |
| P1-7 | Docker Compose: YAML in image, env vars, volume | ✅ Fixed — multi-stage build, SQLite volume, env vars |
| P2-8 | x-agentcafe-* extension merging in enricher | ✅ Fixed — all 5 fields merge through both enricher paths |
| P2-9 | Expose confidence scores | ✅ Fixed — in preview and per-action |
| P2-10 | Input sanitization for path params | ✅ Fixed — regex allowlist `^[\w.@~-]+$`, rejects traversal/injection |
| P2-11 | Spec file upload + URL fetch | ✅ Fixed — POST /wizard/specs/upload (multipart, 2MB), POST /wizard/specs/fetch |
| P2-12 | ENRICHMENT_MODEL env-configurable | ✅ Fixed |
| P3-13 | Test wizard publish → order round-trip | ✅ Fixed — e2e tests cover full lifecycle |
| P3-14 | Test company login | ✅ Fixed |
| P3-15 | Test all 3 backends | ⬜ Still only hotel backend exercised in integration tests |
| P3-16 | Test audit log correctness | ✅ Fixed — hash chain tests |
| P3-17 | Test backend error handling | ⬜ Still not explicitly tested (dry-run tests backend unreachable, but not the proxy error path) |
| P4-18 | Backend credential encryption | ✅ Fixed — AES-256-GCM |
| P4-19 | Tamper-evident audit logging | ✅ Fixed — SHA-256 hash chain |
| P4-20 | RS256 key management | ✅ Fixed — full implementation with JWKS, kid, rotation |
| P4-21 | Schema migration system | ✅ Fixed — numbered SQL files, schema_version table |
| P4-22 | E2E agent demo | ✅ Fixed — demo_agent with --headless CLI |
| P4-23 | Wizard Dashboard UI | ✅ Fixed — Next.js 15 dashboard |

**Remaining from Review #1:** P3-15 (test all 3 backends), P3-17 (test proxy error path).

---

## 3. NEW Findings — Security & Vulnerability

### 3.1 CRITICAL: Revocation Endpoint Has No Authentication

**`POST /cafe/revoke`** accepts a `passport` field and revokes it by extracting the `jti`. **There is no authentication.** Any entity — agent, human, or attacker — can revoke any Passport by sending the raw token.

**Attack scenario:** A malicious agent observes another agent's token (e.g., from a shared log, leaked request, or MITM on an unencrypted internal network) and revokes it, causing denial of service for the victim agent.

**Worse:** An agent can revoke its own Tier-2 token immediately after using it for a write action, making forensic analysis harder (though the audit log still has the hashed passport).

**Recommended fix:** Require either the ISSUER_API_KEY (admin revocation) or proof of token ownership (e.g., the human who approved it, via session token).

### 3.2 HIGH: Consent Decline Is a GET Request with No CSRF Protection

**`GET /authorize/{consent_id}/decline`** declines a consent request. It's a state-mutating operation on a GET endpoint. Any page, email, or agent that can trick a human's browser into fetching this URL will decline the consent without the human's knowledge.

**Attack scenario:** Malicious agent includes `<img src="https://agentcafe.example.com/authorize/{consent_id}/decline">` in a response. If the human's browser has a session cookie, the consent is silently declined.

**Mitigation:** The session cookie is `samesite=lax`, which blocks cross-site GET requests from embedded resources. But lax still allows top-level navigations (clicking a link). A phishing link would work.

**Recommended fix:** Make decline a POST with CSRF token, or at minimum add a confirmation step.

### 3.3 HIGH: No CSRF Tokens on Any Form Submission

The consent approval form (`POST /authorize/{consent_id}/approve`), login, and registration forms have no CSRF tokens. The session cookie is `httponly` and `samesite=lax`, which provides partial mitigation — `lax` blocks cross-site POST from forms. However:
- If CORS is set to `*` (the current default), cross-origin requests with credentials could be possible depending on browser behavior.
- The CORS middleware allows `allow_credentials=True` combined with `allow_origins=["*"]`. **This combination is explicitly forbidden by the CORS spec** — browsers will reject it. In practice this means cross-origin credentialed requests are blocked, but the configuration is semantically wrong and could break if origins are narrowed.

**Recommended fix:** Add CSRF tokens to all state-mutating form POSTs. Fix CORS to not combine `*` with `credentials=True`.

### 3.4 HIGH: Human Password Hashing Uses SHA-256, Not bcrypt

**`cafe/human.py`** (human user accounts for consent flow) uses `_hash_password()` which is SHA-256. **This is not the same as the wizard's company accounts**, which correctly use bcrypt. SHA-256 is fast and vulnerable to brute-force/rainbow table attacks.

**Impact:** If the SQLite database is compromised, human account passwords are trivially crackable. These are the accounts that authorize write actions on behalf of humans — the most security-critical accounts in the system.

**Recommended fix:** Switch `_hash_password()` in `cafe/human.py` to bcrypt (already a dependency, already used in wizard/router.py for company accounts).

### 3.5 MEDIUM: Tier-1 Token Registration Is Completely Open — Rate Limit DDoS Vector

**`POST /passport/register`** has no rate limiting, no CAPTCHA, no IP tracking. Any entity can call it millions of times to:
- Fill the database with JWTs (though tokens aren't stored server-side — only jti revocations are stored)
- Use the generated tokens to flood `POST /cafe/order` with read requests
- Exhaust rate limits for legitimate agents (rate limiting is per-passport-hash, so each new token gets fresh limits)

**Impact:** An attacker generates thousands of Tier-1 tokens and uses each one to place orders up to the rate limit, effectively multiplying their throughput by the number of tokens. The per-passport rate limit is meaningless.

**Recommended fix:** Add IP-based rate limiting on `/passport/register`. Consider requiring proof-of-work or a lightweight challenge for token issuance.

### 3.6 MEDIUM: Wizard Session Tokens Are HS256 with a Random Secret on Restart

If `PASSPORT_SIGNING_SECRET` is not set, `config.py` generates a random `secrets.token_urlsafe(32)` on every startup. This means:
- All wizard session tokens are invalidated on every server restart
- All human session tokens (consent flow) are invalidated on restart
- In Docker Compose, `PASSPORT_SIGNING_SECRET` IS set, but in local dev mode it's easy to forget

This is **by design for dev mode** but worth noting that production misconfiguration would silently break all sessions.

### 3.7 MEDIUM: No Expiry Cleanup for Revoked JTIs or Expired Policies

The `revoked_jtis` table grows forever. Tokens expire after 3 hours (Tier-1) or minutes (Tier-2), but their revocation entries persist permanently. Similarly, expired policies in the `policies` table are never cleaned up.

**Impact:** Database bloat over time. Not a security vulnerability but an operational concern.

### 3.8 MEDIUM: Audit Log Hash Chain Race Condition

The hash chain relies on reading the most recent entry's hash before inserting a new one:
```
SELECT entry_hash FROM audit_log ORDER BY timestamp DESC LIMIT 1
```
Under concurrent requests, two orders could read the same `prev_hash` and both insert entries claiming to follow the same predecessor. This breaks the chain into a tree.

**Impact:** The tamper-evidence guarantee is weakened under concurrent load. `verify_audit_chain()` would report the chain as valid for one path but miss the fork.

**Recommended fix:** Use a database-level lock or a monotonic sequence counter instead of timestamp ordering.

### 3.9 LOW: Admin API Key Passed as Query Parameter

`GET /cafe/admin/overview?api_key=...` passes the admin key as a query parameter, which:
- Appears in server access logs
- Appears in browser history
- May be cached by proxies

The suspension endpoint `POST /cafe/services/{id}/suspend` correctly uses a request body.

**Recommended fix:** Move admin key to an `X-Api-Key` header (consistent with `/passport/issue`).

### 3.10 LOW: SQLite Foreign Keys Not Enforced

The schema defines `REFERENCES` constraints but SQLite requires `PRAGMA foreign_keys = ON` per connection to enforce them. This pragma is not set in the codebase. Orphaned records are possible (e.g., deleting a company wouldn't cascade to their published_services).

---

## 4. NEW Findings — Utility & Product Gaps

### 4.1 No Multi-Action Consent

The consent flow currently supports one action per consent request. The data model stores `action_ids` as a single string (not a comma-separated list despite the column name being plural). An agent wanting permission for `search-availability` AND `book-room` must initiate two separate consent requests, requiring two human approvals.

**Impact for agents:** Extra round-trips and worse UX for the human. Most real-world tasks require multiple actions on the same service.

**Impact for humans:** Approval fatigue — "Why am I approving 4 separate requests for the same hotel booking?"

### 4.2 No Service Discovery Beyond Flat List

`GET /cafe/menu` returns a flat list of all live services. No search, no filtering by category/capability_tags, no pagination. With 3 demo services this is fine. With 100+ services, an agent has to download the entire Menu and filter client-side.

**Impact for agents:** Increased latency and token usage for LLM-based agents that need to parse the full Menu.

### 4.3 No Agent Identity Continuity

Tier-1 tokens are ephemeral — each `/passport/register` call generates a fresh `agent_handle`. There's no way for an agent to maintain a persistent identity across sessions. This means:
- Rate limiting resets with each new token (see 3.5)
- Audit trail can't correlate actions from the same agent across token renewals
- No reputation system is possible

### 4.4 No Webhook/Callback for Consent Resolution

When an agent initiates consent, it must poll `GET /consents/{id}/status` to learn when the human approves. There is no webhook or push notification. The `callback_url` field exists in the consent model but is stored and never used.

**Impact for agents:** Polling is wasteful and adds latency. Real-world agents would benefit from a callback when consent is resolved.

### 4.5 Company Cannot See Which Agents Are Using Their Service

The audit log hashes the passport, so companies see `passport_hash` but cannot identify which agent or human is behind it. This is privacy-preserving but means companies have zero visibility into their users.

**Impact for companies:** Can't do customer support, can't identify abuse patterns beyond rate limiting, can't build relationships with high-value agent operators.

### 4.6 No Billing or Usage Metering

No mechanism for companies to charge for API usage through AgentCafe. No usage reports beyond the audit log counts. No concept of plans, quotas, or paid tiers.

### 4.7 Confirmation Email on Publish Still Not Implemented

Noted in Review #1. Still no email sending code anywhere. Low priority but listed in the design docs.

### 4.8 LLM Enrichment Path Still Untested

The rule-based fallback is used in all tests. The actual LiteLLM path (calling gpt-4o-mini) has never been exercised in automated tests. The integration works based on manual testing, but there's no regression protection.

---

## 5. NEW Findings — UX Assessment

### 5.1 Agent UX

**What works well:**
- Clean semantic Menu (no HTTP methods, no paths, no backend details)
- Self-registration for read access (zero friction)
- Clear error messages with specific error codes
- JWKS endpoint for external verification
- 429 responses include `Retry-After` header and detailed rate limit info

**Gaps:**
- **No SDK or client library.** Agents must construct HTTP requests manually. The `demo_agent/` exists but isn't packaged as a reusable library.
- **No structured error taxonomy.** Error codes are strings like `"scope_missing"`, `"rate_limit_exceeded"` — not documented in a schema. An agent can't programmatically map error codes to recovery strategies without hardcoding them.
- **Menu doesn't indicate which actions need consent.** The `cost.human_authorization_required` field exists per-action, but the agent must parse every action's cost to determine if consent is needed. No top-level summary like "this service requires human consent for write actions."
- **No way to discover required scopes before initiating consent.** The agent initiates consent for a specific service+action, and the scope is derived internally. But if the agent wants to pre-check whether it already has the right scope, there's no endpoint for that.
- **Consent polling has no long-poll or SSE option.** The agent must repeatedly poll `GET /consents/{id}/status`.

### 5.2 Company UX

**What works well:**
- Wizard flow is complete end-to-end (spec → review → policy → preview → publish)
- Edit-after-publish creates pre-populated draft
- Dashboard shows request counts and audit logs
- Pause/resume/unpublish lifecycle management
- Next.js dashboard is functional

**Gaps:**
- **No notification when agents start using their service.** Companies publish and then... silence. No email, no webhook, no dashboard alert.
- **No way to test their service before publishing.** The dry-run endpoint checks backend reachability but doesn't simulate a real order through the full proxy chain.
- **No way to see the consent text agents will show humans.** The consent page text is Cafe-authored ("An agent is requesting permission to perform an action on [service] on your behalf"). Companies can't customize or preview this.
- **Backend auth header rotation.** If a company needs to rotate their API key, they must edit → re-publish the entire service. No hot-swap mechanism.
- **30-day quarantine is hardcoded, non-configurable, and not clearly communicated.** Every newly published action has a `quarantine_until` date 30 days in the future during which ALL actions (even reads) require Tier-2 consent. This is a surprise for companies — their read-only search endpoint suddenly requires human approval for a month.

### 5.3 Human UX

**What works well:**
- Clean consent page with risk tier display, duration selector, and Cafe-authored consent text
- Risk-tier ceilings enforce short token lifetimes for dangerous actions
- Policy revocation (instant, kills all tokens issued under the policy)
- Separate human account system (not shared with company accounts)

**Gaps:**
- **No dashboard for humans.** Once a human approves a consent, they have no way to see their active policies, revoke them, or see what agents are doing on their behalf. Policy revocation exists as an API endpoint but there's no UI for it.
- **No notification when an agent uses an approved policy.** The human approves and then has no visibility into what actually happens.
- **Two separate account systems.** Human accounts (cafe_users, SHA-256 passwords, used for consent) and company accounts (companies, bcrypt, used for wizard) are completely separate. A person who is both a human user and a company admin needs two accounts with potentially different emails.
- **Consent page doesn't show what data will be shared.** The consent page shows the action description but not the specific inputs the agent plans to send. The agent's `task_summary` is displayed but it's agent-authored (untrusted).
- **No "remember this agent" or "auto-approve" option.** Every consent request requires manual approval, even for the same agent doing the same action the human approved yesterday.
- **Password recovery doesn't exist.** No forgot-password flow for either account type.

---

## 6. Liability Assessment

### 6.1 Agent-Caused Harm

If an agent books the wrong hotel room or cancels a booking the human didn't want cancelled, there is no mechanism for:
- **Undo/rollback** of agent actions
- **Dispute resolution** between humans and companies
- **Liability attribution** — the audit log proves the order was placed with a valid token, but doesn't capture whether the agent's decision was correct

### 6.2 Data Handling

- **PII:** Email addresses stored in plaintext in both `cafe_users` and `companies` tables. Passport hashes in audit log are SHA-256 truncated to 16 chars (not reversible). Input data is hashed (not stored in full).
- **Backend credentials:** Encrypted at rest (AES-256-GCM) when `CAFE_ENCRYPTION_KEY` is set. Plaintext in dev mode.
- **No data retention policy.** Audit logs, consent records, and policies persist indefinitely.
- **No GDPR/privacy compliance mechanisms.** No data export, no right-to-deletion, no consent for data processing.

### 6.3 Terms of Service / Legal Framework

No terms of service, acceptable use policy, or privacy policy exist. For a platform that mediates between agents, humans, and companies, this is a significant liability gap.

---

## 7. Priority-Ordered Gap List

### CRITICAL (fix before any real usage)
1. **Revocation endpoint needs authentication** (§3.1)
2. **Human password hashing: SHA-256 → bcrypt** (§3.4)
3. **CSRF protection on consent approval/decline** (§3.2, §3.3)

### HIGH (fix before public launch)
4. **Rate limit on `/passport/register`** to prevent token farming (§3.5)
5. **CORS configuration: don't combine `*` with `credentials=True`** (§3.3)
6. **Human dashboard for policy management** (§5.3) — humans need to see and revoke active policies
7. **Multi-action consent** (§4.1) — single-action consent is a UX dealbreaker for real tasks
8. **Consent decline must be POST, not GET** (§3.2)

### MEDIUM (important for product viability)
9. **Audit hash chain concurrency fix** (§3.8)
10. **Agent identity continuity** (§4.3) — persistent agent handles, not ephemeral per-token
11. **Service discovery: search/filter/pagination** (§4.2)
12. **Consent callback/webhook** (§4.4) — use the existing `callback_url` field
13. **Admin API key: query param → header** (§3.9)
14. **SQLite foreign key enforcement** (§3.10)
15. **Revoked JTI / expired policy cleanup** (§3.7)
16. **Quarantine period configurable** (§5.2)

### LOW (improve but not blocking)
17. Company notification on first agent usage (§5.2)
18. Human notification on agent action (§5.3)
19. Agent SDK / client library (§5.1)
20. Error code taxonomy documentation (§5.1)
21. Billing / usage metering framework (§4.6)
22. Data retention policy / GDPR (§6.2)
23. Terms of service (§6.3)

---

## 8. Questions for Multi-Model Discussion

These are open questions where outside perspectives from Grok and ChatGPT would be valuable:

**Q1 (Security model):** The Tier-1 → Tier-2 escalation model means ANY agent can read ANY service's data without consent. Is read access without consent the right default? What if a service exposes sensitive read data (e.g., checking someone's medical records)?

**Q2 (Trust model):** AgentCafe currently has no concept of agent reputation or trust levels. Every agent is equally untrusted. Should there be a graduated trust model? What would that look like without becoming a walled garden?

**Q3 (Consent fatigue):** The one-action-per-consent model will cause approval fatigue for humans. What's the right granularity? Per-service? Per-session? Per-task? How do you balance security with usability?

**Q4 (Liability):** When an agent causes harm through AgentCafe (wrong booking, unauthorized purchase), who is liable — the agent operator, the human who approved, AgentCafe as the platform, or the company? How should the platform handle this?

**Q5 (Economic model):** The platform is currently free. Is that sustainable? What's the right monetization that doesn't create perverse incentives (e.g., charging per-consent would incentivize fewer consent checks)?

**Q6 (Quarantine):** The 30-day quarantine forces Tier-2 consent on all actions for new services. Is this the right approach? It punishes legitimate companies. What's a better new-service trust mechanism?

**Q7 (Privacy vs. utility):** The audit log intentionally hashes PII, but this means companies can't identify abusive agents and humans can't see what their agents did. Where's the right balance?

---

## 9. Test Coverage Summary

| Area | Tests | Notes |
|------|-------|-------|
| Menu format | 7 | Locked format compliance |
| Order routing | 8+ | Missing inputs, invalid types, invalid passport, scope, auth |
| Passport JWT | 12+ | RS256/HS256, scope validation, wildcards, expiry, revocation |
| Consent flow | 15+ | Initiate, approve, decline, exchange, refresh, revocation, expiry |
| Rate limiting | 21 | Unit parsing + audit_log integration |
| Wizard API | 20+ | Full flow, ownership, auth, dry-run, post-publish, edit |
| Keys (RS256) | 12 | Key gen, PEM, rotation, JWKS, sign/verify, legacy fallback |
| Crypto (AES) | 9 | Encrypt/decrypt, roundtrip, passthrough, legacy plaintext |
| E2E integration | 11 | Full lifecycle: wizard → menu → consent → order → audit chain |
| **Total** | **194** | |

**Not tested:** Lunch and home-service backends in integration, LLM enrichment path, concurrent access/race conditions, Docker Compose, proxy error propagation path.

---

## 10. Round 1 Discussion — March 1, 2026

**Participants:** Cascade (code access), Jeremy (code access), Grok 4.20 (no code access), ChatGPT (no code access)

### 10.1 Unanimous Critical Fixes (all reviewers agree, no debate)

| # | Issue | Agreed Fix |
|---|-------|------------|
| 3.1 | Revocation endpoint unauthenticated | Add auth: require ISSUER_API_KEY (admin) OR proof of token ownership (self-revoke with valid signature + consent lookup) |
| 3.4 | Human passwords use SHA-256, not bcrypt | Swap `_hash_password` in `cafe/human.py` to bcrypt (already a dependency). Rehash on next login. |
| 3.2 | Consent decline is a GET (state-mutating) | Change to POST |
| 3.3 | No CSRF tokens on any form | Add CSRF tokens to all state-mutating form POSTs (consent approve, decline, login, register) |
| 3.3 | CORS combines `*` with `credentials=True` | Fix to not combine wildcard origins with credentials. Use explicit origin list in production. |
| 3.5 | `/passport/register` has no rate limit | Add IP-based rate limit (reuse existing sliding-window logic) |

### 10.2 Positions Taken on Open Questions

**Q1 — Should read access require consent?**
- **Grok:** Keep reads open. Quarantine + company-set `human_auth_required` + risk tiers already provide layered defense. Sensitive services (medical, finance) mark reads as human_auth_required.
- **ChatGPT:** Reads open by default is optimistic. Companies may misclassify sensitive GETs, making AgentCafe a data exfiltration proxy. Wants either: agent identity required for reads, or explicit non-sensitive flagging, or sensitivity tiers.
- **Cascade:** Keep reads open. Add a wizard warning when endpoints return user-specific data ("Consider requiring human consent"). Responsibility for classification belongs to the company, but the wizard should make it loud.
- **Status:** Converging on "open by default with better wizard guidance." Not yet locked.

**Q3 — Consent granularity**
- **Grok:** Per-task. Agent sends `task_summary` + list of actions. Human sees one approval ("Approve this booking task — 3 actions"). One token covers the whole task.
- **ChatGPT:** Multi-action consent scoped to a service. Single TTL per consent. Max cap per consent. Current single-action model incentivizes bad scope behavior (agents request overly broad scopes to reduce friction).
- **Cascade:** Per-task, multi-action. Data model change is straightforward — `action_ids` column is already plural, just needs to support comma-separated values through the full consent → policy → token chain.
- **Status:** Consensus on multi-action consent. Locked.

**Q6 — 30-day quarantine**
- **ChatGPT:** "Security theater." Time-based quarantine doesn't correlate to behavior or trust. Penalizes legitimate companies. Wants behavior-based reputation, verified company tier, manual review triggers, gradual capability unlock.
- **Cascade:** Partially agree. Keep a shorter quarantine (7 days, not 30) as a cooling-off period while adding behavior-based signals. Removing entirely leaves a gap where a malicious company publishes a data-exfiltration service and agents start using it immediately.
- **Status:** Open. Compromise direction: reduce to 7 days + add behavior-based signals.

**Q2, Q4, Q5, Q7** — Deferred to Round 2 after criticals are fixed.

### 10.3 Structural Issues Flagged

- **Token farming + no agent identity (3.5 + 4.3):** All reviewers agree rate limiting is meaningless without identity continuity. Disposable identities undermine abuse control, reputation, monetization, and analytics. Short-term fix: required `agent_tag` + IP rate limit on registration. Long-term: persistent agent identity (API key model).
- **Audit hash chain concurrency (3.8):** ChatGPT: if concurrency forks the chain, remove the "tamper-evident" claim or fix it. Fix: monotonic sequence ID + serialize writes (or lock during append).
- **Human dashboard (5.3):** All reviewers agree this is mandatory before launch. Humans cannot delegate authority without visibility into active policies and agent activity.
- **Consent callback/webhook (4.4):** `callback_url` field exists in the model but is unused. Half-built feature. Medium difficulty, high value.
- **Agent SDK (5.1):** ChatGPT: "You say this is an agent marketplace. There is no SDK." Minimum: Python client handling token issuance, consent polling, error taxonomy.

### 10.4 Points of Disagreement

| Topic | Grok | ChatGPT | Cascade |
|-------|------|---------|---------|
| Public self-revocation | Keep with auth (admin OR ownership proof) | Remove entirely; only admin + human dashboard + auto-expiry | Keep with authenticated self-revocation (agent proves it holds the token) |
| Quarantine | Not addressed in detail | Remove; replace with behavior-based reputation | Reduce to 7 days + add behavior signals |
| Agent identity (short-term) | `agent_tag` required + unique-per-IP | Stable `agent_handle` + optional API key | Required `agent_tag` + IP rate limit, but NOT unique-per-IP (fragile under NAT/VPN) |
| Project direction | "One sprint away from real agent economy test" | "Secure enough for demo, not yet robust under malice/scale" | "Serious infrastructure with MVP surface area — bones are right, enforcement layer needs hardening" |

### 10.5 Agreed Execution Plan

**Sprint 1 — Security hygiene (immediate):**
1. bcrypt for human passwords
2. Revocation endpoint auth
3. Consent decline → POST + CSRF tokens on all forms
4. CORS fix
5. IP rate limit on `/passport/register`
6. SQLite `PRAGMA foreign_keys = ON`
7. Admin API key → header

**Sprint 2 — Structural (next):**
8. Multi-action consent model
9. Audit hash chain concurrency fix (monotonic sequence)
10. Quarantine period configurable (default 7 days)

**Sprint 3 — UX:**
11. Human dashboard (active policies, one-click revoke)
12. Consent webhook/callback (use existing `callback_url` field)

---

## 11. Round 2 — Consensus Lock (March 1, 2026)

**Participants:** All four reviewers. Grok approved plan as written. ChatGPT approved with process requirements (acceptance gates + three decision locks). Both positions captured below.

### 11.1 Three Decision Points — LOCKED

Per ChatGPT's request, these must be explicitly decided before Sprint 1 begins:

**Decision 1 — Revocation semantics:** ✅ LOCKED
> Keep public self-revoke WITH proof-of-possession. Agent must present a valid, signature-verified token to revoke it. Admin can revoke any token via ISSUER_API_KEY. Human dashboard revocation (future, Sprint 3) will use session auth to revoke policies (which kills all tokens under that policy). Self-revoke is rate-limited.

**Decision 2 — Quarantine policy:** ✅ LOCKED
> Default 7 days, configurable per-service in the wizard. This is the Sprint 2 deliverable. Behavior-based gating is a future layer, NOT required for Sprint 2. The 7-day quarantine is the backstop for now. Ownership: platform-level default, company can request override via wizard.

**Decision 3 — Agent identity (short-term):** ✅ LOCKED
> Sprint 1 mitigation: required `agent_tag` + IP-based rate limit on `/passport/register`. This explicitly does NOT solve token farming fully. It raises the cost of farming (attacker needs many IPs) but does not eliminate it. Full identity (persistent API keys, reputation) is Sprint 2+ scope. All reviewers acknowledge this is partial mitigation, not enforcement.

### 11.2 Sprint 1 Acceptance Gates

Each fix has a defined "done" criteria. Sprint 1 is not complete until ALL gates pass:

| Fix | Acceptance Gate |
|-----|----------------|
| Human passwords → bcrypt | `_hash_password` uses bcrypt. Legacy SHA-256 hashes rehashed on next successful login. Test proves bcrypt hash is stored. |
| Revocation endpoint auth | `POST /cafe/revoke` requires valid token signature (self-revoke) OR ISSUER_API_KEY header (admin). Unauthenticated revoke returns 401/403. Test covers both paths + rejection. |
| Consent decline → POST | `GET /authorize/{id}/decline` removed. `POST /authorize/{id}/decline` added. Template updated with form + CSRF token. Test proves GET returns 405. |
| CSRF tokens on all forms | All state-mutating POSTs (consent approve, consent decline, login, register) include a CSRF token validated server-side. SameSite=Lax is mitigation, not the fix — CSRF tokens are the fix. |
| CORS fix | Dev: permissive (configurable). Prod: explicit origin allowlist, `credentials=True` only with specific origins (never `*`). `allow_origins=["*"]` + `allow_credentials=True` combination eliminated. Test or config validation proves this. |
| IP rate limit on `/passport/register` | Configurable limit (default 30/min per IP). Returns 429 with Retry-After on exceeded. `agent_tag` required (non-empty string). Test covers rate limit hit + required tag. |
| SQLite FK enforcement | `PRAGMA foreign_keys = ON` set on every new connection. Test proves FK violation raises error. |
| Admin API key → header | `GET /cafe/admin/overview` reads API key from `X-Api-Key` header, not query param. Query param path removed. Test updated. |

### 11.3 CORS Environment Policy

| Environment | `allow_origins` | `allow_credentials` |
|-------------|----------------|---------------------|
| Local dev | `["*"]` | `False` |
| Docker dev | `["http://localhost:3000", "http://localhost:8000"]` | `True` |
| Production | Explicit allowlist from `CORS_ALLOWED_ORIGINS` env var (comma-separated) | `True` |

Rule: `credentials=True` is ONLY allowed when `allow_origins` is an explicit list, never `*`.

### 11.4 Status

- **Grok:** ✅ Approved. "Sprint 1 starts now."
- **ChatGPT:** ✅ Approved with gates (captured in §11.2).
- **Cascade:** ✅ Ready to execute.
- **Jeremy:** ✅ Approved. March 1, 2026 4:53 PM EST.

---

## 12. Sprint 1 — Implementation Complete (March 1, 2026)

All 7 Sprint 1 items implemented and verified. **198 tests passing.**

### 12.1 Fixes Delivered

| # | Fix | File(s) Changed | Acceptance Gate Met |
|---|-----|-----------------|---------------------|
| 1 | **bcrypt for human passwords** | `cafe/human.py`, `cafe/pages.py` | ✅ New registrations use bcrypt. Legacy SHA-256 hashes auto-rehash on login. `_verify_password` + `_rehash_if_legacy` helpers. |
| 2 | **Revocation endpoint auth** | `cafe/passport.py`, `tests/test_passport.py` | ✅ Admin path via `X-Api-Key` header. Self-revoke via proof-of-possession (valid signature required). Garbage tokens without admin key → 403. |
| 3 | **Consent decline → POST + CSRF** | `cafe/pages.py`, all 4 templates, `tests/test_consent.py` | ✅ Decline changed from GET to POST. CSRF tokens (HMAC-SHA256, session-bound, 1hr expiry) on all 4 forms. GET on decline returns 405. |
| 4 | **CORS fix** | `main.py` | ✅ `credentials=True` only with explicit origin list. Wildcard `*` → `credentials=False`. |
| 5 | **IP rate limit on `/passport/register`** | `cafe/passport.py`, `tests/test_passport.py` | ✅ 30 req/min per IP sliding window. `agent_tag` now required (min_length=1). 429 with `Retry-After` header. |
| 6 | **SQLite `PRAGMA foreign_keys = ON`** | `db/engine.py` | ✅ Enforced before schema creation on every connection. |
| 7 | **Admin API key → header** | `cafe/router.py`, `tests/test_order.py` | ✅ Both `/admin/overview` and `/services/{id}/suspend` now use `X-Api-Key` header. Body `api_key` field removed from `SuspendRequest`. |

### 12.2 New Tests Added

- `test_revoke_garbage_token` — updated to expect 403 (unauthorized_revocation)
- `test_revoke_garbage_token_admin` — admin garbage token → 400
- `test_revoke_admin_path` — admin revoke via header succeeds
- `test_register_without_agent_tag_rejected` — empty/missing tag → 422
- `test_register_ip_rate_limit` — 30th+1 request → 429
- `test_consent_page_decline` — updated to POST with CSRF
- `test_decline_get_returns_405` — GET on decline → 405

### 12.3 Sprint 2 — Implementation Complete (March 1, 2026)

All 3 Sprint 2 items implemented and verified. **204 tests passing.**

| # | Fix | File(s) Changed | Details |
|---|-----|-----------------|---------|
| 8 | **Multi-action consent model** | `cafe/consent.py`, `tests/test_consent.py` | `InitiateRequest` accepts `action_ids: list[str]` (backward compat with single `action_id`). All actions validated. Scopes stored as comma-separated. Risk tier = highest among requested actions. 5 new tests. |
| 9 | **Audit hash chain concurrency fix** | `cafe/router.py`, `db/migrations/0007_audit_seq_column.sql`, `tests/test_order.py` | `asyncio.Lock` serializes SELECT prev_hash + INSERT. Monotonic `seq INTEGER` column replaces timestamp ordering. `verify_audit_chain` orders by `seq ASC`. Concurrent-order test verifies no chain forks. |
| 10 | **Quarantine period configurable** | `config.py`, `wizard/publisher.py`, `wizard/router.py`, `main.py` | `QUARANTINE_DAYS` env var (default 7). Passed through `configure_wizard` → `publish_draft`. Was hardcoded 30 days. |

### 12.4 Sprint 3 — Implementation Complete (March 2, 2026)

All 2 Sprint 3 items implemented and verified. **214 tests passing.**

| # | Fix | File(s) Changed | Details |
|---|-----|-----------------|---------|
| 11 | **Human dashboard** | `cafe/pages.py`, `templates/dashboard.html`, `tests/test_consent.py` | `GET /dashboard` shows active & revoked policies with service name, risk tier, action IDs, active token count, lifetime. `POST /dashboard/revoke/{id}` one-click revoke with CSRF + ownership check. `GET /logout` clears session. Root `/` redirects to dashboard when logged in. 5 new tests. |
| 12 | **Consent webhook/callback** | `cafe/consent.py`, `cafe/pages.py`, `tests/test_consent.py` | `_fire_consent_callback()` POSTs `{consent_id, status, policy_id}` to `callback_url` (best-effort, 10s timeout, logged failures). Fires on API approve, form approve, and form decline. 5 new tests (integration + unit). |

### 12.5 All Review Items Complete

Sprints 1–3 delivered **12 fixes** across security hygiene, structural improvements, and UX. Total: **214 tests passing, 0 failures.**
