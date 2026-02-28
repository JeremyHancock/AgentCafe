# Passport V2 — Design Discussion & Open Questions

**Date:** February 27, 2026
**Participants:** Jeremy (project lead) + Claude (advisor) + Grok (beneficial adversary)
**Status:** **Eight core positions locked** (Feb 27 three-way review + follow-up session). MVP scope defined. Ready for canonical spec drafting. Deferred questions listed in §11.
**External review:** ChatGPT (Feb 27) provided adversarial feedback on the concrete flow — selectively incorporated where it added specificity. Grok (Feb 27) red-teamed all positions across two rounds; convergence reached on all eight. Core framing, Cafe-side identity verification, and bearer-model reasoning are original to this discussion.
**Depends on:** `design.md` (Phase 2 implementation), `threat-model.md` (v1.4, locked)

---

## Context

The existing Passport system (Phase 2) is implemented and tested. The threat model (v1.4) laid out a phased plan through Layers 1–3, passkey enrollment, standing mandates, and velocity rules. This discussion revisits foundational assumptions in light of a core question: **what does it actually mean to issue a Passport?**

The V1 threat model got many things right — Design Principles Zero and One hold up, the three-layer trust model is solid, the activation code flow is well-designed. But the framing of the Passport as a "digital Power of Attorney" and the implicit assumption that agent identity matters have been challenged.

---

## 1. Core Reframing: The Passport Belongs to the Human

### The shift

The V1 design framed the Passport as: "This agent represents this human." The V2 reframing is: **"This human authorizes the bearer to take these actions on their behalf."**

The distinction matters. In V1, the Passport is *for* an agent, approved by a human. In V2, the Passport is *the human's document*, carried by whatever agent the human chooses. The agent is the bearer, not the subject.

### Why

The Power of Attorney analogy breaks down because POA names a specific, verifiable second party — "I authorize *him* (a known human)." In the agent world, there is no verifiable second party. An agent is ephemeral software with no persistent, verifiable identity. What we have is: "I authorize *the bearer*" — closer to a bearer instrument in finance than a POA.

This isn't a failure of the system. It's an honest recognition of a new paradigm: **a verified human delegating authority to an unverifiable autonomous entity.** There is no clean real-world analog. The closest parallels (bearer bonds, cash, credit cards) all break down because those instruments don't act on their own.

### What this preserves from V1

- **Design Principle Zero** (physical-world identity anchor) — still the trust root
- **Design Principle One** (`agent_id` is untrusted) — strengthened: agent identity is now explicitly out of scope, not just untrusted
- **Three-layer trust model** — Layers 2, 2.5, and 3 still hold
- **Activation code flow** — still the right cold-start pattern
- **Cafe-authored consent text** — still essential
- **Backend token isolation** — still essential

### What this changes from V1

- **Layer 1 no longer requires passkey just to browse.** Agents should be able to enter the Cafe and explore freely (see §2 Tiered Model).
- **"Register their agent(s) by agent_id" during account setup** — removed. The human registers themselves, not their agents. The Passport doesn't name an agent.
- **The "digital Power of Attorney" framing** — replaced with "human-issued bearer authorization for autonomous agents."

---

## 2. The Tiered Model

### Tier 1: Read-only Passport (agent self-requests)

An agent can register with the Cafe and receive a read-only Passport without any human involvement. This Passport allows:

- Browsing the Menu
- Discovering services
- Calling read-only endpoints (search, get details, check availability)

The Passport is rate-limited and the Cafe tracks the agent's identity (via a stable hashed handle) for abuse prevention. Companies can override the default and require a full human-approved Passport even for read actions on sensitive endpoints.

**Rationale:** If we require human involvement just to browse, we eliminate the core value proposition — agents acting autonomously to discover whether a task can be accomplished. The Cafe must remain an open marketplace for exploration.

**Earlier rejected alternative:** No Passport at all for reads. Rejected because without any Passport, all anonymous agents look identical and rate limiting becomes impossible. A malicious agent (or simple script) could repeatedly call `GET /cafe/menu` followed by every read-only action across all services, systematically downloading the complete catalog of every registered company’s offerings — full hotel inventories and real-time pricing, restaurant menus and delivery options, home-service availability windows and rates, capability tags, constraints, etc. — every few minutes or faster with proxies. This would enable competitive data theft (rival marketplaces or model training), price arbitrage, and uncontrolled load on the Cafe proxy, audit log, and all backend services (an effective DoS).  

The lightweight Tier-1 Passport solves this cleanly: the Cafe now has a stable, rate-limitable handle (tied to a hashed `agent_tag` + `jti`) without adding any human friction for honest agents. Abuse is detectable, throttleable, and blockable while discovery stays essentially free.

