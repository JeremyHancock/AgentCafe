# Proxy Behavior, State Machine & Failure Modes Spec

**Date:** March 11, 2026
**Author:** Cascade
**Status:** Locked and implemented (March 25, 2026). Code: `cafe/router.py` (Gates 3-4), `cafe/artifact.py`, `cafe/binding.py`. Tests: `tests/test_service_integration.py` (45 tests).
**Parent:** Service Integration Standard Briefing (approved)
**Implements:** Briefing Artifact 0
**Depends on:** Nothing (first in sequence, alongside Artifact 1)

---

## 1. Purpose

This spec defines how `router.py` orchestrates the **jointly-verified proxy path** — the end-to-end flow from agent request through account resolution, artifact signing, proxying, and error handling. It is the "how" document that turns the Per-Request Artifact Spec and the Service Contract into implementable router logic.

**Scope:** This spec is the **authoritative owner of the state machine** and all proxy-time behavior (what happens when an agent calls `POST /cafe/order` for a jointly-verified action). The Service Contract spec (Artifact 2) defines the endpoint contracts that **drive transitions** in this state machine during consent-time and revocation flows, but does not independently define states. If there is a conflict between the state machine here and a state referenced in Artifact 2, this spec wins.

---

## 2. Integration Mode Detection

When `POST /cafe/order` arrives, the router looks up `proxy_configs` for the `service_id + action_id` pair. The existing query (line ~128 of `router.py`) must be extended to include:

```sql
SELECT backend_url, backend_path, backend_method, backend_auth_header,
       scope, human_auth_required, rate_limit, risk_tier,
       human_identifier_field, quarantine_until, suspended_at,
       integration_mode
FROM proxy_configs
WHERE service_id = ? AND action_id = ?
```

| `integration_mode` | Behavior |
|---------------------|----------|
| `standard` (default, NULL) | Current proxy path. No changes. |
| `jointly_verified` | Extended path defined in this spec. |

**NULL defaults to standard.** All existing `proxy_configs` rows have no `integration_mode` column today. The migration adds it with a default of `NULL`, which the router treats as `'standard'`. Zero impact on existing services.

---

## 3. State Machine

The state machine tracks the lifecycle of a human's relationship with a jointly-verified action on a service, from first consent through active use to revocation.

### 3.1 States

