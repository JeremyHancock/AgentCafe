# Service Integration — Onboarding Guide

**Date:** March 25, 2026
**Audience:** Service teams integrating with AgentCafe's jointly-verified mode
**Prerequisite:** Read the three canonical specs (Artifact 0, 1, 2) and complete the configuration agreement template.

This guide supplements the specs with practical implementation guidance, examples, and patterns learned from the first integration (Human Memory).

---

## 1. Identity Binding Lifecycle

The most common source of confusion during onboarding is understanding how a human's identity flows through the system. This diagram shows the complete lifecycle — from account creation through artifact validation.

### Data flow: How `ac_human_id_hash` becomes a proxied request

```
CONSENT TIME (happens once per human+service)
==============================================

Human approves consent on AC
        |
        v
AC computes ac_human_id_hash = SHA-256(human.ac_user_id)
        |
        v
AC calls POST /integration/account-create
  {
    "ac_human_id_hash": "a1b2c3d4...",    <-- stable identity correlator
    "consent_ref": "pol_abc123"             <-- ties to policy or card
  }
        |
        v
Service creates account, returns service_account_id
  {
    "service_account_id": "ns_67890",       <-- service's internal ID
    "created": true
  }
        |
        v
AC stores binding:
  human_service_accounts:
    ac_human_id  = "user_123"               <-- AC's internal user ID
    service_id   = "human-memory"
    service_account_id = "ns_67890"         <-- from service response
    ac_human_id_hash   = "a1b2c3d4..."      <-- for future lookups

AC stores grant:
  authorization_grants:
    consent_ref  = "pol_abc123"             <-- from passport
    service_id   = "human-memory"
    grant_status = "active"


PROXY TIME (happens on every request)
======================================

Agent calls POST /cafe/order
        |
        v
Gate 1: Validate Passport JWT
        |
        v
Gate 3: Resolve identity binding
  - Extract ac_human_id from Passport sub claim
  - Look up human_service_accounts → get service_account_id
  - Look up authorization_grants  → verify grant_status = "active"
        |
        v
Gate 4: Sign per-request artifact (RS256, 30s TTL)
  {
    "sub": "ns_67890",                      <-- service_account_id from binding
    "aud": "human-memory",                  <-- service_id
    "action": "store",                      <-- from proxy_configs
    "scopes": ["human-memory:store"],
    "consent_ref": "pol_abc123",            <-- from Passport
    "ac_human_id_hash": "a1b2c3d4...",      <-- identity correlator
    "request_hash": "e5f6g7h8...",          <-- SHA-256 of method+path+body
    "exp": 1711411230,                      <-- now + 30s
    "jti": "art_uuid"                       <-- replay nonce
  }
        |
        v
AC proxies to service with two headers:
  Authorization: Bearer <service-credential>
  X-AgentCafe-Authorization: Bearer <artifact-jwt>
        |
        v
Service validates artifact:
  1. Verify RS256 signature against JWKS
  2. Check exp, iss, aud, standard_version
  3. Check action, scopes
  4. Verify request_hash matches actual request
  5. Check jti not replayed
  6. Resolve sub → internal namespace/account
  7. Process request in that namespace


REVOCATION (happens when human revokes)
========================================

Human revokes policy/card on AC
        |
        v
AC transitions grant: active → revoke_queued
        |
        v
AC calls POST /integration/revoke
  {
    "consent_ref": "pol_abc123",
    "revoked_at": "2026-03-25T...",
    "reason": "human_revoked",
    "correlation_id": "rev_uuid"
  }
        |
        v
Service stores consent_ref in revoked set
  → rejects future artifacts with this consent_ref
  → responds { "acknowledged": true }
        |
        v
AC transitions grant: revoke_queued → revoke_delivered
        |
        v
Backstop: 30s artifact TTL expires
  → even without revocation delivery, new artifacts stop being issued
```

### Key invariants

- `ac_human_id_hash` is a **full 64-char SHA-256 hex digest**. It never changes for a given human.
- `service_account_id` is **opaque to AC**. AC stores it but never interprets it.
- Bindings outlive grants. Revoking a policy removes the grant but keeps the binding. Re-consenting creates a new grant without re-calling `account-create`.
- The artifact `sub` claim always contains `service_account_id`, never AC's internal user ID.

---

