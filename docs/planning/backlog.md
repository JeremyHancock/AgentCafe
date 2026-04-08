# AgentCafe Consolidated Backlog

## Context

Human Memory launched as the first real service on April 7, 2026 (7 actions, jointly-verified, quarantine until April 14). The project had open items scattered across 6+ docs. This consolidates everything into one prioritized backlog organized by milestones, with dependencies and gaps identified.

**Team:** 1-2 people. **State:** Beta, 485 tests, pylint 10.00, live at agentcafe.io.

---

## Milestones

| ID | Milestone | Definition | Horizon |
|----|-----------|------------|---------|
| **M0** | Protect what's live | No data-loss risk, no session/transport exploit, production config hardened | Immediately |
| **M1** | Ready for 2nd service | A company can self-serve the wizard without hand-holding; real agents validated | 4-6 weeks |
| **M2** | Ready for public beta | 3+ services, email verification, password recovery, observability, polished wizard | 2-3 months |
| **M3** | Scale | Postgres, SDK, accessibility, advanced integration features | 6+ months |

---

## M0 — Protect What's Live

### Operations (not code — just actions, do today)

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 0.1 | **Back up CAFE_ENCRYPTION_KEY** to password manager | 15 min | Encrypts HM's backend credentials (AES-256-GCM). Losing it = ask every company to re-enter credentials. |
| 0.2 | **Back up RSA signing keys** (Passport + artifact private keys) | 15 min | If auto-generated and lost, all issued tokens and artifacts become unverifiable. |
| 0.3 | **Set `CORS_ALLOWED_ORIGINS=https://agentcafe.io`** in fly.toml | 15 min | Currently defaults to `"*"` (`config.py` line 65). Not set in `fly.toml`. |
| 0.4 | **Set `CAFE_LOG_FORMAT=json`** in fly.toml | 15 min | Structured JSON logging is implemented (`logging_config.py`) but not enabled in production. Defaults to `text`. |

### Security hardening (small, high-value code changes)

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 0.5 | **Add `secure=True` to all `set_cookie` calls** | 30 min | Multiple call sites across pages.py and wizard_pages.py. Production is HTTPS-only but the flag should be explicit. |
| 0.6 | **Normalize company email to lowercase** on registration and login | 30 min | Company registration and login should normalize email case to prevent duplicate accounts. |
| 0.7 | **Fix wizard_pages CSRF to use `abs()`** | 15 min | CSRF token validation should use `abs()` for consistency across modules. |

### Testing (validate JV before quarantine lifts April 14)

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 0.8 | **E2E JV integration test** | 4-6 hrs | Full path: consent → binding → grant → artifact → proxy → response. Individual pieces tested (45 + 25 tests) but no single test covers the complete flow through `POST /cafe/order` with a JV service. Should pass before quarantine lifts. |

---

## M1 — Ready for 2nd Service (4-6 weeks)

### Wizard scope/naming (biggest onboarding friction — blocked HM onboarding)

| # | Item | Effort | Notes | Dep |
|---|------|--------|-------|-----|
| 1.1 | **Fix garbled scope strings** | 3-4 hrs | Scope derivation concatenates operationId fragments producing unreadable strings. Options: support `x-ac-scope` extension, derive from path segments, or allow inline editing in Policy step. | |
| 1.2 | **Fix garbled action IDs (not editable)** | 2-3 hrs | Same root cause as 1.1. Make action IDs editable in the Review step. | 1.1 |
| 1.3 | **POST != WRITE classification override** | 2-3 hrs | All POST endpoints auto-classified as WRITE/MEDIUM RISK/Tier-2. Many APIs (GraphQL, Elasticsearch, HM) use POST for reads with request bodies. Allow override via UI toggle or `x-ac-read-only` extension. | |
| 1.4 | **operationId heuristic warning** | 2-3 hrs | Detect auto-generated operationIds containing path segments or HTTP methods (e.g., `store_memory_store_post`) and warn the company, offering path-based alternatives. | 1.1 |

