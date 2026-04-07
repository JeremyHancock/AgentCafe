# AgentCafe — Strategic Review Briefing

**Date:** March 6, 2026
**Purpose:** Give an uninitiated reviewer enough context to critically examine AgentCafe's strategic direction, identify blindspots, and stress-test assumptions.
**Audience:** AI advisors who have not seen this codebase before.

---

## 1. What AgentCafe Is

AgentCafe is a live product (deployed at agentcafe.io) that acts as a **trusted proxy between AI agents and real-world services.**

The core metaphor: a Cafe where agents walk in, browse a Menu of available services, and place orders. The Cafe sits between the agent and the service backend, enforcing safety at every step.

**How it works today:**

1. A company registers a service by submitting its API spec through an onboarding wizard. The Cafe's AI enricher generates a clean, semantic Menu entry. The company configures policies (rate limits, auth requirements, risk tiers). The service is published to the Menu with a 7-day quarantine period.

2. An agent calls `GET /cafe/menu` and receives a semantic listing of all available services — no HTTP methods, no paths, no raw schemas. Just: what each service does, what inputs it needs, what it returns, what authorization is required.

3. For read-only actions (search hotels, check availability), the agent self-registers for a Tier-1 "Passport" (a JWT) and can call these endpoints freely, subject to rate limits.

4. For write actions (book a room, place an order, cancel a reservation), the agent must get human authorization. It initiates a consent request → the human receives a link or activation code → the human reviews a Cafe-authored plain-language description of what's being authorized → the human approves with a passkey → the Cafe issues a short-lived Tier-2 token. The agent uses this token to place the order.

5. Every request flows through the Cafe proxy. The Cafe validates the token, enforces scopes, checks rate limits, verifies identity (for destructive writes), proxies to the backend, and logs an audit trail with SHA-256 hash chaining.

**Key architectural decisions:**

- **The Passport is a human-issued bearer authorization.** Not an agent identity document. "I authorize the bearer" — not "I authorize Agent X." Agent identity is intentionally out of scope because agents are ephemeral, copyable software with no verifiable identity.
- **The Cafe is the sole trusted issuer and consent broker.** No third-party issuers. The Cafe owns the consent text, the authorization UI, the token issuance, and the audit trail.
- **Backends never see the human's identity or the Passport.** The Cafe handles credential injection, identity verification, and scope enforcement. Companies hand over their API credentials during onboarding; these are encrypted at rest (AES-256-GCM).
- **Token expiry is governed by risk-tier ceilings.** Low: 60 min, medium: 15 min, high: 5 min, critical: single-use. The human chooses within these ceilings.

---

## 2. What's Been Built (Current State)

The product is feature-complete for beta. 271 tests passing, deployed to Fly.io, CI/CD via GitHub Actions.

**What exists and works:**
- Full proxy with double validation (Passport + company policy) on every request
- 3 demo backend services (hotel booking, lunch delivery, home services) — these are mock services returning fake data
- Company onboarding wizard (server-rendered Jinja2 pages): upload an OpenAPI spec → AI enriches it into a Menu entry → company reviews/edits → configures policies → publishes
- Human accounts with WebAuthn passkey authentication
- Full consent flow: agent initiates → human approves with passkey → Cafe issues short-lived token
- Human policy dashboard (view/revoke active authorizations)
- Platform admin dashboard
- RS256 JWT signing with JWKS endpoint and key rotation
- Backend credential encryption, tamper-evident audit logging, quarantine system, service suspension
- E2E demo agent CLI and integration examples for GPT and Claude
- Landing page at agentcafe.io

**What doesn't exist yet:**
- Real services (only 3 demo mocks)
- Real agent traffic
- Structured logging / observability
- Any paying customers or external users
- Mobile-optimized consent UX
- Sign-up funnel for companies or humans

---

## 3. The Strategic Conversation So Far

### The core question we're wrestling with:

**Why would an AI agent use AgentCafe?**

### Position 1: "Agents don't choose their own tools — developers do"

Today, most AI agents operate with a fixed toolbox defined by their developer:
- ChatGPT uses GPT Actions configured in a UI
- Claude uses MCP servers installed by the user
- LangChain/CrewAI agents use developer-defined Python tools
- Enterprise agents use IT-admin-configured integrations

In this world, AgentCafe is another integration a developer wires up. The value proposition is weaker — the developer could just integrate directly with each API.

### Position 2: "This is changing fast"

Several categories of agents already choose their own tools at runtime:
- **Computer-use agents** (Claude computer use, OpenAI Operator) navigate the open web
- **Research agents** (Deep Research, Perplexity) choose which sources to query
- **Code agents** (Devin, Cursor) discover and install packages, read docs, call APIs
- **Multi-agent systems** where planners dynamically assign tasks and select tools

In 6–18 months, the trajectory is toward more autonomy, not less. Tool-use is becoming a core model capability. Agents will increasingly discover and evaluate tools at runtime rather than relying on developer pre-configuration.

### Position 3: Jeremy's strategic direction (the one we're examining)

Jeremy (project lead) has taken a clear position:

> **"We're building this for autonomous agents. Ignore non-autonomous agents — they're a quick step in evolution. The best way for AgentCafe to be found by autonomous agents is to have real APIs connected that agents would actually use. Use agent traffic as the selling point for more company onboardings. This might mean writing our own services — services designed for agents, not humans, that offer something beyond what's publicly available. The services are a marketing/bootstrapping strategy. The real product IS the Cafe itself: a safe place to expose non-public APIs to agents without companies having to build that safety infrastructure themselves. The human authorization layer is the other core product."**

This decomposes into:

1. **Target audience:** Autonomous agents only. Non-autonomous agents are irrelevant (here today, gone tomorrow, or persist as helpers to autonomous agents).
2. **Bootstrap strategy:** Build real, agent-useful services that offer value beyond public APIs. These services attract agent traffic. Agent traffic proves demand. Demand attracts company onboardings.
3. **The services are marketing, not the product.** The Cafe (proxy + safety + audit) and the human authorization layer (Passport + consent flow) are the product.
4. **The product's value to companies:** "You want agents to be able to use your API, but you don't want to build the entire safety infrastructure (consent flows, human authorization, audit trails, rate limiting, credential isolation). The Cafe handles all of that. Just plug in your API."

