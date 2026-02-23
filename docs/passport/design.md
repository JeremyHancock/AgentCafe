# AgentCafe — Passport System Design Document (Phase 2.0)

**Date:** February 22, 2026
**Authors:** Grok (advisor) + Claude (lead implementer)
**Status:** **Implemented.** Phase 2.0 code is live behind migration flag. 27 tests passing (7 menu + 8 order + 12 passport).
**Architecture reference:** See `passport-threat-model.md` (v1.4, locked) for the full three-layer trust model, threat model, and Phase 3+ design.

---

## 0. Design Principles (from threat model v1.4)

This implementation follows two non-negotiable principles established in the threat model:

- **Principle Zero:** The system's ultimate trust root is a physical-world identity anchor (passkey/hardware key). Phase 2 does not implement passkey enrollment (that's Phase 3), but the JWT structure and validation logic are designed to be compatible with it.
- **Principle One:** `agent_id` is an untrusted, self-reported label. It is used for display (audit log, dashboard) and nothing else. **No validation logic branches on `agent_id`.** All security rests on human identity.

---

## 1. Goals & Non-Goals

**Must achieve (Phase 2)**
- Prove an agent represents a real human (verifiable delegation)
- Prove the human explicitly authorized this exact action (scoped mandates)
- Support short-lived, revocable permissions
- Keep the agent experience unchanged (`passport` field in `/cafe/order` stays a single string)
- Be future-proof for the three-layer trust model (Layer 1 identity, Layer 2 standing mandates, Layer 2.5 first-use confirmation, Layer 3 just-in-time confirmation)

**Non-goals for Phase 2**
- Human-facing Passport issuance UI (Phase 3)
- Passkey/WebAuthn enrollment (Phase 3)
- Standing mandates with expiry (Phase 3)
- Layer 2.5 first-use confirmation (Phase 3)
- Layer 3 async confirmation flow — `202 Accepted` + polling/webhook (Phase 3)
- Activation code cold-start flow (Phase 3)
- Velocity data model + enforcement (Phase 3 — ships alongside standing mandates)
- Advanced features (zero-knowledge, blockchain, etc.)

---

## 2. JWT Structure (HS256 for MVP)

Signed with HS256 using `PASSPORT_SIGNING_SECRET` (32+ chars, generated at startup if missing).

**Claims:**

```json
{
  "iss": "agentcafe",
  "sub": "user:jeremy@example.com",      // Human identifier
  "aud": "agentcafe",
  "exp": 1740259200,                     // Unix timestamp (max 24h)
  "iat": 1740172800,
  "jti": "uuid-v4",                      // For revocation
  "agent_id": "claude-12345",            // Untrusted label (Principle One)
  "scopes": ["stayright-hotels:search-availability", "quickbite-delivery:browse-menu"],
  "authorizations": [
    {
      "service_id": "stayright-hotels",
      "action_id": "book-room",
      "limits": {
        "max_night_rate": 500,
        "purpose": "work_trip",
        "valid_until": "2026-03-01"
      }
    }
  ],
  "human_consent": true                  // Forward-compatibility only (see §2.1)
}
```

### 2.1 Forward-compatibility notes

- **`agent_id`**: Untrusted string. In Phase 3, this will appear in the human dashboard and audit log. It is never used for security decisions.
- **`human_consent`**: Not validated in Phase 2. In Phase 3, this will be replaced by a `consent_id` linking to the immutable consent record (exact text shown, confirmation method, timestamp).
- **`purpose`** (in `limits`): Descriptive label for the human's benefit. The Cafe does **not** enforce purpose at the protocol level (per threat model v1.4 §10 — purpose locks are unenforceable). The value is passed through to the audit log and human dashboard.
- **`authorizations`**: In Phase 3, these map to standing mandates (Layer 2) with mandatory expiry (max 90 days) and first-use confirmation (Layer 2.5).

---

## 3. Validation Rules (codified)

**Scope format (locked):** `{service_id}:{action_id}` (e.g. `stayright-hotels:search-availability`)

