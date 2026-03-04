# WebAuthn Passkeys — Implementation Plan

**Status:** Sprints 1 + 2 + 3 COMPLETE, Sprint 4 next  
**Created:** March 3, 2026  
**Blocking:** Real-world company onboarding (agents can currently self-register and self-approve Tier-2 consent)

---

## The Problem

`agentcafe/cafe/human.py` uses email+password. An agent can programmatically register an account and approve its own Tier-2 consent — defeating the human-in-the-loop guarantee. The threat model (§5) calls this out: *"Account creation is the most dangerous moment in the system."*

## Locked Design Positions (from threat-model.md)

1. **Passkey-only accounts** — no fallback, no limited-mode tier
2. **Activation code flow** (GitHub device flow) — agent gets 8-char code, human navigates to agentcafe.io themselves (anti-phishing)
3. **Asymmetric ceremony** — raising limits requires passkey; lowering/revoking does not
4. **Consent approval requires passkey** for high-risk actions

---

## Sprint 1 — Server-side WebAuthn

- [x] Add `webauthn` dependency to `pyproject.toml` (v2.7.1)
- [x] DB migration (0008): `webauthn_credentials` table + `webauthn_challenges` table
- [x] Config: `WEBAUTHN_RP_ID`, `WEBAUTHN_RP_NAME`, `WEBAUTHN_ORIGIN`, `ALLOW_PASSWORD_AUTH` in CafeConfig
- [x] New endpoints in `human.py`:
  - `POST /human/passkey/register/begin` → generate registration options
  - `POST /human/passkey/register/complete` → verify attestation, store credential, create account, issue session
  - `POST /human/passkey/login/begin` → generate authentication options
  - `POST /human/passkey/login/complete` → verify assertion, issue session
- [x] Feature flag: `ALLOW_PASSWORD_AUTH` (default `true` for beta, `false` in prod)
- [x] Password endpoints gated behind `_require_password_auth()` guard
- [x] Challenge helpers: `_store_challenge`, `_load_and_consume_challenge`, `cleanup_expired_challenges`
- [x] 15 new tests in `tests/test_webauthn.py`. 229 total passing, pylint 10.00/10

## Sprint 2 — Browser-side Integration

- [x] `static/webauthn.js` — zero-dependency JS helper (base64url, credential serialization, register/login/reauth flows)
- [x] `POST /auth/session` endpoint — sets httponly cookie after JS passkey auth
- [x] `register.html` — passkey primary ("Create Account with Passkey"), password in collapsible `<details>` (if `allow_password_auth`)
- [x] `login.html` — passkey primary ("Sign in with Passkey"), password fallback in `<details>`
- [x] `consent.html` — passkey re-auth before approve submit, `<noscript>` fallback
- [x] `pages.py` — `allow_password_auth` plumbed through `_State`, `configure_pages`, all template contexts
- [x] `main.py` — both startup paths pass `allow_password_auth` to `configure_pages`
- [x] `isPasskeySupported()` check hides passkey UI when browser lacks WebAuthn
- [x] 229 tests passing, pylint 10.00/10

## Sprint 3 — Activation Code Flow (cold-start UX)

- [x] Migration 0009: `activation_code` column on `consents` table (unique index)
- [x] `POST /consents/initiate` now generates 8-char alphanumeric code, returns `activation_code` + `activation_url`
- [x] New page: `GET /activate` — code entry form
- [x] `POST /activate` — validates code, shows consent details + registration form (or redirects if logged in)
- [x] `POST /activate/complete` — combined passkey registration + consent approval in one step
- [x] `POST /activate/decline` — decline via activation code
- [x] `activate.html` template — 3-step flow (enter code → register+approve → success)
- [x] `complete_passkey_registration()` extracted as reusable helper in `human.py`
- [x] Rate limiting: 10 code lookups per IP per 5 minutes on `/activate`
- [x] 11 new activation tests, 240 total passing, pylint 10.00/10

## Sprint 4 — Migration & Hardening

- [ ] Existing password accounts: prompt passkey enrollment on next login
- [ ] Grace period logic: after N days, disable password login for enrolled accounts
- [ ] Additional tests: cross-origin rejection, activation code expiry, activation code rate limiting
- [ ] Update AGENT_CONTEXT.md and development-plan.md

---

## Key Technical Details

- **`py_webauthn`** handles CBOR/COSE/attestation. We store credential + call `verify_registration_response()` / `verify_authentication_response()`
- **Challenge storage**: 5-min TTL in SQLite (cleaned up on use or by periodic sweep)
- **`rp_id`**: must match browser domain. `agentcafe.io` in prod, `localhost` in dev
- **Resident keys**: `resident_key=preferred` for cross-device sync (iCloud Keychain, Google Password Manager)
- **Existing password endpoints**: kept behind `ALLOW_PASSWORD_AUTH` flag during migration

## What This Doesn't Cover (deferred)

- Layer 2.5 / Layer 3 confirmation flows (velocity rules, held requests, push notifications)
- Standing mandates (separate feature, uses passkey as ceremony)
- Cross-device passkey sync (handled by Apple/Google platforms)
