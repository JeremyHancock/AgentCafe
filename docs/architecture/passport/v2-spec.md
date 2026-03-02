# Passport V2 — Specification

**Version:** 1.0  
**Date:** February 27, 2026  
**Status:** **LOCKED** — 9 converged positions (Feb 27 three-way review). Implementation may begin.  
**Authors:** Jeremy (project lead), Claude (advisor), Grok (beneficial adversary)  
**Supersedes:** Passport V1 (POA model). V2 extends Phase 2 implementation.  
**ADRs:** ADR-023 (Menu schema extension), ADR-024 (bearer authorization model)

---

## 1. Overview

The Passport is a **human-issued bearer authorization for autonomous agents.** It is the human's document, not the agent's. The agent is the bearer, not the subject.

The Cafe is the sole trusted issuer and consent broker. No third-party issuers, no company-run issuers, no self-issued write Passports.

Agent identity is intentionally out of scope. Agents are ephemeral, copyable, and non-verifiable. The system works regardless of whether agents develop stable identities in the future.

---

## 2. Definitions

| Term | Definition |
|------|-----------|
| **Passport** | A JWT issued by the Cafe authorizing the bearer to perform specific actions. |
| **Tier-1 Passport** | Read-only. Agent self-requests. No human involvement. Rate-limited. |
| **Tier-2 Passport** | Write-scope. Requires human consent via Cafe-owned flow. Short-lived token under a long-lived policy. |
| **Policy** | Server-side record of a human's consent. Long-lived (30–90 days). Never leaves the Cafe. Contains allowed actions, scopes, constraints, expiry. |
| **Token** | Short-lived JWT issued under a policy. The agent holds this. Expiry governed by risk-tier ceilings. |
| **consent_id** | UUIDv4 claim ticket returned by `POST /consents/initiate`. Durable — the agent platform must persist it. |
| **agent_tag** | Self-reported, untrusted agent label. Audit trail only. Not security enforcement. |
| **risk_tier** | Per-action classification: `low`, `medium`, `high`, `critical`. Determines token lifetime ceiling and verification depth. |

---

## 3. Tiered Model

### 3.1 Tier-1: Read-Only Passport

- Agent calls `POST /passport/register` with optional `agent_tag`.
- Cafe returns a Tier-1 JWT (`tier: "read"`, `granted_by: "self"`).
- Allows: Menu browsing, service discovery, read-only action endpoints.
- Rate-limited per token. Tracked via hashed `agent_tag` + `jti`.
- No human involvement. No passkey. No consent flow.
- Companies may override: `require_full_passport: true` on sensitive read endpoints.

### 3.2 Tier-2: Write-Scope Passport

- Requires human consent via Cafe-owned flow (§4).
- Human must have a Cafe account (password-based for now; passkey planned for Phase 7).
- Token is short-lived; lifetime governed by risk-tier ceilings (§6.2).
- Scoped to specific actions, services, and human-set constraints.

---

## 4. Consent Flow

### 4.1 Step-by-Step

**Step 1 — Agent requests a Tier-1 Passport.**  
`POST /passport/register` → Tier-1 JWT.

**Step 2 — Agent browses the Menu.**  
`GET /cafe/menu` returns actions with `risk_tier`, `human_identifier_field`, `constraints_schema`, `account_linking_required`, `self_only`, `cost.limits.rate_limit_scope`, and `concurrency_guidance`.

**Step 3 — Agent initiates consent.**  
`POST /consents/initiate` (Bearer: Tier-1 token).

Request body:
```json
{
  "service_id": "stayright-hotels",
  "action_id": "book-room",
  "requested_constraints": { "max_night_rate": 500 },
  "task_summary": "Book a hotel in Paris for March 1-3",
  "callback_url": "https://agent-platform.example/webhooks/consent"
}
```

- `task_summary` is untrusted — Cafe uses it only as a hint for consent text authoring.
- `callback_url` is optional (webhook pattern, §5.3).

Response:
```json
{
  "consent_id": "uuid-v4",
  "consent_url": "https://cafe.example/consent/abc123",
  "activation_code": "CAFE-1234",
  "expires_at": "2026-03-02T21:00:00Z",
  "status": "pending"
}
```

- `activation_code` provided for new users (combined signup + approval).
- `expires_at` defaults to 72 hours. Agent may request up to 7 days via `ttl_hours` parameter.

