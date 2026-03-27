# Service Integration — Configuration Agreement Template

**Purpose:** Collaborative worksheet for onboarding a new jointly-verified service. Both AC and the service team fill this out together before implementation begins. Replaces ad-hoc Q&A rounds.

**Instructions:** Fill in all sections. Items marked **(AC fills)** are determined by the AgentCafe team. Items marked **(Service fills)** are determined by the service team. Items marked **(Agree together)** require both teams.

---

## 1. Service Identity

| Field | Value | Notes |
|-------|-------|-------|
| `service_id` | **(Agree together)** | Format: `{brand}-{category}`. Appears in `proxy_configs`, artifact `aud` claim, and scope prefixes. Permanent once published. |
| Display name | **(Service fills)** | Human-readable name shown on Menu and consent pages. |
| Category | **(Service fills)** | e.g., `memory`, `payments`, `banking`, `communication` |

---

## 2. Action Registry

One row per agent-facing operation. All jointly-verified services must declare every proxied action.

| Operation | `action_id` | `backend_method` | `backend_path` | `integration_mode` | `risk_tier` | `human_auth_required` |
|-----------|------------|-----------------|---------------|--------------------|-----------|-----------------------|
| **(Service fills)** | **(Agree together)** | **(Service fills)** | **(Service fills)** | `jointly_verified` | **(Agree together)** | **(Agree together)** |
| | | | | | | |
| | | | | | | |
| | | | | | | |
| | | | | | | |

**Scopes** are derived automatically: `{service_id}:{action_id}` (e.g., `human-memory:store`).

**`backend_path` rules:**
- Exact path as the service exposes it (no version prefix unless the service uses one)
- Path parameters use `{param}` syntax (e.g., `/accounts/{account_id}/balance`)
- The `request_hash` computation uses the resolved path (after parameter substitution), not the template

---

## 3. Identity Matching & Account Creation

| Field | Value | Notes |
|-------|-------|-------|
| `identity_matching` | **(Agree together)** | `opaque_id` (default, privacy-preserving) or `email` (service matches by verified email). |
| `has_direct_signup` | **(Service fills)** | `true` if humans can create accounts directly at the service (outside AC). `false` if accounts are only created through AC. Determines consent-time account creation strategy (see ADR-032). |

**If `opaque_id`:**
- AC sends `ac_human_id_hash` as the sole identity correlator
- `identity_claim` field is **omitted** from integration endpoint requests
- Service MUST accept accounts without human-facing identity (email/phone)
- See "Account Creation Strategy" in `onboarding-guide.md`

**If `email`:**
- AC sends `identity_claim: { type: "email", value: "...", verified: true }` on account-check/create
- Service matches on email address
- Consent page discloses email sharing to the human

**If `has_direct_signup: true`:**
- AC's consent flow asks the human "Do you already have a {Service} account?" before account creation
- Service MUST implement the linking flow (§A.4–A.5) to support existing-account binding
- Service MUST implement `account-check` so AC can verify the human's answer
- See "Account Creation Strategy" in `onboarding-guide.md` for full details

---

## 4. Credential Exchange

| Field | Value | Notes |
|-------|-------|-------|
| Credential type | **(Service fills)** | Typically: static Bearer token with timing-safe comparison |
| Same credential for proxy + integration? | **(Agree together)** | Permitted for MVS (spec §2.2). Separate credentials recommended for production. |
| Credential provisioning | **(Service fills)** | e.g., "HM generates, shares out-of-band with AC" |
| AC storage | **(AC fills)** | Encrypted in `proxy_configs.backend_auth_header` (AES-256-GCM) |

**AC sends two headers on every jointly-verified proxied request:**
```
Authorization: Bearer <service-credential>
X-AgentCafe-Authorization: Bearer <artifact-jwt>
```

---

## 5. Integration Endpoints

Declare which endpoints the service implements. See Service Contract (Artifact 2) for full specs.

| Endpoint | Implemented? | Notes |
|----------|-------------|-------|
| `POST /integration/account-check` | **(Service fills)** | Recommended. If absent, AC skips check and calls account-create directly. |
| `POST /integration/account-create` | **(Service fills)** | Recommended. Required for delegated account provisioning. |
| `POST /integration/link-complete` | **(Service fills)** | Only for services with existing user bases (OAuth/redirect linking). |
| `POST /integration/unlink` | **(Service fills)** | Optional. Enables human-initiated account unlinking from AC dashboard. |
| `POST /integration/revoke` | **(Service fills)** | Strongly recommended. Receives revocation push from AC. |
| `GET /integration/grant-status` | **(Service fills)** | Optional. Used for periodic reconciliation (deferred for co-located services). |

**Minimum viable set:** `account-create` + `revoke` covers most MVS integrations.

---

## 6. Artifact Validation (Service-Side)

These values are determined by AC. The service hard-codes them in artifact validation.

| Parameter | Value |
|-----------|-------|
| Expected `aud` claim | `"{service_id}"` |
| Expected `iss` claim | `"agentcafe"` |
| JWKS URL | `https://agentcafe.io/.well-known/jwks.json` |
| Artifact header | `X-AgentCafe-Authorization` |
| Algorithm | `RS256` |
| `kid` convention | `art_`-prefixed (match on exact `kid`, not prefix) |
| Max TTL | 30 seconds (reject if `exp` has passed) |
| `standard_version` claim | `"1.0"` |
| Valid `action` values | *(from Section 2 above)* |
| Valid `scopes` entries | *(derived: `{service_id}:{action_id}` for each action)* |

---

## 7. Timeline and Dependencies

| Milestone | Owner | Target | Notes |
|-----------|-------|--------|-------|
| Configuration agreement signed off | Both | | This document |
| Credential generated and shared | Service | | Out-of-band |
| `proxy_configs` rows created | AC | | After credential received |
| Artifact validation implemented | Service | | Against AC's JWKS |
| Integration endpoints implemented | Service | | At minimum: account-create + revoke |
| End-to-end smoke test | Both | | AC proxies request → service validates artifact |
| Go live | Both | | Remove any test/dev overrides |

---

## Sign-off

| Team | Reviewer | Date | Notes |
|------|----------|------|-------|
| AC | | | |
| Service | | | |