## 2. Opaque ID Mode

When `identity_matching = 'opaque_id'` (the default), AC does not share the human's email, phone, or any personally identifiable information with the service. The `identity_claim` field is **omitted entirely** from integration endpoint requests.

### `POST /integration/account-check` — opaque_id mode

**Request:**
```json
{
  "standard_version": "1.0",
  "ac_human_id_hash": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
  "consent_ref": "pol_abc123"
}
```

Note: No `identity_claim` field. The `ac_human_id_hash` is the sole identity correlator.

**Response — no account:**
```json
{
  "exists": false
}
```

**Response — account exists:**
```json
{
  "exists": true,
  "service_account_id": "ns_67890"
}
```

### `POST /integration/account-create` — opaque_id mode

**Request:**
```json
{
  "standard_version": "1.0",
  "ac_human_id_hash": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
  "consent_ref": "pol_abc123"
}
```

**Response — created:**
```json
{
  "service_account_id": "ns_67890",
  "created": true
}
```

**Response — already exists (idempotent):**
```json
{
  "service_account_id": "ns_67890",
  "created": false
}
```

**Idempotency key:** `ac_human_id_hash`. If AC retries after a timeout, the service returns the existing account.

### Contrast: `email` mode

When `identity_matching = 'email'`, the request includes `identity_claim`:

```json
{
  "standard_version": "1.0",
  "identity_claim": {
    "type": "email",
    "value": "alice@example.com",
    "verified": true
  },
  "ac_human_id_hash": "a1b2c3d4...",
  "consent_ref": "pol_abc123"
}
```

The service uses `identity_claim.value` to match or create the account. `ac_human_id_hash` is still present as a stable correlator but is secondary to the email for account matching.

---

## 3. Brokered Account Guidance

Services in `opaque_id` mode receive accounts without human-facing identity (no email, no phone). This section describes how to handle these accounts cleanly.

### Recommended: First-class brokered account type

The recommended pattern is to introduce a distinct account type for AC-brokered accounts:

```sql
-- Schema addition
ALTER TABLE accounts ADD COLUMN account_type TEXT NOT NULL DEFAULT 'direct';
-- 'direct' = created via service's own registration (has email, password, etc.)
-- 'ac_brokered' = created via AC's integration endpoint (no email, no password)

ALTER TABLE accounts ALTER COLUMN email DROP NOT NULL;
-- Or: make email nullable only when account_type = 'ac_brokered'
```

**Why this matters:**
- Brokered accounts have no email, no password, and no direct login. They exist only in the AC auth path.
- Making this explicit prevents features that assume email (notifications, password reset, admin lookup) from silently failing.
- The `ac_human_id_hash` is the stable identifier — it should be the account's primary key or indexed lookup field, not a derived synthetic email.
- Admin tooling can distinguish brokered accounts at a glance.

**Account creation in opaque_id mode:**
```python
async def handle_account_create(request: AccountCreateRequest) -> AccountCreateResponse:
    # Look up by ac_human_id_hash (idempotent)
    existing = await find_account_by_ac_hash(request.ac_human_id_hash)
    if existing:
        return AccountCreateResponse(service_account_id=existing.id, created=False)

    # Create brokered account — no email required
    account = await create_account(
        account_type="ac_brokered",
        ac_human_id_hash=request.ac_human_id_hash,
        email=None,  # explicitly null, not synthetic
    )
    return AccountCreateResponse(service_account_id=account.id, created=True)
```

### Fallback: Synthetic identifier (legacy systems only)

If the service **truly cannot modify its schema** to make email nullable (e.g., legacy database with CHECK constraints that cannot be altered), a synthetic identifier is acceptable as a last resort:

```python
# FALLBACK ONLY — use first-class brokered accounts when possible
synthetic_email = f"ac_{ac_human_id_hash[:16]}@ac.internal"
```

**If using this fallback:**
- Document it prominently in the service's codebase
- Add a `is_synthetic` or `account_type` flag to distinguish these accounts in queries
- Ensure no code path attempts to send email to `@ac.internal` addresses
- Plan to migrate to the recommended pattern when schema changes become feasible

### Scope isolation

