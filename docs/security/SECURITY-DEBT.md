# Security Debt Tracker — Human Approval Bypass Risks

**Purpose:** Catalog every code path where an agent could potentially bypass required human approval to access real company endpoints. Each item must be resolved before real companies onboard.

**Invariant:** *No agent may obtain write-scope access to a real service endpoint without a cryptographically verified human approval (passkey).*

**Last audited:** March 3, 2026 (Sprint 4 complete, SEC-2/3/4 resolved, SEC-1 mitigated by grace period)

---

## CRITICAL — Agent can bypass human approval today

### SEC-1: Password auth allows programmatic account creation
- **File:** `agentcafe/cafe/human.py` — `POST /human/register`, `POST /human/login`
- **File:** `agentcafe/cafe/pages.py` — `POST /register`, `POST /login`
- **Risk:** When `ALLOW_PASSWORD_AUTH=true` (current default), an agent can register a human account and obtain a session token via HTTP alone — no passkey, no human.
- **Impact:** Agent creates account → obtains session → approves its own consent → gets write-scope Passport.
- **Fix:** Set `ALLOW_PASSWORD_AUTH=false` in production. The flag and gating code already exist. **Do not enable real company endpoints until this is false.**
- **Mitigation (Sprint 4):** Grace period logic now auto-disables password login per-user 7 days after passkey enrollment. Password login prompts enrollment on every login. Even with `ALLOW_PASSWORD_AUTH=true`, the attack requires: (1) agent registers account, (2) agent somehow completes a passkey enrollment (impossible without physical authenticator), (3) consent approval requires passkey assertion. The remaining gap is that an agent can create a password account and use it during the 7-day grace window — but consent approval independently requires passkey assertion (SEC-2), so the agent still cannot approve its own consent.
- **Status:** ⚠️ OPEN but **effectively mitigated** by SEC-2 (consent approval requires passkey). Set `ALLOW_PASSWORD_AUTH=false` for defense-in-depth.

### ~~SEC-2: Consent API endpoint has no passkey requirement~~ → RESOLVED
- **Resolved:** March 3, 2026. Implemented option (a): `ApproveRequest` now requires `passkey_challenge_id` + `passkey_credential`. Server calls `verify_passkey_assertion()` and confirms passkey user matches session user. Both API and page-flow consent approval use the same `verify_passkey_assertion()` code path.

### ~~SEC-3: Consent page `isPasskeySupported()` fallback skips re-auth~~ → RESOLVED
- **Resolved:** March 3, 2026. Approve button is now disabled with error message when WebAuthn is unavailable.

### ~~SEC-4: Consent page `<noscript>` fallback skips re-auth~~ → RESOLVED
- **Resolved:** March 3, 2026. `<noscript>` block now shows "JavaScript is required" message instead of an approve form.

---

## HIGH — Agent can bypass validation in demo mode

### SEC-5: MVP passport mode accepts "demo-passport" with all scopes
- **File:** `agentcafe/cafe/router.py:188-190, 511-537`
- **Risk:** When `USE_REAL_PASSPORT=false`, the string `"demo-passport"` is accepted as a valid passport with all scopes, including human authorization for write actions.
- **Impact:** Any agent sending `passport: "demo-passport"` gets unrestricted access.
- **Fix:** `USE_REAL_PASSPORT` must be `true` in production. Already configurable. **Verify this is set before real company onboarding.**
- **Status:** ⚠️ OPEN (production currently uses `true`, but no enforcement prevents changing it)

---

## MEDIUM — Defense-in-depth gaps

### SEC-6: Session tokens have no passkey-binding
- **File:** `agentcafe/cafe/human.py` — `_create_human_session_token()`
- **Risk:** Session JWTs are issued identically for password-based and passkey-based logins. There's no claim distinguishing how the session was obtained. A session from password login has the same privileges as one from passkey login.
- **Impact:** If password auth is enabled alongside passkeys, a password-obtained session can approve consents that should require passkey-level assurance.
- **Fix:** Add an `auth_method` claim to session JWTs (`"password"` vs `"passkey"`). Consent approval should reject sessions with `auth_method: "password"` when passkey enforcement is required.
- **Status:** 🔵 DEFERRED (mitigated by SEC-1 fix — disabling password auth removes this path)