```
┌─────────────────────────────────────────────────────────────┐
│                    CONSENT-TIME STATES                       │
│                                                             │
│  consent_initiated ──→ account_checked ──┬──→ account_created ──→ active
│                              │           │                         │
│                              │           ├──→ link_pending ──→ link_complete ──→ active
│                              │           │                                       │
│                              │           └──→ active (account already linked)    │
│                              │                                                   │
│                              └──→ service_unreachable ──→ consent_deferred       │
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                    PROXY-TIME STATES (binding)                                   │
│                                                                                  │
│  active ──→ (proxy request) ──→ success / backend_error                          │
│                                                                                  │
│  authorized_but_unlinked ──→ ACCOUNT_LINK_REQUIRED error to agent               │
│                                                                                  │
│  consent_deferred ──→ SERVICE_SETUP_PENDING error (fail closed)                │
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                    GRANT REVOCATION (authorization_grants)                        │
│                                                                                  │
│  active ──→ revoke_queued ──→ revoke_delivered ──→ revoke_honored              │
│                    │                                                              │
│                    └──→ (delivery failed, retry with backoff, stays revoke_queued)│
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                    TERMINAL / ERROR STATES                                        │
│                                                                                  │
│  account_creation_failed    (service rejected account creation)                   │
│  link_expired               (linking code expired before completion)              │
│  consent_abandoned          (human dropped out mid-flow)                          │
│  partial_failure            (account created but link storage failed)             │
│  reconcile_failed           (service says grant is not honored)                   │
│  unlinked                   (human explicitly unlinked via dashboard)             │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 State Storage

States span **two tables** (defined in Service Contract spec, Artifact 2):

**Identity states** — `human_service_accounts.binding_status`:

| State | `binding_status` value | Notes |
|-------|----------------------|-------|
| `consent_initiated` | Row does not exist yet | Consent in progress, no account binding |
| `account_checked` | Transient (not persisted) | Mid-consent, account check response received |
| `account_created` | `active` | Account created, binding complete |
| `link_pending` | `link_pending` | Linking flow initiated, awaiting completion |
| `link_complete` | `active` | Linking completed |
| `active` | `active` | Normal operating state |
| `authorized_but_unlinked` | No row, but policy/card exists | Policy/card authorized but no binding exists |
| `consent_deferred` | `deferred` | Consent approved, service was unreachable |
| `unlinked` | `unlinked` | Human explicitly unlinked |

**Grant states** — `authorization_grants.grant_status`:

| State | `grant_status` value | Notes |
|-------|---------------------|-------|
| `active` | `active` | Grant is live. Artifacts can be issued. |
| `revoke_queued` | `revoke_queued` | Human revoked. Delivery pending. No new artifacts. |
| `revoke_delivered` | `revoke_delivered` | Service acknowledged receipt (2xx). NOT verified. |
| `revoke_honored` | `revoke_honored` | Reconciliation confirmed service is denying. Terminal. |

**Key insight:** A human can have an `active` binding with a service while having revoked grants. The binding tracks identity ("who is this human on the service?"), grants track authorization ("what are they allowed to do?"). Gate 3 checks both.

### 3.3 State Transitions

| From | Event | To | Side effects |
|------|-------|----|-------------|
| (none) | Human approves consent | `consent_initiated` | Consent record created |
| `consent_initiated` | AC calls account-check, service responds | `account_checked` | Transient |
| `account_checked` | Service says "no account" + human is new | `account_created` → `active` | AC calls account-create, stores binding |
| `account_checked` | Service says "account exists" + linking URL | `link_pending` | Redirect human to service linking page |
| `account_checked` | Service says "account exists" + already linked | `active` | Binding already in table |
| `consent_initiated` | Service unreachable during account-check | `consent_deferred` | Consent approved on AC side, account ops deferred |
| `link_pending` | Service returns linking code | `link_complete` → `active` | AC stores binding with `service_account_id` |
| `link_pending` | Linking code expires (60s) | `link_expired` | Human must retry |
| `link_pending` | Human abandons linking page | `consent_abandoned` | Consent record marked abandoned |
| `active` (binding) | Human unlinks via dashboard | `unlinked` (binding) | AC calls `POST /integration/unlink` |
| `unlinked` (binding) | Human re-initiates consent | `consent_initiated` | New linking flow |
| `consent_deferred` (binding) | Proxy request arrives | `SERVICE_SETUP_PENDING` (503) | Fail closed. Background resolver handles setup. See §5.2.3 |
| `consent_deferred` (binding) | Background resolver succeeds | `active` (binding) | Account created/linked out-of-band |
| `consent_deferred` (binding) | Background resolver finds grant revoked | (binding unchanged) | Skip resolution. Grant already `revoke_queued`. |

**Grant transitions** (on `authorization_grants`):

| From | Event | To | Side effects |
|------|-------|----|-------------|
| `active` (grant) | Human revokes policy/card on AC | `revoke_queued` (grant) | Revocation webhook queued. AC immediately stops issuing new artifacts for this `consent_ref`. |
| `revoke_queued` (grant) | Service returns 2xx with `acknowledged: true` | `revoke_delivered` (grant) | Delivery receipt confirmed. Service claims it will stop honoring, but AC has not verified. |
| `revoke_queued` (grant) | Delivery fails (timeout / 5xx) | `revoke_queued` (grant, unchanged) | Retry tracked in `revocation_deliveries` table. |
| `revoke_delivered` (grant) | Reconciliation confirms `status: revoked` | `revoke_honored` (grant) | Service verified to be actually denying requests. Terminal success state. |
| `revoke_delivered` (grant) | Reconciliation finds `status: active` | `revoke_delivered` (grant) | **Revocation not honored.** Re-push revocation. Alert admin if persistent. |

---

## 4. Standard-Mode Proxy Path (Unchanged)

For reference, the current `POST /cafe/order` flow for `integration_mode = 'standard'` (or NULL):

```
Agent → POST /cafe/order
  → Gate 0: Suspension/quarantine check
  → Gate 1: Passport JWT validation
  → Gate 1b: Identity verification (medium+ risk)
  → Gate 2: Company policy validation (service live, inputs valid, types match)
  → Rate limiting
  → Proxy to backend (inputs + backend_auth_header)
  → Audit log
  → Return response to agent
```

**This path is not modified.** All existing behavior is preserved. The jointly-verified path branches after the `integration_mode` check.

---

## 5. Jointly-Verified Proxy Path

### 5.1 Gate Sequence

```
Agent → POST /cafe/order
  → Gate 0: Suspension/quarantine check           [UNCHANGED]
  → Gate 1: Passport JWT validation               [UNCHANGED]
  → Gate 1b: Identity verification (medium+ risk) [UNCHANGED]
  → Gate 2: Company policy validation              [UNCHANGED]
  → Rate limiting                                  [UNCHANGED]
  → *** integration_mode check ***
  → Gate 3: Account binding resolution             [NEW]
  → Gate 4: Per-request artifact signing           [NEW]
  → Proxy to backend (inputs + backend_auth_header + X-AgentCafe-Authorization)
  → Audit log (with jti as entry_id)
  → Return response to agent
