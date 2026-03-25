# Service Contract & Identity Binding Protocol

**Date:** March 11, 2026
**Author:** Cascade
**Status:** Locked and implemented (March 25, 2026). Code: `cafe/binding.py` (resolve_binding, resolve_human_id), `cafe/integration.py` (revocation delivery), `cafe/consent.py` + `cafe/cards.py` (grant creation). Tests: `tests/test_service_integration.py`, `tests/test_revocation_delivery.py`.
**Parent:** Service Integration Standard Briefing (approved)
**Implements:** Briefing Artifact 2
**Depends on:** Nothing (first in sequence, alongside Artifacts 0 and 1)

---

## 1. Purpose

This spec defines the **full standard interaction** between AgentCafe and a jointly-verified service — from account binding through revocation to reconciliation. It is the contract that services implement to integrate with AC's jointly-verified mode.

The spec is organized into three sections per the briefing's converged structure:

- **§A Identity Binding** — account check, creation, linking, unlinking, relinking
- **§B Revocation** — intake, delivery guarantees, reconciliation
- **§C Capability Negotiation** — declaration, mandatory vs. optional operations, versioning

Each section defines endpoints the service must (or may) implement, the request/response shapes, idempotency requirements, and failure modes.

**Standard-mode services implement none of this.** The current proxy model works unchanged for services where `integration_mode = 'standard'` (or NULL) on all actions.

---

## 2. Conventions

### 2.1 Endpoint Prefix

Integration endpoints have **semantic names** with a default path convention:

| Semantic name | Default path | Override allowed? |
|---------------|-------------|------------------|
| `account_check` | `/integration/account-check` | Yes |
| `account_create` | `/integration/account-create` | Yes |
| `link_complete` | `/integration/link-complete` | Yes |
| `unlink` | `/integration/unlink` | Yes |
| `revoke` | `/integration/revoke` | Yes |
| `grant_status` | `/integration/grant-status` | Yes |

The base URL (`https://<service-host>`) is configured during onboarding via the wizard. By default, AC appends `/integration/<operation>` to the base URL. Services MAY override individual endpoint paths during onboarding if their existing routing doesn't accommodate the default prefix. The SDK defaults to `/integration/...` for easy adopters.

Overrides are stored per-operation in `service_integration_configs` (see §C.4). AC resolves the full URL at call time: `base_url + endpoint_path_override` (or `base_url + default_path` if no override).

### 2.2 Authentication

AC authenticates to the service's integration endpoints using a dedicated integration credential stored in `service_integration_configs.integration_auth_header` (encrypted with AES-256-GCM via `crypto.py`). This is separate from the per-action `backend_auth_header` in `proxy_configs` — integration endpoints are per-service, not per-action. The credential is configured during onboarding and may be the same value as `backend_auth_header` if the service uses a single API key, but it is stored independently.

The service MUST verify this credential on every integration call. Integration endpoints are not public.

### 2.3 Request/Response Format

All requests and responses are JSON. All timestamps are ISO 8601 UTC. All IDs are strings.

### 2.4 Standard Version

All integration requests include a `standard_version` field. The current version is `"1.0"`. Services MUST reject requests with a version they don't support (return `422` with `unsupported_standard_version`).

---

## §A. Identity Binding

Identity binding is the process of establishing a mapping between an AC human account and a service-side account. This mapping is stored in AC's `human_service_accounts` table and is referenced every time AC signs a per-request artifact (the `sub` claim comes from this binding).

### A.1 Schema: `human_service_accounts` (Identity Binding)

This table tracks the **identity relationship** between a human and a service — which service-side account maps to which AC human. It does NOT track authorization grants. A binding can outlive any individual grant.

```sql
CREATE TABLE human_service_accounts (
    id TEXT PRIMARY KEY,                           -- UUIDv4
    ac_human_id TEXT NOT NULL,                     -- AC's internal human ID (from cafe_users)
    service_id TEXT NOT NULL,                      -- The service (from proxy_configs)
    service_account_id TEXT,                       -- The service's own account ID for this human
    binding_method TEXT NOT NULL,                   -- How the binding was established
    binding_status TEXT NOT NULL DEFAULT 'active',  -- Current state
    identity_binding TEXT NOT NULL,                 -- Provenance classification
    linked_at TEXT NOT NULL,                        -- ISO 8601 UTC
    updated_at TEXT NOT NULL,                       -- ISO 8601 UTC
    UNIQUE(ac_human_id, service_id)                -- One binding per human per service
);

CREATE INDEX idx_hsa_human ON human_service_accounts(ac_human_id);
CREATE INDEX idx_hsa_service ON human_service_accounts(service_id);
CREATE INDEX idx_hsa_status ON human_service_accounts(binding_status);
```

