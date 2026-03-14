# Service Integration Standard — Design Briefing

**Date:** March 11, 2026
**Author:** Cascade (for review by Jeremy + advisors)
**Purpose:** Capture what the Human Memory design exercise taught us about AgentCafe's infrastructure gaps, and define what AC needs to build before any "account-bearing" service can be properly onboarded.

**⚠️ This document is a design briefing, not a specification.** The three artifact specs (Artifact 0: Proxy Behavior, Artifact 1: Per-Request Artifact, Artifact 2: Service Contract) are the **canonical source of truth** for all protocol details. Where this briefing's early design thinking diverges from the specs — e.g., `correlation_id` was merged into `jti`, `linking token` became `linking code` (authorization code pattern), `revoked_at`/`consent_ref` moved from `human_service_accounts` to `authorization_grants`, artifact keys were split from Passport keys — **the specs govern.** This briefing is retained as a historical record of how the design converged but MUST NOT be cited as implementation guidance.

**Review status:**
| Reviewer | Status | Key contributions |
|----------|--------|-------------------|
| Grok | ✅ Reviewed | 3 missing concerns, missing artifact, RS256 split, privacy/GDPR risk |
| Cascade (drafter) | ✅ Reviewed | Initial draft, cross-review with all reviewers, converged positions |
| ChatGPT | ✅ Reviewed | Capability-based taxonomy, RS256 correction, canonical request hashing, key distribution, state machine, 5 additional concerns, sequencing reorder |
| Cascade (reviewer) | ✅ Reviewed | Per-action granularity, naming refinement, consent dropout risk, service availability coupling, partial authorization gap, canonicalization, versioning, SDK artifact, conformance testing |

**Why these gaps weren't visible earlier:** AC's infrastructure was designed — correctly — for stateless, company-owned backends. The demo services (hotels, delivery, home repair) don't have per-human state, don't maintain their own user accounts, and don't need to know which human is behind a request. Every component was built and tested against that model, and nothing is broken within it. The gaps only became visible when the HM adversarial review examined AC's proxy from the *service's* perspective for the first time — "What does the backend actually receive, and is it enough?" — and discovered that for services with their own user accounts, the answer is no. This isn't a bug. It's the founding assumption meeting its first real exception.

---

## 1. Where AgentCafe Stands Today

### 1.1 What's Built and Working

AgentCafe's proxy infrastructure handles a complete agent lifecycle for **stateless, company-owned backends**:

| Component | Status | Key Module |
|-----------|--------|------------|
| **Menu discovery** | Shipped | `cafe/menu.py` — `GET /cafe/menu` |
| **Proxy routing** | Shipped | `cafe/router.py` — `POST /cafe/order` |
| **Tier-1 Passports** | Shipped | `cafe/passport.py` — self-registered, read-only, 3h TTL |
| **Tier-2 Passports** | Shipped | `cafe/consent.py` — human-consented, scoped, risk-tier ceilings |
| **Company Cards** | Shipped | `cafe/cards.py` — standing multi-action policies with budget/duration/scope constraints |
| **Human accounts** | Shipped | `cafe/human.py` — email + passkey, session JWT |
| **WebAuthn passkeys** | Shipped | Full enrollment + assertion + grace period migration |
| **Company Onboarding Wizard** | Shipped | `cafe/wizard_pages.py` — Jinja2, 4-step flow (spec → review → policy → publish) |
| **Consent UI** | Shipped | `cafe/pages.py` — `/authorize/{id}`, passkey-gated approval |
| **Audit logging** | Shipped | SHA-256 hash chain in `router.py` |
| **Backend credential encryption** | Shipped | AES-256-GCM via `crypto.py` |
| **Deployment** | Live | Fly.io, agentcafe.io, CI/CD via GitHub Actions |

**Test coverage:** 271 tests passing, pylint 10.00/10.

### 1.2 How the Proxy Works Today

When an agent calls `POST /cafe/order`, the router:

1. Looks up `proxy_configs` for the `service_id + action_id`
2. **Gate 0:** Checks suspension/quarantine
3. **Gate 1:** Validates the Passport JWT (tier, scope, authorization, jti revocation, policy revocation)
4. **Gate 1b:** Identity verification for medium+ risk (read-before-write check)
5. **Gate 2:** Company policy validation (service live, required inputs present, input types match)
6. Rate limiting (sliding window per passport + action)
7. Proxies to the backend: forwards `req.inputs` as JSON body with the company's stored `backend_auth_header`

**What the proxy sends to the backend:**
- The agent's `inputs` dict (as JSON body)
- The company's `backend_auth_header` (e.g., an API key)

**What the proxy does NOT send to the backend:**
- Any human identity information
- Any proof of which human authorized this request
- Any reference to the consent or policy that authorized it
- Any per-request cryptographic binding
- Any account linking information

This is the core gap. The current proxy model works perfectly for services where:
- The backend doesn't know or care which human is behind the request
- The backend trusts AC's API key and processes inputs generically
- There are no per-human accounts, data, or state on the service side

### 1.3 What's Planned But Not Built

| Item | Phase | Status |
|------|-------|--------|
| **MCP Adapter** | 8.3 | Designed (ADR-029, 4-tool pattern), not built |
| **RS256 signing keys** | 6 | **Shipped** (`keys.py` — RS256 signing, JWKS endpoint, dual-key rotation, HS256 legacy fallback) |
| **Service Integration Standard** | — | **Not yet designed.** This document. |
| **Identity claim forwarding** | — | Not designed |
| **Account linking protocol** | — | Not designed |
| **Revocation propagation** | — | Not designed |
| **Consent text from services** | — | Not designed |

---

## 2. What We Learned from the Human Memory Design

