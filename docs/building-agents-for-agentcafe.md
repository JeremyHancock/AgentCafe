# Building Agents for AgentCafe

A practical guide for agent developers integrating with AgentCafe.

---

## Overview

AgentCafe is a marketplace where AI agents discover and use services on behalf of humans. Your agent browses the Menu, finds what it needs, and places orders through the Cafe's secure proxy. The Cafe handles authorization, rate limiting, and backend communication — your agent never touches backend URLs or credentials directly.

## Quick Start

```
1. Browse the Menu         GET  /cafe/menu
2. Get a Tier-1 token      POST /passport/register
3. Place a read order       POST /cafe/order
4. Need a write action?     POST /consents/initiate  →  human approves  →  POST /tokens/exchange
5. Place a write order      POST /cafe/order (with Tier-2 token)
```

## Two-Tier Passport System

| Tier | Access | How to get it | Lifetime |
|------|--------|---------------|----------|
| **Tier 1 (Read)** | Read-only actions (search, browse, check availability) | `POST /passport/register` — self-service, no human needed | 3 hours |
| **Tier 2 (Write)** | Write actions (book, order, cancel) | Human consent flow (see below) | Risk-tier dependent (5–60 min) |

### Tier-1: Self-Service Read Token

```bash
POST /passport/register
Content-Type: application/json

{"agent_tag": "my-travel-agent"}
```

Response:
```json
{
  "passport": "eyJhbG...",
  "expires_at": "2026-03-01T03:00:00+00:00",
  "tier": "read",
  "agent_handle": "a1b2c3d4e5f6"
}
```

Use this token in the `passport` field of `/cafe/order` for any read action.

### Tier-2: Human Consent Flow

For write actions (booking a room, placing an order, canceling), you need human authorization:

```
Agent                          Cafe                         Human
  |                              |                            |
  |-- POST /consents/initiate -->|                            |
  |<-- consent_id + URL ---------|                            |
  |                              |                            |
  |   (present URL to human)     |                            |
  |                              |<-- human visits URL -------|
  |                              |<-- human approves ---------|
  |                              |                            |
  |-- POST /tokens/exchange ---->|                            |
  |<-- Tier-2 token -------------|                            |
```

**Step 1: Initiate consent**
```bash
POST /consents/initiate
Authorization: Bearer <tier-1-token>
Content-Type: application/json

{
  "service_id": "stayright-hotels",
  "action_id": "book-room"
}
```

Response:
```json
{
  "consent_id": "a74eef6a-55ea-4c14-baaf-603067baf716",
  "consent_url": "/authorize/a74eef6a-55ea-4c14-baaf-603067baf716",
  "status": "pending",
  "expires_at": "2026-03-03T19:31:32+00:00"
}
```

**Step 2: Present URL to human**

Give the human the consent URL. They'll see a Cafe-branded authorization page showing exactly what you're requesting, at what risk tier, with duration options. The Cafe controls this page — your agent never sees it.

**Step 3: Poll or wait**

```bash
GET /consents/<consent_id>/status
```

Returns `pending`, `approved`, `declined`, or `expired`.

**Step 4: Exchange for Tier-2 token**

Once approved:
```bash
POST /tokens/exchange
Authorization: Bearer <tier-1-token>
Content-Type: application/json

{"consent_id": "a74eef6a-55ea-4c14-baaf-603067baf716"}
```

Response:
```json
{
  "token": "eyJhbG...",
  "expires_at": "2026-03-01T03:15:00+00:00",
  "tier": "write",
  "scopes": ["stayright-hotels:book-room"],
  "policy_id": "pol-abc123"
}
```

**Step 5: Refresh before expiry**

Tier-2 tokens are short-lived. Refresh without re-consenting:
```bash
POST /tokens/refresh
Authorization: Bearer <tier-2-token>
```

The old token is NOT invalidated — it dies at expiry. Maximum 20 active tokens per policy.

## Placing Orders