### Tier 2: Write-scope Passport (human-approved)

For any action with real-world consequences (booking, canceling, purchasing, modifying), the human must explicitly consent through a Cafe-owned authorization flow. The human must have a Cafe account.

**The consent flow:**
1. Agent discovers a service and selects an action it wants to use
2. Agent requests authorization → gets back a Cafe consent URL (or activation code for new users)
3. Human opens URL / enters code → sees Cafe-branded authorization page
4. Human reviews specific actions in plain language (Cafe-authored, not agent-authored)
5. Human approves selected scopes with a chosen duration
6. Cafe issues Passport with approved scopes
7. Agent receives Passport → can now order

### 2.1 Concrete Flow Specification

The abstract consent flow above maps to specific technical steps. The key architectural choice is **separating the durable policy (server-side) from the ephemeral token (handed to the agent).** The agent never holds the policy — only short-lived tokens issued under it.

**Step 1: Agent requests a read Passport.**
The agent calls the Cafe and receives a Tier-1 read Passport (JWT). This is *not* "agent registration" — the Cafe assigns a rate-limit handle, not an identity. The agent provides an optional `agent_tag` (untrusted, for audit trail only).

**Step 2: Agent browses the Menu.**
The Menu response must include enough metadata for the agent to request consent properly:
- `service_id`, `action_id`, `required_scopes`
- Risk level / value class (determines which Layer of confirmation applies)
- Required constraints schema (what limits the human can set — e.g., max price, time window)
- Whether account linking is required for this action

The current Menu format has `required_scopes` and `cost.limits` but lacks the constraints schema and account-linking flag. These need to be added during onboarding.

**Step 3: Agent initiates consent.**
`POST /consents/initiate` with the read Passport. Body includes: `service_id`, `action_id`, requested constraints. The agent may include a `task_summary` string, but this is untrusted — the Cafe uses it only as a hint when authoring the consent text the human will see.

Cafe returns: `consent_id` (server-side record), a consent URL or activation code, and the Cafe-authored consent text (or a reference to it).

**Step 4: Human approves.**
Human opens the consent URL. If not logged in, the signup flow runs first (combined with approval per the activation code pattern from threat model v1.4 §5). Human reviews Cafe-authored plain-language description, selects scopes, sets constraints and duration, confirms (with passkey for high-value actions).

Cafe writes a **policy record** (server-side, never leaves the Cafe):
- `policy_id`, `cafe_user_id`, `service_id`, allowed `action_ids`, limits/constraints, expiry
- Immutable audit metadata: timestamp, device info, consent text version shown

**Step 5: Agent receives a short-lived token.**
The agent polls `GET /consents/<consent_id>/status` until approved, then exchanges the consent for a short-lived write token via `POST /tokens/exchange`. The token is a Tier-2 Passport JWT (5–15 minute expiry) that references the `policy_id`. The policy is long-lived (30–90 days); the token is not.

This separation means: stealing a token gives 15 minutes of access. Stealing a policy ID is useless without the Cafe issuing a token under it. The human can revoke the policy at any time, killing all future token issuance.

**Step 6: Agent executes.**
Agent calls the order endpoint with the write token. Cafe validates the token, looks up the referenced policy, enforces scopes + constraints + ownership (see §5), proxies to backend if everything passes, records the audit event.

### 2.2 Multi-Agent Token Model

A single human task may involve many agents working together — an orchestrator, sub-agents for discovery, sub-agents for execution. The Cafe must handle 5 or 100 agents arriving simultaneously under the same human's authorization. The bearer model makes this work naturally, but the token lifecycle needs to account for it.

**Browsing: unlimited Tier-1 swarm.**
Each sub-agent can self-request its own Tier-1 read Passport. 50 agents browsing the Menu in parallel is fine — each has its own rate-limited token, no coordination needed. The Cafe handles the swarm through per-token rate limiting.

**Ordering: policy-scoped, not token-scoped.**
When it’s time to act, the orchestrator (or any agent with the `consent_id`) exchanges the approved consent for a Tier-2 write token. The key design choices:

- **Token refresh is non-consuming.** Any agent with a valid token under a live policy can call `POST /tokens/refresh` and get a fresh token. The old token is *not* invalidated — it dies naturally at expiry. This allows multiple agents to independently hold and refresh tokens under the same policy.
- **Concurrent tokens are capped per policy.** The Cafe enforces a **hard global ceiling of 20 active tokens per policy**, regardless of what the Menu's `concurrency_guidance.max_active_tokens_per_policy` suggests. The Menu value is advisory ("recommended max = 5"); the Cafe cap is enforcement. Agents that exceed it receive `429 Policy Token Limit Reached`. This prevents a runaway system from creating thousands of tokens. The orchestrator requests tokens for its executor agents; if it needs more, older ones must expire first.
- **Rate limits are per-policy, not per-token.** If Alice’s policy allows 60 requests/minute for hotel bookings, that’s 60 total across *all* agents using tokens under that policy. 100 agents each making 1 request hit the limit the same as 1 agent making 100. This naturally incentivizes agent systems to coordinate rather than flood. Rate-limit windows are sliding (consistent with the existing Phase 2 policy engine) and logged per `policy_id` in `audit_log` for human dashboard visibility.

