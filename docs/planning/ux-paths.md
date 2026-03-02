# AgentCafe — UX Happy Paths

**Date:** March 2, 2026  
**Status:** Living document — tracks all user-facing flows and their current state  
**Purpose:** Ensure every user type has a complete, coherent experience. Identify gaps.

---

## Four User Types

| User | Who they are | What they want |
|------|-------------|----------------|
| **Agent** | An AI agent acting on behalf of a human | Discover services, read data freely, get human consent for writes, place orders |
| **Company** | A human onboarding their company's API to the Cafe | Register, upload spec, review, set policies, publish, manage live services |
| **Human** | The end-user whose agent acts on their behalf | Approve/decline agent consent requests, monitor active policies, revoke access |
| **Platform Admin** | The Cafe operator / self-hoster | Monitor platform health, suspend bad actors, verify keys, manage the Menu at a global level |

---

## 1. Agent Happy Paths

All agent paths are generic — they work against any service on the Menu, not a specific company's API.

### 1.1 Discover services

```
Agent → GET /cafe/menu → receives full Menu (all live services + actions)
```

- **Auth required:** None
- **What they see:** service_id, name, category, capability_tags, description, actions (with required_inputs, cost, security_status)
- **Status:** ✅ Implemented
- **UX gap:** No search, filter, or pagination. Agent must parse entire Menu client-side.

### 1.2 Read data (Tier-1, no human involved)

```
Agent → POST /passport/register {agent_tag} → receives Tier-1 JWT (3hr, read-only)
Agent → POST /cafe/order {service_id, action_id, passport, inputs} → receives data
```

- **Auth required:** Tier-1 token (self-issued, no human)
- **Scope:** Any action where `human_authorization_required = false`
- **Rate limit:** Per-action limit (e.g., 60/min), enforced per passport hash
- **Status:** ✅ Implemented
- **UX gap:** Agent doesn't know which actions need consent until it reads `cost.human_authorization_required` from the Menu. No pre-flight "will this need consent?" endpoint.

### 1.3 Write data (Tier-2, requires human consent)

```
Agent → POST /consents/initiate {service_id, action_ids, task_summary} → receives consent_id + consent_url
Agent → (sends consent_url to human via its own channel)
Agent → GET /consents/{id}/status → polls until status = "approved"
Agent → POST /tokens/exchange {consent_id} → receives Tier-2 JWT (short-lived, scoped)
Agent → POST /cafe/order {service_id, action_id, passport, inputs} → action executed
```

- **Auth required:** Tier-1 token to initiate, Tier-2 token to order
- **Scope:** Specific service + action(s) approved by the human
- **Token lifetime:** Capped by risk tier (low: 60m, medium: 15m, high: 5m, critical: single-use)
- **Status:** ✅ Implemented (multi-action consent supported)
- **UX gaps:**
  - Polling only — no webhook/SSE push to agent (callback_url exists but fires to a URL, not a push channel)
  - No long-poll option on `/consents/{id}/status`
  - Agent must manage its own channel to deliver `consent_url` to the human (SMS, email, chat, etc.)

### 1.4 Refresh a token

```
Agent → POST /tokens/refresh (Authorization: Bearer <tier-2-token>) → receives new Tier-2 JWT
```

- **Auth required:** Valid, non-expired Tier-2 token
- **Behavior:** Non-consuming (old token stays valid until expiry). New token has same scope + fresh expiry.
- **Status:** ✅ Implemented
- **UX gap:** None — straightforward.

### 1.5 Revoke own token

```
Agent → POST /cafe/revoke {passport: <token>} → token revoked
```

- **Auth required:** Proof of possession (valid token signature)
- **Status:** ✅ Implemented
- **UX gap:** None.

### 1.6 Handle errors

| Error | HTTP | Agent recovery |
|-------|------|----------------|
| Missing/invalid passport | 401 | Re-register or re-exchange |
| Scope insufficient | 403 `scope_missing` | Initiate consent for needed action |
| Human auth required | 403 `human_auth_required` | Initiate consent flow |
| Rate limited | 429 + `Retry-After` header | Wait and retry |
| Service suspended | 503 | Try alternative service or wait |
| Quarantined action | 403 (forces Tier-2) | Initiate consent even for reads |
| Missing inputs | 422 `missing_inputs` | Re-read Menu, include required fields |
| Invalid input type | 422 `invalid_input_type` | Fix input types per Menu schema |

- **Status:** ✅ All error paths implemented and tested
- **UX gap:** No formal error code taxonomy document. Agent must discover codes empirically or from source.