```

Gates 0–2 and rate limiting are identical. The jointly-verified path adds Gate 3 (account binding) and Gate 4 (artifact signing) before the proxy call.

### 5.2 Gate 3: Account Binding Resolution

After rate limiting passes, if `integration_mode = 'jointly_verified'`:

```python
# Extract human identity from the validated passport
passport_claims = decode_passport_token(req.passport)

# IMPORTANT: Passport sub is "user:{email}", not ac_human_id directly.
# Must resolve to cafe_users.id for the binding lookup.
passport_sub = passport_claims["sub"]  # e.g., "user:alice@example.com"
email = passport_sub.removeprefix("user:")
cursor = await db.execute(
    "SELECT id FROM cafe_users WHERE email = ?", (email,)
)
user_row = await cursor.fetchone()
if not user_row:
    raise HTTPException(status_code=403, detail={"error": "HUMAN_NOT_FOUND"})
ac_human_id = user_row["id"]  # UUIDv4 — matches human_service_accounts.ac_human_id

# consent_ref: for card tokens, both policy_id and card_id are set to the card_id.
# For policy tokens, only policy_id is set. Either way, policy_id is always present.
consent_ref = passport_claims["policy_id"]

# Step 1: Check identity binding (human_service_accounts)
cursor = await db.execute(
    """SELECT service_account_id, binding_status, identity_binding
       FROM human_service_accounts
       WHERE ac_human_id = ? AND service_id = ?""",
    (ac_human_id, req.service_id),
)
binding = await cursor.fetchone()

# Step 2: Check grant status (authorization_grants)
cursor = await db.execute(
    """SELECT grant_status FROM authorization_grants
       WHERE consent_ref = ? AND service_id = ?""",
    (consent_ref, req.service_id),
)
grant = await cursor.fetchone()
```

**Six checks (evaluated in order):**

#### 5.2.1 Grant revoked → `GRANT_REVOKED`

Check grant status first — even if the binding is active, a revoked grant means no artifacts:

```python
if grant and grant["grant_status"] in ("revoke_queued", "revoke_delivered", "revoke_honored"):
    raise HTTPException(
        status_code=403,
        detail={
            "error": "GRANT_REVOKED",
            "message": "This authorization has been revoked. A new policy or card is needed.",
            "category": "human_action",
            "consent_ref": consent_ref,
        },
    )
```

#### 5.2.1b Grant missing or not active → `GRANT_NOT_FOUND`

If the binding exists and is active but no valid grant row exists, reject. This catches orphaned bindings (grant row never created, or deleted by admin cleanup):

```python
elif binding and binding["binding_status"] == "active" and (not grant or grant["grant_status"] != "active"):
    raise HTTPException(
        status_code=403,
        detail={
            "error": "GRANT_NOT_FOUND",
            "message": "Your account is linked but no active authorization was found. A new policy or card is needed.",
            "category": "human_action",
            "consent_ref": consent_ref,
        },
    )
```

#### 5.2.2 Binding exists and is active + grant active → proceed to Gate 4

```python
elif binding and binding["binding_status"] == "active":
    # grant is guaranteed active here (5.2.1b rejected all other cases)
    service_account_id = binding["service_account_id"]
    identity_binding = binding["identity_binding"]
    # → Gate 4: sign artifact with these values
```

#### 5.2.3 Binding exists but is deferred → fail closed

If `binding_status = 'deferred'` (consent was approved while the service was unreachable):

```python
elif binding and binding["binding_status"] == "deferred":
    # Do NOT attempt account creation/linking inline during a proxy request.
    # Deferred resolution is a background concern, not a proxy-time concern.
    raise HTTPException(
        status_code=503,
        detail={
            "error": "SERVICE_SETUP_PENDING",
            "message": "Account setup with this service is still pending. Please try again shortly.",
            "category": "retry",
            "retry_after_seconds": 30,
        },
    )
```

**Deferred resolution happens out-of-band**, not inline during proxy requests. A background task periodically attempts to resolve deferred bindings by calling account-check/create on the service. When the service becomes reachable and the binding moves to `active`, subsequent proxy requests proceed normally.

The background resolver MUST check that the `consent_ref` grant is still `active` in `authorization_grants` before attempting resolution. If revoked, it skips resolution entirely.

#### 5.2.4 Binding exists but is unlinked → `BINDING_INACTIVE`

```python
elif binding and binding["binding_status"] == "unlinked":
    raise HTTPException(
        status_code=403,
        detail={
            "error": "BINDING_INACTIVE",
            "message": "Your account link with this service is inactive. Please relink.",
            "category": "human_action",
            "human_action_url": f"https://agentcafe.io/link/{req.service_id}?consent_ref={consent_ref}",
            "consent_ref": consent_ref,
        },
    )