---

## 4. What We Need You to Examine

### A. Assumptions that may be wrong

1. **"Autonomous agents are inevitable and imminent."** What if agent autonomy plateaus? What if regulatory pressure constrains agent autonomy? What if the computer-use/browsing paradigm dominates and agents never need a structured API discovery layer because they just use websites?

2. **"Agents will seek out AgentCafe on their own."** How does an autonomous agent discover AgentCafe? Through training data? Through web search? Through another agent recommending it? Is "build it and they will come" viable for an agent-facing product?

3. **"The Cafe's safety layer is valuable enough that companies will onboard."** Companies already have rate limiting, auth, and audit logging. What specific safety gap does the Cafe fill that companies can't solve themselves? Is the value proposition "safety" or "distribution" (access to agent traffic)?

4. **"Non-autonomous agents are irrelevant."** Is it risky to ignore the dominant form of agent usage today? Could serving developer-configured agents (e.g., as an MCP server) be a low-cost way to build traffic and brand while waiting for autonomous agents to mature?

### B. Strategic gaps to probe

1. **The chicken-and-egg problem.** Agents need services to come. Companies need traffic to justify onboarding. Building your own services bootstraps the supply side — but what bootstraps the demand side? How do agents learn about the Cafe?

2. **Competitive positioning vs. MCP.** Anthropic's Model Context Protocol is becoming a standard for tool discovery and use. If every SaaS ships an MCP server, does AgentCafe become redundant? Or does MCP's lack of authorization/consent/safety features leave room for the Cafe?

3. **What "agent-useful services" actually means.** What services would agents use that they can't get from the open web? Is there a concrete list, or is this still abstract? What's the minimum viable service that would attract real agent traffic?

4. **Revenue model.** The Cafe is free for companies today. What's the business model? Transaction fees? Subscription? Freemium with premium safety features? This affects which services to build and which companies to target.

5. **The "services as marketing" tension.** If the Cafe's own services are the best thing on the platform, does that discourage companies from onboarding? ("Why would I list my hotel API when the Cafe already has a hotel aggregator?") Or does it demonstrate the platform's value?

6. **Human consent UX at scale.** The consent flow requires a human to visit a URL, log in, review a description, and approve with a passkey — every time an agent needs a new authorization. For autonomous agents doing many things, this could become a bottleneck. How many consent requests per day is acceptable before the human disengages?

7. **Trust bootstrapping.** Why would a human trust AgentCafe with their authorization? The Cafe is an unknown startup asking humans to approve financial transactions through its platform. What builds that trust?

### C. Alternative strategic directions to consider

1. **Cafe as an MCP meta-server.** Instead of a proprietary protocol, expose the Cafe's Menu as MCP tools. Any MCP-compatible agent can discover and use Cafe services. The Cafe adds the consent/authorization layer that MCP lacks.

2. **Cafe as an authorization-only layer.** Don't compete on discovery. Let agents find APIs however they want (MCP, web search, training data). Position the Cafe solely as the human authorization and safety proxy. Companies integrate the Cafe when they want to let agents use their API safely.

3. **Cafe as infrastructure for agent platforms.** Sell to LangChain, CrewAI, AutoGen, etc. "Your agents need to interact with the real world safely. Integrate AgentCafe and get human authorization, audit trails, and rate limiting out of the box."

4. **Vertical-first.** Instead of a horizontal marketplace, dominate one vertical (e.g., travel, food delivery, fintech) with real integrations, then expand.

---

## 5. Technical Architecture Summary (for context)

- **Stack:** Python 3.12 + FastAPI + SQLite + LiteLLM
- **Auth:** RS256 JWT with JWKS endpoint, WebAuthn passkeys for humans, bcrypt for passwords (with grace period migration to passkey-only)
- **Proxy model:** Agent calls `POST /cafe/order` → Cafe validates token → enforces policy → injects backend credentials → proxies to real backend → logs audit event
- **Consent flow:** `POST /consents/initiate` → human visits URL → approves with passkey → `POST /tokens/exchange` → agent gets short-lived JWT
- **Menu format:** Semantic, agent-friendly JSON. No HTTP methods or paths exposed. Locked schema (additive changes only via ADR process).
- **Security layers:** Quarantine (7-day default for new services), service suspension, input injection protection, identity verification (read-before-write for medium+ risk), backend credential encryption (AES-256-GCM), tamper-evident audit log (SHA-256 hash chain)
- **Deployment:** Fly.io, Cloudflare DNS, Let's Encrypt TLS, GitHub Actions CI/CD

---

## 6. Key Documents (if you need deeper context)

These exist in the codebase but are not included here for brevity:

- `docs/architecture/passport/v2-discussion.md` — Full Passport V2 design discussion with three-way review (Jeremy + Claude + Grok). 8 locked positions. Contains the bearer-model reasoning, tiered model, consent lifecycle, multi-agent token model, and identity verification approach.
- `docs/architecture/passport/v2-spec.md` — Canonical implementation spec derived from the discussion.
- `docs/architecture/passport/threat-model.md` — Threat model (v1.4). Design Principles Zero and One. Three-layer trust model.
- `docs/architecture/decisions.md` — ADR-001 through ADR-026. All architectural decisions with rationale.
- `docs/planning/development-plan.md` — Full phase-by-phase development history.
- `AGENT_CONTEXT.md` — Codebase map, what's real vs. placeholder, how to run.

---

## 7. What We're Asking You to Do

Be adversarial. Find the holes. Specifically:

1. **Challenge the assumptions** in Section 4A. Which ones are the weakest?
2. **Probe the strategic gaps** in Section 4B. Which ones are existential vs. manageable?
3. **Evaluate the alternative directions** in Section 4C. Is there a better strategic path than the one outlined in Section 3?
4. **Identify blindspots** we haven't considered. What are we not asking that we should be?
5. **Give your honest assessment** of AgentCafe's viability as described. Is this a product with a real market, or a technically impressive solution looking for a problem?

Be specific. Use concrete examples. Don't be polite — be useful.

---

## 8. Post-Review Strategic Directions (March 6, 2026)

The following sections capture decisions and design directions that emerged from the strategic review process (Grok adversarial review + internal discussion). These are not yet implemented — they are design-stage artifacts for further refinement.