```bash
POST /cafe/order
Content-Type: application/json

{
  "service_id": "stayright-hotels",
  "action_id": "search-availability",
  "passport": "<your-token>",
  "inputs": {
    "city": "Austin",
    "check_in": "2026-03-15",
    "check_out": "2026-03-18",
    "guests": 2
  }
}
```

The Cafe validates your Passport, checks rate limits, validates inputs, and proxies the request to the backend. You get the backend's response directly.

## Rate Limits

Rate limits are **per-policy** for Tier-2 tokens. Multiple agents sharing the same policy share the same rate limit budget.

When you hit a limit:
```json
{
  "error": "rate_limit_exceeded",
  "detail": "This action is rate-limited to 10 requests per minute under a shared per-policy budget.",
  "retry_after_seconds": 12,
  "policy_id": "pol-abc123"
}
```

The response includes a `Retry-After` HTTP header. Respect it.

## Risk Tiers and Token Lifetimes

Services declare a risk tier per action. This affects token lifetime ceilings:

| Risk Tier | Max Token Lifetime | Examples |
|-----------|-------------------|----------|
| `low` | 60 minutes | Search, browse, check availability |
| `medium` | 15 minutes | Book a room, place an order |
| `high` | 5 minutes | Cancel a booking, cancel an order |
| `critical` | Single-use | (Reserved for future high-value actions) |

The human chooses the duration at approval time, capped by the ceiling.

## Identity Verification

For medium+ risk write actions, the Cafe enforces a **read-before-write** pattern. Your agent must have performed a read action on the same service (with the same Passport) before attempting a write. This proves the agent has context about what it's modifying.

Some actions also require a `human_identifier_field` in the inputs (e.g., `guest_email` for hotel bookings). The Cafe rejects write requests that don't include this field.

## Error Codes

| Code | HTTP | Meaning |
|------|------|---------|
| `passport_invalid` | 401 | Token expired, malformed, or revoked |
| `tier_insufficient` | 403 | Tier-1 token used for a write action |
| `scope_missing` | 403 | Token doesn't cover this action |
| `human_auth_required` | 403 | Action requires Tier-2 (human consent) |
| `read_before_write_required` | 403 | No prior read on this service |
| `identity_field_missing` | 422 | Required identifier not in inputs |
| `invalid_path_parameter` | 422 | Unsafe characters in a path parameter |
| `missing_inputs` | 422 | Required inputs not provided |
| `rate_limit_exceeded` | 429 | Rate limit hit; check `Retry-After` |
| `policy_revoked` | 401 | Human revoked the authorization policy |
| `action_not_found` | 404 | Unknown service_id or action_id |

## Multi-Agent Coordination

Multiple agents can share a single policy (one human approval, many agents). Key rules:

- **Rate limits are shared.** 10 req/min per policy means 10 total across all agents, not 10 each.
- **Max 20 active tokens** per policy. Each `POST /tokens/exchange` or `/tokens/refresh` counts.
- **Token refresh is non-consuming.** The old token stays valid until expiry. Plan accordingly.
- **Revocation is instant.** If the human revokes the policy, all tokens under it become invalid immediately.

## The Consent ID is Your Claim Ticket

The `consent_id` returned by `/consents/initiate` is a durable identifier. Store it. You'll need it to:
- Poll status (`GET /consents/<id>/status`)
- Exchange for a token (`POST /tokens/exchange`)
- Correlate with the human's approval

Consent IDs are UUIDv4 and expire after 72 hours (default) or 7 days (max). There is no way to list or discover consents — this is by design for privacy.

## What the Cafe Guarantees

- Human verification for write actions
- Scope enforcement (your token only works for what was approved)
- Audit trail (every request logged with hash chain)
- Instant revocation
- Backend credential protection (your agent never sees backend auth)

## What the Cafe Does NOT Guarantee

- Agent identity (tokens are bearer tokens — anyone with the token can use it)
- Agent intent (the Cafe can't verify why you're making a request)
- Human comprehension (the Cafe presents plain-language text, but can't ensure the human reads it)
- Backend compliance (the Cafe proxies faithfully, but can't control what the backend does)

---

*For the full API, run the Cafe locally and visit `http://localhost:8000/docs`.*