### Wizard state & autofill bugs

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 1.5 | **Navigation back resets fields** | 2-3 hrs | Integration mode, backend URL, and auth header lost on back-navigation. Wizard state needs to persist across step transitions (save to draft on each step, reload on back). |
| 1.6 | **Browser autofill pollutes fields** | 1 hr | Backend URL pre-fills with email, auth header pre-fills with spec URL. Affects both policy step and JV integration step. Fix with `autocomplete="new-password"` or more specific field names. Same root cause as 1.7. |
| 1.7 | **JV integration re-asks for base URL/auth header** | 1-2 hrs | Step 3b requests values already provided in step 3. Either carry them forward as defaults or explain why they're different (JV integration endpoint vs backend proxy endpoint). |
| 1.8 | **Endpoint filtering after parse** | 3-4 hrs | Framework-generated specs include internal routes (admin, auth, dashboard). Add include/exclude checkboxes per endpoint after parsing, or support OpenAPI tag-based filtering. |

### Agent validation

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 1.9 | **Execute agent testing plan with real agents** | 8-16 hrs | 3-5 agent families (GPT-4o, Claude, Grok) against live HM + demo services. Plan exists at `docs/research/agent-testing-plan.md`. This is the highest information-gain activity — results will reprioritize M2 agent experience items. |

### Infrastructure & config

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 1.10 | **SQLite backup cron** | 2-4 hrs | `sqlite3 .backup` to Fly volume + periodic copy to S3/R2/Backblaze. Deferred in deployment plan "until first real company onboards" — that's happened. HM data is live and not backed up. |
| 1.11 | **Config validation on startup** | 1-2 hrs | Fail fast if required secrets are empty or malformed in production. Prevent silent fallback to insecure defaults. |
| 1.12 | **Enforce USE_REAL_PASSPORT=true** | 1-2 hrs | Add a startup guard preventing demo passport mode when real services are configured. | 
| 1.13 | **Improved health endpoint** | 1-2 hrs | Currently only runs `SELECT 1`. Add checks for: migration version current, RSA keys loaded, encryption key present. This is what Fly.io polls every 30s. |
| 1.14 | **.env.example file** | 30 min | Document all env vars with example values and required/optional annotations. Currently env vars are only documented in README and fly.toml. |
| 1.15 | **Company account close/transfer** | 4-6 hrs | No way to clean up or hand off old company registrations. Needed before 2nd service since test accounts from development need cleanup. |

### MCP interoperability

| # | Item | Effort | Notes | Dep |
|---|------|--------|-------|-----|
| 1.18 | ~~**OAuth 2.0 on MCP endpoint (blocking)**~~ | — | **Done 2026-04-08.** Implemented minimal OAuth 2.0 AS via `mcp_oauth.py` (migration 0015). Auto-approves authorization (Tier-1 equivalent). See #18. Follow-up hardening tracked below (1.19–1.23). | |
| 1.19 | **Rate-limit MCP OAuth endpoints** | 2-3 hrs | OAuth `/register`, `/authorize`, `/token` endpoints have no rate limiting. Passport registration has 30/min per IP; OAuth endpoints should match. Without this, clients can spam token requests or client registrations unbounded. | 1.18 |
| 1.20 | **OAuth token expiry sweep** | 1-2 hrs | Expired access/refresh tokens and consumed auth codes are only cleaned up lazily (on access). Add a background task or startup sweep to prune `oauth_access_tokens`, `oauth_refresh_tokens`, and `oauth_auth_codes` tables. Without this, tables grow unbounded. | 1.18 |
| 1.21 | **E2E MCP OAuth HTTP test** | 3-4 hrs | Current tests exercise the `AgentCafeOAuthProvider` directly. Need an integration test that hits the actual HTTP endpoints (`/.well-known/oauth-authorization-server`, `/register`, `/authorize`, `/token`) through the mounted MCP app to verify the SDK wiring works end-to-end. | 1.18 |
| 1.22 | **Deferred: bridge OAuth identity to Passport** | 4-8 hrs | Currently the OAuth layer and Passport are independent — OAuth gates transport, Passport gates tools. Agents must OAuth to connect, then separately pass a Passport in tool params. Consider auto-issuing a Tier-1 Passport on OAuth token exchange so agents get a single auth flow. Deferred until validated by agent testing (1.9). | 1.18, 1.9 |
| 1.23 | **Fix MCP OAuth issuer URL startup ordering** | 1 hr | `FastMCP` is created with a localhost issuer URL at import time, then mutated by `configure_mcp_server()` at startup. If `streamable_http_app()` is called before that (test or import order change), metadata endpoints advertise the wrong URL. Either defer `FastMCP` construction to startup, or make the metadata URL lazy. | 1.18 |