---

### 8.1 Company Cards on the Tab — Solving Consent Fatigue

**Problem identified by Grok (severity: existential):** Per-action consent with 5–15 minute token lifetimes means the human gets bombarded with approval requests across services. At scale (10+ services, recurring agent use), the human disengages — either rubber-stamping everything (destroying safety) or abandoning the platform.

**Proposed solution: Company Cards**

Instead of per-action, per-task consent, the human establishes a **company-level relationship** — a "card on the tab" — that pre-authorizes a class of interactions with a specific company, subject to human-set constraints.

**The metaphor:** You walk into the Cafe, open a tab, and tell the bartender: "I'm good for anything on the StayRight menu up to $500/night for the next 60 days." Now your agent can browse StayRight's offerings and order freely within that envelope — no per-order approval needed. The company card on your tab says "this human has an open arrangement with StayRight."

**How a human's Tab would look:**

```
Alice's Tab ☕
├── StayRight Hotels           [card added March 6]
│   ├── Budget: up to $500/night
│   ├── Scope: search, check-availability, book-room
│   ├── Excluded: cancel-reservation (requires per-action approval)
│   ├── Duration: 60 days (expires May 5)
│   ├── First-use confirmation: ON
│   └── Activity: 3 bookings, 12 searches this month
│
├── QuickBite Delivery         [card added March 1]
│   ├── Budget: up to $50/order, $200/week
│   ├── Scope: all actions
│   ├── Duration: 30 days
│   ├── First-use confirmation: OFF (trusted after 2 weeks)
│   └── Activity: 8 orders this month
│
└── FixRight Home Services     [no card — agent must request]
```

**How agents interact with cards:**

1. Agent discovers a service on the Menu.
2. Agent checks: "does my human have a card with this company?" (via token claims or a lightweight status check).
3. **Card exists + action is within scope/budget** → agent orders directly. No human ceremony. Token issued automatically under the card's policy.
4. **Card exists but action is excluded or over budget** → agent initiates a per-action consent request (falls back to current flow).
5. **No card** → agent initiates a card request. Human reviews the company, sets constraints, approves with passkey. One ceremony, many future orders.

**Relationship to existing architecture:**

A Company Card is technically a **multi-action policy with company-scoped constraints.** The existing policy table, token exchange, and validation chain all work. The changes are:

- **Consent ceremony scope:** Currently creates a policy for specific actions on a specific task. A card ceremony creates a policy covering multiple actions for a company, with budget/duration constraints.
- **Token issuance:** Currently requires `POST /tokens/exchange` with a `consent_id`. With a card, the agent could request a token directly if a valid card policy exists — no per-use consent needed.
- **Constraint enforcement:** The Cafe enforces card limits (budget caps, weekly spending, excluded actions) at order time. If the order exceeds constraints, it's rejected — the agent must request a per-action approval or ask the human to raise the limit.
- **Dashboard UX:** The human's "Tab" becomes the primary view — a list of company cards with activity summaries, one-click revocation, and constraint editing.

**Interaction with risk tiers (company override):**

Companies retain control via `risk_tier` per action:
- **Low/medium actions** — covered by the card automatically
- **High-risk actions** — company can require per-action consent even if the human has a card (e.g., cancellations, large purchases). The card covers the company broadly, but specific dangerous actions punch through to a per-action ceremony.
- **Critical actions** — always per-action, always single-use token. No card bypass.

This preserves the safety model: the company decides which actions are dangerous, and dangerous actions always require explicit human intent — even if the human has a standing relationship with the company.

**First-use confirmation (from threat model v1.4 Layer 2.5):**

When a card is first added, the very first real action triggers a lightweight confirmation: "Your agent is about to book the $412 room at Beachfront Miami under your StayRight card. Confirm this first use?" After first-use approval, subsequent actions within constraints proceed silently. This catches misaligned intent early without creating ongoing friction.

The human can toggle first-use confirmation per card: ON for new/untrusted companies, OFF for companies they've used repeatedly.

**Why this addresses consent fatigue without weakening security:**

- The human still explicitly authorizes — just at a higher level of abstraction (company + constraints, not individual transactions).
- Constraints (budget, duration, scope, excluded actions) bound the blast radius.
- First-use confirmation catches misalignment early.
- High/critical actions always punch through to per-action consent.
- The human's Tab is a legible, manageable dashboard — not an inbox of approval requests.
- Revocation is instant: pull the card, kill all tokens.

**Open questions:**

1. **Constraint granularity.** Per-company budget is simple. Do we need per-action budgets? Per-day vs per-week vs per-month? Start simple (per-company, per-month), add granularity based on real usage patterns.
2. **Card discovery by agents.** How does an agent know a card exists? Options: (a) a claim in the Tier-2 token, (b) a `GET /cards/status?service_id=X` endpoint, (c) the agent just tries to order and gets auto-approved if a card exists. Option (c) is simplest and preserves the existing flow — the agent always calls `POST /cafe/order`, and the Cafe checks for an applicable card policy before rejecting.
3. **Card suggestions.** After 3+ per-action approvals for the same company, should the Cafe suggest: "You've approved StayRight 3 times this month. Want to add a card?" This could be a prompt on the consent page or a notification in the dashboard.
4. **Budget tracking accuracy.** For services with variable pricing (hotels, flights), the Cafe needs to know the price of each order to enforce budget caps. This requires either: (a) a `price` field in the order inputs, (b) the Cafe extracting price from the backend response, or (c) the company tagging the price field during onboarding. Option (c) is most consistent with the existing `human_identifier_field` pattern.
5. **Multi-human cards.** A family or team sharing a card? Deferred — single-human cards only for now.

---

### 8.2 MCP Compatibility — The Cafe as a Single MCP Server

**Context from strategic review:** The MCP ecosystem is growing as a standard for tool discovery and invocation. Grok recommended aggressive MCP alignment. Jeremy's counterpoint: MCP is primarily a non-autonomous agent pattern (developer installs servers for agents to use) and requires every company to build and maintain their own MCP server — which is exactly the cost AgentCafe seeks to eliminate.

**Resolved position: "MCP is the on-ramp, not the product."**

The Cafe should be *speakable via MCP* without being *dependent on MCP* for its value proposition. This is a low-cost, high-option-value move.

