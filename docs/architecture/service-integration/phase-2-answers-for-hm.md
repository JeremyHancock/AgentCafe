# Phase 2 Answers from AgentCafe Team

**Date:** 2026-03-25
**From:** AgentCafe team
**Re:** Service Integration Standard — responses to HM Phase 2 questions

We've reviewed all six questions. Answers below reference the implemented code (PR 1 is complete and tested) and the three spec documents where relevant.

---

## Blocking

### Q1: Action registry — IDs, paths, and which operations are jointly-verified

**(a) Which operations are `jointly_verified`?**

All five. HM is a personal memory store — every operation touches the human's data. Reads expose private information; writes mutate it. There is no action where standard-mode (no artifact, no binding) would be appropriate.

The `integration_mode` column on `proxy_configs` is per-action, so this can be revisited per-action if needed, but our recommendation is a uniform `jointly_verified` across the board.

**(b) `action_id` values:**

| Operation | `action_id` | `scopes` array entry |
|-----------|------------|----------------------|
| Store | `store` | `human-memory:store` |
| Retrieve | `retrieve` | `human-memory:retrieve` |
| List | `list` | `human-memory:list` |
| Search | `search` | `human-memory:search` |
| Briefing | `briefing` | `human-memory:briefing` |

These are short, match HM's existing PAT scope naming (minus the `memory:` prefix, which is replaced by the `service_id:` prefix), and read naturally in artifact claims. The artifact's `action` claim will contain the bare `action_id` (e.g., `"store"`); the `scopes` array will contain the qualified form (e.g., `["human-memory:store"]`).

**(c) `backend_path` values:**

AC will configure the exact paths HM exposes today, with no version prefix:

| Operation | `backend_method` | `backend_path` |
|-----------|-----------------|---------------|
| Store | `POST` | `/memory/store` |
| Retrieve | `POST` | `/memory/retrieve` |
| List | `POST` | `/memory/list` |
| Search | `POST` | `/memory/search` |
| Briefing | `POST` | `/memory/briefing` |

The `request_hash` computation (Artifact 1 Section 3.1, implemented in `agentcafe/cafe/artifact.py`) normalizes the path by stripping any trailing slash, then hashes `METHOD\n + normalized_path\n + body_bytes`. Both sides will agree as long as the path strings match exactly. The `/api/v1/` examples in some spec sections are illustrative — they will not appear in the actual configuration.

### Q2: Credential exchange

**(a) Provisioning:** HM generates a static API key and shares it with AC out-of-band. AC stores it encrypted at rest in `proxy_configs.backend_auth_header` (per-action, encrypted via `agentcafe.crypto`). For MVS, a single shared key used across all five actions is simplest.

**(b) Same or different:** For MVS, use the same credential for both proxied requests and integration endpoints. AC's MVS implementation hard-codes integration configuration for HM (no `service_integration_configs` table yet), so a single key simplifies setup. The spec permits this (Artifact 2 Section 2.2: "may be the same value... but stored independently").

**(c) Format:** An opaque Bearer token validated via timing-safe comparison is exactly what we expect. AC sends two headers on every jointly-verified proxied request:

```
Authorization: Bearer <hm-api-key>
X-AgentCafe-Authorization: Bearer <artifact-jwt>
```

The `Authorization` header carries the static service credential. The `X-AgentCafe-Authorization` header carries the per-request artifact JWT. These are independent — HM validates them separately, with no conflation between PAT validation and service-credential validation.

### Q3: `service_id` confirmation

**Confirmed: `human-memory`** is the canonical `service_id`. It appears in:

- `proxy_configs.service_id`
- Artifact `aud` claim (MUST-check per Artifact 1 Section 6.1)
- Artifact `scopes` array as `human-memory:<action_id>`
- `human_service_accounts.service_id`
- `authorization_grants.service_id`

HM should hard-code `"human-memory"` as the expected `aud` value in artifact validation.

---

## Important — non-blocking

### Q4: JWKS endpoint status

**(a) URL:** The endpoint is implemented and live at `/.well-known/jwks.json` on the Cafe host. It serves both Passport and artifact public keys in a single JWKS response. In production this will be `https://agentcafe.io/.well-known/jwks.json`; in development it runs on whatever host/port the Cafe is started on.

