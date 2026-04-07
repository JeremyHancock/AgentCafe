# AgentCafe Consolidated Backlog

## Context

Human Memory launched as the first real service on April 7, 2026 (7 actions, jointly-verified, quarantine until April 14). The project has open items scattered across 6+ docs. This consolidates everything into one prioritized backlog organized by milestones, with dependencies and gaps identified.

**Team:** 1-2 people. **State:** Beta, 485 tests, pylint 10.00, live at agentcafe.io.

---

## Milestones

| ID | Milestone | Definition | Horizon |
|----|-----------|------------|---------|
| **M0** | Protect what's live | No data-loss risk, no session exploit, HM quarantine lifts safely | Before April 14 |
| **M1** | Ready for 2nd service | A company can self-serve through the wizard without a co-pilot | 4-6 weeks |
| **M2** | Ready for public beta | 3+ services, 3+ real agent families tested, email verification, observability | 2-3 months |
| **M3** | Scale | Postgres, SDK, full observability, accessibility | 6+ months |

---

## M0 — Protect What's Live (before April 14)

### Operations (do first — not code, just actions)

| # | Item | Effort | Source |
|---|------|--------|--------|
| 0.1 | **Back up CAFE_ENCRYPTION_KEY** to password manager | 15 min | deployment-plan.md §4 |
| 0.2 | **Back up RSA signing keys** (Passport + artifact private keys) | 15 min | *New — not in any doc* |

### Security hardening (small, high-value)

| # | Item | Effort | Source |
|---|------|--------|--------|
| 0.3 | **Add `secure=True` to all `set_cookie` calls** — 5 call sites across `pages.py` (lines 84, 131, 1278) and `wizard_pages.py` (lines 124, 1252). Production is HTTPS-only but flag should be explicit. | 30 min | *New* |
| 0.4 | **Normalize company email to lowercase** on registration and login — `wizard/router.py` lines 122, 137, 152 store/query raw case unlike `human.py` which lowercases. Prevents duplicate accounts. | 30 min | *New* |
| 0.5 | **Fix wizard_pages CSRF to use `abs()`** — `wizard_pages.py` line 162 uses `now - token_time` (allows future-dated tokens). `pages.py` line 192 already uses `abs()`. Make consistent. | 15 min | *New* |

### Testing (validate JV before quarantine lifts)

| # | Item | Effort | Source |
|---|------|--------|--------|
| 0.6 | **E2E JV integration test** — full path: consent → binding → grant → artifact → proxy → response. Individual pieces tested (45 + 25 tests) but no integrated flow test. | 4-6 hrs | *New* |

---

## M1 — Ready for 2nd Service (4-6 weeks)

### Wizard scope/naming (biggest onboarding friction)

| # | Item | Effort | Source | Dep |
|---|------|--------|--------|-----|
| 1.1 | **Fix garbled scope strings** — derivation concatenates operationId fragments (`human-memory:retrievememoryretrievepost`). Support `x-ac-scope` extension, derive from path, or allow inline editing. Root cause in `spec_parser.py`. | 3-4 hrs | onboarding-improvements |  |
| 1.2 | **Fix garbled action IDs** — same root cause as 1.1. Make editable in Review step. | 2-3 hrs | onboarding-improvements | 1.1 |
| 1.3 | **POST != WRITE override** — all POST classified as WRITE/Tier-2. Allow override via UI toggle or `x-ac-read-only` extension. | 2-3 hrs | onboarding-improvements |  |
| 1.4 | **operationId heuristic warning** — detect auto-generated IDs containing path segments or HTTP methods, offer path-based alternatives. | 2-3 hrs | onboarding-improvements | 1.1 |

### Wizard state & autofill bugs

| # | Item | Effort | Source |
|---|------|--------|--------|
| 1.5 | **Navigation back resets fields** — integration mode, backend URL, auth header lost on back-navigation. Persist wizard state across steps. | 2-3 hrs | onboarding-improvements |
| 1.6 | **Browser autofill on backend URL/auth header** — fields named in ways that trigger autofill with email/URL. Fix with `autocomplete="off"` or specific `autocomplete` attributes. | 1 hr | onboarding-improvements |
| 1.7 | **JV integration re-asks for base URL/auth header** — carry forward from policy step or explain why they differ. | 1-2 hrs | onboarding-improvements |
| 1.8 | **Endpoint filtering after parse** — framework specs include internal routes. Add include/exclude checkboxes per endpoint. | 3-4 hrs | onboarding-improvements |
| 1.9 | **"Try a sample" button** — either wire up or remove. | 30 min | onboarding-improvements |

