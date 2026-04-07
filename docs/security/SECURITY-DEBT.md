# Security Debt Tracker — Human Approval Bypass Risks

**Purpose:** Catalog every code path where an agent could potentially bypass required human approval to access real company endpoints. Human Memory onboarded April 7, 2026 — critical items resolved, remaining items tracked in `docs/planning/backlog.md`.

**Invariant:** *No agent may obtain write-scope access to a real service endpoint without a cryptographically verified human approval (passkey).*

**Last audited:** April 6, 2026 (Production readiness complete: SEC-1/6/8/9 resolved, all checklist items closed)

---

## Status Summary

| ID | Category | Summary | Status |
|----|----------|---------|--------|
| SEC-1 | Critical | Password auth allows programmatic account creation | ✅ Resolved |
| SEC-2 | Critical | Consent API endpoint requires passkey proof | ✅ Resolved |
| SEC-3 | Critical | Consent page passkey fallback | ✅ Resolved |
| SEC-4 | Critical | Consent page noscript fallback | ✅ Resolved |
| SEC-5 | High | Demo passport mode enforcement | ⚠️ Open — see backlog 1.12 |
| SEC-6 | Medium | Session token auth-method binding | ✅ Resolved |
| SEC-7 | Medium | Company wizard auth hardening | 🔵 Deferred — see backlog 3.1 |
| SEC-8 | Medium | Challenge endpoint rate limiting | ✅ Resolved |
| SEC-9 | Medium | Card approval passkey assertion | ✅ Resolved |
| SEC-10 | Medium | Card-agent relationship validation | 🔵 Deferred — see backlog 1.16 |

---

## Production Go-Live Checklist

Before any real company's endpoints are proxied through AgentCafe:

- [x] `ALLOW_PASSWORD_AUTH=false` (SEC-1) — April 6, 2026
- [x] `USE_REAL_PASSPORT=true` (SEC-5) — set in fly.toml
- [x] Consent API endpoint requires passkey proof (SEC-2) — March 3, 2026
- [x] Consent page blocks approval when passkey not supported (SEC-3) — March 3, 2026
- [x] Consent page `<noscript>` fallback removed (SEC-4) — March 3, 2026
- [x] Grace period auto-disables password login after passkey enrollment — March 3, 2026
- [x] Password login prompts passkey enrollment — March 3, 2026
- [x] Company Card page approval requires passkey assertion (SEC-9) — April 6, 2026
- [x] `WEBAUTHN_RP_ID=agentcafe.io`, `WEBAUTHN_ORIGIN=https://agentcafe.io` — set in fly.toml
- [ ] At least one human account registered via passkey and tested end-to-end
- [x] Session tokens include `auth_method` claim (SEC-6) — April 6, 2026
- [x] Challenge endpoint rate limiting (SEC-8) — April 6, 2026

---

## Resolved Items

- **SEC-1** — April 6, 2026: `ALLOW_PASSWORD_AUTH` default changed to `false`
- **SEC-2** — March 3, 2026: Consent approval requires passkey assertion (API + page flow)
- **SEC-3** — March 3, 2026: Approve button disabled when WebAuthn unavailable
- **SEC-4** — March 3, 2026: `<noscript>` block replaced with "JavaScript required" message
- **SEC-6** — April 6, 2026: `auth_method` claim added to all human session JWTs
- **SEC-8** — April 6, 2026: IP-based sliding-window rate limiting on challenge endpoints
- **SEC-9** — April 6, 2026: Card approval page requires passkey assertion