### 2.1 The Design Journey (Rounds 1–9)

The HM design exercise ran through 9 rounds of adversarial review with 4 reviewers (Grok, ChatGPT, Cascade-W, Claude) plus Jeremy. It produced a mature, independent service design — and in doing so, revealed exactly where AC's infrastructure falls short.

The key evolutionary arc:

1. **Rounds 1–3:** Core service design — storage, quotas, key validation, revision semantics, audit, guarantee split. All solid. All carry forward unchanged.

2. **Round 4 (Jeremy's independence reframe):** "If HM can't work without AgentCafe, AgentCafe doesn't work." HM reframed as fully independent — own accounts, own token validation, own dashboard. Three-layer architecture: MCP (discovery) → broker (auth) → HM (storage).

3. **Round 5:** PATs alongside broker tokens. Lightweight account system. MCP as capability discovery only.

4. **Rounds 7–8:** Deep dive into the AC↔HM integration contract. Six decisions locked unanimously:
   - **D1:** Broker assertion contract (two-layer verification)
   - **D2:** Namespace derivation (shared endpoint)
   - **D3:** Unified consent flow
   - **D4:** Zombie namespaces (inform + magic-link email)
   - **D5:** Prefix-scoped consent
   - **D6:** Email as Phase 1 identity anchor

5. **Round 9:** The big reframe — "Passport Presentation Contract." Recognized AC as gateway + consent authority, not a generic broker. **Eliminated D2, D4, D6 by construction.** HM internal UUID replaces HMAC-derived namespace. Account creation becomes part of the D1 contract.

6. **Round 9 ownership discussion:** "Who owns this spec?" → led to the key insight: **the Passport Contract is an AC platform spec, not an HM document.** AC's "ridiculously easy onboarding" intent means these are platform-level patterns, not per-service bilateral contracts.

### 2.2 What Matters to AC as a Gateway

The HM design exercise identified integration concerns that apply to **any service requiring AC to provide delegated human-scoped authority at execution time.** This is not limited to services with user accounts — payments, messaging, regulated actions, and anything with external side effects may also need the richer model. The defining characteristic is not "has accounts" but "must independently verify that AC authorized this specific action for this specific human." These are platform problems, not HM-specific:

> **⚠️ Taxonomy evolution (three corrections):**
> - *Original:* "account-bearing" — too narrow, too implementation-shaped (ChatGPT).
> - *Revision 1:* "jointly-verified" — better, but confusing because ALL Tier-2 services involve delegated authority (Cascade reviewer).
> - *Final:* **"jointly-verified"** — the distinction is about *who verifies at execution time.* **Standard mode:** AC is the sole verifier; the backend trusts AC's API key. **Jointly-verified mode:** the service independently verifies that AC authorized this specific action for this specific human. The term "account-bearing" is retained where account lifecycle is specifically relevant.
>
> **⚠️ Granularity correction (Cascade reviewer):** `integration_mode` is **per-action, not per-service.** `proxy_configs` is keyed by `service_id + action_id`, so this is inherently per-action. A service can have standard actions (e.g., `list` public templates) and jointly-verified actions (e.g., `store` personal data) side by side. The wizard already configures scope, rate_limit, and human_auth per-action; `integration_mode` is consistent with that model. Per-service would be a shortcut that needs ripping out when the first service has mixed actions.

#### 1. Human Identity in Proxied Requests

**The problem:** Today's proxy sends `inputs` + API key. The backend has no idea which human authorized the request. For stateless services (hotel search, delivery order), this doesn't matter. For services with per-human data (memory, payments, messaging), it's a showstopper.

**What HM needs:** Every proxied request must carry a verifiable claim identifying which human authorized it. Not an email — an opaque, stable identifier that the service can use as a partition key.

**What this means for AC:** AC must include human identity information in the proxied request. The format, the signing, and the trust model need to be standardized.

#### 2. Account Linking

**The problem:** A human has an AC account AND a separate account on the service. During consent, the service needs to know: "Is this human already my customer?"

**What HM designed (Round 9 Q2):** A linking token — HM-issued, single-use, 60s JWT. During the consent flow, AC redirects to an HM endpoint. If the human has an existing HM account, HM issues a linking token that ties the AC identity to the HM account.

**What this means for AC:** The consent flow needs a standard "check with the service" step. Not a custom bilateral negotiation — a standard callback pattern that any service with existing users can implement.

#### 3. Account Creation for New Users

**The problem:** A human approves access to a service they've never used. The service needs an account to exist before it can store data. Who creates it?

**What HM designed (Round 9 Q4):** Three paths — new user (AC tells HM to create), email collision (AC tells HM the email, HM checks for existing account), self-identified existing user (human claims to have an account, linking flow triggers). AC acts as "delegated identity proofer" for new account creation.

**What this means for AC:** AC needs a standard "ensure account exists" step in the consent flow. For services without user accounts, this step is skipped. For services with user accounts, AC calls a standard endpoint on the service.

#### 4. Per-Request Authorization Artifacts

**The problem:** Today, the backend trusts AC's API key. That proves "this request came through AC," but not "this specific human authorized this specific action at this specific moment." A compromised or buggy AC could send unauthorized requests indefinitely.

**What HM designed (Round 9 Q5):** Per-request signed artifacts — AC signs a JWT for each proxied request containing: account ID, action, timestamp, scope. The service validates the signature. Replay is impossible (short TTL + action binding). Blast radius is one request.

**What this means for AC:** AC must sign each proxied request. The artifact format, the signing key, and the validation rules need to be standardized. This replaces or supplements the current `backend_auth_header` for account-bearing services.

#### 5. Revocation Propagation

**The problem:** When a human revokes consent on AC (via dashboard or policy revocation), the service needs to know. Today, revocation only affects AC-side token validation — the backend is never notified.

**What HM designed (Round 9 Q6):** AC pushes revocation events to `POST /auth/revoke` on the service. HM maintains a deny state. Short token TTL acts as backstop.

**What this means for AC:** AC needs a standard revocation webhook. Services register a revocation endpoint during onboarding. When a human revokes a policy or card, AC fires the webhook.

#### 6. Consent Text from Services

**The problem:** HM's design doc (§7.4) defines required consent text elements — operations, delete policy, storage limits, revocation terms, durability disclaimer. Today, AC's consent page shows generic text. Services with specific requirements have no way to inject their language.

**What HM's UX flows identified (Gap 1c.8):** "How does HM's required consent language get into AgentCafe's consent page?" This is a general onboarding wizard concern.

**What this means for AC:** The company onboarding wizard needs a step where services provide consent text requirements. AC templates this into the consent page alongside its own standard language.

#### 7. Service-Side Identity Mapping

**The problem:** During onboarding, a service needs to tell AC how it identifies its users. "We match by verified email." "We use phone number." "We have no existing user accounts — assign an opaque ID." Today, the wizard doesn't ask.

**What HM's UX flows identified (Gap 4a):** The onboarding wizard needs an identity mapping step. This determines what human identity claim AC includes in proxied requests.

**What this means for AC:** The onboarding wizard needs a new step: "How do you identify your customers?" The answer configures what AC sends in the per-request artifact.

#### 8. Structured Error Contract for Agents

*Added after Grok review — accepted by Cascade.*

**The problem:** When account linking fails, a service rejects a revoked grant, or account creation hits a collision, the proxy currently forwards raw backend errors. Agents receive opaque JSON and have no standard way to interpret or communicate the failure to the human.

**What this means for AC:** AC needs a standard set of error shapes for account-bearing service failures — `ACCOUNT_LINK_REQUIRED`, `REVOKED_BY_SERVICE`, `ACCOUNT_CREATION_FAILED`, `QUOTA_EXCEEDED`, etc. — with correlation IDs. Agents must be able to distinguish "retry later" from "human action needed" from "permanent failure." This is part of the proxy behavior spec.

#### 9. End-to-End Audit Correlation

*Added after Grok review — accepted by Cascade.*

**The problem:** AC has its own audit hash chain. Services have their own logs. Without a shared correlation ID, "who did what when" disputes are unresolvable across the trust boundary.

**What this means for AC:** The per-request artifact must carry a `correlation_id` that both AC's audit chain and the service log. AC already generates `entry_id` in the audit log — extending that into the per-request artifact is straightforward but must be explicitly spec'd.

#### 10. Revocation Cascade Under Company Cards

*Added after Grok review — accepted by Cascade.*

**The problem:** A Company Card (ADR-028) can cover multiple account-bearing services. When a human revokes a card, revocation must fan out to *every* jointly-verified service under that card — not just one. The briefing's original revocation concern (§5) treated it as single-service.

**What this means for AC:** Card revocation must enumerate all jointly-verified services covered by the card and push revocation events to each. Short token TTL acts as backstop, but the push must be near-instant. Periodic reconciliation ("are you still honoring this grant?") may be needed as a safety net.

#### 11. Revocation Delivery Guarantees & Reconciliation

*Added after ChatGPT review — accepted by Cascade. Grok hinted at this; ChatGPT elevated it from operational to architectural.*

**The problem:** Push-only revocation without delivery guarantees is incomplete by design. If the service is down when AC pushes, the revocation is lost. If the push succeeds but the service fails to persist, the revocation is silently dropped. Push + short TTL is a backstop, not a solution.

**What this means for AC:** Revocation propagation is not just "fire a webhook." It is an architecture of trust that requires: delivery receipts, retry with backoff, periodic reconciliation (AC asks "are you still honoring grant X?"), and admin visibility into delivery status. This must be designed at the protocol level, not bolted on later.

#### 12. Idempotency and Retry Semantics

*Added after ChatGPT review — accepted by Cascade.*

**The problem:** Account creation, revoke delivery, and link completion are all retry-prone operations over network boundaries. If AC times out after the service created the account, what happens on retry? If revoke is delivered twice, what is the expected response? If link completion races with revoke, who wins?

**What this means for AC:** Every service-facing endpoint in the lifecycle protocol must define idempotency behavior. Account creation must be idempotent on the identity claim. Revoke delivery must be idempotent on the grant reference. The protocol must define expected responses for duplicate calls and specify conflict resolution for race conditions.

#### 13. Unlinking, Relinking, and Account Migration

*Added after ChatGPT review — accepted by Cascade.*

**The problem:** Once AC stores "human X ↔ service account Y," the link is not permanent. The human may change email, merge AC accounts, delete their service account, or want to relink to a different service account. "Link once" is not a real lifecycle.

**What this means for AC:** The account lifecycle protocol must include unlinking and relinking operations. The `human_service_accounts` table needs status tracking (active, unlinked, migrated). The consent flow must handle the case where a prior link exists but is stale or revoked.

#### 14. Service-Declared Capability Negotiation

*Added after ChatGPT review — accepted by Cascade.*

**The problem:** Not every jointly-verified service will support all lifecycle operations. Some services support account creation but not linking. Some support linking but not AC-delegated identity proofing. Some want revocation webhooks but don't support reconciliation queries.

**What this means for AC:** The onboarding wizard must capture capability flags — which lifecycle operations the service supports. The protocol must clearly distinguish mandatory operations (artifact validation, revoke intake) from optional operations (account creation, linking, reconciliation queries). AC adapts its consent flow based on declared capabilities.

#### 15. Consent Scope Drift

*Added after ChatGPT review — accepted by Cascade.*

**The problem:** A human approves a Company Card or policy for a service. Later, the service adds a destructive action, expands what "store" means, or changes its data retention. The existing authorization was granted for a different capability set.

**What this means for AC:** AC must detect when a service's declared capabilities change in ways that affect existing authorizations. Options include: invalidating affected policies/cards, notifying humans, requiring re-consent, or freezing the authorization at the capability snapshot that was approved. This needs a position in ADR-030.

#### 16. Consent Flow Complexity and Human Dropout

*Added after Cascade reviewer. Consent fatigue was identified as the #1 existential risk in the strategic review (ADR-028).*

**The problem:** The current consent flow is one click: agent initiates → human approves → done. The jointly-verified consent flow is 3–5 clicks across potentially two sites: agent initiates → AC checks account → redirect to service login → human authenticates with the service → linking token exchange → return to AC → human approves → account creation fires → done. That's 3–5x more steps, more failure modes, more latency, and more places for the human to **abandon the flow.**

**What this means for AC:** Company Cards solved repeat-action consent fatigue. But the first-time ceremony for a jointly-verified action just got substantially harder. The state machine (Artifact 0) must model dropout at every step. The UX must minimize redirects — consider whether account-check and account-creation can happen *after* consent approval rather than during it (the human approves on AC; AC completes setup asynchronously; the first proxied request triggers any pending operations).

#### 17. Service Availability Coupling at Consent Time

*Added after Cascade reviewer.*

**The problem:** Today, AC's consent flow depends only on AC being up. With jointly-verified mode, the consent flow depends on the **service being available** during consent: account-check must respond, the linking page must load, account-creation must succeed. If any of these are down, the consent flow fails — which blocks the agent — which blocks the human's task.

**What this means for AC:** AC's human-facing availability is now partially dependent on the service's operational status — a dependency that doesn't exist today. The state machine must support **deferred account operations**: AC completes consent without the service being available, then performs account-check/create/link asynchronously. The first proxied request triggers any pending setup. This adds complexity to the state machine but eliminates the availability coupling.

#### 18. Partial Authorization Gap

*Added after Cascade reviewer.*

**The problem:** A human has a Company Card for Human Memory. The card says `memory:store` is authorized. But account linking hasn't happened yet — maybe the card was approved before the linking protocol existed, or the linking failed and was never retried. The agent calls `POST /cafe/order` for `memory:store`. AC checks the card — authorized. AC builds the per-request artifact — but there's no `service_account_id` to put in the `sub` claim because the link doesn't exist.

**What this means for AC:** `authorized_but_unlinked` is a distinct state in the state machine. The agent needs a specific error: `ACCOUNT_LINK_REQUIRED` with a human-action URL. The consent flow must handle the case where authorization exists but the service-side binding is incomplete. This is a timing gap between AC-side authorization (card/policy says yes) and service-side readiness (no linked account).

#### 19. Standard Versioning

*Added after Cascade reviewer.*

**The problem:** The briefing proposes the integration standard as a fixed artifact ratified by ADR-030. Standards evolve. When v1.1 adds a new mandatory operation (e.g., reconciliation becomes mandatory), do existing services need to upgrade? Is there a deprecation policy? A version field in capability declarations?

**What this means for AC:** Without versioning, the first standard revision creates a backward-compatibility crisis. ADR-030 must include: a version field in capability declarations, a deprecation policy for old versions, and a migration path for existing services. The per-request artifact should carry a `standard_version` claim so services know which protocol variant AC is using.

### 2.3 What This Changes in AC's Architecture

The current architecture assumes **all backends are stateless and company-owned.** The HM design exercise proved that the first real service breaks this assumption.

| Current assumption | What HM revealed | Impact |
|---|---|---|
| Backend doesn't need human identity | Services with user accounts need it on every request | Per-request artifact required |
| `backend_auth_header` is sufficient trust | Services need proof of authorization, not just proof of origin | Per-request signing required |
| Consent is AC-only concern | Services have consent text requirements | Onboarding wizard extension |
| Revocation is AC-internal | Services need revocation notification | Webhook infrastructure |
| No service-side accounts | Services may have existing user bases | Account linking protocol |
| One proxy model fits all | Delegated-authority services need richer proxying | Opt-in via `integration_mode` in `proxy_configs` |
| Proxy errors are pass-through | Account lifecycle failures need standard error shapes | Structured error contract |
| Audit is AC-internal | Cross-boundary disputes need shared correlation | Correlation ID in per-request artifact |
| Revocation is per-policy | Company Cards span multiple services | Revocation fan-out infrastructure |
| Revocation is push-and-forget | Push can fail, service can lose state | Delivery receipts + reconciliation protocol |
| Operations are fire-once | Network boundaries cause retries and races | Idempotency semantics on all lifecycle endpoints |
| Links are permanent | Humans change email, merge accounts, delete service accounts | Unlinking/relinking/migration protocol |
| All services support all operations | Services vary in lifecycle capability | Capability negotiation during onboarding |
| Service capabilities are static | Services add/change actions after authorization | Consent scope drift detection |
| AC doesn't broadcast identity (ADR-024) | Jointly-verified services need identity claims | **Deliberate exception to ADR-024** — constrained by data minimization |
| Consent is one click | Jointly-verified consent is 3–5 steps across two sites | Consent dropout risk; deferred operations |
| Consent depends only on AC | Account-check/create depends on service being up | Service availability coupling; deferred consent |
| Authorization = ready to execute | Card says yes but account link may not exist | `authorized_but_unlinked` state in state machine |
| Standard is fixed at v1 | Protocol will evolve | Versioning in capability declarations + artifact claims |

---

## 3. What We Need to Do Now

### 3.1 The Core Design Artifacts

These are the documents AC needs before any account-bearing service (HM being the first) can be properly onboarded:

#### Artifact 0: Proxy Behavior, State Machine & Failure Modes Spec

*Added after Grok review, expanded after ChatGPT review. ChatGPT: "teams routinely under-spec distributed state transitions when they are merely implied across two artifacts."*

**Priority: Critical — without this, implementation of everything else will be ad-hoc.**

Defines how `router.py` orchestrates the jointly-verified path end-to-end, including an **explicit state machine** for the consent-to-execution lifecycle:

- **State machine:** `consent_initiated` → `account_checked` → `account_created` | `link_pending` → `link_complete` → `active` → `revoke_queued` → `revoke_delivered` → `revoke_acknowledged`. Plus: `reconcile_failed`, `link_expired`, `account_creation_failed`, `partial_failure`, **`authorized_but_unlinked`** (Cascade reviewer: card says yes, account link doesn't exist), **`service_unreachable`** (Cascade reviewer: service down during consent), **`consent_abandoned`** (Cascade reviewer: human dropped out mid-flow).
- When does the proxy call account-check vs. account-create?
- What does the agent receive during a linking redirect?
- What are the standard error shapes for every failure mode (linking required, account creation failed, service-side revocation, quota exceeded, **account link required**)?
- How does the proxy decide which path to take (answer: `integration_mode` per-action in `proxy_configs`)?
- What correlation ID is attached and where?
- **Idempotency:** Expected behavior for duplicate calls at every state transition.
- **Race conditions:** Link completion vs. revoke, account creation timeout vs. retry, concurrent consent for same human+service.
- **Consent dropout modeling (Cascade reviewer):** The jointly-verified consent flow is 3–5 steps vs. 1 for standard mode. The state machine must model human abandonment at every step and define recovery paths. Consider deferred operations: human approves on AC first, account setup completes asynchronously.
- **Deferred account operations (Cascade reviewer):** If the service is unreachable during consent, AC can complete the human-facing approval and perform account-check/create/link when the service comes back. The first proxied request triggers pending setup. This eliminates service availability coupling.

This is the "how" document that turns the other artifacts into implementable router logic. It may be folded into the lifecycle protocol later, but must be designed first-class.

#### Artifact 1: Per-Request Authorization Artifact Spec

**Priority: Critical — blocks everything else.**

This is the signed JWT that AC attaches to every proxied request to a jointly-verified service. It replaces (or supplements) the static `backend_auth_header`.

Must define:
- **Claims:** `sub` (human account ID on the service), `iss` (agentcafe), `aud` (service_id), `iat`, `exp` (short — 30s), `action` (action_id), `scopes`, `consent_ref` (policy_id or card_id), `correlation_id`, `identity_binding` (e.g., `broker_delegated` | `service_native`), `request_hash` (canonical hash of the request body + method + path), `standard_version` (protocol version, Cascade reviewer)
- **Signing:** RS256 (asymmetric). Service validates with AC's public key via JWKS endpoint. JWT header MUST include `kid` (key ID) so the service can select the correct public key without parsing the full JWT header (Cascade reviewer — standard JWKS practice).
- **Delivery:** Header `X-AgentCafe-Authorization: Bearer <jwt>`. Does not mutate the inputs body. The `request_hash` claim binds the signature to the request content without restructuring it.
- **Validation rules:** What the service MUST check. What it MAY ignore.

> **⚠️ Correction (ChatGPT review):** The original briefing stated RS256 was deferred to Phase 6. This was wrong. Phase 6 is **complete** — `keys.py` already implements RS256 signing, JWKS endpoint, dual-key rotation, and HS256 legacy fallback. Passport V2 tokens are already signed with RS256. The question is not "does this force migration" but "how do we extend the existing RS256/JWKS infrastructure to serve per-request artifacts to external services." The infrastructure exists; the extension is operational (separate key pair or shared, JWKS endpoint visibility, cache TTL guidance for services).
>
> This error weakened confidence in the briefing's specificity at the exact point where architectural reality mattered most. The sequencing and open question framing were based on a stale premise. Fixed.

**Canonical request hashing (ChatGPT, all reviewers converged):** The artifact must include a hash of the canonical request (body + HTTP method + path) so the signature covers what was actually sent. This is the cleanest answer to the header-vs-wrapper question: the header carries the signed artifact, the artifact binds to the request content via hash. Services can verify both the authorization and the request integrity without AC restructuring their payload.

**⚠️ Canonicalization precision (Cascade reviewer):** Canonical hashing across trust boundaries is notoriously fragile. JSON key ordering, Unicode normalization, floating-point representation, and whitespace handling can all cause disagreements between AC's canonicalization and the service's. The artifact spec must define the canonicalization algorithm precisely — either RFC 8785 (JSON Canonicalization Scheme) or the simpler approach: hash the raw request body bytes as-is with SHA-256 (no re-parsing, no normalization). The simpler approach avoids all canonicalization disagreements because both sides hash the same bytes. **Do not leave this to "implementation."**

**Key distribution sub-spec (ChatGPT; Cascade reviewer confirms as subsection of this artifact):** If services validate AC-signed artifacts, AC must publish a standard for: JWKS endpoint discovery, key rotation signaling, rollover grace periods, cache TTL recommendations, and failure behavior when keys are unavailable. This is a subsection of this artifact, not a standalone spec — it's tightly coupled to artifact validation and splitting it increases the chance of inconsistency.

This is the artifact that Round 9 Q3 and Q5 converged on. The HM design work already defined most of the requirements — this artifact just needs to be written as an AC-owned spec.

#### Artifact 2: Service Contract & Identity Binding Protocol

**Priority: High — needed for HM onboarding.**

*Renamed from "Account Lifecycle Protocol" after ChatGPT review: "This is not just lifecycle. It is identity binding, account provisioning, linking, unlinking, revocation intake, and recovery from partial failure. Call it what it is or people will under-scope it."*

Defines the full standard interaction between AC and a jointly-verified service:

1. **Account check:** During consent, AC calls `POST /integration/account-check` on the service with the human's identity claim. Service responds with `exists: true/false`, optionally a linking URL, and account status (active, banned, rate-limited).
2. **Account creation:** If the human is new, AC calls `POST /integration/account-create` with the identity claim after human approval. Service creates an account, returns the account ID. Must be idempotent on the identity claim.
3. **Account linking:** If the human has an existing account, the consent flow includes a redirect to the service's linking page. Service issues a linking token. AC stores the account ID.
4. **Unlinking / relinking:** Human can unlink via AC dashboard. AC calls `POST /integration/unlink`. Service marks the binding inactive. Relinking follows the linking flow with a new binding.
5. **Revocation:** `POST /integration/revoke` — AC pushes revocation events with delivery receipt expected. Service maintains deny state. Must be idempotent on the grant reference.
6. **Reconciliation:** `GET /integration/grant-status?ref=<consent_ref>` — AC periodically verifies the service is still honoring (or denying) a grant. Service responds with current grant status.
7. **Capability declaration:** During onboarding, the service declares which of the above operations it supports. Mandatory: artifact validation, revoke intake. Optional: account creation, linking, reconciliation.

Services that do not need joint verification skip all of this — the current proxy model works unchanged.

*Artifact 2 internal structure (Cascade reviewer convergence):* This artifact covers three tightly coupled but distinct concerns. It must be organized with explicit sections and dependency arrows: **§A Identity Binding** (check, create, link, unlink, relink), **§B Revocation** (intake, deny state, delivery receipts, reconciliation), **§C Capability Negotiation** (declaration, mandatory vs. optional, versioning). These are not split into separate artifacts because the operations are interdependent — but the internal structure must prevent under-scoping.

#### Artifact 3: Onboarding Wizard Extensions

**Priority: Medium — needed before the first account-bearing service ships.**

New wizard steps for services that have user accounts:

- **Identity mapping:** "How do you identify your customers?" (email, phone, opaque ID, none)
- **Integration endpoints:** Account check URL, account creation URL, linking URL, revocation URL
- **Consent text:** Required consent language, service-specific terms
- **Service type:** Stateless (current model) vs. account-bearing (extended model)

This extends the existing 4-step wizard flow. Stateless services see no change.

#### Artifact 4: ADR-030 — Service Integration Standard

**Priority: High — but ratifies, not leads.**

*Sequencing revised after ChatGPT review: "Writing the ADR before the artifact spec is locked invites a fake sense of closure. The ADR should ratify the standard after the per-request artifact and service contract are concrete enough to survive implementation review."*

A new ADR that:
- Defines two integration modes (standard vs. jointly-verified) via `integration_mode` in `proxy_configs` — classified by required capabilities, not by whether the service has user accounts
- Ratifies the per-request artifact format (RS256, header delivery, canonical request hash, correlation ID)
- Ratifies the service contract & identity binding protocol
- Establishes the "AC owns the standard, services implement it" principle
- References the HM design exercise as the driver
- **Names the ADR-024 exception explicitly:** AC originally enforced human-scoping without broadcasting identity to backends. The jointly-verified mode is a deliberate, constrained exception. The ADR must state this plainly and define data minimization boundaries: when AC may send verified email vs. opaque ID, who may persist what, and what disclosures are required on the consent page
- Includes explicit privacy/GDPR section: processor obligations, consent disclosure requirements, data minimization constraints
- Defines consent scope drift handling: what happens to existing authorizations when a service changes its declared capabilities
- Includes standard versioning: version field in capability declarations, deprecation policy, migration path for existing services

#### Artifact 5: Reference SDK (`agentcafe-service-sdk`)

*Added after Cascade reviewer.*

**Priority: High — needed before HM onboarding. Without this, the standard is a spec that services implement incorrectly.**

A Python reference library that handles the service side of the integration standard:

- Per-request artifact validation (RS256 signature verification, `kid` lookup via JWKS, `request_hash` verification, expiry check, scope enforcement)
- JWKS key fetching and caching (with rotation handling and failure behavior)
- Linking token handling (issue, validate, exchange)
- Revocation intake endpoint helpers (idempotent processing, delivery receipt response)
- Reconciliation query response helpers

This is the difference between "spec that works" and "spec that sits in a docs folder." HM is the first consumer. The SDK is how we prove the standard is implementable and how we keep the onboarding burden manageable for jointly-verified mode.

#### Artifact 6: Integration Conformance Test Suite

*Added after Cascade reviewer.*

**Priority: Medium — needed before second jointly-verified service.**

A test harness that verifies a service correctly implements the integration standard before going live:

- Calls declared integration endpoints with test data
- Verifies artifact validation (sends valid artifact → 200, expired artifact → 401, wrong audience → 403, tampered `request_hash` → 400)
- Verifies revocation intake (sends revoke → delivery receipt, sends duplicate revoke → idempotent response)
- Verifies account-check and account-creation endpoints respond with expected shapes
- Reports pass/fail per capability

The onboarding wizard should include a "test your integration" step that runs conformance checks. Without this, AC will debug every service individually — exactly what the standard is supposed to prevent.

### 3.2 What Does NOT Need to Change

The HM design exercise confirmed that most of AC's existing infrastructure is correct and carries forward:

- **Passport V2 model** — bearer authorization, two-tier, risk-tier ceilings. Correct.
- **Company Cards** — standing policies with constraints. Correct.
- **Consent flow** — agent initiates, human approves with passkey. Correct. Needs extension, not replacement.
- **Menu format** — additive changes only (ADR-009). The per-request artifact is outside the Menu.
- **Proxy routing** — `POST /cafe/order` is the right entry point. The proxy gets smarter for account-bearing services, but the agent-facing API doesn't change.
- **Audit logging** — hash chain is correct. Per-request artifacts add to the audit trail, not replace it.
- **WebAuthn passkeys** — human authentication is solid.
- **Onboarding wizard** — extends, doesn't replace.

### 3.3 Sequencing

*Sequencing revised after ChatGPT review: lock protocol surface first, then ratify with ADR.*

| Step | What | Depends on |
|------|------|------------|
| 1 | Write Per-Request Artifact Spec (RS256 extension, canonical hash, key distribution, `kid`, canonicalization algo, `standard_version`) | Nothing |
| 2 | Write Proxy Behavior, State Machine & Failure Modes Spec (incl. deferred ops, consent dropout, `authorized_but_unlinked`) | Nothing |
| 3 | Write Service Contract & Identity Binding Protocol (§A Identity Binding, §B Revocation, §C Capability Negotiation) | Nothing |
| 4 | Write ADR-030 (ratifies artifacts 1–3, names ADR-024 exception, privacy/GDPR, consent scope drift, versioning) | Artifacts 1–3 stable |
| 5 | Build Reference SDK (`agentcafe-service-sdk`, Python) | Artifact spec + service contract |
| 6 | Implement per-request signing in `router.py` (extend existing RS256/JWKS) | Artifact spec |
| 7 | Implement proxy jointly-verified path + state machine | Proxy behavior spec |
| 8 | Implement service contract endpoints (AC side) | Service contract spec |
| 9 | Extend onboarding wizard (`integration_mode` per-action + capability flags + endpoints) | Service contract spec |
| 10 | Extend consent flow (account check/create/link/unlink + deferred operations) | Service contract + wizard |
| 11 | Implement revocation webhook (with card fan-out + delivery receipts + reconciliation) | Service contract spec |
| 12 | Build Conformance Test Suite | SDK + all specs |
| 13 | Onboard HM as first jointly-verified service | All of the above |

### 3.4 Open Questions — Converged Positions & Remaining

Positions marked ✅ are converged across all four reviewers (Grok + Cascade drafter + ChatGPT + Cascade reviewer).

1. **Per-request artifact delivery mechanism.** ✅ *Converged (4/4):* Header `X-AgentCafe-Authorization: Bearer <jwt>` with canonical request hashing inside the artifact and `kid` in the JWT header for key selection. Body wrapper rejected — it mutates the inputs schema. The `request_hash` claim binds the signature to the request content without restructuring the payload. Services verify both authorization and request integrity.

2. **Signing key management.** ✅ *Converged (4/4), corrected:* RS256 infrastructure already exists (`keys.py`, Phase 6 complete). The question is how to extend the existing RS256/JWKS infrastructure for per-request artifacts to external services. Key decisions needed: separate key pair for service artifacts vs. shared with Passport signing, JWKS endpoint visibility (same `/.well-known/jwks.json` or separate), and operational guidance (cache TTL, rotation signaling, rollover grace periods, failure behavior).

3. **Account ID storage.** ✅ *Converged (4/4):* New `human_service_accounts` table. Schema: `ac_human_id`, `service_id`, `service_account_id`, `binding_method` (email_match | linking_token | delegated_creation), `binding_status` (active | unlinked | migrated), `identity_binding` (broker_delegated | service_native), `linked_at`, `updated_at`, `revoked_at`, `consent_ref`. Provenance matters for trust decisions downstream.

4. **Backward compatibility.** ✅ *Converged (4/4):* Opt-in via `integration_mode` **per-action** in `proxy_configs` (inherently per-action because `proxy_configs` is keyed by `service_id + action_id`). Standard-mode actions see zero changes. Jointly-verified actions declare required capabilities and integration endpoints. A single service can have mixed modes. Strict mode declaration, not a fuzzy toggle.

5. **Consent text integration.** ✅ *Converged (4/4):* Wizard captures structured blocks + `terms_url` with **maximum length enforced by the wizard** (Cascade reviewer). AC renders standardized sections with service branding on the consent page, inside AC-owned templates. Human acknowledges service-specific terms separately if flagged as required. No free-text injection. AC owns the human-facing frame (consistent with ADR-025) and **reserves the right to edit or reject** service consent text that is misleading, overly complex, or inconsistent with the actual operation.

6. **MCP adapter interaction.** ✅ *Converged (4/4):* Adapter stays transparent. `cafe.invoke` hits existing `POST /cafe/order` path; per-request artifacts are added server-side by `router.py`. The real risk is leaking account-linking complexity into agent-visible semantics — if an MCP client has to know whether a service is jointly-verified, we failed. Keep the complexity in AC.

7. **Privacy/GDPR processor status.** ✅ *Converged (4/4):* AC MUST default to **opaque ID** for all jointly-verified proxying. Verified email is sent ONLY when the service explicitly declares `identity_matching: email` during onboarding AND the consent page tells the human: *"Your email address will be shared with [Service Name]."* This is not optional — GDPR Article 13 requires disclosure of recipients. The ADR-024 exception is framed as "data minimization with explicit, informed consent," not "we're sending identity now."

8. **HM rework scope.** ✅ *Converged (4/4):* ~70% of Round 9's design carries forward directly. The linking token format (Q2), three-path hybrid (Q4), and revocation push (Q6) map to Artifact 2 operations. The per-request signature (Q5) maps to Artifact 1. What needs rewriting: endpoint paths (HM-specific → standard), payload shapes (conform to standard schemas), and the linking token audience. The rework is real but bounded — adaptation, not redesign.

9. **Broker-delegated account trust semantics.** ✅ *Converged (4/4):* The per-request artifact carries `identity_binding` (e.g., `broker_delegated` | `service_native`). Services make their own trust decisions based on provenance. Document explicitly rather than pretending it's invisible. The `human_service_accounts` table stores this permanently.

10. **Identity matching blame boundaries.** ✅ *Converged (4/4):* AC's liability is limited to: *"Did AC present the identity claim that matches its own verified human account?"* If AC's human account has verified email X, and the service said "match by email," and AC sent email X, and the match was wrong because the service had a different person with the same email — that's the service's problem. AC's contractual obligation: (a) send only verified identity claims, (b) log every identity-binding decision with correlation ID, (c) provide the forensic trail on request. This must be in the onboarding agreement, not buried in docs.

11. **Onboarding burden vs. "ridiculously easy."** ✅ *Converged (4/4):* Accept it. Jointly-verified is a **premium integration tier**, not the default happy path. "Ridiculously easy" applies to standard mode. For jointly-verified, the promise is "as standardized and well-supported as possible." Provide: (a) reference SDK (Artifact 5), (b) sandbox/staging environment with mock consent flows, (c) conformance test suite (Artifact 6), (d) worked examples.

12. **Two trust domains.** ✅ *Converged (4/4):* The human doesn't need to understand the distinction — AC's consent page communicates practical implications in plain language (*"This service will create an account for you using your email"* vs. nothing for standard mode). Admin dashboard and audit logs distinguish modes clearly. Keep codepaths cleanly separated (not interleaved with conditionals throughout `router.py`). The bigger risk is organizational: the complex mode will consume disproportionate engineering attention. Mitigate by keeping explicit engineering budgets per mode.

**New questions surfaced in final review round:**

13. **Audience-of-one risk.** *Surfaced by Cascade reviewer, not yet tested.* HM is the first and only customer of the jointly-verified standard. Before finalizing the protocol artifacts, run a **thought experiment** with at least one other hypothetical service (Agent Payments via Stripe is the obvious candidate). If the standard doesn't work for Stripe integration without modifications, it's not a standard — it's an HM adapter.

---

## 4. Summary

The Human Memory design exercise was the most thorough adversarial review in AC's history — 9 rounds, 4 reviewers, 30+ documents. Its most important output isn't the HM service design. It's the discovery that **AC's proxy model is incomplete for services that need delegated human-scoped authority**, and the identification of exactly which platform-level capabilities are missing.

The key insight from the Round 9 ownership discussion: the Passport Contract isn't an HM spec — it's an **AC Service Integration Standard.** AC defines it once. Services implement it. HM is the first customer.

The briefing is directionally correct. Four adversarial reviews surfaced 19 integration concerns, 7 artifacts, 13 converged positions, and a taxonomy that evolved three times before stabilizing. The document is still under-specified where distributed systems usually fail — state transitions, retries, canonicalization, consent dropout, and service availability coupling — but those are now explicitly flagged for the artifact specs to resolve. The next step is to lock the protocol-level artifacts (per-request artifact, state machine, service contract), validate with a second-service thought experiment (Stripe), build the reference SDK, and then ratify with ADR-030.

### Risks Identified During Review

*Surfaced across all four reviews. All converged.*

- **Privacy/GDPR processor obligations.** Forwarding identity claims to services creates regulatory exposure. Default to opaque ID; verified email only with explicit `identity_matching: email` declaration + consent page disclosure. Deliberate exception to ADR-024.
- **HM rework debt.** ~30% of Round 9 bilateral artifacts need rewriting to conform to the AC-owned standard. Bounded but real.
- **Revocation delivery and reconciliation.** Push-only revocation is incomplete by design. Delivery receipts, retry, periodic reconciliation, and admin visibility are architectural requirements.
- **Revocation cascade under Company Cards.** Card revocation must fan out instantly to multiple services.
- **Broker-delegated account trust.** Document `identity_binding` explicitly. Services make their own trust decisions.
- **Crypto latency on hot path.** Existing `keys.py` handles signing efficiently. Service-side JWKS caching behavior needs measurement.
- **Identity matching liability.** AC guarantees verified identity claims. Service is responsible for matching accuracy. Contractual position in onboarding agreement.
- **Two trust domains.** Sole-enforcer (standard) vs. joint-enforcer (jointly-verified). Strategic shift. Keep codepaths separated. Budget engineering attention per mode.
- **Consent scope drift.** No detection or remediation mechanism exists. ADR-030 must take a position.
- **Onboarding burden.** Jointly-verified is a premium integration tier. SDK + conformance suite + examples are the mitigation.
- **Consent flow dropout.** First-time jointly-verified consent is 3–5 steps vs. 1. Deferred operations reduce human-facing complexity.
- **Service availability coupling.** Consent flow now depends on service being up. Deferred account operations eliminate the coupling.
- **Partial authorization gap.** Card says yes, account link doesn't exist. `authorized_but_unlinked` state must be handled explicitly.
- **Canonicalization disagreements.** `request_hash` across trust boundaries is fragile. Must specify algorithm precisely (raw bytes + SHA-256 recommended).
- **Standard versioning.** First revision without a version field creates backward-compatibility crisis.
- **Audience-of-one.** Standard designed around HM. Must validate with Stripe thought experiment before finalizing.
- **Briefing credibility note.** Original RS256 factual error fixed. Weakened confidence in sequencing specificity.