### Infrastructure & config

| # | Item | Effort | Source |
|---|------|--------|--------|
| 1.10 | **SQLite backup cron** — `sqlite3 .backup` to Fly volume + periodic copy to S3/R2. Deferred until real company onboarded — that's now. | 2-4 hrs | deployment-plan.md §4 |
| 1.11 | **Config validation on startup** — fail fast if `PASSPORT_SIGNING_SECRET` empty (currently generates random = tokens invalid on restart), if `CAFE_ENCRYPTION_KEY` malformed, if `ISSUER_API_KEY` empty. | 1-2 hrs | *New* |
| 1.12 | **Enforce USE_REAL_PASSPORT=true** — remove the toggle or add startup check refusing to start with `false` when real services are configured. | 1-2 hrs | SECURITY-DEBT SEC-5 |
| 1.13 | **CORS tightening** — `cors_allowed_origins` defaults to `"*"` (`config.py` line 65). Set to `https://agentcafe.io` in `fly.toml`. | 30 min | *New* |
| 1.14 | **Improved health endpoint** — currently only `SELECT 1`. Add migration version check and RSA key loading check. | 1-2 hrs | *New* |
| 1.15 | **.env.example file** — document all env vars with examples. | 30 min | *New* |

### Security

| # | Item | Effort | Source |
|---|------|--------|--------|
| 1.16 | **SEC-10: Card-agent relationship check on report-spend** — verify calling passport's `card_id` claim matches, or restrict to system API key. | 2-3 hrs | SECURITY-DEBT |
| 1.17 | **JSON body size limits** — no payload size cap on any endpoint. Add middleware rejecting bodies > 1 MB (2 MB for spec upload). | 1-2 hrs | *New* |

---

## M2 — Ready for Public Beta (2-3 months)

### Agent experience (highest information gain)

| # | Item | Effort | Source | Dep |
|---|------|--------|--------|-----|
| 2.1 | **Execute agent testing plan** — 3-5 agent families (GPT-4o, Claude, Grok) against HM + demo services. Plan exists at `docs/research/agent-testing-plan.md`. Results inform priority of everything below. | 8-16 hrs | development-plan |  |
| 2.2 | **Agent error code documentation** — publish error taxonomy. Responses are consistent (`{"error":"...", "message":"..."}`) but undocumented. | 3-4 hrs | ux-paths.md |  |
| 2.3 | **Menu search/filter/pagination** — `cafe.search` does keyword matching but no category/tag filtering. Needed at 10+ services. | 4-8 hrs | ux-paths.md |  |
| 2.4 | **Rate limit transparency** — add `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset` response headers. `Retry-After` already returned on 429. | 2-3 hrs | *New* |
| 2.5 | **Long-poll or webhook push for consent status** — agents currently poll. Reduces latency and load. | 4-6 hrs | ux-paths.md |  |

### Account security

| # | Item | Effort | Source |
|---|------|--------|--------|
| 2.6 | **Email verification** — neither account type verifies email. Add token flow for company registration at minimum. | 4-6 hrs | ux-paths.md |
| 2.7 | **Password recovery** — no forgot-password for either account type. Companies are password-only. | 4-8 hrs | ux-paths.md |

### Wizard polish

| # | Item | Effort | Source |
|---|------|--------|--------|
| 2.8 | Prettify JSON in spec input window | 1-2 hrs | onboarding-improvements |
| 2.9 | Spec upload example + docs link | 1-2 hrs | onboarding-improvements |
| 2.10 | Info hoverables/tooltips everywhere | 4-8 hrs | onboarding-improvements |
| 2.11 | Copy button on raw JSON preview | 30 min | onboarding-improvements |
| 2.12 | Confidence score explanation | 2-3 hrs | onboarding-improvements |
| 2.13 | Identity mode explanation | 1 hr | onboarding-improvements |
| 2.14 | Extension field documentation (x-ac-*) | 2-3 hrs | onboarding-improvements |
| 2.15 | Company account transfer/close | 4-6 hrs | onboarding-improvements |

### Human-facing UX

| # | Item | Effort | Source |
|---|------|--------|--------|
| 2.16 | Consent text shows agent inputs | 3-4 hrs | ux-paths.md |
| 2.17 | Human activity notifications | 4-6 hrs | development-plan |
| 2.18 | Company usage notifications | 3-4 hrs | ux-paths.md |
| 2.19 | Landing page → sign-up funnel | 4-8 hrs | development-plan |
| 2.20 | Per-field form validation feedback | 3-4 hrs | *New* |

### Observability