**Step 4 — Human approves.**  
Human opens `consent_url` or enters `activation_code`. Cafe-branded page shows:
- Cafe-authored plain-language description of what's being authorized.
- Specific actions and scopes.
- Constraint controls (rendered from `constraints_schema`).
- Duration selector (within risk-tier ceiling).
- Passkey confirmation (required for all Tier-2 approvals).

On approval, Cafe writes a **policy record**:
```
policy_id, cafe_user_id, service_id, allowed_action_ids,
limits, constraints, expiry, revoked_at (NULL),
audit: { timestamp, device_info, consent_text_version }
```

**Step 5 — Agent receives a token.**  
Agent calls `POST /tokens/exchange` with `consent_id` + Tier-1 token.

Response:
```json
{
  "token": "eyJ...",
  "expires_at": "2026-02-27T21:15:00Z",
  "policy_id": "policy-uuid",
  "tier": "write",
  "scopes": ["stayright-hotels:book-room"]
}
```

**Step 6 — Agent executes.**  
Agent calls order endpoint with Tier-2 token. Cafe validates token → looks up policy → enforces scopes + constraints + identity verification (§7) → proxies to backend → records audit event.

### 4.2 Consent Privacy

- No consent discovery endpoint. No `GET /consents?agent_tag=...`.
- Human dashboard shows "Pending approvals" via `GET /human/dashboard/pending-consents` (filtered server-side by `cafe_user_id`).
- Agent Passports (`aud: "agentcafe"`) are rejected by human-session middleware (`aud: "human-dashboard"`). Strict JWT audience separation.

---

## 5. Consent Lifecycle

The `consent_id` is a **durable claim ticket**. The agent platform is 100% responsible for persisting it. The Cafe does not store, query, or recover consent IDs on behalf of agents.

### 5.1 Primary Pattern: Pickup

- `consent_id` is UUIDv4 (high-entropy, not guessable).
- Agent stores it in persistent state (conversation memory, task queue, database).
- Any agent instance — original or new — calls `POST /tokens/exchange` with the `consent_id` when ready.
- **TTL default: 72 hours.** Configurable per consent (max 7 days). Expired consents return `410 Gone`.

### 5.2 Secondary Pattern: Polling

- Agent polls `GET /consents/<consent_id>/status`.
- Mandatory exponential backoff: min 30-second interval after the first 60 seconds.
- Suitable for delays under 30 minutes. Not recommended for longer waits.

### 5.3 Secondary Pattern: Webhook

- Agent provides `callback_url` in `POST /consents/initiate`.
- Cafe posts once on approval: `POST <callback_url>` with `{ "consent_id": "...", "status": "approved" }`.
- Idempotent delivery. Agent must validate HTTPS. Authentication via secret in URL query param.
- Designed for persistent agent platforms (LangGraph, CrewAI, etc.) with ephemeral agent processes.

### 5.4 Lost consent_id

If the agent loses the `consent_id`, the link is broken. By design — the Cafe cannot safely determine which agent "should" receive a token. Recovery: human sees pending approval on dashboard and re-initiates, or agent system re-requests consent.

---

## 6. Token Lifecycle

### 6.1 Token Types

| Property | Tier-1 (Read) | Tier-2 (Write) |
|----------|--------------|----------------|
| Issued by | Agent self-request | Consent exchange |
| Human involvement | None | Required (passkey) |
| Scopes | Read-only | Action-specific write |
| Lifetime | Long (hours) | Short (risk-tier ceiling) |
| Refresh | N/A | Non-consuming (`POST /tokens/refresh`) |
| Revocation | Per-token rate limiting | Instant via policy `revoked_at` |

### 6.2 Token Expiry Ceilings

Human chooses token lifetime within Cafe-enforced ceilings per risk tier. Human can go shorter freely; longer requires step-up auth.

| Risk Tier | Ceiling | Default | Examples |
|-----------|---------|---------|----------|
| Low | 60 min | 30 min | Search with side effects, save preferences |
| Medium | 15 min | 10 min | Book a room, place an order |
| High | 5 min | Single-use | Cancel reservation, financial transaction |
| Critical | Single-use only | Single-use | Delete account, large purchase |

### 6.3 Token Refresh

- `POST /tokens/refresh` with a valid Tier-2 token.
- Returns a new token under the same policy. **Non-consuming** — the old token is not invalidated; it dies at expiry.
- Allows multiple agents to independently hold and refresh tokens under the same policy.

### 6.4 Concurrent Token Cap

- **Hard global ceiling: 20 active tokens per policy.** Cafe-enforced regardless of Menu `concurrency_guidance`.
- Exceeding the cap returns `429 Policy Token Limit Reached`.
- The orchestrator must wait for older tokens to expire before requesting more.