```

#### 5.2.5 No binding exists → `ACCOUNT_LINK_REQUIRED`

The human has a valid policy or card that authorizes this action, but no account binding exists for this service. This is the **authorized_but_unlinked** state.

```python
else:
    raise HTTPException(
        status_code=403,
        detail={
            "error": "ACCOUNT_LINK_REQUIRED",
            "message": (
                "Your authorization is valid, but your account has not been linked "
                "with this service yet. Please complete account setup."
            ),
            "human_action_url": f"https://agentcafe.io/link/{req.service_id}?consent_ref={consent_ref}",
            "consent_ref": consent_ref,
        },
    )
```

The `human_action_url` directs the human to AC's linking flow for this service. The agent can present this URL to the human.

### 5.3 Gate 4: Per-Request Artifact Signing

Once the account binding is resolved:

```python
import hashlib, json, time, uuid
from agentcafe.keys import get_artifact_key_manager
import jwt as pyjwt

# Pre-generate jti — serves as artifact ID, replay nonce, and audit correlation ID
jti = str(uuid.uuid4())

# Compute request_hash: method + normalized path + body
method = backend_method.upper()
normalized_path = backend_path.rstrip("/")  # no case change — see Artifact 1 §3.3
body_bytes = json.dumps(req.inputs, sort_keys=True, separators=(',', ':')).encode('utf-8')
hash_input = f"{method}\n{normalized_path}\n".encode('utf-8') + body_bytes
request_hash = hashlib.sha256(hash_input).hexdigest()

km = get_artifact_key_manager()  # separate key pair from Passport keys
entry = km.current_key

artifact_payload = {
    "iss": "agentcafe",
    "aud": req.service_id,
    "sub": service_account_id,
    "iat": int(time.time()),
    "exp": int(time.time()) + 30,
    "jti": jti,
    "action": req.action_id,
    "scopes": [f"{req.service_id}:{req.action_id}"],
    "consent_ref": consent_ref,
    "ac_human_id_hash": hashlib.sha256(ac_human_id.encode('utf-8')).hexdigest(),
    "identity_binding": identity_binding,
    "request_hash": request_hash,
    "standard_version": "1.0",
}

artifact_token = pyjwt.encode(
    artifact_payload,
    entry.private_key,
    algorithm="RS256",
    headers={"kid": entry.kid},
)
```

### 5.4 Proxy Call (Modified)

The proxy call is extended to include the artifact header and use the pre-serialized body bytes:

```python
headers = {"Content-Type": "application/json"}
if backend_auth_header:
    headers["Authorization"] = backend_auth_header
headers["X-AgentCafe-Authorization"] = f"Bearer {artifact_token}"

# Use body_bytes directly (same bytes used for request_hash)
resp = await client.post(target_url, content=body_bytes, headers=headers)
```

**Important:** The body bytes sent to the service MUST be the same bytes used to compute `request_hash`. This is why we serialize once and reuse. Because we use `content=body_bytes` (raw bytes) instead of `json=req.inputs`, httpx will NOT auto-set `Content-Type`. We set it explicitly.

**Serialization difference from standard mode:** The existing standard-mode proxy uses `json=req.inputs` (httpx's default serializer, which may differ in whitespace and key ordering from `json.dumps(sort_keys=True, separators=(',',':'))`). Jointly-verified mode uses `content=body_bytes` with explicit canonical serialization. This means identically-valued inputs will produce different byte sequences on the wire between standard and jointly-verified modes. This is intentional and correct.

**⚠️ Jointly-verified mode uses deterministic canonical serialization (`sort_keys=True, separators=(',',':')`) for `request_hash` integrity. Standard-mode uses httpx's default serializer. Services MUST NOT rely on exact JSON whitespace or key order for business logic.**

**Artifact verification scope:** The per-request artifact is verified **service-side only**, using the JWKS public keys. AC's `decode_passport_token()` function (in `keys.py`) is NOT used for artifact verification — it hardcodes `audience="agentcafe"` and uses Passport keys. Artifacts have `aud=<service_id>` and use separate artifact keys. The SDK (Artifact 5) provides `verify_artifact()` for service-side verification.

### 5.5 Audit Log (Modified)

The `jti` is generated before the proxy call (§5.3) and doubles as the audit log `entry_id`. For jointly-verified actions, the audit write is **split into two phases** to prevent a crash between signing and audit-write from leaving the `jti` pointing to a non-existent row:

```python
# PHASE 1: Write placeholder BEFORE proxy call
await _audit_log_placeholder(
    db, req, entry_id=jti, status="pending",
)

