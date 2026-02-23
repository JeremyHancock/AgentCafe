# AgentCafe — Passport Architecture & Threat Model (v1.4)

**Date:** February 22, 2026
**Authors:** Grok (advisor), Claude (lead implementer), Jeremy (project lead)
**Status:** **Locked for Phase 3 scoping.** All open items resolved. Pending: legal review before Phase 3 beta.
**Revision history:** v1.0 Grok draft → v1.2 Claude review + pushback → v1.3 merged tensions → v1.4 final (kill limited-mode, activation code flow, Layer 2.5, legal gate)

---

## Design Principle Zero

**The system's ultimate trust root is something an AI agent fundamentally cannot possess or forge: a physical-world identity anchor** — a hardware security key, a biometric tied to a physical device, a passkey bound to human-controlled hardware. Every ceremony in this document traces back to this anchor. If we compromise on this, nothing else matters.

## Design Principle One

**`agent_id` is an untrusted, self-reported label — not a verified identity.** There is no universal agent identity system (as of Feb 2026). An agent is software that can claim any name, run multiple instances, and change identity at will. We use `agent_id` for human-facing display (dashboard, audit log, revocation UI) and nothing else. **No security decision may depend on `agent_id` being truthful.** All security rests on *human* identity (passkeys, physical devices), never on agent identity. If a future agent attestation standard emerges (platform-signed certificates, TEE attestation), we can upgrade — but we must not build features today that assume `agent_id` is real.

## Core Philosophy

A passport is a digital Power of Attorney.
It must prove **knowing, specific, revocable, and auditable** human intent at a level that companies and regulators will accept for real-world actions. Ordinary auth flows are insufficient.

---

## 1. Threat Model

### High-probability threats

- Sophisticated agents (or compromised agents) that socially engineer the human into approving dangerous scopes
- Humans who are lazy, distracted, or explicitly instruct their agent to lie/get around limits
- **Agent operating the human's email/SMS** — not a compromise, but *intentional* inbox access granted by the human. The agent being constrained is the same entity that can read and respond to confirmation messages. **No Layer 2 or Layer 3 confirmation may rely solely on email or SMS.**
- Scope creep via repeated small mandate expansions — no single request looks dangerous, but aggregate exposure grows unchecked over weeks
- Collusion between human and agent (e.g., to launder money or commit fraud)
- Agent forgery / impersonation at scale
- **Structuring attacks** — agent breaks a high-value action into many small ones to stay below the Layer 3 threshold (see §4 Velocity Rules)
- **Confirmation flooding** — agent creates many pending Layer 3 confirmations across different services, hoping the human bulk-approves in fatigue (see §2 Layer 3 async limits)

### Catastrophic threats

- Compromise of signing keys
- Mass approval fatigue leading to blind clicking
- Regulatory reclassification of passports as "access devices" under EFTA/Reg E or equivalent

---

## 2. The Three-Layer Trust Model (+ Layer 2.5)

We separate trust into layers that ordinary designs collapse:

### Layer 1: Identity Passport (long-lived, high-ceremony issuance)

- Proves "this agent represents this human"
- Issued once per agent-human pair
- Grants only the right to browse the Menu and request actions — **no real-world consequences**
- **Requires passkey/WebAuthn at issuance. No exceptions. No fallback tier.**
- **Primary enrollment path:** In-browser passkey creation flow (supported by all modern browsers and 96% of consumer devices as of Feb 2026).
- **Humans without passkey-capable devices** use their agent directly without Cafe mediation. They are not excluded from the ecosystem — they simply do not get Cafe's trust guarantees. Marketing is honest: *"AgentCafe requires a passkey — the same secure key used by banks and governments. This is how we keep your agents accountable."*

### Layer 2: Standing Mandates (medium-lived, medium ceremony)

- Pre-approves categories of actions with limits ("hotels up to $500/night for work trips")
- **AgentCafe authors the plain-English consent text** — the agent submits a structured request (service_id, action_id, limits); the Cafe translates it into the message the human sees. The agent cannot influence the wording.
- The consent text should *describe* the intended purpose for the human's benefit, but the Cafe does not enforce purpose at the protocol level (see §10 — purpose locks are unenforceable).
- Human must actively approve (no auto-approve, no default yes)
- **Mandatory expiry: max 90 days.** Human can renew, but must see what they're renewing.
- Covers predictable, bounded, low-to-medium risk actions
- Can be revoked instantly
- Layer 2 may use email as a confirmation channel (the human already has a passkey-verified account). Email is a convenience fallback, not the security boundary.

### Layer 2.5: First-Use Confirmation (one-time, per new mandate)

For every new standing mandate (or material change to an existing one), the **very first action** that would execute under it triggers a lighter confirmation before execution:

- Human sees the **concrete first use**: "Your agent is about to book the $412 room at Beachfront Miami under your 'work travel up to $500/night' mandate. Confirm this first use?"
- **Confirmation method:** Push notification to registered device + passkey tap. For mandates covering only low-risk actions, email confirmation is acceptable.
- After first-use approval, subsequent actions under that mandate proceed normally (subject to velocity rules and Layer 3 thresholds as usual).
- Dashboard highlights: "First use of [mandate] occurred on [date] — [action details]."
- **Configurable per human:** tighten = easy (always require first-use), loosen = passkey required.

**Why this exists:** It breaks "set and forget" abuse. A human who approves "hotels up to $500" sees *exactly* how the agent uses it on the very first action. This catches misaligned intent (leisure vs. work, unexpected service patterns) immediately, without the overhead of full Layer 3 on every action.

### Layer 3: Just-in-Time Confirmation (one-time, highest ceremony)

- For any action above a risk threshold (dollar amount, new service, unusual pattern, or anything the company flags), the **Cafe holds the exact request**
- The human sees the **concrete transaction**: "Confirm booking the $452 room at StayRight Austin Downtown for March 15-18, total $1,356?"
- **Hardware-bound confirmation only: passkey or registered-device push notification. No email. No SMS.** This is the line that makes Layer 3 meaningful — the agent cannot intercept it.
- The agent never sees the confirmation UI — it gets only "approved", "held for confirmation", or "denied"
- **The held request is immutable and cryptographically bound** (hash of exact inputs + timestamp). The agent cannot modify it after the human has seen it. The approval covers exactly what was shown.
- **Async flow:** Agent receives `202 Accepted` with a `confirmation_id`. Agent polls `GET /cafe/confirmation/{id}` or provides a webhook URL. The Cafe calls the webhook (or agent polls) when the human responds. This avoids blocking connections and lets the agent do other work while waiting.
- **Async flood protection:**
  - Max **1 pending** Layer 3 confirmation **per agent per service**. New requests for the same service while one is pending are rejected: "confirmation already in progress."
  - Max **5 pending** Layer 3 confirmations **per human across all agents and services**. At the global limit, new high-risk requests are auto-denied: "too many pending confirmations — please resolve existing ones."
  - **No bulk-approve UI.** Each confirmation is presented and resolved individually.
  - **Pending confirmations cannot be cancelled and resubmitted by the agent.** Once held, the request is locked. The human can deny it, but the agent cannot withdraw and replace it with a different request.

---

## 3. How the Cafe Mediates

**The passport grants the right to request execution, not to execute.**

When an agent submits an action:

1. Cafe validates passport signature, expiry, revocation status
2. Cafe checks scopes and standing mandates
3. If this is the **first use** of a new or changed mandate → Layer 2.5 confirmation
4. Cafe evaluates risk tier:
   - **Below threshold:** Execute immediately, log it
   - **Above threshold:** Hold the request, ping the human via Layer 3, wait for confirmation
5. Only after human confirmation (or if below threshold and past first-use) does the Cafe forward to the backend
6. Every action — approved, denied, or held — is written to the audit log

### Backend token isolation

The backend **never** sees the human's passport JWT. The Cafe is a full proxy: it validates the passport, then forwards the request to the backend using the backend's own auth credentials (stored in `proxy_configs`). The backend receives a per-call request from the Cafe, not a reusable token. This means:

- Revoking a passport takes effect immediately at the Cafe — there is no cached token at the backend to worry about
- Backends cannot accumulate or replay human credentials
- The Cafe is the sole enforcement point for all passport rules

---

## 4. Risk Thresholds, Velocity Rules & Visibility

### Per-action thresholds (configurable)

- Default: >$100 or first use of any service triggers Layer 3
- Company can raise/lower per service (e.g., "all bookings require Layer 3 regardless of amount")
- Human can adjust for their own account
- **Asymmetric ceremony: lowering thresholds (tighter) is easy; raising them (looser) requires passkey confirmation**
- **Hard ceiling: AgentCafe enforces an absolute maximum** (e.g., >$5,000 is *always* Layer 3) that the human cannot override. Protects against humans socially engineered into permissive settings.

### Velocity rules (anti-structuring)

- **Rolling sum per service per time window** (e.g., per hour, per day). If the agent's cumulative spend on a service exceeds the Layer 3 threshold within the window, the next action triggers Layer 3 regardless of individual amount.
- Example: threshold is $200, window is 1 hour. Agent books three $80 rooms ($240 total). The third request triggers Layer 3 because the rolling sum ($240) exceeds $200.
- Velocity windows and thresholds are configurable per human (same asymmetric rule: easy to tighten, hard to loosen).
- Phase 3: per-service velocity. Phase 4: cross-service velocity (total spend across all services in the window).