### 6.5 Policy Revocation

- Human revokes a policy → Cafe sets `policy.revoked_at = NOW()`.
- Any token whose `iat < policy.revoked_at` is rejected on next presentation.
- **Instant revocation for all risk tiers.** No per-`jti` blocklist. No extra DB table.
- Single-use tokens are already self-invalidating; the `revoked_at` check simply accelerates the rest.
- The policy row is already fetched during token validation; the check is one additional column read.

---

## 7. Identity Verification

The Cafe enforces human-scoping by inspecting data flowing through it, without requiring backend changes or sending human identity to backends.

### 7.1 Verification by Risk Tier

| Risk Tier | Verification | Rationale |
|-----------|-------------|-----------|
| Low | Agent-supplied identifier match only | Fast reject on mismatch. Minimal latency. |
| Medium | Agent-supplied match + Cafe read-before-write | Belt and suspenders. Extra read ~200ms. |
| High / Critical | Cafe read-before-write mandatory. No shortcut. | Maximum safety for destructive operations. |

### 7.2 Mechanics

**For writes on existing resources (cancel, modify):**
1. Agent requests action on resource (e.g., reservation #ABC123).
2. Cafe forces a read: `GET /reservations/ABC123` → response includes `customer_email`.
3. Cafe checks: does the `human_identifier_field` value match the Passport's human? Yes → forward. No → reject.

**For writes that create new resources (book, order):**
- Cafe verifies the agent-supplied `human_identifier_field` in the request body matches the Passport's human.

### 7.3 Onboarding

- Companies tag identity fields during wizard onboarding.
- AI enricher infers from field names (`customer_email`, `user_id`, `guest_name`).
- Company confirms or corrects.
- **Requirement:** At least one strong identifier (email or phone) per destructive write endpoint. Name matching is supplementary, never sole.

### 7.4 MVP Scope

- `self_only` is the only valid resource constraint for MVP.
- `on_behalf_of` scenarios deferred past MVP.
- Account linking friction acknowledged; MVP proceeds with per-service linking.

---

## 8. Rate Limits

### 8.1 Scope

Rate limits are **per-policy, not per-token.** If Alice's policy allows 60 requests/minute, that's 60 total across all agents using tokens under that policy.

Rate-limit windows are sliding. Logged per `policy_id` in `audit_log` for human dashboard visibility.

### 8.2 Communication Principle

Agents must never learn per-policy semantics through trial-and-error. The Cafe communicates at every touchpoint:

| Touchpoint | Mechanism | MVP? |
|-----------|-----------|------|
| Discovery (Menu) | `cost.limits.rate_limit_scope: "per_policy"` | Yes |
| Enforcement (429) | Machine-readable error with `retry_after_seconds` and `policy_id` | Yes |
| Issuance (token response) | Optional `policy_limits` snapshot | No |
| Documentation | Developer guide | No |

### 8.3 429 Response Format

```json
{
  "error": "rate_limit_exceeded",
  "detail": "Policy rate limit reached (60/minute per policy). This limit is shared across all tokens under this policy_id.",
  "retry_after_seconds": 12,
  "policy_id": "pol_abc123"
}
```

---

## 9. JWT Claims

### 9.1 Tier-1 (Read)

```json
{
  "iss": "agentcafe",
  "sub": "agent:hashed-handle",
  "aud": "agentcafe",
  "exp": 1740270000,
  "iat": 1740259200,
  "jti": "uuid-v4",
  "tier": "read",
  "granted_by": "self",
  "agent_tag": "travel-assistant"
}
```

### 9.2 Tier-2 (Write)

```json
{
  "iss": "agentcafe",
  "sub": "user:alice@example.com",
  "aud": "agentcafe",
  "exp": 1740260100,
  "iat": 1740259200,
  "jti": "uuid-v4",
  "tier": "write",
  "scopes": ["stayright-hotels:book-room", "stayright-hotels:cancel-booking"],
  "human_scope_constraint": "self_only",
  "granted_by": "human_consent",
  "policy_id": "policy-uuid",
  "agent_tag": "travel-assistant",
  "authorizations": [
    {
      "service_id": "stayright-hotels",
      "action_id": "book-room",
      "limits": {
        "max_night_rate": 500,
        "valid_until": "2026-03-01"
      }
    }
  ]
}
```

### 9.3 Field Reference

| Field | Tier-1 | Tier-2 | Purpose |
|-------|--------|--------|---------|
| `iss` | `"agentcafe"` | `"agentcafe"` | Issuer |
| `sub` | `"agent:<hash>"` | `"user:<email>"` | Subject — agent handle or human identity |
| `aud` | `"agentcafe"` | `"agentcafe"` | Audience |
| `exp` | Timestamp | Timestamp | Expiry (risk-tier ceiling enforced) |
| `iat` | Timestamp | Timestamp | Issued-at (used for revocation check) |
| `jti` | UUIDv4 | UUIDv4 | Unique token ID |
| `tier` | `"read"` | `"write"` | Passport tier |
| `scopes` | — | Array | Authorized action scopes |
| `human_scope_constraint` | — | `"self_only"` | Resource ownership constraint |
| `granted_by` | `"self"` | `"human_consent"` | How the Passport was obtained |
| `policy_id` | — | UUID | Reference to the long-lived policy |
| `agent_tag` | Optional | Optional | Untrusted. Audit trail only. |
| `authorizations` | — | Array | Per-action limits and constraints |

---

## 10. Menu Schema Extension (ADR-023)

All fields are optional. 100% backward compatible with existing Menu entries.

### 10.1 Action-Level Fields

```json
{
  "risk_tier": "medium",
  "human_identifier_field": "customer_email",
  "constraints_schema": {
    "max_night_rate": { "type": "number" },
    "valid_until": { "type": "string", "format": "date" }
  },
  "account_linking_required": false,
  "self_only": true
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `risk_tier` | `"low"` \| `"medium"` \| `"high"` \| `"critical"` | `"medium"` | Token lifetime ceiling and verification depth |
| `human_identifier_field` | `string \| null` | `null` | Field name containing human identity. Single string for MVP. |
| `constraints_schema` | `object \| null` | `null` | JSON Schema for human-settable limits in consent UI |
| `account_linking_required` | `boolean` | `false` | Human must link service account before using action |
| `self_only` | `boolean` | `true` | Scoped to human's own resources |

### 10.2 Rate-Limit Scope (in `cost.limits`)

```json
"cost": {
  "required_scopes": ["stayright-hotels:book-room"],
  "limits": {
    "rate_limit": "60/minute",
    "rate_limit_scope": "per_policy"
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cost.limits.rate_limit_scope` | `"per_policy"` | `"per_policy"` | Rate-limit budget shared across all tokens under same policy |

### 10.3 Concurrency Guidance

```json
{
  "concurrency_guidance": {
    "recommended_executors": 1,
    "max_active_tokens_per_policy": 5,
    "reason": "Hotel booking API processes reservations sequentially"
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `concurrency_guidance` | `object \| null` | `null` | Advisory signal for multi-agent systems |
| `.recommended_executors` | `integer` | `1` | Recommended parallel agents for this action |
| `.max_active_tokens_per_policy` | `integer` | `5` | Advisory cap. **Cafe enforces hard ceiling of 20.** |
| `.reason` | `string \| null` | `null` | Human-readable explanation |

---

## 11. API Endpoints

### 11.1 Agent Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/passport/register` | None | Register agent, receive Tier-1 Passport |
| `GET` | `/cafe/menu` | Tier-1+ | Browse service Menu |
| `POST` | `/consents/initiate` | Tier-1+ | Initiate consent request |
| `GET` | `/consents/<consent_id>/status` | Tier-1+ | Poll consent status |
| `POST` | `/tokens/exchange` | Tier-1+ | Exchange approved consent for Tier-2 token |
| `POST` | `/tokens/refresh` | Tier-2 | Refresh token under existing policy |
| `POST` | `/cafe/order` | Tier-2 | Execute action via proxy |

### 11.2 Human Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/consent/<code>` | Session | Consent approval page |
| `POST` | `/consent/<code>/approve` | Session + passkey | Approve consent |
| `GET` | `/human/dashboard/pending-consents` | Session (`aud: "human-dashboard"`) | View pending approvals |
| `POST` | `/human/dashboard/revoke-policy` | Session + passkey | Revoke a policy |

### 11.3 Error Responses

| Code | Error | When |
|------|-------|------|
| `401` | `policy_revoked` | Token's `iat < policy.revoked_at` |
| `401` | `token_expired` | Token past `exp` |
| `403` | `scope_denied` | Action not in token's `scopes` |
| `403` | `identity_mismatch` | Human identifier verification failed |
| `410` | `consent_expired` | Consent TTL exceeded (72h default, 7d max) |
| `429` | `rate_limit_exceeded` | Per-policy rate limit hit. Includes `retry_after_seconds`. |
| `429` | `policy_token_limit_reached` | Hard ceiling of 20 active tokens per policy exceeded |

---

## 12. Security Guarantees

### 12.1 The Cafe Guarantees

- A real human with a verified Cafe account (passkey) authorized these specific actions.
- The human saw Cafe-authored plain-language descriptions of what they were approving.
- The Passport's scopes are enforced — out-of-scope requests are rejected.
- Actions on existing resources are verified against the human's identity (via Cafe-side data inspection).
- The audit trail records what happened under every Passport.
- The human can revoke any policy instantly. All tokens issued under that policy (regardless of remaining expiry) are rejected on their next presentation (`401 policy_revoked` if `token.iat < policy.revoked_at`).
- Backends never see the human's Passport or identity.

### 12.2 The Cafe Does NOT Guarantee

- That the agent carrying the Passport is any specific entity.
- That the agent will act in the human's best interest within the authorized scopes.
- That the human fully understood the implications of their authorization.
- That backends correctly implement their APIs or honor the Cafe's proxy semantics.
- That the Passport hasn't been shared with other agents (sharing is detectable but not preventable).

---

## 13. Multi-Agent Systems

The Cafe does not manage agent orchestration topology. It manages policies and tokens. Multi-agent coordination is the agent system's responsibility.

### 13.1 Browsing

Each sub-agent self-requests its own Tier-1 Passport. Unlimited parallel browsing. Per-token rate limiting.

### 13.2 Ordering

- One orchestrator (or any agent with the `consent_id`) exchanges consent for a Tier-2 token.
- Token refresh is non-consuming — multiple agents can independently hold and refresh tokens under the same policy.
- Single-use tokens for critical actions serialize destructive operations (one cancel per token).
- The policy is the single kill switch — human revokes → all tokens rejected.

### 13.3 Expected Pattern

- Many agents browse freely (Tier-1).
- Small number of executor agents place orders (Tier-2).
- One orchestrator manages token lifecycle.

---

## 14. MVP Scope

### 14.1 In Scope

- Tier-1 read Passports (agent self-request).
- Human accounts with passkey registration.
- Consent endpoints: `POST /consents/initiate`, `GET /consents/<id>/status`, `POST /tokens/exchange`.
- Server-rendered consent page (Jinja2 via FastAPI).
- Policy table with `revoked_at` for instant revocation.
- Token refresh (`POST /tokens/refresh`).
- One demo service (hotel booking) with full ADR-023 schema fields populated.
- Rate-limit 429 response with machine-readable error body.
- `cost.limits.rate_limit_scope` in Menu responses.

### 14.2 Not in MVP (updated March 2, 2026)

- ~~Webhook consent notifications.~~ ✅ Implemented (Sprint 3, `_fire_consent_callback`).
- Identity verification beyond agent-supplied input matching (no read-before-write yet).
- Anomaly detection.
- Multi-agent concurrency enforcement (hard cap of 20 is enforced; `concurrency_guidance` populated but not policed).
- Token response `policy_limits` snapshot.
- Rolling proof / hash chain.
- `on_behalf_of` resource constraints.
- Beautiful consent page UI (functional Jinja2 is sufficient).
- Passkey/WebAuthn enrollment (password-based accounts for now).

---

## 15. Key Management

✅ **Implemented** (Phase 6, `agentcafe/keys.py`).

- RS256 asymmetric signing. HS256 legacy fallback.
- Public keys via `/.well-known/jwks.json` (JWKS endpoint).
- `kid` claim in every JWT header for key lookup.
- Dual-key overlap rotation for zero-downtime.
- Cloud KMS integration deferred to production deployment (Phase 7).

---

## 16. Relationship to Existing Documents

| Document | Path | Relationship |
|----------|------|--------------|
| Threat model (v1.4) | `docs/architecture/passport/threat-model.md` | Core principles hold. POA framing updated to bearer model (March 2026). |
| V2 design discussion | `docs/architecture/passport/v2-discussion.md` | Source material for this spec. Contains rationale, rejected alternatives, and discussion history. |
| Decisions log | `docs/architecture/decisions.md` | ADR-023 (Menu schema), ADR-024 (bearer model), ADR-026 (Sprint 1–3 fixes). |
| Development plan | `docs/planning/development-plan.md` | Phase 4 implementation tasks track this spec. |

---

*Locked specification (v1.0). Reviewed and approved by three-way review (Jeremy + Claude + Grok, Feb 27, 2026). Source: `v2-design-discussion.md`. Implementation may begin.*