# ... proxy call happens here ...

# PHASE 2: Update placeholder with outcome AFTER proxy call
await _audit_log_finalize(
    db, entry_id=jti, outcome=outcome,
    status_code=resp.status_code, latency_ms=latency_ms,
)
```

**Why two-phase:** The existing `_audit_log()` writes after the proxy response, which is fine for standard-mode actions (no external party references the entry_id). For jointly-verified actions, the service receives the `jti` in the artifact *before* the audit row exists. If AC crashes between signing and audit-write, the `jti` in the service's logs points to nothing in AC's audit chain — breaking cross-boundary dispute resolution.

**Placeholder row:** Contains `entry_id`, `request` metadata, `status="pending"`, and a valid `prev_hash` for the hash chain. The finalize step updates `status`, `outcome`, `response_code`, and `latency_ms`. The hash chain integrity is preserved because the placeholder participates in the chain at write time.

**Crash recovery:** If AC crashes after placeholder but before finalize, the row exists with `status="pending"`. A startup recovery task scans for stale `pending` entries (older than 60s) and marks them `status="crashed"`. The `jti` still resolves in the audit chain — it just shows the request was interrupted.

For standard-mode actions, the current single-write behavior is unchanged.

---

## 6. Error Handling & Agent-Facing Error Contract

### 6.1 Principle

Agents never see raw service errors for account lifecycle failures. AC translates service-side errors into a standard set of agent-facing error shapes. The agent can distinguish between:

- **Retry later** — transient failure, try again
- **Human action needed** — the human must do something (link account, re-consent)
- **Permanent failure** — this request cannot succeed

### 6.2 Standard Error Codes

| Error code | HTTP | Category | Agent should... |
|------------|------|----------|----------------|
| `GRANT_REVOKED` | 403 | Human action | Authorization has been revoked. Human needs a new policy or card. |
| `GRANT_NOT_FOUND` | 403 | Human action | Account is linked but no active authorization exists. Human needs a new policy or card. |
| `ACCOUNT_LINK_REQUIRED` | 403 | Human action | Present `human_action_url` to the human |
| `ACCOUNT_CREATION_FAILED` | 502 | Retry/escalate | Retry once, then inform human |
| `SERVICE_SETUP_PENDING` | 503 | Retry | Retry after `retry_after_seconds` |
| `SERVICE_UNREACHABLE` | 502 | Retry | Retry after `retry_after_seconds` |
| `REVOKED_BY_SERVICE` | 403 | Human action | The service has revoked this grant. Human must re-consent. |
| `QUOTA_EXCEEDED` | 429 | Retry | Service-side quota hit. Retry after `retry_after_seconds`. |
| `ARTIFACT_REJECTED` | 502 | Escalate | The service rejected AC's authorization artifact. This is an AC-side issue. |
| `BINDING_INACTIVE` | 403 | Human action | Account link is inactive (unlinked/migrated). Human must relink. |

### 6.3 Error Response Shape

All jointly-verified errors follow this shape:

```json
{
  "error": "ACCOUNT_LINK_REQUIRED",
  "message": "Your authorization is valid, but your account has not been linked with this service yet.",
  "category": "human_action",
  "human_action_url": "https://agentcafe.io/link/human-memory?consent_ref=pol_abc123",
  "consent_ref": "pol_abc123",
  "retry_after_seconds": null,
  "correlation_id": "a1b2c3d4-..."
}
```

| Field | Always present | Description |
|-------|---------------|-------------|
| `error` | Yes | Machine-readable error code |
| `message` | Yes | Human-readable description |
| `category` | Yes | One of: `retry`, `human_action`, `permanent`, `escalate` |
| `human_action_url` | When `category = human_action` | URL for the human to resolve the issue |
| `consent_ref` | When relevant | The policy/card reference |
| `retry_after_seconds` | When `category = retry` | Seconds before retry is likely to succeed |
| `correlation_id` | When available | Request-level debug ID (UUIDv4). For pre-proxy errors (Gate 3), this is a fresh UUID. For post-proxy errors (§6.4), this is the artifact `jti` if available. **Not** the same as the revocation `correlation_id` (prefixed `rev_`) in Artifact 2 §B.2. |

### 6.4 Service Error Translation

When the proxied request to a jointly-verified service returns an error, the router translates it:

| Service returns | Router translates to |
|----------------|---------------------|
| `401` with `artifact_invalid` or `artifact_expired` | `ARTIFACT_REJECTED` (502) — AC's artifact was rejected, which is an AC-side issue |
| `403` with `artifact_audience_mismatch` | `ARTIFACT_REJECTED` (502) — configuration error |
| `403` with `artifact_subject_unknown` | `BINDING_INACTIVE` (403) — the service doesn't recognize the account |
| `400` with `request_hash_mismatch` | `ARTIFACT_REJECTED` (502) — body/hash mismatch, AC-side issue |
| `429` (any) | `QUOTA_EXCEEDED` (429) — pass through `retry_after_seconds` |
| `5xx` (any) | `SERVICE_UNREACHABLE` (502) with retry |
| `2xx` | Normal success response, pass through |
| Other `4xx` | Pass through as-is (business logic errors, not lifecycle errors) |

---

## 7. Consent-Time Flow (High-Level)

The detailed consent-time protocol is defined in Artifact 2 (Service Contract). This section provides the high-level flow for context on how the state machine is entered.

### 7.1 Standard Consent (Current — Unchanged)

```
Agent → POST /consents/initiate
Human → GET /authorize/{consent_id} → reviews → approves
Agent → POST /tokens/exchange → gets Passport token
```

### 7.2 Jointly-Verified Consent

```
Agent → POST /consents/initiate (for a jointly-verified action)
  AC checks: does a binding exist for this human + service?

  Case A: No binding, service reachable
    → AC calls POST /integration/account-check on service
    → Service responds: "no account" or "account exists + linking URL"
    → If no account:
        Human approves on AC → AC calls POST /integration/account-create → binding created → active
    → If existing account:
        Human redirected to service linking page → linking code → binding created → active

  Case B: No binding, service unreachable
    → Human approves on AC → consent_deferred
    → Background resolver retries until service is reachable
    → Proxy requests fail closed with SERVICE_SETUP_PENDING until resolved

  Case C: Binding already exists and is active
    → Standard consent flow (human approves, token issued)
    → No account lifecycle needed