### SEC-7: Company wizard uses password-only auth
- **File:** `agentcafe/wizard/router.py:127-135, 147-163`
- **Risk:** Company accounts (service publishers) use email+password with no passkey option. A compromised company account could publish malicious service definitions.
- **Impact:** Lower priority than human account bypass (companies are not the consent gate), but still a supply-chain risk.
- **Fix:** Add passkey support to company wizard auth. Sprint 3+ item.
- **Status:** 🔵 DEFERRED

### SEC-8: No rate limiting on passkey challenge endpoints
- **File:** `agentcafe/cafe/human.py` — `POST /human/passkey/register/begin`, `POST /human/passkey/login/begin`
- **Risk:** An attacker could flood challenge generation, filling the `webauthn_challenges` table with garbage rows. Challenges expire after 5 minutes and `cleanup_expired_challenges()` exists, but there's no per-IP throttle.
- **Impact:** Denial of service via DB bloat. No auth bypass risk.
- **Fix:** Add IP-based rate limiting (similar to `_register_hits` in `passport.py`).
- **Status:** 🔵 DEFERRED

### SEC-9: Company Card page approval skips passkey assertion
- **File:** `agentcafe/cafe/pages.py` — `POST /tab/approve/{card_id}/submit`
- **Risk:** ADR-028 requires passkey for card approval ceremony. The API endpoint (`POST /cards/{card_id}/approve`) enforces `passkey_challenge_id` + `passkey_credential` via `verify_passkey_assertion()`. The page-based approval only requires session cookie + CSRF token — no passkey re-auth.
- **Impact:** If an attacker has a valid session cookie (e.g., from an XSS or session theft), they can approve Company Cards without proving physical presence via passkey. Company Cards grant standing authorization for multiple actions, making this a higher-value target than single-action consents.
- **Fix:** Add WebAuthn assertion to the card approval page flow, matching the consent approval page pattern (SEC-2 resolution). The `card_approve.html` template needs a JS-driven passkey assertion step before form submission.
- **Status:** ⚠️ OPEN

### SEC-10: `report-spend` endpoint has no card-agent relationship check
- **File:** `agentcafe/cafe/cards.py` — `POST /cards/{card_id}/report-spend`
- **Risk:** Any agent with a valid Tier-1 passport can report arbitrary spend against any Company Card by knowing the card_id (a UUID). This could drain a card's budget and block token issuance for the legitimate agent.
- **Impact:** Low-medium. Card IDs are UUIDs (not guessable), and in practice this endpoint is called by the Cafe system after proxied orders, not directly by agents. But the API surface is unprotected.
- **Fix:** Verify the calling agent's passport was issued under this card (check `card_id` claim in JWT), or restrict to human session auth (card owner) / system API key.
- **Status:** 🔵 DEFERRED (low practical risk due to UUID card IDs; no test-breaking change needed)

---

## Production Go-Live Checklist

Before any real company's endpoints are proxied through AgentCafe:

- [ ] `ALLOW_PASSWORD_AUTH=false` (SEC-1) — defense-in-depth; consent approval already requires passkey
- [ ] `USE_REAL_PASSPORT=true` (SEC-5)
- [x] Consent API endpoint requires passkey proof (SEC-2) — March 3, 2026
- [x] Consent page blocks approval when passkey not supported (SEC-3) — March 3, 2026
- [x] Consent page `<noscript>` fallback removed (SEC-4) — March 3, 2026
- [x] Grace period auto-disables password login after passkey enrollment — March 3, 2026
- [x] Password login prompts passkey enrollment — March 3, 2026
- [ ] Company Card page approval requires passkey assertion (SEC-9)
- [ ] `WEBAUTHN_RP_ID=agentcafe.io`, `WEBAUTHN_ORIGIN=https://agentcafe.io`
- [ ] At least one human account registered via passkey and tested end-to-end
- [ ] Session tokens include `auth_method` claim (SEC-6) — nice-to-have if SEC-1 is resolved
- [ ] Challenge endpoint rate limiting (SEC-8) — nice-to-have

---

## Resolved Items

- **SEC-2** — March 3, 2026: `ApproveRequest` requires `passkey_challenge_id` + `passkey_credential`; `verify_passkey_assertion()` shared by API + page flow; user mismatch returns 403
- **SEC-3** — March 3, 2026: `consent.html` now disables approve button + shows error when `isPasskeySupported()` is false
- **SEC-4** — March 3, 2026: `consent.html` `<noscript>` block replaced with "JavaScript required" message