### Security

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 1.16 | **SEC-10: Card-agent relationship check on report-spend** | 2-3 hrs | Add authorization check to verify the calling agent is associated with the target card. |
| 1.17 | **JSON body size limits** | 1-2 hrs | Add middleware enforcing maximum request body size on all endpoints. |

---

## M2 — Ready for Public Beta (2-3 months)

### Agent experience

| # | Item | Effort | Notes | Dep |
|---|------|--------|-------|-----|
| 2.1 | **Agent error code documentation** | 3-4 hrs | Responses are consistent (`{"error":"...", "message":"..."}`) but undocumented. Publish error taxonomy so agents can handle failures programmatically. | |
| 2.2 | **Menu search/filter/pagination** | 4-8 hrs | `cafe.search` does keyword matching but no category/tag filtering. Not needed yet (2 services), becomes critical at 10+. | |
| 2.3 | **Rate limit transparency** | 2-3 hrs | Add `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset` response headers. `Retry-After` already returned on 429. Agents currently can't discover limits without reading code. | |
| 2.4 | **Long-poll or webhook push for consent status** | 4-6 hrs | Agents currently poll `GET /cafe/order/{id}`. Polling works but adds latency and load. Long-poll is simplest; webhooks better for production agents. | 1.9 |
| 2.4a | ~~**Clean response for MCP OAuth discovery**~~ | — | Done as part of M1 1.18. See #18. | |

### Account security

| # | Item | Effort | Notes | Dep |
|---|------|--------|-------|-----|
| 2.5 | **Email verification** | 4-6 hrs | Neither account type verifies email. Add token-based verification flow for company registration at minimum. Humans use passkeys so email is less critical there. | |
| 2.6 | **Password recovery** | 4-8 hrs | No forgot-password for either account type. Companies are password-only, so a locked-out company has no recovery path today. | 2.5 |

### Wizard polish

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 2.7 | **Prettify JSON in spec input window** | 1-2 hrs | Raw JSON is hard to scan. Auto-format on paste or provide a toggle. |
| 2.8 | **Spec upload example + docs link** | 1-2 hrs | First-time companies don't know what format to provide. Link to OpenAPI docs and show a minimal example. |
| 2.9 | **Info hoverables/tooltips everywhere** | 4-8 hrs | Terms like "jointly-verified," "Tier-2," and "binding" are Cafe-specific jargon. Add `(?)` tooltips on every wizard step. |
| 2.10 | **Copy button on raw JSON preview** | 30 min | Review step shows generated JSON but no easy way to copy it. |
| 2.11 | **Confidence score explanation** | 2-3 hrs | Spec parsing shows confidence % but doesn't explain what it means or what lowers it. |
| 2.12 | **Identity mode explanation** | 1 hr | "Service" vs "Capability" identity model choice has no inline explanation of trade-offs. |
| 2.13 | **Extension field documentation (x-ac-\*)** | 2-3 hrs | Custom OpenAPI extensions (`x-ac-scope`, `x-ac-read-only`, etc.) need a reference page so companies can enrich their specs before uploading. |