**Rate-limit communication principle (three-way agreement, Feb 27):**
Agents must never learn per-policy semantics through trial-and-error. The Cafe communicates rate-limit scope at every natural touchpoint:

- **Discovery time (Menu):** `cost.limits` includes the explicit qualifier `rate_limit_scope: "per_policy"` (additive, ADR-023). Agents reading the Menu before acting already know the budget is shared.
- **Enforcement time (429):** Clear, machine-readable error body explaining the shared budget, `retry_after_seconds`, and `policy_id`. Required for Phase 4 MVP.
- **Issuance time (token response):** Optional `policy_limits` snapshot with `remaining_requests_in_window` and `active_tokens_under_policy` for convenience. Phase 4.x (not MVP).
- **Documentation:** "Building Agents for AgentCafe" guide states the rule upfront. Essential long-term, zero cost now.

- **Single-use tokens serialize critical actions.** A single-use token for “cancel reservation” means exactly one cancel per token. Each cancellation requires a separate `POST /tokens/refresh`. The orchestrator must deliberately request each one.
- **The policy is the single kill switch.** Human revokes the policy → no new tokens can be issued, all existing tokens are rejected on next use. Doesn't matter how many agents hold copies.

**Revocation mechanics:** Revoking a policy sets `policy.revoked_at = NOW()`. Any token whose `iat` is older than the revocation timestamp is rejected on next use. This gives true instant revocation for all risk tiers without per-`jti` blocklisting or per-request overhead — the policy row is already fetched during token validation, so the check is one additional column read.

**Concurrency guidance in the Menu.**
The Menu can signal to agent systems how many parallel executors make sense per action (see §13 `concurrency_guidance`). This is advisory, not enforced — but well-behaved agent frameworks will read it. Combined with per-policy rate limits, this steers multi-agent systems toward the organized pattern:
- Many agents browse freely (Tier-1)
- A small number of executor agents place orders (Tier-2)
- One orchestrator manages token lifecycle

**What the Cafe does NOT do:** manage which agents are part of the same “system.” The Cafe doesn’t know or care about agent orchestration topology. It manages policies and tokens. The multi-agent coordination is the agent system’s responsibility.

### 2.3 Consent Lifecycle and Agent Ephemerality (converged, Feb 27 — three-way review)

The flow in §2.1 assumes the agent that initiates consent is still running when the human approves. In practice, this may not be true. The human might take hours or days to sign up. The agent might be a short-lived process, a serverless function that times out, or part of a conversation the human closed and will return to tomorrow.

The `consent_id` is a **durable claim ticket** — not a transient polling handle. The agent platform is **100% responsible** for persisting it. The Cafe does not store, query, or recover consent IDs on behalf of agents.

**Primary pattern: Pickup.**
- `consent_id` is a UUIDv4 (high-entropy, not guessable).
- Agent stores it in its own persistent state (conversation memory, task queue, database).
- When the human approves (via activation code or consent URL), the consent becomes "approved" server-side.
- Any agent instance — the original or a new one — calls `POST /tokens/exchange` with the `consent_id` + its read Passport. No continuous polling required.
- **TTL default: 72 hours** (human has 3 days to approve). Configurable per consent in `POST /consents/initiate` (max 7 days). Expired consents return `410 Gone`.

**Secondary pattern: Polling (opt-in, short-lived tasks).**
- Agent polls `GET /consents/<consent_id>/status` with mandatory exponential backoff (min 30s interval after the first 60s).
- Works when the human approves within minutes. Probably 80% of real-world cases — agent asks, human is already in the conversation, approves quickly.
- Not recommended for delays >30 minutes. Documented as such in API docs.

**Secondary pattern: Webhook (opt-in, persistent agent platforms).**
- Agent provides an optional `callback_url` in `POST /consents/initiate`.
- Cafe posts once on approval: `POST <callback_url>` with `{consent_id, status}`. Idempotent. Agent must validate HTTPS and provide a secret in the URL query param for authentication.
- Designed for platforms like LangGraph, CrewAI, etc. that have persistent backends but ephemeral agent processes.