Agent → POST /tokens/exchange → gets Passport token
```

### 7.3 Consent Dropout Modeling

The jointly-verified consent flow has 3–5 steps vs. 1 for standard consent. Dropout is modeled at each transition:

| Step | Dropout triggers | Recovery |
|------|-----------------|----------|
| Human sees consent page | Closes page | `consent_abandoned`. Agent gets `consent_pending` on status check. |
| Human redirected to service linking | Doesn't complete linking | `link_expired` after 60s. Agent gets `consent_pending`. Human can retry. |
| Service linking page | Service error during linking | `partial_failure`. AC logs. Human can retry from AC dashboard. |
| Human returns to AC after linking | Doesn't click "Approve" | `consent_abandoned`. Linking code expires. |

**Mitigation: Deferred operations.** For Case A (new account), consider deferring account creation to after human approval: the human approves on AC first (1 click, same as standard), then AC creates the account asynchronously. The first proxy request confirms the account exists. This reduces the human-facing ceremony to 1 step for new accounts.

For Case A (existing account requiring linking), the redirect is unavoidable — the human must authenticate with the service. This is the irreducible complexity of jointly-verified mode for services with existing user bases.

---

## 8. Idempotency

### 8.1 Proxy-Time Idempotency

The per-request artifact is unique per request (`jti` is a UUIDv4). There is no proxy-time idempotency concern — each request gets a fresh artifact.

If the agent retries a failed request (e.g., timeout), it sends a new `POST /cafe/order` with the same inputs. AC issues a new artifact with a new `jti`, `iat`, and `exp`. The service sees it as a new request. This is correct behavior — the agent is retrying, not replaying.

### 8.2 Consent-Time Idempotency

Consent-time operations (account-check, account-create, link) have idempotency requirements defined in the Service Contract spec (Artifact 2). The key invariant: **if AC times out after the service created the account, retrying account-create must return the existing account, not create a duplicate.**

### 8.3 Revocation Idempotency

Revocation delivery is idempotent on the `consent_ref`. If AC sends `POST /integration/revoke` twice with the same `consent_ref`, the service must accept both (returning success) and maintain the deny state. See Service Contract spec §B.

---

## 9. Race Conditions

### 9.1 Link Completion vs. Revocation

**Scenario:** Human initiates linking (state: `link_pending`). While the human is on the service's linking page, they revoke the consent on AC's dashboard.

**Resolution:** Revocation wins. When the linking flow completes and returns a linking code, AC checks whether the consent is still valid. If revoked, AC discards the code and does not create the binding. The state moves to `consent_abandoned`, not `active`.

### 9.2 Account Creation Timeout vs. Retry

**Scenario:** AC calls `POST /integration/account-create`. The service creates the account and sends a 200 response, but AC times out before receiving it. AC retries.

**Resolution:** Account creation is idempotent on the identity claim. The service recognizes the identity claim matches an existing account and returns the existing `service_account_id`. AC creates the binding. No duplicate account.

### 9.3 Concurrent Consent for Same Human + Service

**Scenario:** Two agents simultaneously initiate consent for the same human on the same service (different actions, or the human has two browser tabs open).

**Resolution:** The `human_service_accounts` table has a unique constraint on `(ac_human_id, service_id)`. The first consent flow to complete creates the binding. The second finds the binding already exists (Case C in §7.2) and proceeds without account lifecycle operations. The unique constraint prevents duplicate bindings.

### 9.4 Proxy Request During Linking

**Scenario:** Human has a Company Card for the service. An agent sends `POST /cafe/order` while the human is mid-linking flow (`link_pending`).

**Resolution:** Gate 3 (§5.2) finds no `active` binding. Returns `ACCOUNT_LINK_REQUIRED`. The agent informs the human. The human completes the linking flow, then the agent retries.

### 9.5 Revocation During Proxy Request

**Scenario:** Human revokes consent while AC is mid-proxy to the service (artifact already signed, request in flight).

**Resolution:** The artifact has 30s TTL. The revocation webhook fires asynchronously. The in-flight request completes (the service hasn't received the revocation yet). The next request finds the grant in `revoke_queued` state at Gate 3 (§5.2.1) and is rejected with `GRANT_REVOKED`. The revocation webhook eventually reaches the service, which denies future requests with this `consent_ref`. Short TTL + revocation push + reconciliation provide defense in depth. The identity binding remains `active` — only the grant is revoked.

### 9.6 Revocation During Deferred Binding Resolution

**Scenario:** Consent was approved while the service was unreachable (binding state: `deferred`). The human then revokes the policy/card on AC (grant moves to `revoke_queued`). The background resolver picks up the deferred binding for resolution.

**Resolution:** The background resolver MUST check that the grant's `grant_status` is still `active` in `authorization_grants` before attempting account-check/create. If revoked, the resolver skips resolution entirely and leaves the binding in `deferred` state. The grant is already `revoke_queued` and the revocation delivery worker handles pushing that to the service. The resolver must never create an account on a service for a grant that the human has already revoked.

---

## 10. Proxy Configs Schema Change

### 10.1 Migration

```sql
ALTER TABLE proxy_configs ADD COLUMN integration_mode TEXT DEFAULT NULL;
```

`NULL` = standard mode. Existing rows are unaffected. The wizard sets this per-action during onboarding.

### 10.2 Values

| Value | Meaning |
|-------|---------|
| `NULL` or `'standard'` | Current behavior. No artifact, no account binding. |
| `'jointly_verified'` | Extended path. Artifact signing, account binding resolution. |

---

## 11. Stripe Thought Experiment

The proxy behavior spec works for Stripe without modification:

- **Gate 3 (binding resolution):** AC looks up `human_service_accounts` for the human + `agent-payments` service. `service_account_id` = Stripe Customer ID (`cus_xxx`). `identity_binding` = `service_native` (human linked their existing Stripe account).
- **Gate 4 (artifact signing):** Artifact `sub` = `cus_xxx`, `aud` = `agent-payments`, `action` = `payments:charge`. Stripe verifies the artifact against AC's JWKS.
- **`ACCOUNT_LINK_REQUIRED`:** If the human has a Company Card for payments but hasn't linked their Stripe account, the agent gets this error. The human clicks the link, authenticates with Stripe, and the linking code flows back to AC.
- **Consent dropout:** Stripe's OAuth flow (linking) requires the human to authenticate on Stripe's site — the same redirect pattern as any jointly-verified service with existing users. The deferred-operations optimization doesn't help here because the link requires human action on Stripe's side.

**No modifications needed.**

---

## 12. Minimum Viable Slice (MVS) for Human Memory

The full spec describes the destination-state protocol. **HM (Human Memory) is the first jointly-verified service.** The following scopes what is needed for HM vs. what is deferred.

**⚠️ Implementation scoping only — not a weakening of the standard.** The MVS defers features that HM does not need at launch (e.g., reconciliation, linking flow, dashboard unlink). The standard itself is unchanged — every deferred feature remains **mandatory for third-party services** that declare the corresponding capability. A third-party service onboarding after HM MUST implement the full protocol as specified above. The MVS is a rollout sequencing tool, not a trust-model carveout.

### 12.1 MVS: In Scope for HM

| Feature | Why needed |
|---------|-----------|
| Gate 3 (binding resolution) | Core — every proxy request needs identity |
| Gate 4 (artifact signing) | Core — the entire point of jointly-verified mode |
| `active` + `deferred` + `unlinked` binding states | Minimum identity lifecycle |
| `active` + `revoke_queued` + `revoke_delivered` grant states | Minimum revocation lifecycle |
| Two-phase audit log (§5.5) | Required for cross-boundary correlation |
| `GRANT_REVOKED`, `GRANT_NOT_FOUND`, `BINDING_INACTIVE`, `ACCOUNT_LINK_REQUIRED`, `SERVICE_SETUP_PENDING` errors | Agent must know what happened |
| Revocation delivery (push + retry) | Non-negotiable for trust |
| Consent-time account-check + account-create | HM uses `broker_delegated` (AC creates accounts) |

### 12.2 MVS: Deferred Past HM

| Feature | Why deferred | When needed |
|---------|-------------|-------------|
| `revoke_honored` state + reconciliation worker | HM is AC-owned — we trust our own service to honor revocations. Reconciliation is for third-party services. | Stripe / third-party onboarding |
| Linking flow (A.4, A.5) | HM has no existing users — everyone is new via `account_create`. No linking needed. | Third-party services with existing user bases |
| Unlinking + relinking | HM accounts are AC-managed. Unlinking is a dashboard UX feature, not a protocol requirement for launch. | Dashboard polish phase |
| Deferred binding background resolver | HM is co-located — if it's unreachable, AC is unreachable. Deferred state is unlikely. | External services with independent uptime |
| Grant-status reconciliation endpoint | AC owns HM — no need to poll ourselves | Third-party services |
| `service_integration_configs` table | HM capabilities are hard-coded in AC (no wizard onboarding needed) | Second jointly-verified service |
| Endpoint path overrides | HM uses default `/integration/` paths | Services with existing routing |
| Company Card fan-out | Cards not yet implemented | Company Cards phase |

### 12.3 MVS: Hard-Coded HM Configuration

Instead of the full wizard + `service_integration_configs` table, HM is configured directly in code:

```python
HM_CONFIG = {
    "service_id": "human-memory",
    "integration_mode": "jointly_verified",
    "integration_base_url": "http://localhost:8001",  # co-located
    "identity_matching": "opaque_id",
    "capabilities": {
        "account_check": True,
        "account_create": True,
        "link_complete": False,
        "unlink": False,
        "revoke": True,
        "grant_status": False,
    },
}
```

This avoids the `service_integration_configs` migration and wizard extensions for launch. When the second service is onboarded, the config moves to the database.

---

## 13. Open Items for Implementation

1. **Two-phase `_audit_log()` (§5.5):** Split into `_audit_log_placeholder()` (before proxy) and `_audit_log_finalize()` (after proxy) for jointly-verified actions. Must maintain hash chain integrity. Add crash recovery for stale `pending` entries.
2. **`human_service_accounts` + `authorization_grants` tables:** Must be created (migration) before Gate 3 can function. Schema defined in Service Contract spec.
3. **`ArtifactKeyManager`:** Separate key pair for artifact signing. See Artifact 1 §4.1.
4. **Deferred binding background resolver:** *Deferred past HM MVS.* Background task that periodically resolves `deferred` bindings. Must check grant validity before resolution.
5. **Linking flow integration in consent.py:** The consent approval flow must be extended for jointly-verified actions: call account-check, create account if new. *Linking redirect deferred past HM.*
6. **Dashboard extensions:** *Deferred past HM MVS.* "Linked Services" section with binding status, grant status, unlink button.
7. **Company Card interaction:** *Deferred past HM MVS.* When deployed, binding is shared across all actions on a service.
8. **`authorized_but_unlinked` detection for Company Cards:** *Deferred past HM MVS.*
9. **Linking callback endpoint security (Grok):** `GET /link-callback` MUST use a `state` parameter (opaque token tied to the consent session) to prevent CSRF. The `state` is generated when AC redirects to the linking URL and verified on callback. Must also be gated behind an active human session (passkey-authenticated). *Not needed for HM MVS (no linking flow).*
10. **`human_action_url` endpoint:** The URLs returned in `ACCOUNT_LINK_REQUIRED` and `BINDING_INACTIVE` errors (e.g., `https://agentcafe.io/link/{service_id}`) do not exist yet. Must be implemented in `pages.py` with passkey-gated session. *Can return a helpful error page for HM MVS; full linking UI deferred.*
11. **Revocation worker infrastructure:** No background worker exists in the current codebase. For HM MVS, revocation delivery can be synchronous (inline during the revoke API call) since HM is co-located. For external services, a proper async worker is needed — likely a `lifespan` event polling loop, consistent with AC's existing async architecture.
