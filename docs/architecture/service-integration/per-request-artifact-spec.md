# Per-Request Authorization Artifact Spec

**Date:** March 11, 2026
**Author:** Cascade
**Status:** Draft — for adversarial review
**Parent:** Service Integration Standard Briefing (approved)
**Implements:** Briefing Artifact 1
**Depends on:** Nothing (first in sequence)

---

## 1. Purpose

When AC proxies a request to a **jointly-verified** action (as declared by `integration_mode = 'jointly_verified'` on the action's `proxy_configs` row), the service must be able to independently verify:

1. **Who** — which human authorized this request
2. **What** — which action and scopes were authorized
3. **When** — that the authorization is current (not replayed)
4. **Which request** — that the artifact is bound to this specific request body
5. **Under what grant** — which consent or card authorized it

Today, AC sends only `req.inputs` as JSON body + the company's `backend_auth_header`. That proves "this request came through AC" but nothing else. This spec defines the per-request artifact that fills the gap.

**Standard-mode actions are unaffected.** The artifact is only attached when `integration_mode = 'jointly_verified'` on the action's `proxy_configs` row.

---

## 2. Artifact Format

The artifact is a signed JWT. It consists of a JOSE header and a claims payload.

### 2.1 JOSE Header

```json
{
  "alg": "RS256",
  "typ": "JWT",
  "kid": "<key-id>"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `alg` | Yes | Always `RS256`. No other algorithms are accepted. |
| `typ` | Yes | Always `JWT`. |
| `kid` | Yes | Key ID matching a key in AC's JWKS endpoint. Services use this to select the correct public key without parsing the full token first. Derived from SHA-256 thumbprint of the public key (first 16 hex chars), consistent with existing `keys.py` implementation. |

### 2.2 Claims

```json
{
  "iss": "agentcafe",
  "aud": "<service_id>",
  "sub": "<service_account_id>",
  "iat": 1710100000,
  "exp": 1710100030,
  "jti": "<unique-request-id>",
  "action": "<action_id>",
  "scopes": ["<service_id>:<action_id>"],
  "consent_ref": "<policy_id or card_id>",
  "ac_human_id_hash": "<sha256-of-ac-human-id>",
  "identity_binding": "broker_delegated",
  "request_hash": "<sha256-hex>",
  "standard_version": "1.0"
}
```

| Claim | Required | Type | Description |
|-------|----------|------|-------------|
| `iss` | Yes | string | Always `"agentcafe"`. |
| `aud` | Yes | string | The `service_id` from `proxy_configs`. Service MUST reject tokens where `aud` doesn't match. |
| `sub` | Yes | string | The human's account ID **on the service** (from `human_service_accounts.service_account_id`). This is the service's own identifier for the human, not AC's internal `ac_human_id`. |
| `iat` | Yes | number | Unix timestamp of artifact creation. |
| `exp` | Yes | number | Expiry. `iat + 30` seconds. Non-negotiable ceiling. The artifact authorizes a single proxied request — it is not a session token. |
| `jti` | Yes | string | Unique identifier for this artifact. UUIDv4. In v1.0, `jti` is used as both the artifact nonce and the audit `entry_id` (two-phase placeholder pattern in Artifact 0 §5.5). Services log this value alongside their own request logs to enable cross-boundary dispute resolution with AC's audit chain. See §9.1 for replay detection requirements. |
| `action` | Yes | string | The `action_id` being executed. |
| `scopes` | Yes | string[] | The authorized scopes, in `service_id:action_id` format. Matches the policy or card scope grant. In v1.0, this array always has exactly one element (the proxied action). The array format is retained for forward compatibility with future multi-action proxy batching under Company Cards. |
| `consent_ref` | Yes | string | The `policy_id` (for single-action consent) or `card_id` (for Company Card) that authorized this request. Enables audit correlation and revocation tracing. |
| `ac_human_id_hash` | Yes | string | SHA-256 hex digest of AC's internal `ac_human_id` (full 64 hex chars). Opaque — the service cannot reverse it. Ties the artifact back to the specific AC human who authorized the request. Services that stored this value during account-check/create (§A.2/A.3 in Artifact 2) MAY verify it matches the binding on file. Defense-in-depth: closes the "rogue AC proxy" vector where a compromised or buggy router signs an artifact for a different human than the Passport holder. **Design note:** Full hash (64 chars) is used rather than a truncated prefix. The `kid` field uses a 16-char prefix because it's a key selector (collision tolerance is fine). `ac_human_id_hash` is an identity correlator stored by services for cross-check — truncation would create a v1→v2 compatibility issue if the truncation length later proved insufficient. 32 extra bytes per artifact is acceptable. |
| `identity_binding` | Yes | string | How the human's identity was established on the service. One of: `broker_delegated` (AC created the account via delegated identity proofing), `service_native` (human linked an existing account via linking flow), `email_match` (AC matched by verified email). Stored in `human_service_accounts.identity_binding`. **Design note:** This is a static property of the binding — it never changes per-request. Including it avoids a service-side database lookup on every request to determine trust tier. The cost is ~20 bytes per artifact. |
| `request_hash` | Yes | string | SHA-256 hex digest of the canonical request material: HTTP method + normalized path + body (see §3). Binds the signature to the specific request, including its target endpoint. |
| `standard_version` | Yes | string | Protocol version. `"1.0"` for this spec. **Design note:** The service already declared its supported version during onboarding, so this is redundant in the happy path. It earns its bytes as a fail-safe: if AC ever routes a v2 artifact to a v1 service due to a config bug, the service can reject cleanly with `422` instead of silently misinterpreting unknown claims. Cost: 25 bytes. |

**Claims NOT included (by design):**

- **`correlation_id`.** In v1.0, `jti` serves as both artifact ID and audit correlation ID. AC's audit log entry uses the same `jti` value as its `entry_id`. A separate `correlation_id` claim would be redundant clutter. If these concepts need to diverge in a future version, a new claim can be added via version bump.
- **Human email or name.** The artifact carries `sub` (the service's own account ID), not AC's human identity. AC defaults to opaque ID. If the service needs the human's email, that was exchanged during account creation/linking — it is not repeated per-request. This is the ADR-024 exception under data minimization.
- **Agent identity.** Agents are ephemeral and non-verifiable (ADR-024). The artifact authorizes the bearer, not a specific agent.
- **Full inputs.** The request body travels alongside the artifact, not inside it. The `request_hash` binds them cryptographically.

---

## 3. Canonical Request Hashing

The `request_hash` claim binds the artifact to the specific request — including its target endpoint. This prevents an attacker from taking a valid artifact and replaying it against a different endpoint or with a different body.

### 3.1 Algorithm

**Method + normalized path + body bytes, concatenated, then SHA-256.**

```
request_hash = SHA-256(METHOD + "\n" + normalized_path + "\n" + body_bytes)
```

Where:
- `METHOD` is the uppercase HTTP method (`POST`, `GET`, etc.)
- `normalized_path` is the backend path from `proxy_configs.backend_path`, with trailing slashes removed (no case transformation)
- `body_bytes` is the exact byte sequence of the HTTP request body as sent by AC to the service
- `\n` (newline, `0x0A`) separates the components

**Why method + path, not just body:** If only the body is hashed, a captured artifact can be replayed against a different endpoint on the same service — as long as that endpoint accepts the same body shape. Binding the method and path into the hash closes this attack vector. The `action` claim provides a second layer of defense (see §6.1).

### 3.2 Why raw bytes, not RFC 8785

JSON canonicalization (RFC 8785) requires both parties to re-parse and re-serialize JSON identically. This introduces failure modes around:

- Key ordering across JSON libraries
- Unicode normalization (NFC vs. NFD)
- Floating-point representation (`1.0` vs. `1` vs. `1.00`)
- Whitespace handling (compact vs. pretty-printed)

Raw bytes avoids all of these for the body component. AC serializes `req.inputs` to JSON bytes (using Python's `json.dumps(req.inputs, sort_keys=True, separators=(',', ':'))`), hashes those bytes, and sends those same bytes as the request body. The service hashes the received body bytes. If the bytes match, the hashes match. No re-parsing required.

### 3.3 Path Normalization

The `normalized_path` is derived from `proxy_configs.backend_path` at AC’s signing time:

1. Take `backend_path` from the proxy config (e.g., `/api/v1/memory/store`)
2. Remove any trailing slash (`/api/v1/memory/store/` → `/api/v1/memory/store`)

The service applies the same normalization to the request path it receives. Both sides use the normalized form for hashing. This avoids mismatches from trailing slashes.

**No case transformation.** The path is used exactly as configured in `proxy_configs.backend_path` (modulo trailing slash). Lowercasing was considered but rejected: third-party services may have case-sensitive path segments (e.g., `/api/v1/Users/aBcDeF`), and lowercasing would cause `request_hash_mismatch` errors that are difficult to debug. The path in `proxy_configs` is the authoritative source — if it says `/api/v1/Memory/Store`, both AC and the service use exactly that.

**Path does NOT include query parameters.** Query parameters are not part of the canonical request material. If a future action relies on query parameters for business logic, those should be moved into the request body.

### 3.4 Implementation

**AC side (router.py):**
```python
import hashlib, json

method = backend_method.upper()  # from proxy_configs.backend_method
normalized_path = backend_path.rstrip("/")  # from proxy_configs.backend_path, no case change
body_bytes = json.dumps(req.inputs, sort_keys=True, separators=(',', ':')).encode('utf-8')

hash_input = f"{method}\n{normalized_path}\n".encode('utf-8') + body_bytes
request_hash = hashlib.sha256(hash_input).hexdigest()

# body_bytes is sent as the HTTP request body
# request_hash goes into the artifact claims
```

**Service side (validation):**
```python
import hashlib

method = request.method.upper()
normalized_path = request.path.rstrip("/")
received_body_bytes = request.body()  # raw bytes from HTTP request

hash_input = f"{method}\n{normalized_path}\n".encode('utf-8') + received_body_bytes
expected_hash = artifact_claims["request_hash"]
actual_hash = hashlib.sha256(hash_input).hexdigest()

if actual_hash != expected_hash:
    return 400, {"error": "request_hash_mismatch"}
```

### 3.5 Edge Cases

- **Empty body:** Hash input is `POST\n/api/v1/memory/store\n` (method + path + empty body). Valid.
- **Binary bodies:** If a future action sends non-JSON bodies, `request_hash` covers the raw bytes. The algorithm is encoding-agnostic.
- **Path mismatch between AC config and service routing:** If the service’s internal routing path differs from `backend_path` in `proxy_configs`, the hashes will not match. The service must normalize using the same path that AC was configured with. The SDK (§12) will handle this automatically.

---

## 4. Signing

### 4.1 Key Infrastructure

AC's existing `keys.py` (`PassportKeyManager`) already implements:

- RS256 signing with `kid` in JWT header
- JWKS endpoint serving public keys
- Dual-key rotation (current + previous)
- Key ID derived from SHA-256 thumbprint of DER-encoded public key

**Decision: Separate key pair for service artifacts.**

Passports and per-request artifacts are different token classes with different relying parties and different compromise surfaces. Reusing the same signing key expands blast radius for no compelling reason — a Passport key compromise would also compromise all service artifacts, and vice versa.

| Aspect | Passport keys | Artifact keys |
|--------|--------------|---------------|
| **Relying party** | AC itself (Passport validation in `router.py`) | External services |
| **Token lifetime** | Minutes to hours | 30 seconds |
| **Compromise impact** | Agent impersonation within AC | Forged human authorization on external services |
| **Rotation trigger** | AC ops decision | AC ops or service-requested |

**Implementation:** Extend `PassportKeyManager` (or create `ArtifactKeyManager` using the same pattern) to manage a second RS256 key pair. Both key pairs appear in the same JWKS endpoint but are distinguished by a `use` or `key_ops` field, or simply by `kid` prefix convention (e.g., `art_` prefix for artifact keys). Services select the correct key by matching the `kid` from the artifact header against JWKS entries.

The existing dual-key rotation infrastructure handles both key pairs independently. Rotating one does not affect the other.

### 4.2 Signing Flow

In `router.py`, after all gates pass and before the HTTP proxy call:

```python
from agentcafe.keys import get_artifact_key_manager
import jwt, time, uuid, hashlib, json

km = get_artifact_key_manager()  # separate key pair from Passport keys
entry = km.current_key

# Pre-generate jti — serves as both artifact ID and audit correlation ID
jti = str(uuid.uuid4())

# Compute request_hash: method + normalized path + body
method = backend_method.upper()
normalized_path = backend_path.rstrip("/")  # no case change — see §3.3
body_bytes = json.dumps(req.inputs, sort_keys=True, separators=(',', ':')).encode('utf-8')
hash_input = f"{method}\n{normalized_path}\n".encode('utf-8') + body_bytes
request_hash = hashlib.sha256(hash_input).hexdigest()

artifact_payload = {
    "iss": "agentcafe",
    "aud": req.service_id,
    "sub": service_account_id,       # from human_service_accounts lookup
    "iat": int(time.time()),
    "exp": int(time.time()) + 30,
    "jti": jti,                      # artifact ID + replay nonce + audit correlation
    "action": req.action_id,
    "scopes": [f"{req.service_id}:{req.action_id}"],
    "consent_ref": policy_id_or_card_id,
    "ac_human_id_hash": hashlib.sha256(ac_human_id.encode('utf-8')).hexdigest(),
    "identity_binding": binding_type,  # from human_service_accounts
    "request_hash": request_hash,
    "standard_version": "1.0",
}

artifact_token = jwt.encode(
    artifact_payload,
    entry.private_key,
    algorithm="RS256",
    headers={"kid": entry.kid},
)
```

### 4.3 Performance

RS256 signing is ~0.5ms per operation on modern hardware. For context, AC already signs Passport tokens with RS256 on every `/tokens/exchange` and `/tokens/refresh` call. Per-request signing adds one additional RS256 operation per proxied request to a jointly-verified action. This is well within acceptable latency for a proxy that already makes an outbound HTTP call (~10-500ms).

---

## 5. Delivery

### 5.1 Header

```
X-AgentCafe-Authorization: Bearer <artifact-jwt>
```

The artifact is delivered in a custom header, not the standard `Authorization` header — which continues to carry the company's `backend_auth_header` (the service's own API key or credentials).

### 5.2 What the service receives

For a jointly-verified action, the proxied request looks like:

```http
POST /api/v1/memory/store HTTP/1.1
Host: memory.example.com
Authorization: Bearer <service-api-key>
X-AgentCafe-Authorization: Bearer <artifact-jwt>
Content-Type: application/json

{"key": "meeting-notes", "value": "..."}
```

- `Authorization` — the service's own credential (from `proxy_configs.backend_auth_header`, decrypted)
- `X-AgentCafe-Authorization` — the per-request artifact (signed by AC)
- Body — the agent's `inputs` dict, serialized as JSON (same bytes used for `request_hash`)

### 5.3 Standard-mode actions

Standard-mode actions (`integration_mode = 'standard'`) do NOT receive the `X-AgentCafe-Authorization` header. The proxy behavior is unchanged — `inputs` + `backend_auth_header` only.

---

## 6. Service Validation Rules

### 6.1 MUST check (mandatory)

Services MUST reject the request if any of these fail:

| Check | Expected | Failure response |
|-------|----------|------------------|
| Header present | `X-AgentCafe-Authorization` exists and starts with `Bearer ` | `401 Unauthorized` |
| JWT signature | Valid RS256 signature against AC's JWKS public key (artifact key, not Passport key) | `401 Unauthorized` |
| `kid` match | `kid` in JWT header matches an artifact key in AC's JWKS | `401 Unauthorized` |
| `iss` | `"agentcafe"` | `403 Forbidden` |
| `aud` | Matches the service's own `service_id` | `403 Forbidden` |
| `exp` | Not expired (`exp > now`) | `401 Unauthorized` |
| `iat` | Not in the future (`iat <= now + 5s`). Rejects artifacts with clock skew. 5s tolerance for network/clock drift. | `401 Unauthorized` |
| `sub` | Matches a known, service-owned account ID on the service | `403 Forbidden` |
| `action` | Matches the action/endpoint being called | `403 Forbidden` |
| `request_hash` | SHA-256 of method + normalized path + received body bytes matches claim | `400 Bad Request` |
| `jti` uniqueness (mutating actions) | `jti` has not been seen before within a 60s tracking window. **MUST** for any action that mutates state (writes, charges, deletes). See §9.1. | `409 Conflict` |
| `standard_version` | Service supports this version | `422 Unprocessable Entity` |

### 6.2 SHOULD check (recommended)

| Check | Purpose |
|-------|--------|
| `iat` not too old | `iat` should be within 35s of `now` (30s TTL + 5s tolerance). Catches artifacts that are technically unexpired but suspiciously old. |
| `jti` uniqueness (read-only actions) | Replay detection for non-mutating endpoints. SHOULD track, but not required because replaying a read has no side effects. |
| `scopes` include the required scope | Defense-in-depth. AC already enforces scopes, but services may have their own scope model. |
| `ac_human_id_hash` matches stored value | Defense-in-depth. Verify the artifact was signed for the same AC human who established the binding. Catches rogue-proxy or key-compromise scenarios. |

### 6.3 MAY use (informational)

| Claim | Usage |
|-------|-------|
| `jti` | Audit correlation. Log alongside the service's own request log. Same value appears in AC's audit chain as `entry_id`. |
| `consent_ref` | Audit trail. Enables tracing back to the specific policy or Company Card that authorized this request. |
| `ac_human_id_hash` | Cross-check against the value stored during account-check/create. If mismatched, indicates a binding integrity issue. |
| `identity_binding` | Trust-level decisions. A `broker_delegated` account may warrant different limits than `service_native`. |

---

## 7. Key Distribution

Services need AC's public keys to verify artifact signatures. This section defines how AC publishes keys and how services consume them.

### 7.1 JWKS Endpoint

**URL:** `https://agentcafe.io/.well-known/jwks.json`

This endpoint serves both Passport keys and artifact keys. The key pairs are distinct (§4.1). The endpoint returns:

```json
{
  "keys": [
    {
      "kty": "RSA",
      "use": "sig",
      "alg": "RS256",
      "kid": "art_<current-artifact-key-id>",
      "n": "<modulus-base64url>",
      "e": "<exponent-base64url>"
    },
    {
      "kty": "RSA",
      "use": "sig",
      "alg": "RS256",
      "kid": "art_<previous-artifact-key-id>",
      "n": "<modulus-base64url>",
      "e": "<exponent-base64url>"
    },
    {
      "kty": "RSA",
      "use": "sig",
      "alg": "RS256",
      "kid": "psp_<current-passport-key-id>",
      "n": "<modulus-base64url>",
      "e": "<exponent-base64url>"
    }
  ]
}
```

Services select the correct key by matching the `kid` from the artifact JWT header. The `art_` prefix convention makes it easy to filter, but `kid` matching is the authoritative selection mechanism. The existing `PassportKeyManager.jwks()` method is extended to include both key sets.

**AC-side rate limiting:** The JWKS endpoint is public and unauthenticated (standard for JWKS). AC MUST rate-limit this endpoint to prevent cache-refresh storms from malicious clients sending fake `kid` values. Recommended: 60 requests per IP per minute, with `Cache-Control: public, max-age=3600` header. This is sufficient for legitimate key rotation discovery while preventing abuse.

**Dedicated service-facing endpoint (recommended):** For operational isolation, AC SHOULD expose a second JWKS endpoint at `GET /integration/jwks.json` that returns the same key set but with stricter rate limiting (10/min per IP) and per-service request logging. Services would be directed to this endpoint during onboarding. This is an **operational hygiene** improvement — not a security requirement, since JWKS public keys are public by design and cannot be used to forge signatures. The benefit is monitoring: AC can detect anomalous JWKS fetch patterns from specific services independently from general `.well-known` traffic. For HM MVS, the shared `/.well-known/jwks.json` endpoint is sufficient. The dedicated endpoint is recommended before third-party onboarding.

**Service-side caching:** Services MUST cache JWKS responses and only re-fetch when encountering an unknown `kid`. Services SHOULD NOT poll JWKS on a schedule — fetch on cache miss only. The SDK (§12) handles this automatically.

### 7.2 Key Rotation

AC's `PassportKeyManager` supports dual-key rotation:

1. New key pair is generated or loaded
2. Current key becomes previous
3. New key becomes current
4. JWKS endpoint serves both keys
5. After a grace period, the previous key is dropped

**Rotation signaling:** AC does not push rotation events. Services discover new keys by re-fetching JWKS when a `kid` is not found in their cache. To prevent an attacker from triggering unlimited JWKS fetches by sending artifacts with fake `kid` values, services MUST enforce a **fetch cooldown** — at most one JWKS re-fetch per 60 seconds. If a `kid` is not found and the cooldown has not elapsed, reject the artifact immediately.

**Recommended service behavior on unknown `kid`:**
1. Check local cache
2. If `kid` not found, fetch JWKS from AC (at most once per 60s to prevent abuse)
3. If `kid` still not found after refresh, reject with `401`

### 7.3 Cache TTL Recommendations

| Context | Recommended TTL | Rationale |
|---------|----------------|-----------|
| Service-side JWKS cache | 300s (5 minutes) | Balances freshness with request volume. AC rotates keys infrequently (manual operation). |
| Retry on unknown `kid` | At most 1 refresh per 60s | Prevents a malicious artifact with a fake `kid` from triggering a JWKS fetch storm. |
| Grace period after rotation | 3600s (1 hour) minimum | Previous key stays in JWKS for at least 1 hour after rotation. In practice, much longer — dual-key stays until the next rotation. |

### 7.4 JWKS Endpoint Unavailability

If the JWKS endpoint is unreachable:

- **Service has cached keys:** Continue verifying with cached keys. Log a warning. Retry JWKS fetch on next cache expiry.
- **Service has no cached keys:** Reject all requests with `503 Service Unavailable` and a `Retry-After` header. Do NOT accept unverified artifacts.
- **Never fall back to unverified mode.** An unreachable JWKS endpoint is not a reason to skip verification.

---

## 8. Error Response Format

When artifact validation fails, the service SHOULD return errors in this format for consistency with AC's error contract:

```json
{
  "error": "<error_code>",
  "message": "<human-readable description>",
  "jti": "<from artifact, if parseable>"
}
```

| Error code | HTTP status | Meaning |
|------------|-------------|---------|
| `artifact_missing` | 401 | `X-AgentCafe-Authorization` header not present |
| `artifact_invalid` | 401 | JWT signature verification failed |
| `artifact_expired` | 401 | `exp` claim is in the past |
| `artifact_audience_mismatch` | 403 | `aud` doesn't match this service |
| `artifact_issuer_mismatch` | 403 | `iss` is not `"agentcafe"` |
| `artifact_subject_unknown` | 403 | `sub` doesn't match any known account |
| `request_hash_mismatch` | 400 | Body hash doesn't match `request_hash` claim |
| `artifact_version_unsupported` | 422 | `standard_version` not supported |
| `artifact_replay_detected` | 409 | `jti` has been seen before within TTL window |

AC's proxy (`router.py`) will map these error codes to agent-facing errors. The agent never sees the raw service error — AC translates it into the standard error contract (defined in Artifact 0).

---

## 9. Security Considerations

### 9.1 Replay Protection

Replay protection is layered:

1. **30s TTL** — limits the window for any replay attack
2. **`request_hash`** (method + path + body) — prevents using a captured artifact against a different endpoint or body
3. **`action` binding** (MUST check) — prevents using an artifact for action A against action B's endpoint
4. **`jti` tracking** — catches exact replays (same artifact, same endpoint, same body) within the TTL window
5. **`aud` binding** — prevents using an artifact for Service A against Service B

**`jti` replay detection requirements:**

| Action type | `jti` tracking | Rationale |
|-------------|---------------|----------|
| **Mutating** (writes, charges, deletes) | **MUST** — reject duplicate `jti` within 60s window | An exact replay of a mutating action causes real harm: double-writes, double-charges, double-deletes. 30s TTL is not replay protection; it is a shorter replay window. |
| **Read-only** (queries, lookups) | **SHOULD** | Replaying a read has no side effects beyond resource consumption. Services SHOULD track for defense-in-depth but are not required to. |

The service determines whether an action is mutating based on its own semantics. The SDK (§12) provides a configuration option to mark endpoints as mutating, which enables automatic `jti` enforcement.

### 9.2 Man-in-the-Middle

- Artifacts are signed with RS256 — tampering is detectable
- `request_hash` covers the body, so body tampering is detectable
- TLS between AC and the service is assumed (and should be required for production)
- The artifact does NOT cover HTTP headers other than the body — if a service relies on other headers for business logic, those are not integrity-protected by the artifact

### 9.3 Key Compromise

If AC's signing key is compromised:

1. Attacker can forge arbitrary artifacts for any service
2. Mitigation: rotate the key immediately (generate new key pair, old becomes previous, then drop)
3. 30s TTL limits the blast radius of any single forged artifact
4. Services that track `jti` will catch exact replays but not novel forged artifacts
5. AC must notify affected services out-of-band (mechanism: TBD, likely part of the service contract)

### 9.4 Data Minimization (ADR-024 Exception)

The artifact carries `sub` (service account ID) — not the human's email or name. The service already knows this account ID because it was established during the account linking/creation flow. The artifact does not introduce new identity information per-request; it confirms the binding that was established at consent time.

This is the constrained exception to ADR-024's principle of "enforcement without broadcasting identity." The artifact confirms *which* authorized account is making this request, using the service's own identifier. It does not broadcast AC's internal human identity.

---

## 10. Stripe Thought Experiment (Q13 Validation)

To validate this spec isn't HM-specific, consider Agent Payments via Stripe:

| Claim | HM value | Stripe value | Works? |
|-------|----------|-------------|--------|
| `aud` | `human-memory` | `agent-payments` | ✅ |
| `sub` | HM internal UUID | Stripe Customer ID (`cus_xxx`) | ✅ |
| `action` | `memory:store` | `payments:charge` | ✅ |
| `scopes` | `["human-memory:store"]` | `["agent-payments:charge"]` | ✅ |
| `consent_ref` | policy_id | card_id (Company Card with spending limit) | ✅ |
| `identity_binding` | `broker_delegated` (AC created HM account) | `service_native` (human linked existing Stripe account) | ✅ |
| `ac_human_id_hash` | SHA-256 of AC human ID (same for both) | SHA-256 of AC human ID (same for both) | ✅ |
| `request_hash` | SHA-256 of `POST\n/api/v1/memory/store\n{"key":"notes",...}` | SHA-256 of `POST\n/api/v1/payments/charge\n{"amount":1500,...}` | ✅ |

**No modifications needed.** The artifact format works for Stripe without changes. The `identity_binding` claim correctly distinguishes between AC-created accounts (HM) and human-linked accounts (Stripe). The `consent_ref` correctly references either a policy or a Company Card.

**Stripe-specific observation:** A payments endpoint (charge, transfer) is mutating — `jti` replay detection is MUST per §6.1 and §9.1. This is enforced by the spec, not a service-level decision.

---

## 11. Migration & Backward Compatibility

### 11.1 No Migration Required

This is net-new functionality. No existing service receives the `X-AgentCafe-Authorization` header today. Standard-mode actions continue unchanged.

### 11.2 Rollout

1. AC implements artifact signing in `router.py` for jointly-verified actions
2. Service implements artifact validation (using SDK or directly)
3. Service registers as jointly-verified during onboarding
4. First proxied request includes the artifact

There is no gradual rollout needed — the artifact is either present (jointly-verified) or absent (standard). No existing integration is affected.

---

## 12. Minimum Viable Slice (MVS) for Human Memory

The full spec is the destination state. For HM (first jointly-verified service):

**⚠️ Implementation scoping only — not a weakening of the standard.** The MVS defers operational features that HM does not need at launch. The artifact format itself is NOT simplified — every claim is present from day one. Third-party services MUST implement the full validation rules (§6.1) regardless of when they onboard. The MVS is a rollout sequencing tool, not a trust-model carveout.

### 12.1 MVS: In Scope

| Feature | Notes |
|---------|-------|
| All claims including `ac_human_id_hash` | Full artifact from day one — no claim subsetting |
| `ArtifactKeyManager` + separate key pair | Security non-negotiable |
| JWKS endpoint with `art_` / `psp_` prefixes | Services need the keys |
| JWKS rate limiting (AC-side) | Prevents abuse of public endpoint |
| `request_hash` (method + path + body) | Integrity binding from day one |
| `jti` replay detection for mutating actions | HM `memory:store` / `memory:delete` are mutating |
| SDK `verify_artifact()` helper | HM is the first consumer — the SDK is tested here |

### 12.2 MVS: Deferred

| Feature | Why |
|---------|-----|
| `jti` deduplication for read actions | SHOULD, not MUST. HM reads are idempotent. |
| Scope-drift detection | Over-engineering for a single-service deployment |
| Key compromise notification mechanism | TBD for third-party services. HM is AC-owned. |

**The artifact itself is not simplified for MVS.** Every claim is present from day one. The MVS savings come from the proxy and service contract layers (see Artifact 0 §12, Artifact 2 §7).

---

## 13. Open Items for Implementation

1. **`ArtifactKeyManager`** — implement a second key manager instance (or extend `PassportKeyManager`) for the artifact signing key pair. Must support independent dual-key rotation.
2. **JWKS endpoint** — extend `/.well-known/jwks.json` to serve both Passport and artifact public keys, distinguished by `kid` prefix. Add AC-side rate limiting (§7.1): 60 req/IP/min, `Cache-Control: public, max-age=3600`.
3. **`human_service_accounts` + `authorization_grants` tables** — must exist before artifact can be populated (dependency on Service Contract spec, Artifact 2).
4. **Two-phase audit log** — the `jti` is generated before signing, used as the audit `entry_id`. Artifact 0 §5.5 specifies placeholder-then-finalize pattern with crash recovery. Implementation must maintain hash chain integrity.
5. **`ac_human_id_hash` computation** — SHA-256 of `ac_human_id` string, hex-encoded. Must be computed from the same `ac_human_id` used for Gate 3 lookup. Service MAY verify it matches the value stored during account-check/create.
6. **SDK validation helpers** — Artifact 5 (Reference SDK) will provide `verify_artifact()` that handles JWKS fetching with cooldown caching, `kid` selection, signature verification, `request_hash` recomputation (method + normalized path + body), and all MUST checks from §6.1 including `jti` deduplication for mutating endpoints.
7. **`request_hash` path agreement** — the SDK must accept the expected `backend_path` as configuration so the service can recompute the hash using the same normalized path that AC used. Misconfigured paths will cause `request_hash_mismatch` errors.