### Agent path summary

| Path | Friction | Status |
|------|----------|--------|
| Discover | Zero (no auth) | ✅ |
| Read | Low (self-register, 1 call) | ✅ |
| Write | Medium (consent flow, 4+ calls) | ✅ |
| Refresh | Zero (1 call) | ✅ |
| Revoke | Zero (1 call) | ✅ |
| Error recovery | Medium (must interpret codes) | ✅ (no formal docs) |

---

## 2. Company Happy Paths

### 2.1 Create account

```
Company → POST /wizard/companies {name, email, password} → receives company_id + session_token
```

- **Validation:** Name 1–200 chars, valid email, password min 8 chars
- **Password:** bcrypt hashed
- **Session:** HS256 JWT, 8hr expiry, `iss=agentcafe-wizard`
- **Status:** ✅ Implemented (API + Next.js dashboard)
- **UX gap:** No email verification. No password recovery.

### 2.2 Log in

```
Company → POST /wizard/companies/login {email, password} → receives session_token
```

- **Status:** ✅ Implemented (API + dashboard)

### 2.3 Onboard a service (the Wizard)

**Step 1: Upload/paste/fetch OpenAPI spec**
```
Company → POST /wizard/specs/parse {raw_spec}           → draft created, AI-enriched
      OR → POST /wizard/specs/upload (multipart file)   → same
      OR → POST /wizard/specs/fetch {url}               → same
```

- **Output:** draft_id, parsed spec, candidate Menu entry (AI-enriched)
- **AI enrichment:** LiteLLM (gpt-4o-mini) or rule-based fallback
- **Status:** ✅ All 3 input methods implemented

**Step 2: Review & edit**
```
Company → PUT /wizard/drafts/{id}/review {service_name, actions[...]}
```

- **What they edit:** Service name, action descriptions, input descriptions, example responses
- **Status:** ✅ Implemented (API + dashboard with pre-populated forms)

**Step 3: Set policies**
```
Company → PUT /wizard/drafts/{id}/policy {backend_url, backend_auth_header, actions[{scope, rate_limit, human_auth}]}
```

- **What they set per action:** scope name, rate limit, whether human auth is required, risk tier
- **Backend auth:** Encrypted at rest (AES-256-GCM)
- **Status:** ✅ Implemented

**Step 4: Preview**
```
Company → GET /wizard/drafts/{id}/preview → locked Menu entry format (what agents will see)
```

- **Status:** ✅ Implemented (includes confidence scores)

**Step 5 (optional): Dry run**
```
Company → POST /wizard/drafts/{id}/dry-run → tests backend reachability
```

- **Status:** ✅ Implemented

**Step 6: Publish**
```
Company → POST /wizard/drafts/{id}/publish → service goes live on Menu
```

- **Behavior:** Atomic insert to published_services + proxy_configs. 7-day quarantine on all actions.
- **Status:** ✅ Implemented

### 2.4 Manage live services

```
Company → GET  /wizard/services                     → list all their published services
Company → GET  /wizard/services/{id}/dashboard      → request counts, audit stats
Company → GET  /wizard/services/{id}/logs           → anonymized audit log entries
Company → PUT  /wizard/services/{id}/pause          → hide from Menu (reversible)
Company → PUT  /wizard/services/{id}/resume         → restore to Menu
Company → PUT  /wizard/services/{id}/unpublish      → permanently remove
Company → POST /wizard/services/{id}/edit           → create new draft from live config (re-enter wizard)
```

- **All require:** JWT session auth + ownership verification
- **Status:** ✅ All implemented
- **UX gaps:**
  - No notification when agents start using their service
  - No real-time usage metrics (only audit log counts)
  - No way to preview the consent text agents show to humans
  - No way to simulate a full order through the proxy (dry-run only checks backend reachability)

### Company path summary

| Path | Friction | Status |
|------|----------|--------|
| Create account | Low (name, email, password) | ✅ |
| Login | Low | ✅ |
| Onboard (6 steps) | Medium (guided wizard) | ✅ |
| Manage services | Low (dashboard) | ✅ |
| Edit & re-publish | Low (pre-populated draft) | ✅ |

---

## 3. Human Happy Paths

### 3.1 First-time consent (new account)

```
Human → (receives consent_url from their agent)
Human → GET /authorize/{consent_id} → redirected to /register (no account yet)
Human → GET /register → sees registration form
Human → POST /register {email, password} → account created, session cookie set, redirected back
Human → GET /authorize/{consent_id} → sees consent approval page
Human → POST /authorize/{consent_id}/approve {duration, csrf_token} → policy created, agent notified
```