| # | Item | Effort | Source |
|---|------|--------|--------|
| 2.21 | Alerting on 5xx spikes (Fly.io Prometheus or UptimeRobot) | 2-4 hrs | development-plan |
| 2.22 | Health/latency/error-rate dashboard (Grafana Cloud free tier) | 4-8 hrs | development-plan |
| 2.23 | Graceful shutdown — drain in-flight requests on SIGTERM before closing DB | 2-3 hrs | *New* |

### Testing

| # | Item | Effort | Source |
|---|------|--------|--------|
| 2.24 | E2E passkey account test (last go-live checklist item) | 2-4 hrs | SECURITY-DEBT |
| 2.25 | Error path tests for wizard_pages.py | 3-4 hrs | *New* |
| 2.26 | Concurrent card token issuance edge cases | 2-3 hrs | *New* |

### Service integration (deferred past HM MVS)

| # | Item | Effort | Source |
|---|------|--------|--------|
| 2.27 | `revoke_honored` grant state | 3-4 hrs | development-plan |
| 2.28 | Grant-status reconciliation endpoint | 3-4 hrs | development-plan |
| 2.29 | Company Card revocation fan-out | 2-3 hrs | development-plan |

---

## M3 — Scale (6+ months)

| # | Item | Effort | Source |
|---|------|--------|--------|
| 3.1 | SEC-7: Company wizard passkey auth | 8-16 hrs | SECURITY-DEBT |
| 3.2 | Accessibility (aria labels, role="alert", aria-live) | 8-16 hrs | *New* |
| 3.3 | Mobile responsiveness for consent/approval pages | 4-8 hrs | *New* |
| 3.4 | Deferred binding background resolver | 4-6 hrs | development-plan |
| 3.5 | Account linking/unlinking/reconciliation | 8-16 hrs | development-plan |
| 3.6 | Two-phase audit log | 4-6 hrs | development-plan |
| 3.7 | SQLite → PostgreSQL migration | 16-24 hrs | development-plan |
| 3.8 | Agent SDK (`agentcafe-py`) | 16-24 hrs | development-plan |
| 3.9 | OpenTelemetry distributed tracing | 8-16 hrs | development-plan |
| 3.10 | Secrets manager integration | 2-4 hrs | development-plan |

---

## Probably Not Worth Doing

| Item | Why |
|------|-----|
| **Audit log web viewer** | Admin dashboard already shows recent logs. Full tamper-detection browser is impressive engineering with zero user demand at this scale. |
| **Consent delivery mechanism** | Correct architectural decision to leave this to agents. Cafe provides URL, not notification channel. |
| **Admin pagination** | One admin user, hardcoded LIMIT 50 is fine until 50+ services. |
| **Capability wizard UI for service identity model** | Superseded by JV integration page (step 3b) already built. |

---

## Dependency Map

```
0.1 (backup key) ──── irreversible if lost, blocks nothing but consequences are permanent
0.6 (E2E JV test) ──── should complete before quarantine lifts April 14

1.1 (garbled scopes) ──┬── 1.2 (garbled action IDs, same root cause)
                       └── 1.4 (operationId heuristic, builds on same parser code)
1.1 + 1.3 + 1.5 ──── block M1 (self-serve onboarding)

2.1 (agent testing) ──── informs priority of 2.2-2.5 (do first, then decide)
2.6 (email verification) ──── 2.7 (password recovery needs verified email)
```

---

## Gaps Identified (not in any existing doc)

| Gap | Milestone | Reasoning |
|-----|-----------|-----------|
| RSA signing key backup | M0 | Same risk as encryption key — if auto-generated and lost, all tokens unverifiable |
| Cookie `secure=True` flag | M0 | 5 call sites missing. Production is HTTPS but flag should be explicit |
| Company email case normalization | M0 | Prevents duplicate accounts, data corruption |
| Wizard CSRF `abs()` consistency | M0 | `pages.py` uses `abs()`, `wizard_pages.py` doesn't |
| Config startup validation | M1 | Silent random secret generation = tokens invalid on restart |
| CORS tightening | M1 | Defaults to `*` in production |
| JSON body size limits | M1 | No payload cap on any endpoint |
| .env.example file | M1 | No template for required env vars |
| Rate limit headers on responses | M2 | Agents can't discover limits without reading code |
| Graceful shutdown handling | M2 | In-flight requests interrupted during deploys |

---

*This document is the single source of truth for all open work. Items from `docs/todo-onboarding-improvements.md`, `docs/security/SECURITY-DEBT.md`, `docs/planning/development-plan.md`, and `docs/planning/ux-paths.md` are consolidated here.*