| Column | Values | Description |
|--------|--------|-------------|
| `binding_method` | `email_match`, `linking_code`, `delegated_creation` | How the binding was established |
| `binding_status` | `active`, `link_pending`, `deferred`, `unlinked` | Current state (identity lifecycle only — matches state machine in Artifact 0) |
| `identity_binding` | `broker_delegated`, `service_native`, `email_match` | Trust provenance — carried into per-request artifact `identity_binding` claim |

**Key separation:** `binding_status` tracks identity lifecycle only. Revocation states (`revoke_queued`, `revoke_delivered`, `revoke_honored`) live on `authorization_grants`, not here. A human can have an active binding with a service while having zero, one, or multiple grants (some active, some revoked).

### A.1.1 Schema: `authorization_grants` (Grant Provenance)

This table tracks **authorization grants** — the individual policies or Company Cards that authorize a human to use a service. Multiple grants can exist for the same human+service pair (e.g., Card A revoked, Card B active). Revocation is per-grant, not per-binding.

```sql
CREATE TABLE authorization_grants (
    id TEXT PRIMARY KEY,                           -- UUIDv4
    ac_human_id TEXT NOT NULL,                     -- AC's internal human ID
    service_id TEXT NOT NULL,                      -- The service
    consent_ref TEXT NOT NULL,                     -- policy_id or card_id
    grant_status TEXT NOT NULL DEFAULT 'active',   -- active, revoke_queued, revoke_delivered, revoke_honored
    granted_at TEXT NOT NULL,                      -- ISO 8601 UTC
    revoked_at TEXT,                               -- ISO 8601 UTC, NULL if not revoked
    updated_at TEXT NOT NULL,                      -- ISO 8601 UTC
    UNIQUE(consent_ref, service_id)                 -- One row per grant per service (supports card fan-out)
);

CREATE INDEX idx_ag_human_service ON authorization_grants(ac_human_id, service_id);
CREATE INDEX idx_ag_status ON authorization_grants(grant_status);
CREATE INDEX idx_ag_consent ON authorization_grants(consent_ref);
```

| `grant_status` | Description |
|----------------|-------------|
| `active` | Grant is live. Artifacts can be issued under this `consent_ref`. |
| `revoke_queued` | Human revoked. AC has queued revocation delivery. No new artifacts issued. |
| `revoke_delivered` | Service returned 2xx delivery receipt. Not yet verified via reconciliation. |
| `revoke_honored` | Reconciliation confirmed the service is denying requests under this `consent_ref`. Terminal. |

**Why separate tables:** The same human can authorize the same service through multiple policies/cards over time. Revoking one card while keeping another active is a first-class operation. If binding and grants are conflated in one row, this becomes ambiguous — audits get misleading, relinking history gets flattened, and revocation of one grant incorrectly appears to revoke all access.

### A.2 `POST /integration/account-check`

**Called by AC during consent flow.** Determines whether the human already has an account on the service.

**When called:** Primarily during consent approval — after the human initiates consent for a jointly-verified action on this service. May also be called by the **deferred binding background resolver** (Artifact 0 §5.2.2) if the service was unreachable during the original consent. The service MUST handle account-check identically regardless of when it is called — the contract is the same whether invoked at consent-time or during deferred resolution.

**Request:**