**No consent discovery endpoint.** A `GET /consents?agent_tag=...` endpoint would be a privacy foot-gun — anyone with a stolen `agent_tag` could enumerate pending consents. The human's dashboard will show "Pending approvals" (queried by `cafe_user_id`), but agents cannot query it.

**Enforcement of consent privacy boundaries**  
The human dashboard’s “Pending approvals” view (queried by `cafe_user_id`) is deliberately inaccessible to agents. Enforcement is achieved through strict separation of JWT families: agent Passports (`aud: "agentcafe"`) are rejected by human-session middleware (`aud: "human-dashboard"`), and the dashboard endpoint `GET /human/dashboard/pending-consents` accepts no query parameters and performs server-side filtering only. There is no `/consents?agent_tag=…` or equivalent discovery surface. This mirrors the existing wizard/company-session separation and prevents any agent (malicious or compromised) from enumerating another human’s pending consents even if it possesses a stolen `agent_tag`.

**If the agent loses the `consent_id`, the link is broken.** This is by design — the Cafe cannot safely determine which agent "should" receive a token. Recovery path: the human can see the pending approval on their dashboard and re-initiate, or the agent system re-requests consent.

---

## 3. The Cafe as Sole Issuer and Consent Broker

### Decision

The Cafe is the only entity that issues Passports. The Cafe owns the entire consent flow — the authorization page, the consent text, the issuance ceremony. No third-party issuers, no company-run issuers, no self-issued Passports (except tier-1 read-only).

### Why

- **Product value:** If anyone else can issue Passports, the Cafe's trust guarantee is meaningless. The Passport's value comes from the Cafe standing behind it.
- **Liability clarity:** The Cafe can defend its issuance process. If a company or third party issues a bad Passport, the Cafe gets blamed but had no control.
- **Consistency:** Every human sees the same consent experience. No fragmented UX.

### Risks acknowledged

- **Single point of failure.** If the Cafe is down, no write-scope Passports can be issued or refreshed. Every write action across every service is blocked.
- **Key management burden.** One signing key for all Passports. Compromise affects everything. Key rotation and HSM storage are required for production.
- **Liability position.** The Cafe is now in the authorization chain, not just the verification chain. This is a different legal position. The Cafe guarantees the consent was obtained, but not that the human fully understood the implications or that the agent acted in the human's interest.

---

## 4. Human Registration

### Decision

Humans must have a Cafe account to authorize write scopes. The agent can direct the human to sign up, but the human must complete the registration themselves.

### What this provides

- **Verified identity chain:** Passport → Human (verified Cafe account holder)
- **Revocation:** Human can log in and revoke any Passport
- **Audit trail:** Human can see what was done under their authorization
- **Liability anchor:** "alice@example.com, verified account holder, approved these scopes on this date"

### Friction acknowledged

This adds friction. The agent must tell the human to create a Cafe account before any write action can happen. For a first-time user, the flow is: agent asks → human signs up → human approves → agent acts. The activation code flow (from threat model v1.4 §5) mitigates this by combining account creation and first approval into a single ceremony.

---

## 5. Cafe-Side Identity Verification (Proposed Principle Two)

### The problem

If a human authorizes "cancel hotel reservations," that authorization must be scoped to *the human's own reservations*, not anyone's. Without this constraint, a Passport with cancel-reservation scope is a weapon.

### The naive solution (rejected)

Require backends to accept a human identity header and scope every operation to that human's data. Rejected because:
- Most APIs don't have per-user data isolation at the API level
- Dramatically shrinks the pool of onboardable services
- Broadcasts human identity to every backend (privacy problem)
- The Cafe can't verify the backend actually enforced the scoping

### The proposed solution: Cafe-side data inspection

The Cafe enforces human-scoping by inspecting the data flowing through it, without requiring backend changes or sending human identity to backends.

**During onboarding**, the company tags API response/request fields with identity semantics:
```json
{
  "field": "customer_email",
  "type": "string",
  "human_identifier": "email"
}
```

**At runtime, for writes on existing resources (cancel, modify):**
1. Agent wants to cancel reservation #ABC123
2. Cafe forces a read first — `GET /reservations/ABC123`
3. Response includes `customer_email: "alice@example.com"`
4. Cafe checks: does the tagged `human_identifier:email` field match the Passport's human? Yes → forward the cancel. No → reject.

**For writes that create new resources (book, order):**
- If the request has input fields tagged as `human_identifier`, the Cafe verifies the agent is submitting the correct human's information.

**The wizard AI enricher** could infer identity field tags during spec parsing — fields named `customer_email`, `user_email`, `guest_name` are strong candidates.

### Converged position: layered verification by risk tier (three-way review, Feb 27)

The three-way review (Jeremy + Claude + Grok) resolved the identity verification approach. Instead of a single mechanism, verification is **layered by risk tier**:

| Risk tier | Verification | Rationale |
|-----------|-------------|----------|
| **Low-risk write** | Agent-supplied identifier match only | Fast reject on mismatch. Minimal latency. |
| **Medium-risk write** | Agent-supplied match (fast reject) + Cafe read-before-write (ground truth) | Belt and suspenders. Extra read is ~200ms. |
| **High / Critical write** | Cafe read-before-write mandatory, no shortcut. Single-use token. | Maximum safety for destructive operations. |

**Why agent-supplied alone is insufficient for high-risk:** The agent holds the JWT and can read `sub` from it. A rogue agent targeting Bob's reservation can trivially echo Alice's email from the JWT while submitting Bob's reservation ID. The Cafe's exact-string match passes — but the resource doesn't belong to Alice. Only the read-before-write check catches this, because it gets ground truth from the backend's data.

**Why agent-supplied is acceptable for low-risk:** The blast radius is small, and the performance win matters. The agent-supplied check still catches accidental mismatches and non-adversarial errors.

The `human_identifier_field` in the Menu schema (see §13) serves double duty: it tells the Cafe what to check in both the fast path (agent-supplied input matching) and the full path (read-before-write field targeting).

**Onboarding impact:** Companies tag identity fields during the wizard flow. The AI enricher infers most of them from field names (`customer_email`, `user_id`, `guest_name`). The company confirms or corrects — minutes of work.

**Requirement:** At least one strong identifier (email or phone) per endpoint that involves destructive writes. Name matching is supplementary, never sole.

### Remaining open questions on identity verification

- **Actions that aren't "self_only."** "Book a hotel for my boss," "send flowers to my mom" — the human is the requester but not the subject. Is `self_only` the only valid constraint? Are there legitimate `on_behalf_of` scenarios? For MVP, `self_only` only. Needs further thought.
- **Account linking.** If the Cafe knows Alice as `alice@example.com` but StayRight knows her as `alice.h@gmail.com`, the match fails. Does this require a per-service account linking step? How much friction does that add?

---

## 6. Agent Identity: Intentionally Out of Scope

### The hard problem

Agents are ephemeral software. They have no physical-world anchor. An agent's entire identity — code, keys, tokens, secrets — is copyable data. You cannot give an agent something it can possess but not share.

### Approaches considered