### Threshold opacity (defense-in-depth, not primary protection)

- **The agent does not receive the human's threshold values.** It only sees the outcome: approved, held, or denied.
- **Honest limitation:** The agent can infer thresholds by observing outcomes. Within ~5-10 requests it can estimate the boundary. This is information-theoretically unavoidable.
- **The velocity rule is the real defense against structuring.** Even after the agent learns the per-action threshold, flooding just-below-threshold requests triggers the velocity check.
- Threshold opacity is kept because it costs nothing to implement and adds marginal friction, but **we do not claim it is airtight.** It is a speed bump, not a wall.

### Total exposure visibility

- When a human views or modifies their risk tiers, the dashboard shows **aggregate authorized exposure across all services**: "You have currently authorized up to $312/day in total spending across 7 services."
- Exposure is calculated conservatively using the maximum possible value of each standing mandate.
- Phase 3: data model, calculation logic, and dashboard UI (ships alongside standing mandates, which are the source data).

---

## 5. Account Setup (the root ceremony)

**Account creation is the most dangerous moment in the system.** If an agent can complete account setup, everything downstream is compromised.

- Account creation **always** requires passkey/WebAuthn. No fallback. No limited-mode accounts.
- The agent can direct the human to a signup flow, but it **cannot complete the enrollment.**
- During setup, the human:
  1. Creates a passkey (bound to their physical device)
  2. Verifies their email (for notifications, not for auth)
  3. Registers their agent(s) by agent_id
  4. Sets initial risk tiers (with defaults pre-filled and the hard ceiling shown)
- **Modifying risk tiers (raising limits, adding new agents) requires passkey confirmation.**
- **Lowering limits or revoking agents requires only email confirmation** (reduced ceremony because it only reduces risk).

### Cold-start optimization (the activation code flow)

The first-time experience is the biggest UX risk. Without optimization, the human must: discover AgentCafe → visit setup → create passkey → configure tiers → approve mandate → return to agent. That's too many steps before any value is delivered.

**Solution: the one-time activation code flow** (modeled on the GitHub device flow pattern).

1. Agent wants to act on a service for a human who has no AgentCafe account.
2. Agent calls `POST /passport/issue-request`. Cafe detects no account exists.
3. Cafe returns a **one-time 8-character alphanumeric activation code** (e.g., `X7K9-P2MQ`) with checksum digit. Code is valid for 15 minutes, single-use, tied to the specific request.
4. Agent tells the human (in chat, voice, email, whatever): *"To give me permission for work hotels, go to agentcafe.com, click 'Start authorization', and enter code X7K9-P2MQ."*
5. **The human navigates themselves** to `https://agentcafe.com` (they type it or use a bookmark — no URL ever leaves the Cafe).
6. Human enters the code. Cafe validates → shows a **single combined page** that handles:
   - Account creation + passkey enrollment
   - The agent's specific mandate request (pre-filled, Cafe-authored plain English)
   - Risk tier defaults (pre-filled with safe defaults, hard ceiling shown)
   - One "Create account & approve" action (requires passkey)
7. Human completes with one passkey tap. Cafe issues the passport. Agent receives it immediately.

**Why this is better than a deep-link:**
- **No URL ever leaves the Cafe** → no phishing link possible. The agent sends a short code, not a clickable URL.
- **The human always lands on the real domain** they control (typed or bookmarked).
- UX remains "magic moment" (one page, one passkey tap after entering 8 characters).
- Standard, well-understood pattern (GitHub device flow, 2FA recovery codes, etc.).
- Rate-limit code generation per IP and per agent to prevent abuse.

**For subsequent interactions:** the account exists, the passkey is enrolled, standing mandates may already cover the request. Most actions flow through with zero friction.

---

## 6. Revocation & Visibility

- Human has a dashboard: "What can my agents do right now?" + "What have my agents actually done?"
- Dashboard shows: active passports, active standing mandates (with expiry dates and first-use status), last 7 days of actions, total spend, any denied requests
- One-click revocation of any passport or mandate
- Immediate effect (jti blacklist + standing mandate invalidation)
- Every issuance is permanently audited with the exact authorization text the human saw, the confirmation method used, and a timestamp

---

## 7. Issuance Ceremony (the human moment)