**What this means concretely:**

AgentCafe registers itself as a **single MCP server** that exposes the entire Menu as MCP-compatible tools. Any agent framework that speaks MCP (Claude Desktop, LangChain, CrewAI, etc.) can discover every Cafe service through standard MCP tool discovery — without the Cafe changing its core architecture.

**How it works:**

```
MCP Client (any agent framework)
    │
    ▼
AgentCafe MCP Server (one server, many tools)
    │
    ├── Tool: stayright-hotels/search-availability
    ├── Tool: stayright-hotels/book-room
    ├── Tool: quickbite/browse-menu
    ├── Tool: quickbite/place-order
    ├── Tool: fixright/check-availability
    ├── Tool: fixright/book-appointment
    └── ... (every action on the Menu becomes an MCP tool)
    │
    ▼
AgentCafe Proxy (consent, auth, audit — unchanged)
    │
    ▼
Backend Services
```

**What the MCP layer does:**
- Translates the Cafe's Menu into MCP tool definitions (JSON schema for inputs, descriptions, etc.)
- Routes MCP tool invocations to `POST /cafe/order`
- Returns Cafe responses in MCP-compatible format
- For Tier-2 actions, returns the consent URL/activation code as part of the tool response (the agent or framework handles the human-in-the-loop step)

**What the MCP layer does NOT do:**
- Replace the Cafe's own `GET /cafe/menu` and `POST /cafe/order` endpoints (those remain the primary API)
- Handle consent or authorization (that stays in the Cafe's protocol)
- Give agents direct access to backends (everything still flows through the proxy)

**Why this matters strategically:**

1. **For non-autonomous agents (today):** A developer adds the AgentCafe MCP server to their Claude Desktop or LangChain setup. Instantly, their agent has access to every service on the Cafe. The developer didn't have to find, evaluate, or configure N separate MCP servers — just one.

2. **For autonomous agents (tomorrow):** If MCP registries become the way agents discover tools, the Cafe is listed once and exposes its entire Menu. The autonomous agent finds the Cafe through the registry, then uses the Cafe's consent flow for writes. MCP was the on-ramp; the Cafe's authorization layer is the product.

3. **For companies:** "You don't need to build an MCP server. Publish your API spec through the Cafe's wizard, and your service is automatically available to every MCP-compatible agent in the world — with safety, consent, and audit included."

**Implementation cost:** Low. An MCP server is essentially a JSON-RPC endpoint that returns tool definitions and handles invocations. The Cafe already has the Menu (tool definitions) and the order endpoint (invocations). The MCP layer is a thin translation adapter. Estimated effort: a few days of development.

**What this is NOT:**
- A pivot to MCP. The Cafe's own protocol remains primary.
- A dependency on Anthropic or any MCP ecosystem player.
- A replacement for the Cafe's consent flow. MCP has no authorization standard — that's the gap the Cafe fills.

#### 8.2.1 Open Questions — For Adversarial Review

The ADR-029 rationale (see `docs/architecture/decisions.md`) was reviewed internally and found to be directionally sound but with three unresolved implementation realities and one strategic tension. These should be pressure-tested by Grok and ChatGPT before implementation begins.

**Open Question 1: Transport Model**

MCP servers use two transport modes: stdio (local process, developer installs it) or SSE (remote HTTP endpoint). The Cafe is a remote service. The §8.2 narrative implies both models — "a developer adds the AgentCafe MCP server to their Claude Desktop" (sounds like stdio/local) and "the Cafe is listed once in MCP registries" (sounds like remote SSE).

These are architecturally different:
- **Stdio/local:** The developer installs an `agentcafe-mcp` package that runs locally and proxies MCP calls to the remote Cafe API. Simple for the developer, but now there's a local process to distribute, version, and maintain. The "thin adapter" is a shipped artifact, not just a server-side endpoint.
- **Remote SSE:** The Cafe itself speaks MCP over SSE at e.g. `agentcafe.io/mcp`. No local install needed. But not all MCP clients support remote servers yet, and SSE has its own operational concerns (connection persistence, timeouts, reconnection).

Which model is primary? Can we support both? What's the maintenance cost of each? Does the choice affect the "low implementation cost" claim?

**Open Question 2: Consent Flow UX via MCP**

For Tier-2 (write) actions, the adapter "returns the consent URL/activation code as part of the tool response." But MCP tool results are consumed programmatically by the agent, not displayed to the human. The human-in-the-loop step depends entirely on the host framework:

- **Claude Desktop:** The agent could present the consent URL in chat text. The human clicks it, approves in the browser, returns to the chat. Workable but clunky — the agent has to poll for consent completion.
- **Headless pipelines (LangChain, CrewAI):** There may be no human present. The consent URL has no one to click it. The tool call simply fails or blocks indefinitely.
- **Custom frameworks:** Behavior is unpredictable.

The Cafe has no control over how the host framework handles human-in-the-loop. This is a real UX gap:
- Should the MCP adapter only expose Tier-1 (read) actions by default, requiring explicit opt-in for Tier-2?
- Should Tier-2 tool responses include structured metadata (not just a URL string) that MCP clients could render as an interactive prompt?
- Is there an emerging MCP standard for human-in-the-loop confirmation that we should track?
- Does this weaken the "one MCP server gives you everything" pitch if write actions are second-class?

**Open Question 3: Tool Scaling**

One MCP server exposing the entire Menu means every action across every service appears in a single `tools/list` response. With 3 demo services and ~6 actions, this is fine. With 50 services and 500 actions, the tool list becomes:

- **A context window problem:** Agents that load all tool definitions into their prompt may hit token limits or degrade in tool selection accuracy. Research shows LLM tool-use accuracy drops significantly beyond ~30-50 tools.
- **A discovery problem:** The whole point of the Cafe is structured discovery. Dumping 500 tools into an MCP `tools/list` response undoes that — the agent is back to scanning a flat list.

Possible mitigations:
- **Category-scoped sub-servers:** `agentcafe.io/mcp/hotels`, `agentcafe.io/mcp/payments` — one MCP server per category. Developers subscribe to relevant categories.
- **Dynamic tool filtering:** The MCP server accepts a query/filter parameter and returns only matching tools. Non-standard but could work with custom MCP clients.
- **Pagination or lazy loading:** Return a small set of "discovery" tools first (e.g., `cafe-search`, `cafe-browse-category`) that return service/action lists, then dynamically register specific tools on demand.
- **Accept the limit:** Cap the Cafe at a curated set of high-value services rather than pursuing marketplace scale. This aligns with the "services are bootstrap, not the product" positioning (ADR-027).

Which approach fits our strategic positioning? Does tool scaling change the "one server, many tools" pitch into "one server, curated tools"?

**Strategic Tension: ADR-027 vs ADR-029 Enthusiasm**

ADR-027 says: "Build for autonomous agents. Non-autonomous agents served incidentally via MCP adapter."
ADR-029's rationale builds its strongest case around non-autonomous agents: "a developer adds one MCP server and gets access to every Cafe service."

If the MCP adapter primarily serves non-autonomous agents, and non-autonomous agents are not the target audience, how much design effort and architectural weight should the MCP adapter carry? Options:

- **Minimal viable adapter:** Ship something basic, see if anyone uses it. Don't optimize for MCP UX.
- **Strategic on-ramp:** Invest in MCP UX because today's non-autonomous agent developers become tomorrow's autonomous agent builders. The MCP adapter is a funnel.
- **Defer entirely:** Build for autonomous agents first (Company Cards, Agent Memory). Add MCP later when the ecosystem matures and the transport/consent/scaling questions have clearer answers.

This tension should be resolved before implementation begins.

#### 8.2.2 Resolved Implementation Spec — Four-Review Convergence (March 8, 2026)

All four open questions from §8.2.1 were resolved through three independent adversarial reviews (Grok, ChatGPT) with cross-validation by Claude. A final round by ChatGPT identified two micro-cracks in the "locked" design — tool naming and schema context bloat — which Grok confirmed and fixed. All four reviewers now converge.

**Review contributions:**
- **Grok:** Concrete implementation spec, MCP ecosystem details (registries, Streamable HTTP transport), 4-tool discovery pattern, sequencing.
- **ChatGPT:** Traffic-source hierarchy question (the most important blindspot), "MCP adapts to the Cafe" guardrail, LLM-native tool naming, schema context bloat risk.
- **Claude:** Technical cross-validation, sequencing consistency, assessment of novel contributions.

The following is the locked implementation spec for the MCP adapter. No further iteration needed — this is shippable.

**Resolved: Transport Model**

Remote Streamable HTTP only, at `https://agentcafe.io/mcp`. This is the recommended remote transport in the current MCP spec (replaced HTTP+SSE in the June 2025 spec update). Implementation: one new FastAPI endpoint using Starlette's `StreamingResponse` + JSON-RPC 2.0 routing over the existing Menu and order endpoints. Estimated: ~200 LOC.

Stdio is deferred entirely. If demand appears, a separate `pip install agentcafe-mcp-local` wrapper (50 lines of subprocess + pipe to the remote endpoint) can ship later. No maintenance cost today. We do not own or maintain a local package.

Identity implication (from ChatGPT): "If the adapter is remote, AgentCafe becomes infrastructure. If it's local, AgentCafe becomes a developer tool. Those are very different companies." Remote is the correct choice for our positioning.

**Resolved: Tool Surface — The 4-Tool LLM-Native Discovery Pattern**

MCP `tools/list` returns exactly 4 permanent tools (never more, ~1000 tokens forever). Tool names are **verb-first and LLM-aligned** — matching model priors (`search`/`get`/`invoke` appear in every training corpus) rather than leaking internal Cafe metaphors (`menu`/`order`/`company_card`). Internal Cafe code keeps the original metaphors; the MCP layer translates at the boundary only.

1. **`cafe.search`** (Tier-1 read) — Semantic search across the entire Menu. Accepts `query` (natural language), `category`, `capability_tags`, `max_results` (default 10, max 20). Returns **summaries only** — `service_id`, `action_id`, `name`, `short_description`, `risk_tier`, `relevance`. Supports cursor-based pagination. Implementation: structured filter via existing `menu.py` + keyword boost + LiteLLM rerank (reuses the wizard enricher, no new infra). Full vector search deferred to Phase 9 if needed.

2. **`cafe.get_details`** (Tier-1 read) — Returns the full semantic Menu entry for a specific `service_id` (and optionally `action_id`). Provides `required_inputs`, `constraints_schema`, `risk_tier`, `human_identifier_field`, etc. on demand. One service at a time = no context explosion. Agent calls this after `cafe.search` to get the exact inputs needed before invoking.

3. **`cafe.request_card`** (Tier-1) — Initiates the Company Card flow (ADR-028). Agent passes `service_id`, optional `suggested_scope`, `suggested_budget`, `suggested_duration_days`. Cafe returns immediately (non-blocking): `card_request_id`, `consent_url`, `activation_code`, `status: "pending"`. Human approves asynchronously via existing passkey ceremony. Agent retries `cafe.invoke` later — auto-approved under card policy. This tool teaches agents the Cafe's core authorization pattern and is the on-ramp to the Cafe's moat.

4. **`cafe.invoke`** (universal execution) — Generic input: `service_id`, `action_id`, `inputs`. Routes to existing `POST /cafe/order`. All consent/card/policy logic stays in the proxy.

**Why 4 tools (evolution from earlier designs):**
- ChatGPT initially proposed 2 tools (`search_services` + `invoke_action`) — elegant but hides the Company Cards flow.
- Grok proposed 4 tools, then optimized to 3 by inlining full schemas into high-relevance search results (>0.85 threshold).
- ChatGPT's final review identified the 0.85 inline hack as a context bloat risk: agents that search multiple times accumulate duplicate schemas. The safer pattern (used by OpenAI tool registries and Anthropic's MCP catalog) is summaries from search + explicit schema fetch on demand.
- **Final: 4 tools with LLM-native names.** `cafe.search` returns lightweight summaries. `cafe.get_details` provides full schema exactly once per service. Context stays stable regardless of how many times the agent searches.