Brokered accounts should not be eligible for:
- Direct login (password or passkey)
- PAT issuance (the service's own token system)
- Password reset or email verification flows

They exist only in the AC-brokered auth path. Enforce this with a check in the relevant endpoints:

```python
if account.account_type == "ac_brokered":
    raise HTTPException(403, "AC-brokered accounts cannot use direct auth")
```

---

## 4. Testing Patterns

### JWKS test helper

Services need to validate artifacts against AC's JWKS in production, but unit tests shouldn't require a running AC instance. The pattern:

```python
class JWKSFetcher:
    """Production: fetches from AC's /.well-known/jwks.json with caching."""

    def _inject_key(self, kid: str, public_key) -> None:
        """Test helper: inject a key directly, bypassing HTTP fetch."""
        self._cache[kid] = public_key
        self._cache_expiry = time.time() + 3600  # long TTL for tests

    def _clear(self) -> None:
        """Test helper: reset all cached keys."""
        self._cache.clear()
        self._cache_expiry = 0
```

Test setup:
```python
# Generate a test key pair
from cryptography.hazmat.primitives.asymmetric import rsa
private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

# Inject the public key into the JWKS fetcher
jwks_fetcher._inject_key("art_test", private_key.public_key())

# Sign test artifacts with the private key
artifact = sign_test_artifact(private_key, kid="art_test", claims={...})

# Validate as normal — fetcher returns the injected key
result = artifact_validator.validate(artifact, request)
```

### JTI replay guard test helper

```python
class ReplayGuard:
    def _clear(self) -> None:
        """Test helper: reset seen JTIs."""
        self._seen.clear()
```

### Request hash in tests

When testing artifact validation end-to-end, the `request_hash` must match the actual request body. Use the same canonical serialization:

```python
import hashlib
import json

def compute_test_request_hash(method: str, path: str, body: dict) -> str:
    body_bytes = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    canonical = f"{method.upper()}\n{path.rstrip('/')}\n".encode() + body_bytes
    return hashlib.sha256(canonical).hexdigest()
```

### Dual auth regression testing

When adding artifact auth, run all existing PAT-path tests unchanged. The `X-AgentCafe-Authorization` header's absence should route to the PAT path with zero behavior changes. HM achieved this with 234 PAT tests passing unmodified after adding artifact auth.

---

## 5. Checklist

Use this checklist to track onboarding progress. Order follows the recommended implementation sequence.

- [ ] **Configuration agreement** — Complete `onboarding-configuration-template.md` with AC team
- [ ] **Schema migration** — Add tables for AC bindings and revoked consent refs
- [ ] **Config** — Add `AC_SERVICE_KEY`, `AC_JWKS_URL`, `AC_JWKS_CACHE_TTL_SECONDS` to service config
- [ ] **JWKS fetcher** — Singleton with caching (300s TTL, 60s cooldown on unknown kid)
- [ ] **JTI replay guard** — In-memory dict with TTL >= artifact max lifetime (60s recommended)
- [ ] **Service credential auth** — Validate `Authorization: Bearer <key>` on integration endpoints
- [ ] **`POST /integration/account-create`** — Idempotent on `ac_human_id_hash`, returns `service_account_id`
- [ ] **`POST /integration/revoke`** — Store `consent_ref` in revoked set, respond `{ "acknowledged": true }`
- [ ] **Artifact validation** — All MUST checks from Artifact 1 §6.1 (11 checks, see validation table)
- [ ] **Dual auth middleware** — Route on `X-AgentCafe-Authorization` header presence
- [ ] **Scope mapping** — Map `{service_id}:{action_id}` scopes to service-internal scopes
- [ ] **End-to-end test with AC** — AC proxies a real request, service validates artifact, returns data
- [ ] **Credential exchange** — Generate and share service credential with AC team
- [ ] **Go live** — AC creates `proxy_configs` rows, both teams verify in production

---

## References

- **Artifact 0:** `proxy-behavior-state-machine-spec.md` — How `router.py` orchestrates the proxy path
- **Artifact 1:** `per-request-artifact-spec.md` — Artifact format, signing, validation (especially §6.1 MUST table and §8 error codes)
- **Artifact 2:** `service-contract-identity-binding-protocol.md` — Integration endpoints, identity binding, revocation
- **Configuration template:** `onboarding-configuration-template.md` — Fill this out first
- **HM Phase 2 Q&A:** `phase-2-answers-for-hm.md` — Example of a completed configuration agreement (pre-template)