**Validation order in `place_order`:**
1. Signature, expiry, issuer, audience, and `jti` not revoked.
2. **Scope check** (required for ALL actions): the requested `service_id:action_id` must be in `scopes` (or `service_id:*` wildcard).
3. **Authorization check** (required only if `human_auth_required == true` in proxy_configs): there must be a matching entry in `authorizations`.
4. **Universal limit check** (Cafe enforces): `valid_until` must be in the future. Service-specific `limits` are passed through as metadata (backend or future policy engine handles them).

`human_consent` claim is kept for future use but is **not** validated in Phase 2.

**Wildcard rule (MVP):** Only `{service_id}:*` is supported. No other patterns.

### 3.1 Backend token isolation (already implemented)

The backend **never** sees the human's passport JWT. The Cafe is a full proxy: it validates the passport, then forwards the request to the backend using the backend's own auth credentials (stored in `proxy_configs`). This means revocation is instant (no cached tokens at backends) and backends cannot accumulate or replay human credentials.

---

## 4. Revocation (MVP)

- Short expiry (≤ 24h)
- SQLite table `revoked_jtis` (jti + revoked_at)
- `POST /cafe/revoke` endpoint (accepts full passport, extracts jti, blacklists it)
- Revocation takes effect immediately at the Cafe (sole enforcement point)

---

## 5. Issuance Endpoint (MVP)

`POST /passport/issue` (protected by `ISSUER_API_KEY` env var for now)

In Phase 3, this becomes the activation code flow: agent calls `POST /passport/issue-request`, Cafe returns an 8-character activation code, human navigates to `agentcafe.com` and enters the code to complete issuance with passkey confirmation. The current API-key-gated endpoint is for dev/testing only.

Request body:
```json
{
  "human_id": "jeremy@example.com",
  "agent_id": "claude-12345",
  "scopes": ["stayright-hotels:*"],
  "authorizations": [ ... ],
  "duration_hours": 24
}
```

Response:
```json
{
  "passport": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires_at": "2026-02-23T12:00:00Z"
}
```

---

## 6. Migration Status

- ✅ Existing `_mvp` functions kept untouched alongside new system.
- ✅ `USE_REAL_PASSPORT=true` config flag added (default False).
- ✅ In `place_order`, routes to `_validate_passport_jwt()` or `_mvp` based on flag.
- ✅ `seed.py` and all tests updated for new scope format.
- ⬜ During Company Onboarding (Phase 3), automatically generate canonical scopes as `{service_id}:{action_id}`.

---

## 7. Library & Security

- Use `PyJWT` with explicit `algorithms=["HS256"]` whitelist.
- Signing secret from environment only.
- All sensitive data stays inside the signed JWT.
- Audit log already hashes passport.

---

## 8. Phase 3 Upgrade Path

This section summarizes what changes when Phase 3 implements the full three-layer trust model from `passport-threat-model.md` v1.4.

| Phase 2 (current) | Phase 3 (next) |
|---|---|
| API-key-gated issuance | Activation code flow + passkey enrollment |
| `human_consent: true` (not validated) | `consent_id` linking to immutable consent record |
| `authorizations` as static JWT claims | Standing mandates (Layer 2) with 90-day max expiry |
| No first-use check | Layer 2.5: first action under new mandate triggers confirmation |
| Synchronous order flow | Layer 3: `202 Accepted` + async confirmation for high-risk actions |
| No velocity data model or enforcement | Velocity data model + per-service rolling-sum enforcement |
| No human dashboard or exposure tracking | Total exposure calculation + dashboard (active mandates, action history, aggregate exposure) |
| No risk tiers | Configurable thresholds + asymmetric ceremony + hard ceiling |

**Key constraint:** The JWT structure (§2) does not need breaking changes for Phase 3. Standing mandates will be stored server-side (not in the JWT). The JWT remains an identity + scope token; mandates and confirmations are Cafe-side state.

---

This design gives us real cryptographic safety today and a clean upgrade path to the full three-layer trust model in Phase 3.