```json
{
  "standard_version": "1.0",
  "identity_claim": {
    "type": "email",
    "value": "alice@example.com",
    "verified": true
  },
  "ac_human_id_hash": "a1b2c3d4e5f6...",
  "consent_ref": "pol_abc123"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `standard_version` | Yes | Protocol version |
| `identity_claim` | Yes | The human's identity as known to AC. `type` is one of: `email`, `phone`, `opaque_id`. `verified` indicates whether AC has verified this claim. |
| `ac_human_id_hash` | Yes | SHA-256 hash of AC's internal `ac_human_id`. Opaque — the service cannot reverse it. Used as a stable correlator if the human has no matching identity on the service. |
| `consent_ref` | Yes | The policy or card that triggered this check. |

**Privacy note:** AC sends `identity_claim` only when the service declared `identity_matching: email` (or `phone`) during onboarding AND the consent page disclosed this to the human (per converged OQ7). If the service declared `identity_matching: opaque_id`, the `identity_claim` field is omitted from the request entirely — the `ac_human_id_hash` field serves as the sole identity correlator, avoiding redundancy.

**Response — account exists (direct match, no linking needed):**

```json
{
  "exists": true,
  "service_account_id": "usr_12345",
  "account_status": "active",
  "linking_url": null
}
```

When the service returns `exists: true` with a `service_account_id` and no `linking_url`, the service has confirmed the identity match directly (e.g., email matched an existing account). AC creates the `human_service_accounts` binding immediately with `binding_method = 'email_match'` and `identity_binding = 'email_match'`. No redirect or linking flow needed.

**Response — account exists, linking required:**

```json
{
  "exists": true,
  "service_account_id": null,
  "account_status": "active",
  "linking_url": "https://memory.example.com/link?ref=pol_abc123"
}
```

The `linking_url` is provided when the service has a matching account but cannot confirm the binding without the human authenticating on the service's side. The human is redirected to `linking_url` to complete the linking flow.

**Response — no account:**

```json
{
  "exists": false,
  "service_account_id": null,
  "account_status": null,
  "linking_url": null
}
```

**Response — account banned/rate-limited:**

```json
{
  "exists": true,
  "service_account_id": "usr_12345",
  "account_status": "banned",
  "linking_url": null,
  "message": "This account has been suspended."
}
```

AC presents the `message` to the human on the consent page if `account_status` is not `active`.

| `account_status` | AC behavior |
|-------------------|------------|
| `active` | Proceed with binding |
| `banned` | Block consent. Show message to human. |
| `rate_limited` | Warn human. Allow consent but note limitation. |
| `suspended` | Block consent. Show message to human. |

**Failure modes:**

| Scenario | Service response | AC behavior |
|----------|-----------------|-------------|
| Service unreachable | Timeout / connection error | State → `consent_deferred`. Consent approved on AC side. Account ops deferred. |
| Invalid identity claim | `400 Bad Request` | Show error to human. Consent cannot proceed. |
| Internal service error | `500 Internal Server Error` | Retry once after 5s. If still failing, defer. |

### A.3 `POST /integration/account-create`

**Called by AC after human approval** when the account-check returned `exists: false`. Creates a new account on the service for this human.

**Idempotency:** MUST be idempotent on the identity claim. If AC calls account-create twice with the same `identity_claim` (e.g., after a timeout), the service MUST return the existing account, not create a duplicate.

**Request:**

```json
{
  "standard_version": "1.0",
  "identity_claim": {
    "type": "email",
    "value": "alice@example.com",
    "verified": true
  },
  "ac_human_id_hash": "a1b2c3d4e5f6...",
  "consent_ref": "pol_abc123"
}
```

Same shape as account-check. The service uses `identity_claim` to create the account.

**Response — success:**

```json
{
  "service_account_id": "usr_67890",
  "created": true
}
```

**Response — account already exists (idempotent):**

```json
{
  "service_account_id": "usr_67890",
  "created": false
}
```

Both responses are treated identically by AC. AC stores the `service_account_id` in `human_service_accounts` with `binding_method = 'delegated_creation'` and `identity_binding = 'broker_delegated'`.

**Failure modes:**

| Scenario | Service response | AC behavior |
|----------|-----------------|-------------|
| Identity collision (email exists but different person) | `409 Conflict` with `{"error": "identity_collision", "linking_url": "..."}` | Redirect human to linking flow instead. State → `link_pending`. |
| Service at capacity | `503 Service Unavailable` | State → `consent_deferred`. Retry on first proxy request. |
| Invalid request | `400 Bad Request` | State → `account_creation_failed`. Show error to human. |

### A.4 Account Linking Flow

**Triggered when:** Account-check returns `linking_url`, or account-create returns `409` with a `linking_url`.

**Flow:**

```
1. AC generates a random `state` token (UUIDv4), stores it server-side
   tied to the consent session (consent_ref + human session).

2. AC redirects human to service's linking_url:
   {linking_url}?return_url=https://agentcafe.io/link-callback&state={state}

3. Human authenticates on the service's site.

4. Service generates a single-use linking code (opaque to AC, 60s TTL).

5. Service redirects human back to AC via POST redirect (auto-submitting form):
   POST https://agentcafe.io/link-callback
   Body: consent_ref=pol_abc123&linking_code={code}&state={state}

   If POST redirect is not feasible, GET is acceptable:
   https://agentcafe.io/link-callback?consent_ref=pol_abc123&linking_code={code}&state={state}
   (The single-use + 60s TTL mitigate URL exposure in logs/history.)