**(b) Staging/dev:** In dev mode, both key pairs (Passport and artifact) are auto-generated at startup. You can point HM at a local AC instance for integration testing. For fully offline HM development, generate your own test RSA key pair, sign test artifacts with it, and configure your validator to trust that key via a mock JWKS response. When ready for integration testing, switch to a running AC instance — the real JWKS endpoint works out of the box.

**(c) `art_` kid prefix:** Yes, confirmed. Artifact key IDs are prefixed with `art_`; Passport key IDs have no prefix. Both are served in the same JWKS response. HM should match on the specific `kid` from the artifact JWT header (standard JWT `kid`-based key selection), not filter by prefix. The prefix is useful for logging and debugging but is not part of the validation contract.

### Q5: Auth path coexistence

The dual-path model HM described is exactly correct:

- `X-AgentCafe-Authorization` present: artifact validation path (verify JWT against AC's JWKS, enforce all MUST checks from Artifact 1 Section 6.1, resolve namespace from artifact's `sub` claim)
- `X-AgentCafe-Authorization` absent: existing PAT validation path (verify JWT against HM's own keys, resolve namespace from PAT claims)

PATs are a **permanent first-class path**, not transitional. Not every agent will use AC as a broker. AC adds value (consent management, Company Cards, rate limiting, audit logging, human-in-the-loop controls) but does not monopolize access. HM should treat both paths as production-grade.

### Q6: Timeline and sequencing

**(a) AC's side is built.** PR 1 is complete and tested:

- Database migration (integration_mode, human_service_accounts, authorization_grants, revocation_deliveries tables)
- Artifact key infrastructure (separate key pair with `art_` prefix, JWKS endpoint serving both)
- Per-request artifact signing (canonical request hashing, 30s TTL RS256 JWT)
- Identity binding and grant resolution (Gate 3 in the proxy path)
- Artifact attachment to proxied requests (Gate 4, `X-AgentCafe-Authorization` header)
- Consent and Company Card integration (binding + grant creation on approval)
- 45 new tests, 380 total passing, pylint 10.00/10

What remains on AC's side is PR 2 (revocation push delivery to services). This is non-blocking for HM's initial integration — the 30-second artifact TTL provides the backstop described in Artifact 1 Section 9.

**(b) For HM development:** Mock AC's artifact signing locally. Generate a test RSA key pair, build artifact JWTs with the claims structure from Artifact 1 Section 2.2, and trust that key in your validator. This gives you full unit and integration test coverage without a running AC instance. When ready for end-to-end testing, point at a local AC instance.

---

## Notes on HM's confirmed items

Everything in the "not questions" section is correct. One nuance on `service_account_id`:

> **`service_account_id` is HM's choice.** We'll return HM's namespace ID (`ns_` prefixed) from account-create. This becomes the artifact `sub` claim.

This is correct for the full implementation; however, the **MVS currently skips the integration endpoint calls.** Today, consent approval creates the binding using the AC user ID as `service_account_id` directly (a known MVS simplification). When we wire up the real `POST /integration/account-create` call — replacing the MVS stub — HM's returned `ns_`-prefixed ID will flow into the binding row and subsequently into the artifact's `sub` claim.

This does not affect HM's artifact validation logic: treat `sub` as opaque, use it for namespace resolution, and the transition from AC-user-ID to HM-namespace-ID will be transparent once the integration endpoints are live.

---

## Summary of values HM needs to hard-code

For convenience, here are the concrete values HM should use:

| Parameter | Value |
|-----------|-------|
| Expected `aud` claim | `"human-memory"` |
| Expected `iss` claim | `"agentcafe"` |
| JWKS URL | `https://agentcafe.io/.well-known/jwks.json` |
| Artifact header | `X-AgentCafe-Authorization` |
| Artifact algorithm | `RS256` |
| Artifact `kid` convention | `art_`-prefixed (match on exact `kid`, not prefix) |
| Artifact TTL | 30 seconds (reject if `exp` has passed) |
| `standard_version` claim | `"1.0"` |
| Service credential header | `Authorization: Bearer <static-key>` |
| Valid `action` claim values | `store`, `retrieve`, `list`, `search`, `briefing` |
| Valid `scopes` entries | `human-memory:store`, `human-memory:retrieve`, `human-memory:list`, `human-memory:search`, `human-memory:briefing` |