**Why these names (from ChatGPT's naming review):**
- `cafe.search` not `cafe.search_menu` — agents reason in verbs, not Cafe metaphors. "Menu" is brand, not an LLM concept.
- `cafe.get_details` not `cafe.get_service_details` — shorter, verb-aligned, matches the universal `get` pattern.
- `cafe.request_card` not `cafe.request_company_card` — "company" is internal jargon. Agents just need to know it's a card (standing authorization).
- `cafe.invoke` not `cafe.order` — "order" implies commerce. "Invoke" matches how every agent framework describes tool execution.

**How autonomous agents discover everything (real flow):**
- Agent boots → loads the 4-tool MCP server (`agentcafe.io/mcp`)
- Calls `cafe.search(query="book a hotel under $500 in Miami")` → gets 3–8 ranked summaries
- Calls `cafe.get_details(service_id="stayright-hotels")` → full schema with required inputs + risk tier
- Calls `cafe.invoke(...)` → if Tier-2 without card, gets `HUMAN_AUTH_REQUIRED` error with card suggestion
- Calls `cafe.request_card(...)` → human approves asynchronously → future invocations auto-approved

Context cost: 4 tool definitions + summaries from search + one full schema per service the agent actually uses. Scales to 10,000+ services. No flat tool list, no schema duplication, no context bloat.

**Resolved: Consent Flow via MCP**

Tier-2 (write) actions are NOT exposed as separate tools in `tools/list`. All writes go through the single `cafe.invoke` tool. When a Tier-2 action is attempted without authorization, the adapter **fails fast** with a structured JSON-RPC error:

```json
{
  "error": "HUMAN_AUTH_REQUIRED",
  "consent_id": "...",
  "consent_url": "...",
  "activation_code": "CAFE-7482",
  "card_suggestion": true
}
```

The MCP adapter does NOT orchestrate consent. It does not poll, surface URLs in chat text, or become a workflow engine. The agent (or host framework) decides what to do with the error. Post-Company Cards: most writes are auto-approved under card policy — the error path becomes the exception, not the rule.

No emerging MCP standard for human-in-the-loop exists or is expected. The Cafe IS the consent layer — MCP delegates this to us.

**Architectural Guardrail (from ChatGPT, adopted verbatim):**

> MCP adapts to the Cafe — not the other way around.

If the MCP adapter ever begins dictating consent flow, tool structure, discovery mechanism, or token behavior, it has become the product and must be killed or rolled back. ADR-027 explicitly states: authorization is the product.

**Resolved: Strategic Tension (ADR-027 vs ADR-029)**

MCP is non-negotiable distribution infrastructure for autonomous agents — not just a non-autonomous convenience. Concrete evidence (March 2026):
- Autonomous agents use MCP registries (`registry.discover`) as first-class tool discovery at runtime, without developer pre-configuration. This is production behavior in LangChain autonomous mode, OpenAI Agents SDK, and CrewAI orchestrators.
- Three registry surfaces exist: official MCP Registry (registry.modelcontextprotocol.io), GitHub MCP Registry (mirror), Microsoft Azure MCP Hub (enterprise mirror). All are self-service, no approval gate.
- Registration is one command: `npx @modelcontextprotocol/registry publish --url https://agentcafe.io/mcp --name AgentCafe`

Resolution: MCP is a **strategic on-ramp with minimal viable investment**. Maintenance must not change Cafe architecture. It serves both autonomous agents (registry discovery) and non-autonomous agents (developer config) without optimizing for either. Usage data post-launch determines further investment.

**Resolved: Traffic Source Hierarchy (from ChatGPT's challenge)**

ChatGPT identified the most important blindspot in the original briefing: it implicitly assumed autonomous agents would discover the Cafe on their own (#3 below), which is the least reliable path. The locked traffic source hierarchy:

1. **Short-term:** Framework/IDE developers adding the single MCP server (Claude Desktop, LangChain, CrewAI). Cheap, immediate, non-autonomous but builds brand, data, and early ecosystem.
2. **Medium-term:** Agent Memory service creating stickiness. Agents come for memory, stay for the Menu, discover everything else via `cafe.search`. This is the bootstrap wedge.
3. **Long-term:** Pure autonomous registry discovery (official MCP Registry + Azure Hub). Agents query the registry at runtime with no human setup.

We are NOT relying on #3 as the first source. The MCP adapter enables #1 (developers) and #3 (registries). Agent Memory enables #2 (stickiness). Company Cards enable all three to convert to recurring authorized relationships.

**Locked Sequencing:**

1. **Company Cards** — existential consent fatigue fix, ships first. Required by `cafe.request_card` tool and `HUMAN_AUTH_REQUIRED` error handling.
2. **Agent Memory** — bootstrap traffic wedge. Publishes through the wizard; auto-appears in `cafe.search` once on the Menu.
3. **MCP Adapter** — thin distribution layer on top of both. Ships last because it rides on the primitives above.

**Registry Listing:**

Self-service, <5 minutes, no approval gate:
- Ship the adapter (remote Streamable HTTP at `/mcp`)
- Run: `npx @modelcontextprotocol/registry publish --url https://agentcafe.io/mcp --name AgentCafe --description "Safe human-authorized services for agents"`
- Indexed instantly in official + GitHub + Azure registries
- Requirements: valid HTTPS endpoint (already have Let's Encrypt), basic metadata. No paid tier, no waitlist.

---

### 8.3 First Real Service — What to Build for Autonomous Agents

**The bootstrap problem:** The Cafe needs real services to attract real agents. The 3 demo backends (hotel, lunch, home services) return fake data and prove nothing to an autonomous agent that can verify results.

**The strategic filter:** The first real service must:

1. **Be genuinely useful to autonomous agents** — something they'd actually call during real tasks
2. **Offer something the open web doesn't** — public APIs or web scraping can't replace it
3. **Naturally demonstrate the consent flow** — so agents learn the Passport pattern through use
4. **Be feasible to build without third-party partnerships** — no dependency on hotel chains or payment processors
5. **Generate recurring agent traffic** — not a one-off query

**Candidate services (ranked by fit):**

#### Candidate A: Agent Memory / Persistent State (STRONGEST FIT)

**What it is:** A key-value + document storage service where agents can persist state across sessions. The human authorizes what the agent can store and retrieve under their account.

**Why agents need it:** Today's agents lose context between conversations. An autonomous agent that researches flights on Monday can't remember the results on Tuesday unless its platform provides persistence — and many don't. A Cafe-hosted memory service with human-authorized access controls fills a real gap.

**Why it fits the filter:**
- ✅ Genuinely useful — every autonomous agent benefits from persistent memory
- ✅ Not available on the open web — there's no "public memory API" an agent can Google
- ✅ Demonstrates consent naturally — the human authorizes "this agent can store/retrieve data under my account" (Tier-2 write)
- ✅ No third-party dependencies — we build and host it ourselves
- ✅ Recurring traffic — agents read/write memory constantly, not just once

**Menu actions:**
- `store` (write, medium risk) — save a key-value pair or document under the human's namespace
- `retrieve` (read) — fetch stored data by key or semantic search
- `list` (read) — list all stored keys/documents
- `delete` (write, high risk) — remove stored data

**Consent flow example:** "Your agent wants to save notes and data under your AgentCafe account. Approve storage access for 90 days? Storage limit: 100 MB."

**Why this is the strongest first service:** It creates a reason for agents to *keep coming back* to the Cafe. An agent that stores state through the Cafe has an ongoing relationship with the platform — which means it'll naturally discover other services on the Menu during subsequent visits.

#### Candidate B: Agent Payments / Spending Proxy

**What it is:** A payment proxy where the human pre-authorizes a spending limit. The agent can make purchases through the Cafe up to that limit without per-transaction approval.

**Why agents need it:** Agents can't hold credit cards. When an autonomous agent needs to buy something (a domain, an API subscription, a small service), it's stuck — it needs to ask the human to go buy it manually. A Cafe payment proxy with human-set spending limits solves this.

**Why it fits the filter:**
- ✅ Genuinely useful — agents that can spend money are dramatically more capable
- ✅ Not on the open web — no public "spend money on behalf of a human" API exists
- ✅ Demonstrates consent powerfully — the human authorizes a spending limit with a passkey (exactly what the Passport was designed for)
- ✅ Recurring traffic — spending is a continuous need
- ⚠️ **Requires payment processor integration** (Stripe Connect or similar) — adds complexity and regulatory considerations
- ⚠️ **Trust bootstrapping is harder** — humans are more cautious with money than with data storage

**This is the highest-value service but the hardest to build first.** Consider it for service #2 after agent memory proves the model.

#### Candidate C: Agent-to-Agent Messaging / Task Handoff

**What it is:** A message broker where agents can send structured messages to other agents, with human-visible audit trails. The human authorizes which agents can communicate under their account.

**Why it fits:**
- ✅ Useful for multi-agent systems (orchestrators, sub-agents, handoffs)
- ✅ Not available publicly
- ✅ Natural consent model — "authorize these agents to communicate under your account"
- ⚠️ Narrower audience — only useful for multi-agent architectures
- ⚠️ Harder to explain to humans — "your agents want to talk to each other" is less intuitive than "your agent wants to remember things"

**Good service #3, not #1.**

#### Candidate D: Verified Web Search / Fact Retrieval

**What it is:** A search service optimized for agents — returns structured, source-attributed results with confidence scores. Unlike raw web search, results are curated for reliability.

**Why it's weaker:**
- ⚠️ Competes directly with model-native search (ChatGPT browsing, Perplexity, Gemini)
- ⚠️ Read-only — doesn't demonstrate the write consent flow
- ⚠️ Hard to differentiate — "better search" is a crowded space

**Not recommended as the first service.**

#### Candidate E: Scheduling / Calendar Proxy

**What it is:** An agent-accessible calendar service. The human authorizes agents to view, create, and modify calendar events.

**Why it fits:**
- ✅ Extremely useful — scheduling is a core autonomous agent task
- ✅ Natural consent model — "authorize your agent to manage your calendar"
- ⚠️ **Requires integration with Google Calendar, Outlook, etc.** — third-party dependency
- ⚠️ Competitive — many calendar APIs and MCP servers already exist

**Good service if you have calendar API partnerships. Not ideal for bootstrapping.**

**Recommended sequence:**

| Order | Service | Rationale |
|-------|---------|-----------|
| 1st | **Agent Memory** | Lowest build cost, highest stickiness, no dependencies, recurring traffic, creates ongoing Cafe relationship |
| 2nd | **Agent Payments** | Highest value, demonstrates the Passport at its best, requires Stripe integration |
| 3rd | **Agent Messaging** | Enables multi-agent coordination, broadens the platform |

**The agent memory service is the minimum viable real service.** It can be built entirely in-house, requires no third-party partnerships, creates recurring agent traffic, and naturally teaches agents the Cafe's consent pattern. Once agents are coming to the Cafe for memory, they'll discover other services on the Menu — which is the bootstrap flywheel.

---

## 9. Review Convergence — Grok + ChatGPT (March 6, 2026)

Two independent adversarial reviews were conducted. Grok reviewed the original briefing (Sections 1–7). ChatGPT reviewed the full document including Section 8. Their conclusions are strikingly aligned on the important points and diverge usefully on framing.

### 9.1 Where Both Reviews Agree

| Point | Grok | ChatGPT |
|-------|------|---------|
| **Consent fatigue is existential** | Ranked #1 severity. "5–15 min tokens force re-consent very often." | "Without [Company Cards] the system dies instantly." |
| **Company Cards solve it** | "Excellent design improvement." | "Best design improvement in the document." |
| **Agent Memory is the right first service** | Validated as strongest candidate. | "Surprisingly strong... creates lock-in... first thing that feels like a bootstrap wedge." |
| **MCP compatibility is necessary** | "Strongest pivot." Recommended aggressive alignment. | "Ignoring MCP would be suicidal." Endorsed "on-ramp, not the product." |
| **The consent/authorization layer is the real moat** | "Differentiating in 2026 — not commoditized." | "The real product is human-authorized delegation to software agents." |
| **Architecture is sound** | "Technically coherent, philosophically honest, unusually thoughtful." | "One of the more coherent agent infrastructure ideas I've seen." |

### 9.2 Where They Diverge (Usefully)

**Grok frames AgentCafe as a niche within the agent tooling ecosystem.**
- Compares to MCP gateways, auth proxies, HITL identity products.
- Sees the Cafe as competing for a slice of the agent infrastructure market.
- Recommends: "consent & authorization specialist that plugs into MCP ecosystems."
- Warning: "If you stay on the current path without MCP alignment, it's 'Yubico for agents' rather than 'Stripe for agents.'"

**ChatGPT frames AgentCafe as a new primitive — human delegation infrastructure.**
- Compares to OAuth, Stripe, identity providers — not other agent tools.
- Sees the Cafe metaphor and marketplace as potentially misleading.
- Recommends: lead with a concrete service (Agent Memory), then expand into the authorization primitive.
- Warning: "You're probably closer to an important primitive than to a finished product."

**The browser as competitor** — ChatGPT raised this; Grok did not. ChatGPT argues that computer-use agents (Operator, Claude Computer Use) can just use websites, making structured API discovery less valuable. AgentCafe only wins where the browser fails: authorization, payments, persistent state, cross-service coordination.

**Distribution** — Both flagged the chicken-and-egg problem, but ChatGPT was more direct: "Agents don't discover infrastructure organically. They get access through platforms." The real go-to-market question is "why would agent platforms integrate AgentCafe?" not "why would agents use AgentCafe?"

### 9.3 ChatGPT's Five Strategic Risks

These are well-stated versions of risks partially identified in Section 4, sharpened by ChatGPT's framing:

**Risk 1: The agent autonomy timeline might be wrong.**
Three forces push against autonomy: liability (who's responsible?), platform control (OpenAI/Anthropic prefer curated tool ecosystems), and UX friction (humans prefer reviewing decisions). If the dominant model for 5–10 years is "assistive agents inside platforms with curated tools," the discovery marketplace never materializes. The product only works if it becomes infrastructure for those platforms.

**Risk 2: The browser might remain the universal tool.**
Computer-use models change the equation. If agents can reliably open websites, navigate, and submit forms, then APIs stop being the default integration surface. The web becomes the API. AgentCafe only wins where the browser fails: authorization, payments, persistent state, cross-service coordination. Hotel search is not defensible. Agent memory is.

**Risk 3: Distribution is unsolved.**
Three paths: agents discover it, developers integrate it, platforms integrate it. The current plan implicitly assumes agents discover it — the least reliable path. Infrastructure historically wins when embedded in platforms, developer frameworks, or ecosystem standards. Not when it waits to be discovered.

**Risk 4: The trust gap for humans is large.**
The consent flow is logically sound, but humans don't evaluate trust logically. They ask "why should I trust this thing?" Right now the answer is "because the architecture is good" — insufficient. Trust comes from: brand reputation, open source transparency, enterprise adoption, or platform endorsement. Without one of those, the consent step becomes a psychological bottleneck.

**Risk 5: The "marketplace" instinct may be misleading.**
Marketplaces have the hardest bootstrap problem in tech. The Cafe metaphor subtly pushes toward marketplace thinking. But the strongest parts of the system — human delegation tokens, policy-bounded permissions, auditable actions, cross-service authorization — are infrastructure primitives, not marketplace features. Identity infrastructure positioning may be stronger than marketplace positioning.

### 9.4 Jeremy's Responses (During Review)

**On MCP as gravity:** "That's a tough sentence that contains a lot of certainty about something less than 2 years old. MCP is for non-autonomous agents — scripted tools, a set toolbox. It also requires every company to create and maintain their own MCP server. We seek to offboard that cost to a centralized solution."

**On reads through the proxy:** "Why would the agent go around the Cafe? The reads are there for convenience. The secure, human-authorized writes are the product." (Both reviewers accepted this — proxy-everything is a feature, not overhead.)

**On non-autonomous agents:** "Ignore them. They're a quick step in evolution, here today and gone tomorrow — or they stay on as helpers to autonomous agents." (ChatGPT's Risk 1 directly challenges this timeline assumption.)

### 9.5 Synthesized Position

After two adversarial reviews, the following positions have strengthened:

**Confirmed (high confidence):**
- Company Cards are essential and must be built. Both reviewers independently called this the most important design improvement.
- Agent Memory is the right first real service. Both reviewers endorsed it — ChatGPT called it the only thing in the doc that "feels like a bootstrap wedge."
- MCP compatibility is a low-cost, high-option-value move. "On-ramp, not the product."
- The consent/authorization layer is the real moat. Everything else (Menu, discovery, proxy) is secondary to the delegation primitive.

**Challenged (needs resolution):**
- **Discovery vs. authorization — which do we lead with?** Grok says pick one lane. ChatGPT says the authorization primitive is the important thing and the marketplace framing is misleading. Jeremy's instinct is that the Cafe is both — but the go-to-market may need to choose.
- **Distribution strategy.** "Build real services and agents will come" is the current plan. Both reviewers flag this as the weakest assumption. The MCP server + Agent Memory combination partially addresses it, but platform integration may be necessary.
- **Autonomy timeline.** Jeremy bets on autonomous agents. ChatGPT warns this could take 5–10 years. The hedge: if the Cafe works as infrastructure for platforms (MCP server, SDK), it survives the slow-autonomy scenario too.
- **Trust bootstrapping.** Unsolved. Open source is the most accessible path (code is already MIT-licensed). Platform endorsement or enterprise adoption would be stronger but harder to obtain early.

**New framing from ChatGPT worth considering:**

> "Human-owned memory for agents" (not "agent memory service")

This is a subtle but important reframe. It emphasizes:
- Multiple agents can access it (not locked to one)
- Humans control it (not agent-owned data)
- It persists across platforms (not tied to ChatGPT or Claude)

This positions the memory service as a human-empowerment tool, not an agent infrastructure tool — which may be important for trust bootstrapping with humans.

### 9.6 Recommended Next Steps

Based on review convergence:

| Priority | Action | Addresses |
|----------|--------|-----------|
| 1 | **Build Agent Memory service** — real backend, real storage, real consent flow | Bootstrap, distribution, first real service |
| 2 | **Implement Company Cards** — multi-action policies with company-scoped constraints | Consent fatigue (existential risk) |
| 3 | **Build MCP server adapter** — thin layer exposing Menu as MCP tools | Distribution, platform integration, MCP hedge |
| 4 | **Open source the core** — already MIT-licensed, make the repo public with good docs | Trust bootstrapping |
| 5 | **Decide: marketplace or infrastructure primitive?** — affects positioning, naming, pitch | Strategic clarity |

Item 5 is the strategic question that remains open. Everything else can proceed in parallel regardless of the answer.