6. AC validates `state` matches the stored value (CSRF protection).
7. AC calls POST /integration/link-complete to exchange the linking code.
```

**Security note on transport:** The linking code is a single-use authorization code, not a bearer token. Even if captured from browser history or logs, it cannot be replayed (single-use) and expires in 60s. POST redirect is preferred to avoid URL exposure, but GET is acceptable given these mitigations. This follows the same pattern as OAuth 2.0 authorization codes.

**Trust requirements for the linking code:**

| Requirement | Rationale |
|-------------|----------|
| **Single-use** | Prevents replay. Service MUST reject a linking code that has already been exchanged via `link-complete`. |
| **Short-lived (60s TTL)** | Limits the window for interception. Service MUST reject expired codes. |
| **AC-audience-bound** | The code MUST be scoped to AC's return URL. Service MUST NOT accept codes generated for other redirect targets. This prevents a service from being tricked into completing a link via a code generated for a different callback. |
| **Bound to `consent_ref`** | The code MUST encode which `consent_ref` initiated the linking. `link-complete` validates that the `consent_ref` in the request matches the code. |

**AC-side validation before calling `link-complete`:**

1. AC verifies the `state` parameter matches the value stored for this consent session (CSRF protection)
2. AC verifies the `consent_ref` in the callback matches a pending consent
3. AC verifies the consent is still valid (not revoked, not expired)
4. AC calls `link-complete` with the linking code. All further validation is the service's responsibility — AC treats the code as opaque.

**Return URL integrity:** The `linking_url` provided by the service MUST include AC's return URL as a parameter. The service MUST redirect back to exactly this URL. AC MUST reject callbacks that don't match its expected return URL origin. The `state` parameter provides CSRF protection (OAuth 2.0 §10.12 pattern).

### A.5 `POST /integration/link-complete`

**Called by AC** after receiving the linking code from the redirect callback.

**Request:**

```json
{
  "standard_version": "1.0",
  "linking_code": "<opaque-code-from-service>",
  "consent_ref": "pol_abc123"
}
```

**Response — success:**

```json
{
  "service_account_id": "usr_12345",
  "linked": true
}
```

AC stores the binding with `binding_method = 'linking_code'` and `identity_binding = 'service_native'`.

**Failure modes:**

| Scenario | Service response | AC behavior |
|----------|-----------------|-------------|
| Code expired | `401` with `{"error": "linking_code_expired"}` | State → `link_expired`. Human must retry. |
| Code already used | `409` with `{"error": "linking_code_used"}` | Check if binding exists (idempotent case). If yes, proceed. If no, error. |
| Code invalid | `400` with `{"error": "linking_code_invalid"}` | State → `partial_failure`. Log. Human must retry. |

### A.6 `POST /integration/unlink`

**Called by AC** when a human unlinks a service from their AC dashboard.

**Request:**

```json
{
  "standard_version": "1.0",
  "service_account_id": "usr_12345",
  "ac_human_id_hash": "a1b2c3d4e5f6...",
  "reason": "human_requested"
}
```

**Response — success:**

```json
{
  "unlinked": true
}
```

The service marks the binding inactive on its side. AC updates `binding_status = 'unlinked'` in `human_service_accounts`.

**Idempotency:** MUST be idempotent. If the binding is already inactive, return success.

**Important:** Unlinking is NOT revocation. Unlinking removes the identity binding. Revocation removes the authorization grant. A human can unlink (remove the account mapping) without revoking (the policy/card may still exist for relinking later). Conversely, revocation doesn't necessarily unlink — the account mapping may be preserved for re-consent.

### A.7 Relinking

Relinking follows the same flow as initial linking (A.4). The human initiates a new consent flow for the service. AC calls account-check, which may return a new `linking_url`. The human authenticates on the service, and a new binding is created.

The `UNIQUE(ac_human_id, service_id)` constraint on `human_service_accounts` means the old row is updated (status changes from `unlinked` to `active`, new `service_account_id` if changed, new `linked_at`). It is not a new row. A new `authorization_grants` row is created for the new consent_ref — relinking creates a fresh grant, it does not reuse the old one.

---

## §B. Revocation

Revocation is the process of invalidating an authorization grant (policy or Company Card) and ensuring the service stops honoring requests under that grant.

### B.1 Design Principles

1. **Push + backstop.** AC pushes revocation events to the service. Short artifact TTL (30s) acts as a backstop — even if the push fails, artifacts expire quickly.
2. **Delivery guarantees.** Push-only is insufficient. AC must confirm delivery and retry on failure.
3. **Reconciliation.** Periodic verification that the service's grant state matches AC's intent.
4. **Idempotency.** All revocation operations are idempotent on the `consent_ref`.

### B.2 `POST /integration/revoke`

**Called by AC** when a human revokes a policy or Company Card that covers this service.

**Request:**

```json
{
  "standard_version": "1.0",
  "consent_ref": "pol_abc123",
  "revoked_at": "2026-03-11T14:30:00Z",
  "reason": "human_revoked",
  "correlation_id": "rev_a1b2c3d4"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `consent_ref` | Yes | The policy_id or card_id being revoked |
| `revoked_at` | Yes | When the human revoked (AC-side timestamp) |
| `reason` | Yes | One of: `human_revoked`, `admin_revoked`, `policy_expired`, `card_exhausted` |
| `correlation_id` | Yes | Unique ID for this revocation event. Used for delivery tracking. **Note:** This is a revocation-specific ID (prefixed `rev_`), distinct from the per-request artifact `jti` (UUIDv4). Services logging both per-request artifacts and revocation webhooks should use separate columns or prefix-aware indexing to avoid confusion during dispute resolution. |

**Response — success (delivery receipt):**

```json
{
  "acknowledged": true,
  "correlation_id": "rev_a1b2c3d4",
  "effective_at": "2026-03-11T14:30:05Z"
}
```

The `effective_at` is when the service will begin denying requests under this `consent_ref`. It SHOULD be immediate (within seconds of `revoked_at`). A small delay for propagation is acceptable. A large delay (>60s) is a protocol violation.

**Idempotency:** If AC sends the same revocation (same `consent_ref` + `correlation_id`) twice, the service MUST return success and maintain the deny state. It MUST NOT un-revoke.

**Card-level revocation:** When `consent_ref` is a `card_id` (Company Card), the service MUST deny **all** actions that were authorized under that card — not just a single action. The service matches incoming artifacts by `consent_ref` claim, so revoking a card means rejecting any artifact whose `consent_ref` matches the revoked `card_id`, regardless of `action`. Services do not need to know which specific actions the card covered; they simply store the revoked `consent_ref` and deny on match. This is the same mechanism as single-policy revocation — the only difference is that a card covers multiple actions. Services store revoked `consent_ref` values (whether `policy_id` or `card_id`) and reject any incoming artifact whose `consent_ref` claim matches a revoked value. No scope lookup required.

### B.3 Delivery Guarantees

AC implements the following delivery protocol:

```
1. Human revokes on AC → revocation event created
2. AC calls POST /integration/revoke on the service
3. If 2xx response with acknowledged: true → delivery confirmed
   → Update grant: revoke_queued → revoke_delivered
   → revoke_honored only after reconciliation confirms (see B.5)
4. If timeout / 5xx / connection error → delivery failed
   → Retry with exponential backoff: 5s, 15s, 45s, 135s, 300s (max)
   → Max retries: 10 (over ~13 minutes)
   → After max retries: alert AC admin. Manual intervention.
5. Short TTL (30s) acts as backstop — artifacts expire even without delivery
```

**Delivery status tracking:**

AC tracks revocation delivery in a new table:

```sql
CREATE TABLE revocation_deliveries (
    id TEXT PRIMARY KEY,
    consent_ref TEXT NOT NULL,
    service_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'queued',   -- queued, delivered, failed, manual
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    delivered_at TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_revdel_consent ON revocation_deliveries(consent_ref);
CREATE INDEX idx_revdel_status ON revocation_deliveries(status);
```

### B.4 Company Card Fan-Out

When a Company Card is revoked, AC must push revocation to **every** jointly-verified service covered by that card:

```python
# Get all services with jointly-verified actions under this card
cursor = await db.execute(
    """SELECT DISTINCT pc.service_id
       FROM proxy_configs pc
       JOIN card_scopes cs ON cs.scope = (pc.service_id || ':' || pc.action_id)
       WHERE cs.card_id = ? AND pc.integration_mode = 'jointly_verified'""",
    (card_id,),
)
services = await cursor.fetchall()

# Push revocation to each service
for svc in services:
    await _queue_revocation(db, consent_ref=card_id, service_id=svc["service_id"])
```

Fan-out is parallelized (fire all pushes concurrently, track delivery independently per service).

### B.5 `GET /integration/grant-status`

**Called by AC periodically** to verify the service's grant state matches AC's intent.

**Request:**

```
GET /integration/grant-status?consent_ref=pol_abc123&standard_version=1.0
Authorization: Bearer <service-api-key>
```

**Response — grant honored:**

```json
{
  "consent_ref": "pol_abc123",
  "status": "active",
  "last_activity_at": "2026-03-11T12:00:00Z"
}
```

**Response — grant revoked:**

```json
{
  "consent_ref": "pol_abc123",
  "status": "revoked",
  "revoked_at": "2026-03-11T14:30:05Z"
}
```

**Response — grant unknown:**

```json
{
  "consent_ref": "pol_abc123",
  "status": "unknown"
}
```

**Reconciliation logic (AC-side):**

| AC thinks | Service says | Action |
|-----------|-------------|--------|
| `active` | `active` | OK. No action. |
| `active` | `revoked` | **Discrepancy.** AC should investigate — someone revoked on the service side without going through AC. Log alert. |
| `revoked` | `active` | **Revocation not honored.** Re-push revocation. If still not honored after retry, alert admin. |
| `revoked` | `revoked` | OK. No action. |
| `revoked` | `unknown` | **Service lost state.** Re-push revocation. Alert admin. |

**Reconciliation frequency:** Every 24 hours for active grants. Immediately after a revocation delivery failure. On-demand via admin dashboard.

### B.6 Revocation vs. Unlinking

| | Revocation | Unlinking |
|---|-----------|----------|
| **Trigger** | Human revokes policy/card on AC | Human unlinks service on AC dashboard |
| **Effect on authorization** | Policy/card is invalidated. No more artifacts issued. | Policy/card may still exist. |
| **Effect on grant** | Grant marked `revoke_queued` → `revoke_delivered` → `revoke_honored`. Identity binding stays `active`. | Identity binding stays (policy/card may still exist). |
| **Effect on identity binding** | Binding status unchanged (stays `active`). Binding outlives grants. | Binding marked `unlinked`. |
| **Service-side effect** | Service denies requests under this `consent_ref` | Service marks binding inactive |
| **Can re-consent?** | Yes, new policy/card needed | Yes, relinking flow |
| **Service endpoint** | `POST /integration/revoke` | `POST /integration/unlink` |

---

## §C. Capability Negotiation

Not every jointly-verified service supports all operations. Capability negotiation allows services to declare which operations they implement, enabling AC to adapt its consent flow and proxy behavior.

### C.1 Capability Declaration

During onboarding (wizard step), the service declares its capabilities. These are stored per-service (not per-action — capabilities are a property of the service's integration implementation, not individual actions).

**HM (MVS) exception:** For AC-owned services operating under MVS (see §7), capabilities are hard-coded in AC's router config (see Artifact 0 §12.3) — not declared via the wizard or stored in `service_integration_configs`. The wizard extension and this table are deferred until the second jointly-verified service is onboarded. This avoids unnecessary onboarding ceremony for an AC-owned service whose capabilities AC already knows.

```json
{
  "integration_base_url": "https://memory.example.com",
  "identity_matching": "email",
  "standard_version": "1.0",
  "capabilities": {
    "account_check": true,
    "account_create": true,
    "link_complete": true,
    "unlink": true,
    "revoke": true,
    "grant_status": false
  }
}
```

### C.2 Mandatory vs. Optional Operations

| Operation | Required? | Why |
|-----------|-----------|-----|
| **Artifact validation** | **Mandatory** | Without this, jointly-verified mode is meaningless. The service must validate `X-AgentCafe-Authorization`. |
| `POST /integration/revoke` | **Mandatory** | Without revocation intake, a revoked grant continues to be honored. Non-negotiable for trust. |
| `POST /integration/account-check` | **Recommended** | Required for the consent flow to determine account status. If not supported, AC assumes "no account" and tries account-create. |
| `POST /integration/account-create` | **Recommended** | Required if the service wants AC to create accounts via delegated identity proofing. If not supported, only linking is available. |
| `POST /integration/link-complete` | **Optional** | Only needed if the service has existing users who must authenticate on the service's site. Services with no existing user base skip this. |
| `POST /integration/unlink` | **Optional** | Needed for full lifecycle management. If not supported, unlinking is AC-side only (binding status updated, service not notified). |
| `GET /integration/grant-status` | **Optional** | Needed for reconciliation. If not supported, AC relies solely on push + TTL backstop. |

### C.3 Capability-Driven Consent Flow

AC adapts the consent flow based on declared capabilities:

| Service capabilities | Consent flow |
|---------------------|-------------|
| `account_check` + `account_create` | AC checks → creates if needed → active |
| `account_check` + `link_complete` (no `account_create`) | AC checks → link if exists, error if not → active |
| `account_create` only (no `account_check`) | AC always creates → active (idempotent handles duplicates) |
| `link_complete` only | AC skips check, sends human to linking URL (configured in wizard) → active |
| None of the above | **Error: invalid configuration.** A jointly-verified service MUST implement at least one of `account_check`, `account_create`, or `link_complete`. If none are declared, onboarding is rejected. Without any account lifecycle operation, there is no way to establish a service-owned `sub` — and the artifact's `sub` claim requires a genuine service account ID. |

### C.4 Schema: `service_integration_configs`

Capability declarations are stored per-service:

```sql
CREATE TABLE service_integration_configs (
    service_id TEXT PRIMARY KEY,
    integration_base_url TEXT NOT NULL,
    integration_auth_header TEXT NOT NULL,          -- encrypted, per-service credential for /integration/ calls
    identity_matching TEXT NOT NULL DEFAULT 'opaque_id',
    standard_version TEXT NOT NULL DEFAULT '1.0',
    cap_account_check BOOLEAN NOT NULL DEFAULT 0,
    cap_account_create BOOLEAN NOT NULL DEFAULT 0,
    cap_link_complete BOOLEAN NOT NULL DEFAULT 0,
    cap_unlink BOOLEAN NOT NULL DEFAULT 0,
    cap_revoke BOOLEAN NOT NULL DEFAULT 1,         -- mandatory, default true
    cap_grant_status BOOLEAN NOT NULL DEFAULT 0,
    -- Endpoint path overrides (NULL = use default /integration/<operation>)
    path_account_check TEXT,
    path_account_create TEXT,
    path_link_complete TEXT,
    path_unlink TEXT,
    path_revoke TEXT,
    path_grant_status TEXT,
    configured_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Endpoint path overrides default to NULL, meaning AC uses the standard `/integration/<operation>` paths. Services with existing routing can set per-operation paths during onboarding (e.g., `path_revoke = '/api/v1/ac-revoke'`). AC constructs the full URL as `integration_base_url + path_override` (or `integration_base_url + default_path` if NULL).

| `identity_matching` | What AC sends in `identity_claim.type` |
|--------------------|--------------------------------------|
| `email` | Verified email from AC human account. Consent page says: "Your email will be shared with [Service Name]." |
| `phone` | Verified phone. Same disclosure requirement. |
| `opaque_id` | `ac_human_id_hash` only. No personally identifiable information shared. Default. |

### C.5 Standard Versioning

The `standard_version` field in capability declarations and all integration requests enables protocol evolution.

**Version negotiation:**

1. During onboarding, the service declares `standard_version: "1.0"`
2. AC stores this in `service_integration_configs`
3. All integration requests include `standard_version: "1.0"`
4. Per-request artifacts include `standard_version: "1.0"` (defined in Artifact 1)

**When AC supports v1.1:**

1. AC sends `standard_version: "1.1"` to services that declared v1.1 support
2. AC sends `standard_version: "1.0"` to services still on v1.0
3. Services that receive a version they don't support return `422`
4. AC falls back to the service's declared version

**Deprecation policy (to be defined in ADR-030):**

- New mandatory operations added in a minor version (e.g., 1.1) have a 6-month adoption window
- Major version changes (e.g., 2.0) require explicit migration and are not backward-compatible
- AC maintains support for N-1 major versions

---

## 4. Stripe Thought Experiment

Validating the service contract against Agent Payments via Stripe:

| Operation | HM | Stripe | Works? |
|-----------|-----|--------|--------|
| `account-check` | Email match against HM user table | Email match against Stripe Customer list | Yes |
| `account-create` | HM creates internal user + namespace | Not applicable — Stripe accounts are human-created | Yes (Stripe declares `cap_account_create: false`) |
| `link-complete` | Not needed (HM has no existing users initially) | Required — human authenticates with Stripe OAuth, linking code | Yes |
| `unlink` | HM marks namespace inactive | Stripe disconnects the AC integration | Yes |
| `revoke` | HM denies requests under this consent_ref | Stripe blocks AC-authorized charges | Yes |
| `grant-status` | HM checks internal grant table | Stripe checks AC integration status | Yes |
| `identity_matching` | `email` (HM matches by verified email) | `email` or `opaque_id` (Stripe matches by Customer ID after OAuth) | Yes |

**Stripe-specific capability declaration:**

```json
{
  "integration_base_url": "https://payments.agentcafe.io",
  "identity_matching": "opaque_id",
  "standard_version": "1.0",
  "capabilities": {
    "account_check": true,
    "account_create": false,
    "link_complete": true,
    "unlink": true,
    "revoke": true,
    "grant_status": true
  }
}
```

The key difference: Stripe does NOT support `account_create` — humans must create their own Stripe accounts. The consent flow adapts: AC calls `account_check` → if no account, tells the human "You need a Stripe account first" → if account exists, redirects to Stripe OAuth for linking. **No spec changes needed.**

---

## 5. Complete Endpoint Summary

| Endpoint | Method | Required? | Called by | When |
|----------|--------|-----------|----------|------|
| `/integration/account-check` | POST | Recommended | AC | During consent |
| `/integration/account-create` | POST | Recommended | AC | During consent (new user) |
| `/integration/link-complete` | POST | Optional | AC | After human links on service |
| `/integration/unlink` | POST | Optional | AC | Human unlinks from dashboard |
| `/integration/revoke` | POST | **Mandatory** | AC | Human revokes policy/card |
| `/integration/grant-status` | GET | Optional | AC | Periodic reconciliation |

All endpoints are authenticated via `integration_auth_header` (from `service_integration_configs`, see §2.2). All requests include `standard_version`. Idempotency keys: `identity_claim` (or `ac_human_id_hash`) for account-check/create, `consent_ref` for revocation, `(ac_human_id_hash, service_id)` for unlinking.

---

## 6. Account-Create Idempotency Guarantee

The `POST /integration/account-create` endpoint is idempotent on `identity_claim` (or `ac_human_id_hash` for opaque mode). If AC calls account-create twice with the same identity (e.g., retry after timeout), the service MUST return the existing `service_account_id` — not create a duplicate account.

**Critical invariant:** AC MUST store the `human_service_accounts` binding **before** returning success to the human during consent approval. If AC crashes after the service creates the account but before AC stores the binding, the next retry (whether consent-time or background resolver) will call account-create again. The service returns the existing account ID (idempotent), and AC stores the binding. No duplicate account, no orphaned binding.

**Service-side requirement:** Account-create MUST return the same `service_account_id` for the same `ac_human_id_hash`, even across multiple calls. Services SHOULD use `ac_human_id_hash` as a stable correlator key internally. This ensures that even if AC's binding storage fails and retries, the service-side state is consistent.

---

## 7. Minimum Viable Slice (MVS) for Human Memory

The full spec describes the destination-state protocol. HM (Human Memory) is the first jointly-verified service. The following scopes what is needed vs. deferred.

**⚠️ Implementation scoping only — not a weakening of the standard.** The MVS defers operations that HM does not need at launch (e.g., linking, unlinking, reconciliation). The service contract itself is unchanged — every deferred operation remains **mandatory or recommended for third-party services** per §C.2. In particular: `POST /integration/revoke` is mandatory for ALL jointly-verified services regardless of MVS phase. Reconciliation (`GET /integration/grant-status`) is deferred for HM only because AC owns HM and can verify compliance directly — third-party services MUST implement it. The MVS is a rollout sequencing tool, not a trust-model carveout.

### 7.1 MVS: In Scope for HM

| Feature | Notes |
|---------|-------|
| `POST /integration/account-check` | HM implements this — returns `exists: false` for new users |
| `POST /integration/account-create` | HM creates namespaced storage for the human. `broker_delegated` binding. |
| `POST /integration/revoke` | Mandatory. HM denies requests for revoked `consent_ref`. |
| `human_service_accounts` table | Identity binding. Required for Gate 3. |
| `authorization_grants` table | Grant provenance. Required for revocation tracking. |
| `revocation_deliveries` table | Delivery tracking with retry logic. |
| `proxy_configs.integration_mode` | Column to mark HM actions as `jointly_verified`. |
| Card-level revocation semantics (§B.2) | Even for single policies — the mechanism is the same. |

### 7.2 MVS: Deferred Past HM

| Feature | Why deferred | When needed |
|---------|-------------|-------------|
| `service_integration_configs` table | HM config is hard-coded (see Artifact 0 §12.3) | Second jointly-verified service |
| Wizard onboarding extensions | No wizard needed for AC-owned service | Third-party onboarding |
| Endpoint path overrides | HM uses default `/integration/` paths | Services with custom routing |
| `POST /integration/link-complete` | HM has no existing users | Stripe / third-party |
| `POST /integration/unlink` | Dashboard feature, not launch blocker | Dashboard polish |
| `GET /integration/grant-status` | AC owns HM — no need to reconcile with ourselves | Third-party services |
| Capability negotiation (§C) | Hard-coded for HM | Wizard-driven onboarding |
| Reconciliation worker | AC trusts its own service | Third-party services |

### 7.3 HM-Specific Simplifications

- **Identity matching:** `opaque_id` only. No email/phone exchange needed — HM is AC-owned.
- **Account creation:** Always succeeds (HM creates a namespace keyed on `ac_human_id_hash`). No 409 conflicts, no linking fallback.
- **Revocation delivery:** Synchronous (inline) since HM is co-located. No async worker needed for launch.
- **No linking flow:** Every HM user is new. `account_check` → `exists: false` → `account_create` → `active`. Three calls, no redirects.

---

## 8. Open Items for Implementation

1. **Migration for `human_service_accounts`** — identity binding table. Required for HM MVS.
2. **Migration for `authorization_grants`** — grant provenance table. Required for HM MVS.
3. **Migration for `service_integration_configs`** — *Deferred past HM MVS.* Populated during wizard onboarding for third-party services.
4. **Migration for `revocation_deliveries`** — delivery tracking table. Required for HM MVS.
5. **Migration for `proxy_configs`** — add `integration_mode` column (TEXT, DEFAULT NULL). Required for HM MVS.
6. **Wizard extensions** — *Deferred past HM MVS.* New steps for identity matching, integration endpoints, path overrides, capability declaration. Must reject configs with no account lifecycle operations.
7. **Consent flow extensions in `consent.py`** — account-check + account-create integration into `/consents/<id>/approve`. Must create both `human_service_accounts` row and `authorization_grants` row atomically. Required for HM MVS. *Linking redirect deferred.*
8. **Dashboard extensions** — *Deferred past HM MVS.* "Linked Services" section with binding status, grant status, unlink button.
9. **Revocation delivery worker** — For HM MVS: synchronous delivery (inline during revoke call). For external services: async worker with retry logic, updating `authorization_grants.grant_status`.
10. **Reconciliation worker** — *Deferred past HM MVS.* Periodic task for `grant-status` polling on third-party services.
11. **Deferred binding background resolver** — *Deferred past HM MVS.* Resolves `deferred` bindings out-of-band.
12. **Linking callback endpoint** — *Deferred past HM MVS.* `GET /link-callback` in `pages.py`. MUST use `state` parameter (opaque token tied to consent session) for CSRF protection. Must validate return URL origin. Must be passkey-gated.
13. **SDK implementation** — the Reference SDK (Artifact 5) must include helpers for all service-side endpoints. HM is the first consumer and test bed for the SDK.