- **What they see on the consent page:** service name, action description(s), risk tier badge, duration selector (bounded by risk-tier ceiling), Cafe-authored consent text
- **Status:** ✅ Implemented (server-rendered Jinja2 pages)
- **UX gaps:**
  - No email verification on registration
  - No password recovery
  - Consent text is Cafe-authored — human cannot see what specific inputs the agent plans to send
  - No "remember this agent" or auto-approve for repeat requests

### 3.2 Returning consent (existing account)

```
Human → GET /authorize/{consent_id} → redirected to /login
Human → POST /login {email, password} → session cookie set, redirected back
Human → GET /authorize/{consent_id} → consent page
Human → POST /authorize/{consent_id}/approve {duration, csrf_token} → approved
```

- **Status:** ✅ Implemented

### 3.3 Decline a consent request

```
Human → GET /authorize/{consent_id} → consent page
Human → POST /authorize/{consent_id}/decline {csrf_token} → declined, agent notified via callback
```

- **Status:** ✅ Implemented (POST with CSRF)

### 3.4 View active policies

```
Human → GET /dashboard → sees all active and revoked policies
```

- **What they see per policy:** service name, action IDs, risk tier, duration, active token count, created/expires timestamps
- **Status:** ✅ Implemented
- **UX gap:** No notification when an agent uses an approved policy. No activity log visible to the human.

### 3.5 Revoke a policy

```
Human → POST /dashboard/revoke/{policy_id} {csrf_token} → policy revoked, all tokens killed instantly
```

- **Behavior:** Sets `revoked_at` timestamp. All tokens issued under this policy are immediately invalid (checked on every order).
- **Status:** ✅ Implemented

### 3.6 Log out

```
Human → GET /logout → session cookie cleared, redirect to /login
```

- **Status:** ✅ Implemented

### Human path summary

| Path | Friction | Status |
|------|----------|--------|
| First-time consent (register + approve) | Medium (create account, review, approve) | ✅ |
| Returning consent (login + approve) | Low (login, review, approve) | ✅ |
| Decline consent | Low (one click + CSRF) | ✅ |
| View policies | Low (dashboard) | ✅ |
| Revoke policy | Low (one click) | ✅ |
| Logout | Low | ✅ |

---

## 4. Cross-Cutting UX Gaps (all user types)

| # | Gap | Affects | Severity | Notes |
|---|-----|---------|----------|-------|
| 1 | **No email verification** | Companies, Humans | Medium | Anyone can register with any email. No proof of ownership. |
| 2 | **No password recovery** | Companies, Humans | High | Locked out = start over. No forgot-password flow. |
| 3 | **No agent error code docs** | Agents | Medium | Agents must discover error codes empirically. Formal taxonomy needed. |
| 4 | **No Menu search/filter** | Agents | Medium (grows with scale) | Flat list only. Fine for 3 services, not for 100+. |
| 5 | **No consent delivery mechanism** | Agents, Humans | Medium | Agent must build its own channel (SMS, email, chat) to send consent_url to the human. Cafe provides the URL but not the delivery. |
| 6 | **No human activity notifications** | Humans | Low | Human approves and then has no visibility into what actually happens. |
| 7 | **No company usage notifications** | Companies | Low | Company publishes and gets silence. No "first agent used your service" alert. |
| 8 | **Consent text doesn't show agent inputs** | Humans | Medium | Human sees action description but not what the agent plans to send. `task_summary` is agent-authored (untrusted). |
| 9 | **No auto-approve / remember agent** | Humans | Low (grows with usage) | Every consent request requires manual approval, even repeats. |
| 10 | **Two separate account systems** | Companies, Humans | Low | A person who is both a company admin and a human user needs two accounts. |

---

## 5. Platform Admin Paths

The Cafe operator who manages the platform itself. Authenticates via API key, not a user account.

```
Admin → GET  /cafe/admin/overview (X-Api-Key header)     → full Menu + audit stats
Admin → POST /cafe/services/{id}/suspend (X-Api-Key)     → suspend a service (503 on all orders)
Admin → GET  /.well-known/jwks.json                      → public key verification
Admin → GET  /health                                     → health check
```

- **Status:** ✅ Implemented
- **UX gap:** No admin UI for these actions (API-only, or via Next.js `/admin` page for overview). No unsuspend endpoint (requires direct DB update or re-publish).

---

*This document tracks the current state. As paths are added or gaps are closed, update the relevant section.*