### Human-facing UX

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 2.14 | **Consent text shows agent inputs** | 3-4 hrs | Humans approve/deny consent but can't see what the agent is asking to do. Show the agent's requested parameters in the consent UI. |
| 2.15 | **Human activity notifications** | 4-6 hrs | No way for humans to know when an agent requests consent except by checking the dashboard. Email or push notification on new consent requests. |
| 2.16 | **Company usage notifications** | 3-4 hrs | Companies have no visibility into how their service is being used. Basic email digest: requests/day, unique agents, error rates. |
| 2.17 | **Landing page → sign-up funnel** | 4-8 hrs | Landing page has no CTA for company registration. Add clear paths: "List your service" for companies, "Browse services" for humans. |
| 2.18 | **Per-field form validation feedback** | 3-4 hrs | Wizard and registration forms show errors only on submit. Add inline validation (email format, URL format, password strength). |

### Observability

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 2.19 | **Alerting on 5xx spikes** | 2-4 hrs | No alerting today. Use Fly.io Prometheus metrics or UptimeRobot. Even a basic "5xx rate > 1% for 5 min → email" would be a big improvement. |
| 2.20 | **Health/latency/error-rate dashboard** | 4-8 hrs | Grafana Cloud free tier. Visualize request volume, p95 latency, error rates. Currently blind to performance trends. |
| 2.21 | **Graceful shutdown** | 2-3 hrs | Drain in-flight requests on SIGTERM before closing DB. Currently deploys may interrupt active proxy requests or consent flows. |

### Testing

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 2.22 | **E2E passkey account test** | 2-4 hrs | Last unchecked go-live checklist item in SECURITY-DEBT. Requires WebAuthn test harness (e.g., `py_webauthn` soft authenticator). |
| 2.23 | **Error path tests for wizard_pages.py** | 3-4 hrs | Happy paths well tested but error/edge cases (expired CSRF, malformed spec, concurrent draft edits) not covered. |
| 2.24 | **Concurrent card token issuance edge cases** | 2-3 hrs | Two agents requesting tokens from the same Company Card simultaneously — verify budget accounting is atomic. |

### Service integration (deferred past HM MVS)

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 2.25 | **`revoke_honored` grant state** | 3-4 hrs | When a service confirms it has processed a revocation, mark the grant as `revoke_honored` (not just `revoked`). Needed for audit trail completeness. |
| 2.26 | **Grant-status reconciliation endpoint** | 3-4 hrs | Services need a way to query "what grants are active for my service?" to reconcile their state with Cafe's. |
| 2.27 | **Company Card revocation fan-out** | 2-3 hrs | Revoking a Company Card should revoke all grants issued under it. Currently only the card itself is revoked; child grants are orphaned. |

---

## M3 — Scale (6+ months)

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 3.1 | **Company wizard passkey auth** | 8-16 hrs | SEC-7: Company accounts use password-only auth. A compromised company account can publish malicious service definitions. Add passkey as primary auth, password as fallback during migration. |
| 3.2 | **Accessibility** | 8-16 hrs | No aria labels, `role="alert"`, or `aria-live` regions. Consent and approval pages are critical flows that must be screen-reader accessible. |
| 3.3 | **Mobile responsiveness** | 4-8 hrs | Consent and approval pages are the most likely to be accessed on mobile (human gets notification, taps link). Currently desktop-only layout. |
| 3.4 | **Deferred binding background resolver** | 4-6 hrs | When a binding can't be established immediately (service down, async provisioning), queue it and resolve in background. Currently fails synchronously. |
| 3.5 | **Account linking/unlinking/reconciliation** | 8-16 hrs | Humans may want multiple identities or to merge accounts. No account linking today — each passkey credential is one account. |
| 3.6 | **Two-phase audit log** | 4-6 hrs | Current audit log is append-only but not tamper-evident. Add hash chaining so log entries can be verified as unmodified. |
| 3.7 | **SQLite → PostgreSQL migration** | 16-24 hrs | SQLite works for single-instance deployment. PostgreSQL needed for horizontal scaling, concurrent writes, and connection pooling. |
| 3.8 | **Agent SDK (`agentcafe-py`)** | 16-24 hrs | Python SDK wrapping the Cafe API: `cafe.discover()`, `cafe.order()`, `cafe.check_consent()`. Reduces integration friction for agent developers. |
| 3.9 | **OpenTelemetry distributed tracing** | 8-16 hrs | Trace requests end-to-end: agent → Cafe → service backend. Critical for debugging latency in proxied requests. |
| 3.10 | **Secrets manager integration** | 2-4 hrs | Move `CAFE_ENCRYPTION_KEY`, `PASSPORT_SIGNING_SECRET`, and RSA keys from env vars to Fly.io secrets or Vault. Reduces exposure surface. |