| Approach | Stops casual sharing? | Stops intentional sharing? | Practical for MVP? |
|----------|----------------------|---------------------------|-------------------|
| Shared registration secret | Marginally | No | Yes |
| Cryptographic key pair (request signing) | Yes | No (key is copyable) | Possible |
| Rolling proof / hash chain | Detectable fork | No (agents can coordinate) | Complex |
| One-time nonces per request | N/A (nothing to share) | No (proxy trivial) | Performance concern |
| Hardware attestation (TPM/TEE) | Yes | Yes | No (agents don't run in TEEs) |

### Decision

Agent identity is out of scope. The Passport is the human's document. The Cafe does not attempt to verify that the agent presenting the Passport is a specific entity. The system works regardless of whether agents develop stable identities in the future.

### Rationale

- **The thing we can verify (human identity) is the thing that matters.** The human authorized the actions. The human is accountable. The human can revoke.
- **Intentional cooperation between agents is expected and acceptable.** Modern AI agents are often multi-agent systems. An agent delegating to sub-agents, all operating under the same Passport, is normal behavior — not abuse.
- **Unintentional sharing (theft) is mitigated by:** short-lived tokens, revocation, audit trail, and anomaly detection. Not by agent identity.

### The `agent_tag` field

The Passport may carry an optional `agent_tag` — a self-reported, untrusted label. It appears in the audit trail so the human can see "something calling itself 'TravelBot' used my Passport." This is for UX, not security. It follows Design Principle One exactly.

---

## 7. Bearer Risk and Mitigation

### The core risk

The Passport is a bearer token. Whoever holds it can use it. An agent that receives a Passport can share it with any other agent, and the Cafe cannot tell the difference.

### Why this might not be the threat we think

- **Read-only Passports are free.** No motivation to share.
- **Write-scope Passports are tied to a human.** The audit trail shows the human's Passport was used. If something goes wrong, the human is accountable — the sharing agent gets no benefit and the original agent gets blamed.
- **Agents that cooperate intentionally are one system.** An agent delegating to a sub-agent isn't "sharing" any more than a web server sharing a database connection with its worker threads.

### Converged mitigation: per-policy token expiry with risk-tier ceilings (three-way review, Feb 27)

Fixed 15-minute expiry was too blunt (Grok). The converged position: **human chooses token lifetime within Cafe-enforced ceilings per risk tier.** The threat model's asymmetric ceremony applies — the human can go *shorter* freely, but *longer* requires step-up auth.

| Risk tier | Ceiling | Default | Examples |
|-----------|---------|---------|----------|
| Low-risk write | 60 min | 30 min | Search with side effects, save preferences |
| Medium-risk write | 15 min | 10 min | Book a room, place an order |
| High-risk write | 5 min | Single-use | Cancel reservation, financial transaction |
| Critical | Single-use only | Single-use | Delete account, large purchase |

The risk tier is declared per-action during onboarding (see §13 Menu schema extension) and shown in the Menu response. The Cafe enforces the ceiling; the human can only go shorter.

**Key insight (Grok):** Single-use tokens for critical actions mean a cancel-reservation token can only cancel *one* reservation. The agent needs a fresh token for each destructive action. That's the right friction.

### Rolling proof: deferred to Phase 4 (three-way review, Feb 27)

The rolling proof / hash chain concept (detection signal for Passport forking) is **deferred to Phase 4.** Rationale:

- Short-lived tokens with per-risk-tier ceilings already reduce blast radius to minutes or single-use
- The rolling proof has unsolved operational problems (false positives from network failures, recovery mechanism as attack vector)
- Building it now is premature optimization against an unquantified threat

**Phase 3 defense-in-depth instead:**
- Short-lived tokens (primary mitigation)
- Server-side anomaly signals — IP change, request volume, geographic impossibility (rule-based, not ML)
- Human-facing audit dashboard (the human can see what their Passport did and react)

If Phase 3 telemetry shows token theft/sharing is a real operational problem, rolling proof becomes a Phase 4 priority with real data to inform the design.

### Remaining open questions on bearer risk

- **Anomaly detection specifics.** What signals exactly? How much infrastructure? Rule-based is sufficient for Phase 3 but what's the schema?
- **Concurrent token cap per policy.** What’s the right default? 5? 10? Should this be human-configurable or Cafe-enforced?

---

## 8. Rogue Agent + Inattentive Human

### The threat

An agent sends a consent URL (or activation code) to someone who isn't the human it claims to represent, or to a human who doesn't read what they're approving.

### Mitigations discussed

- **Human must log into their Cafe account** on the consent page. The Passport binds to a verified identity, not just "whoever clicked."
- **Cafe-authored plain-language consent text.** "This will allow the bearer to book hotel rooms on your behalf and charge your credit card." Not technical scope strings.
- **Cooling-off / second factor** for high-value actions. Email confirmation, SMS code. Prevents drive-by approvals.
- **The agent cannot self-approve.** The consent page could require interaction patterns that are hard for automated agents to complete (CAPTCHA, etc.).

### The deeper problem

The threat model v1.4 already identified this: *"Agent operating the human's email/SMS — not a compromise, but intentional inbox access granted by the human."* If the agent has access to the human's email, it can intercept confirmations. The passkey requirement (Layer 3, high-value actions) is the real defense — the agent cannot tap a physical device. For lower-value actions where email confirmation is acceptable, this remains a risk.

### Converged position: passkey hard for Tier-2, N/A for Tier-1 (three-way review, Feb 27)

Tier-1 read Passports involve no human ceremony — the agent self-requests. There's no human to do a passkey tap. The passkey requirement was always about anchoring *human identity*, and Tier-1 has no human in the loop. The question doesn't apply.

For Tier-2 (write-scope approval): **no softening.** The threat model v1.4 killed limited-mode accounts for exactly the right reason — any account without a hardware anchor is a mass-registration attack surface. The V2 reframe makes this even more important: the Passport is the *human's* document, and the human's identity is the only security in the system. Weaken the identity anchor and the entire Passport is worthless.

The 96% passkey coverage number should be re-verified, but the principle is sound: if your device can't do passkeys, you don't get Cafe trust guarantees. You can still use agents — just not through the Cafe's mediated trust layer.

**Locked.** Design Principle Zero preserved.

---

## 9. Proposed Passport V2 Claims

```json
{
  "iss": "agentcafe",
  "sub": "user:alice@example.com",
  "aud": "agentcafe",
  "exp": 1740260100,
  "iat": 1740259200,
  "jti": "uuid-v4",
  "tier": "write",
  "scopes": ["stayright-hotels:book-room", "stayright-hotels:cancel-booking"],
  "human_scope_constraint": "self_only",
  "granted_by": "human_consent",
  "policy_id": "policy-uuid",
  "agent_tag": "travel-assistant",
  "authorizations": [
    {
      "service_id": "stayright-hotels",
      "action_id": "book-room",
      "limits": {
        "max_night_rate": 500,
        "valid_until": "2026-03-01"
      }
    }
  ]
}
```

### New fields vs V1

| Field | Purpose | Notes |
|-------|---------|-------|
| `tier` | `"read"` or `"write"` | Distinguishes self-requested vs human-approved |
| `human_scope_constraint` | `"self_only"` | Actions scoped to human's own resources. Only valid value for now. |
| `granted_by` | `"self"` or `"human_consent"` | How this Passport was obtained |
| `policy_id` | Reference to long-lived policy | Short-lived token traces back to the human's consent policy |
| `agent_tag` | Self-reported agent label | Untrusted. For audit trail UX only. |

### Open questions on claims

- Is `human_scope_constraint` the right name? Alternatives: `scope_boundary`, `resource_constraint`, `ownership_scope`.
- Should `agent_tag` be in the JWT at all, or only in the Cafe's server-side session state?
- Should the rolling proof state be a JWT claim or a separate header?

---

## 10. What the Cafe Guarantees (and What It Doesn't)

### The Cafe guarantees

- A real human with a verified Cafe account authorized these specific actions
- The human saw Cafe-authored plain-language descriptions of what they were approving
- The Passport's scopes are enforced — out-of-scope requests are rejected
- Actions on existing resources are verified against the human's identity (via Cafe-side data inspection)
- The audit trail records what happened under every Passport
- The human can revoke any policy instantly. All tokens issued under that policy are rejected on their next presentation to the Cafe.
- Backends never see the human's Passport or identity

### The Cafe does NOT guarantee

- That the agent carrying the Passport is any specific entity
- That the agent will act in the human's best interest within the authorized scopes
- That the human fully understood the implications of their authorization
- That backends correctly implement their APIs or honor the Cafe's proxy semantics
- That the Passport hasn't been shared with other agents (sharing is detectable but not preventable)

---

## 11. Open Questions — Resolved vs. Remaining

### Resolved (three-way review, Feb 27)

| # | Question | Resolution |
|---|----------|------------|
| 3 | Passkey requirement for new tiered model? | **Hard for Tier-2, N/A for Tier-1.** Principle Zero preserved. |
| 6 | Read-before-write mandatory or advisory? | **Layered by risk tier.** Low = agent-supplied only. Medium = both. High/Critical = Cafe read mandatory. |
| 7 | 15-minute token expiry? | **Per-policy human-chosen with risk-tier ceilings.** Single-use for critical. |
| 8 | Rolling proof false positive rates? | **Deferred to Phase 4.** Short-lived tokens + anomaly detection sufficient for Phase 3. |
| 14 | Menu response additions? | **ADR amendment now, before dashboard.** See §13 for schema. |
| 9 | Concurrent token cap default? | **Cafe-enforced hard ceiling of 20 per policy.** Menu `concurrency_guidance` is advisory only. `429 Policy Token Limit Reached` on exceed. |
| 10 | Consent lifecycle for ephemeral agents? | **Primary: Pickup** (agent persists `consent_id`). Webhook optional. Polling with backoff. TTL 72h default, max 7 days. No discovery endpoint. See §2.3. |
| 11 | Who persists the `consent_id`? | **Agent platform, 100%.** Cafe does not query by `agent_tag`. Lost `consent_id` = broken link (by design). Human dashboard shows pending approvals for recovery. |

### Remaining open (deferred past MVP — none block Phase 4 implementation)

#### Foundational

1. **Is the bearer model robust if agent attestation standards emerge?** The model should work regardless — agent identity is additive, not required — but this needs validation against emerging standards (e.g., IETF agent attestation drafts).
2. **Is `self_only` the only valid resource constraint?** "Book for my team," "manage my company's account," "send a gift to someone else" — legitimate use cases that don't fit `self_only`. Deferred past MVP.

#### Architectural

3. **How does Cafe-side identity verification scale?** Read-before-write adds one backend call per medium+ destructive write. Acceptable at MVP volume; needs caching/optimization strategy at scale.
4. **Account linking friction.** Cafe email ≠ service email. Per-service linking step adds friction. How much? Is there a lighter approach? MVP proceeds with the friction; UX refinement follows.
5. **Anomaly detection specifics.** What signals, what schema, how much infrastructure? Rule-based is decided; details emerge during implementation.

---

## 12. Relationship to Existing Documents

- **`design.md` (Phase 2):** Still accurate for what's currently implemented. V2 does not change the existing JWT structure or validation — it extends it.
- **`threat-model.md` (v1.4):** Core principles and Layers 2–3 hold. Layer 1 entry requirements, agent registration during setup, and the POA framing need revision per V2 reframing. The threat model should not be modified until remaining open questions are resolved.
- **`DECISIONS.md`:** ADR-023 (Menu schema extension, ADR-009 amendment) + ADR-024 (Passport V2 bearer authorization model).
- **`DEVELOPMENT-PLAN.md`:** Updated to reflect Passport V2 design convergence. Phase 4 rewritten with specific implementation items.

---

## 13. Three-Way Review — Converged Positions (Feb 27, 2026)

**Reviewers:** Jeremy (project lead), Claude (advisor), Grok (beneficial adversary)

### Summary table

| Question | Position | Status |
|----------|----------|--------|
| Identity inspection | Layered by risk tier: agent-supplied for low, +read-before-write for medium+, mandatory read for high/critical | **Locked** |
| Passkey | Hard requirement for Tier-2 (write scopes). N/A for Tier-1 (no human ceremony). | **Locked** |
| Token expiry | Per-policy human-chosen with Cafe-enforced ceilings per risk tier. Single-use for critical. Asymmetric ceremony preserved. | **Locked** |
| Menu schema | ADR amendment now (ADR-023), before dashboard UI. Additive fields, 100% backward compatible. | **Locked** |
| Rolling proof | Deferred to Phase 4. Short-lived tokens + rule-based anomaly detection + human audit dashboard for Phase 3. | **Locked** |
| Multi-agent token model | Non-consuming refresh. Per-policy rate limits. Hard ceiling of 20 concurrent tokens. Single-use serializes critical actions. Policy is the kill switch. | **Locked** |
| Consent lifecycle | Primary: Pickup (agent persists `consent_id`). Webhook optional. Polling with backoff. 72h TTL default. No discovery endpoint. Agent platform owns persistence. | **Locked** |
| MVP scope | Thinnest vertical slice: Tier-1 read Passports, human accounts + passkey, consent endpoints (initiate/status/exchange), server-rendered consent page (Jinja2), one demo service with new schema fields, policy table + token refresh. No webhooks, no identity verification beyond input matching, no anomaly detection, no multi-agent concurrency enforcement. | **Locked** |

### Menu schema extension (ADR-023)

New optional fields inside each Action object in the Menu:

```json
{
  "risk_tier": "low | medium | high | critical",
  "human_identifier_field": "customer_email",
  "constraints_schema": { "max_night_rate": {"type": "number"}, "valid_until": {"type": "string", "format": "date"} },
  "account_linking_required": false,
  "self_only": true
}
```

| Field | Type | Default | Purpose |
|-------|------|---------|--------|
| `risk_tier` | `"low"` \| `"medium"` \| `"high"` \| `"critical"` | `"medium"` | Determines token lifetime ceiling and verification depth |
| `human_identifier_field` | `string \| null` | `null` | Single field name in action inputs/responses containing human identity (e.g., `"customer_email"`). Single string for MVP; array support additive later if needed. |
| `constraints_schema` | `object \| null` | `null` | JSON Schema the consent UI renders for human-settable limits |
| `account_linking_required` | `boolean` | `false` | Whether the human must link their service account before using this action |
| `self_only` | `boolean` | `true` | Actions scoped to human's own resources. Future `on_behalf_of` support will use `false`. |

Additionally, inside the existing `cost.limits` object, one additive qualifier:

```json
"cost": {
  "required_scopes": ["stayright-hotels:book-room"],
  "limits": {
    "rate_limit": "60/minute",
    "rate_limit_scope": "per_policy"
  }
}
```

| Field | Type | Default | Purpose |
|-------|------|---------|--------|
| `cost.limits.rate_limit_scope` | `"per_policy"` | `"per_policy"` | Tells agents the rate-limit budget is shared across all tokens under the same policy. See §2.2 rate-limit communication principle. |

Additionally, a new optional **concurrency guidance** object at the action level:

```json
{
  "concurrency_guidance": {
    "recommended_executors": 1,
    "max_active_tokens_per_policy": 5,
    "reason": "Hotel booking API processes reservations sequentially"
  }
}
```

| Field | Type | Default | Purpose |
|-------|------|---------|--------|
| `concurrency_guidance` | `object \| null` | `null` | Advisory signal to multi-agent systems about optimal parallelism |
| `.recommended_executors` | `integer` | `1` | How many agents should place orders for this action simultaneously |
| `.max_active_tokens_per_policy` | `integer` | `5` | Advisory recommended cap. **Cafe enforces a hard global ceiling of 20** regardless of this value. |
| `.reason` | `string \| null` | `null` | Human-readable explanation for agent systems to log/display |

All fields are optional → 100% backward compatible with existing Menu entries. Old seeded services default sensibly. Wizard-published services populate them via the review step (AI enricher pre-fills, company confirms).

---

*This document is a working artifact. Eight core positions are locked (§13). Remaining open questions (§11) are deferred past MVP — none block Phase 4 implementation. Ready for canonical spec drafting. Last updated: February 27, 2026.*