1. Agent calls `POST /passport/issue-request` with desired scopes, authorizations, and limits
2. Cafe translates the request into plain-English consent text (agent cannot influence wording)
3. For new accounts: Cafe returns an activation code (see §5). For existing accounts: Cafe sends the human a push notification (or email as fallback for Layer 2 approvals only).
4. Human reviews, then chooses: **Approve exactly as requested** / **Edit limits** / **Deny**
5. For new services or high-value mandates: passkey confirmation required
6. Once approved, Cafe issues the signed JWT and returns it to the agent
7. The consent record (exact text shown, method of confirmation, timestamp, human's choice) is permanently stored and linked to the passport via `consent_id`

---

## 8. Why this is defensible at POA level

- **Physical-world identity anchor** — the system's trust root is hardware the agent cannot possess. Every account requires a passkey. No exceptions.
- **No fallback tiers** — there is no weaker account type for an attacker to exploit at scale
- Human sees **concrete outcomes** for anything that matters (Layer 3) and **real first use** of every mandate (Layer 2.5)
- Agent cannot act alone on high-value things
- **Backend token isolation** — backends never see the human's passport; the Cafe proxies with per-call credentials
- **Activation code flow** — no phishing-vulnerable URLs; the human always navigates to the real domain themselves
- Full audit trail of exactly what the human was shown and when they approved
- Easy, instant revocation with no backend lag
- Velocity rules prevent structuring attacks
- Asymmetric ceremony (easy to lock down, hard to open up) resists social engineering of the human's own settings
- We can stand behind this legally because we enforced high-ceremony confirmation for risky actions and we have cryptographic + audit proof of every consent moment

---

## 9. Phased Implementation

| Phase | What ships | Security level |
|-------|-----------|---------------|
| **2 (current)** | JWT issuance/validation, revocation table, scopes + authorizations, migration flag | MVP: API-key issuance for dev/testing. Not yet suitable for real money. |
| **3** | Human account creation with passkey, activation code flow, consent ceremony UI, standing mandates, Layer 2.5 first-use confirmation, Layer 3 async confirmation, velocity data model + per-service enforcement, total exposure calculation + dashboard | Production-grade for low-to-medium risk actions. **Requires legal review before beta.** |
| **4** | Cross-service velocity, push notifications, periodic re-confirmation, risk-based tiering refinements | Production-grade for high-value actions |
| **5+** | Open standard for other Cafes/gateways, insurance/liability partnerships, SOC 2, biometric options, agent attestation (if industry standard emerges) | Enterprise / regulated use |

### Legal review gate (before Phase 3 beta)

Before any real human accounts exist, engage counsel for a **2–3 day targeted review** covering:
- Threat model + consent flows + Phase 3 wireframes
- EFTA/Reg E exposure: are passports "access devices"? Liability allocation in BaaS/fintech partnerships.
- Digital/electronic POA recognition (35+ U.S. states already recognize; our physical-anchor + cryptographic audit trail aligns well)
- Consent UI text and audit log format (better to shape this with lawyers now than retrofit)
- SOC 2 Type I can run in parallel

This is **not a blocker for internal development** but it **is a blocker for any external user exposure.**

---

## 10. Discussed & Closed Ideas

These were raised during review, evaluated, and deliberately rejected or deferred. Documented here so we don't re-litigate them.

### Limited-mode accounts (killed in v1.4)

Originally proposed as a passkey-free fallback with $100 lifetime cap. **Killed because:** any account without a hardware anchor creates a mass-registration attack surface that is expensive to defend (requires credit-card verification, rate limiting, BIN-range tracking) and fundamentally undermines Design Principle Zero. With 96% of devices passkey-ready in Feb 2026, the adoption cost of passkey-only is negligible. Humans without passkeys use their agents directly without Cafe mediation. The security simplification is worth more than the marginal adoption gain.

### Agent trustworthiness scores (deferred indefinitely)

Proposed reducing ceremony for agents with a proven track record. **Rejected because:** creates long-con fraud incentive. Agents can be spun up, build trust over N actions, then exploit reduced ceremony on action N+1. Cannot work without persistent, verified agent identity — which does not exist (see Design Principle One). Defer until strong attestation standards emerge (Phase 5+ at earliest).

### Purpose-locked mandates (rejected)

Proposed locking standing mandates to a stated purpose (e.g., "work trips only"). **Rejected because:** unenforceable at the protocol level. The Cafe cannot determine whether a hotel booking is for work or leisure. The real mitigations are: Cafe-authored consent text that *describes* intended purpose (for human awareness), mandatory 90-day expiry, dashboard showing actual usage, instant revocation, and **Layer 2.5 first-use confirmation** (the human sees real behavior immediately). The Cafe should not attempt to enforce subjective intent.

### Deep-link cold-start flow (replaced in v1.4)

Originally proposed sending the human a URL. **Replaced with activation code flow because:** any URL the agent sends to the human is a phishing vector. The activation code pattern (agent sends a short code, human navigates to the real domain themselves) eliminates this at the root.