---

## Probably Not Worth Doing

| Item | Why |
|------|-----|
| **Audit log web viewer** | Admin dashboard already shows recent logs. Full tamper-detection browser is impressive engineering with zero user demand at this scale. |
| **Consent delivery mechanism** | Correct architectural decision to leave this to agents. Cafe provides a consent URL; agents deliver it to humans via their own channels. Adding email/push here would duplicate what agents already do. |
| **Admin pagination** | One admin user, hardcoded LIMIT 50 is fine until 50+ services. |
| **Capability wizard UI for service identity model** | Superseded by JV integration page (step 3b) already built. |
| **Agent-side SDK before real agent testing** | Building `agentcafe-py` (3.8) before testing with real agents (1.9) risks building for imagined pain points. Agent testing first, SDK shaped by results. |

---

## Dependency Map

```
M0 (do immediately)
  0.1-0.4 (ops actions) ──── no code, just config/backups
  0.5-0.7 (security hardening) ──── small code changes, no deps
  0.8 (E2E JV test) ──── should pass before quarantine lifts April 14

M1 (2nd service ready)
  1.1 (garbled scopes) ──┬── 1.2 (garbled action IDs, same parser code)
                         └── 1.4 (operationId heuristic, builds on 1.1)
  1.1 + 1.3 + 1.5 ──── block self-serve onboarding (M1 gate)
  1.9 (agent testing) ──── results reprioritize M2 agent experience items
  1.18 (MCP OAuth) ──── DONE; follow-up hardening:
    1.19 (rate limits) ──── unbounded OAuth endpoint spam risk
    1.20 (token sweep) ──── unbounded table growth
    1.21 (E2E HTTP test) ──── verify SDK wiring, not just provider
    1.22 (OAuth→Passport bridge) ──── deferred until 1.9 agent testing
    1.23 (issuer URL ordering) ──── fragile startup dependency
  1.10 (SQLite backup) ──── HM data is live and unbackuped, urgent

M2 (public beta)
  1.9 results ──── inform priority of 2.1-2.4
  2.5 (email verification) ──── 2.6 (password recovery needs verified email)
  2.25 (revoke_honored) ──── 2.26 (reconciliation endpoint uses this state)
```

---

## Gaps Found During Consolidation

These items were not tracked in any existing document and were discovered during this audit:

| Gap | Placed in | Why it matters |
|-----|-----------|----------------|
| RSA signing key backup | M0 (0.2) | Same catastrophic-loss risk as encryption key |
| Cookie `secure=True` flag | M0 (0.5) | Cookie call sites missing explicit secure flag |
| Company email normalization | M0 (0.6) | Allows duplicate company accounts |
| Wizard CSRF `abs()` fix | M0 (0.7) | Accepts future-dated CSRF tokens |
| CORS tightening | M0 (0.3) | Defaults to `*` in production |
| Structured logging in prod | M0 (0.4) | Implemented in code but not enabled |
| Config startup validation | M1 (1.11) | Silent random secret = tokens invalid on restart |
| JSON body size limits | M1 (1.17) | No payload cap on any endpoint |
| `.env.example` file | M1 (1.14) | No template for required env vars |
| Rate limit response headers | M2 (2.3) | Agents can't discover limits without reading code |
| Graceful shutdown | M2 (2.21) | Deploys may interrupt active requests |

---

*Single source of truth for all open work. Consolidated from `docs/todo-onboarding-improvements.md` (retired), `docs/security/SECURITY-DEBT.md`, `docs/planning/development-plan.md`, and `docs/planning/ux-paths.md`.*
