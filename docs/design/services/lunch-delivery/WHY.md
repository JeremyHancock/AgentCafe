# LunchDeliveryService — Why a Food Delivery Company Would Join AgentCafe

## The Business Case

Food delivery is a massive, competitive market. The next frontier is **agent-initiated orders** — a user's AI agent ordering lunch at noon without the user opening an app, browsing menus, or tapping through checkout.

"Order me a chicken Caesar salad from somewhere nearby, under $15, delivered by 12:30" — this is a natural agent task. The delivery company that's discoverable on AgentCafe gets that order. The one that isn't, doesn't.

## Why AgentCafe Specifically?

- **Capture zero-effort demand**: When an agent needs to order food, it checks the Cafe Menu. If you're listed, you're in the running. If not, the agent moves on.
- **No new integration work**: Upload your existing API spec. The Onboarding Wizard handles the rest. No agent SDK, no webhook setup, no custom auth flow.
- **You set the rules**: Define which actions agents can take, require human approval for orders over a certain amount, set rate limits. Full control.
- **Secure by design**: AgentCafe proxies every request. Your backend URL, API keys, and internal systems are never exposed to any agent.
- **Free forever on the Cafe side**: No listing fees, no per-order commission from AgentCafe. Your existing pricing model stays intact.

## What They Register

| Action | Why |
|--------|-----|
| `browse-menu` | Let agents see what's available — this is demand generation |
| `place-order` | Convert agent intent into a paid order (human authorization required) |
| `track-order` | Reduce support load — agents can check status without calling support |
| `cancel-order` | Graceful cancellation before preparation starts |

## The Bottom Line

A lunch delivery company that joins AgentCafe taps into the "agent orders food for me" workflow that's about to become mainstream. Free to join, safe by default, and the company keeps full control over pricing, availability, and policy.
