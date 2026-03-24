# AgentCafe ☕

**The Cafe for Agents.** Live at [agentcafe.io](https://agentcafe.io).

Services register their offerings on the Menu. Agents walk in, browse the clean semantic Menu, present their Passport, and order. The Cafe handles everything in between — authorization, proxying, audit logging, and consent.

## How It Works

You give your personal agent a task. Your agent visits AgentCafe, discovers services on the Menu, and when it finds the right action, it presents your authorization (Passport). The Cafe safely proxies the request — agents never see backend URLs or credentials.

**Two-tier Passport system:**
- **Tier 1 (Read):** Agent self-requests. No human involved. Browse menus, check availability.
- **Tier 2 (Write):** Requires human consent. You review and approve on a Cafe-branded page, and the agent gets a short-lived token to act on your behalf.

**Company Cards:** Standing authorization policies that eliminate per-action consent for trusted services. Humans set budget, duration, and scope constraints. High/critical-risk actions always require individual approval.

## For Agents (MCP)

Any MCP-compatible agent can connect directly:

```
Endpoint: https://agentcafe.io/mcp
Transport: Streamable HTTP (stateless)
```

Four tools are exposed:

| Tool | Description |
|------|-------------|
| `cafe.search` | Search the service catalog (summaries only) |
| `cafe.get_details` | Full Menu entry with input schemas and constraints |
| `cafe.request_card` | Request a Company Card for standing authorization |
| `cafe.invoke` | Execute a service action through the Cafe proxy |

Works with Claude Desktop, Cursor, Windsurf, LangChain, CrewAI, OpenAI Agents SDK, and any MCP client.

See also `examples/` for GPT function-calling and Claude tool_use integration snippets.

## For Companies

Upload your existing API spec → answer a few guided questions → preview how it appears on the Menu → publish.

Free to join. You stay in full control of what agents can do, and every call is protected by double validation (human authorization + company policy).

- `/services/login` — Sign in
- `/services/register` — Registration
- `/services/onboard` — 4-step onboarding wizard
- `/services` — Service management (pause/resume/unpublish)
- `/admin` — Platform admin dashboard

A sample spec is included at `examples/sample-spec.yaml` for testing.

## Quick Start (Docker)

```bash
docker compose up --build
curl http://localhost:8000/cafe/menu
```

## Quick Start (Local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Delete stale DB if you've pulled schema changes
rm -f agentcafe.db

# Start the Cafe (launches Cafe + 3 demo backends)
PASSPORT_SIGNING_SECRET=dev-secret-minimum-32-bytes!! \
ISSUER_API_KEY=admin123 \
python -m agentcafe.main

# Browse the Menu
curl http://localhost:8000/cafe/menu

# Place an order through the proxy
curl -X POST http://localhost:8000/cafe/order \
  -H "Content-Type: application/json" \
  -d '{"service_id":"stayright-hotels","action_id":"search-availability","passport":"demo-passport","inputs":{"city":"Austin","check_in":"2026-03-15","check_out":"2026-03-18","guests":2}}'

# Run tests
pytest tests/ -v
```

## Demo Agent

```bash
python -m agentcafe.demo_agent --headless
# 9-step lifecycle: browse menu → register → read → consent → approve → exchange → read-before-write → write → refresh

# Against live production:
python -m agentcafe.demo_agent --base-url https://agentcafe.io
```

## Consent Flow

With the Cafe running:

```bash
# 1. Agent registers and gets a Tier-1 read token
TOKEN=$(curl -s http://localhost:8000/passport/register \
  -X POST -H "Content-Type: application/json" \
  -d '{"agent_tag":"my-agent"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['passport'])")

# 2. Agent requests consent for a write action
CONSENT_ID=$(curl -s http://localhost:8000/consents/initiate \
  -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"service_id":"stayright-hotels","action_id":"book-room"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['consent_id'])")

# 3. Open the consent page in your browser
echo "Approve at: http://localhost:8000/authorize/$CONSENT_ID"
```

You'll be prompted to create an account (or log in), then you'll see the consent approval page with service details, risk tier, and duration selector.

**Activation codes:** The `initiate` response also includes an `activation_code` and `activation_url`. New users can visit `/activate`, enter the code, register with a passkey, and approve — all in one step.

## Architecture

- **Locked Menu format** — semantic, no HTTP methods/paths, agent-friendly
- **Full proxy** — agents never see backend URLs or tokens
- **Double validation** — Human Passport + Company Policy on every order
- **Passport V2** — Two-tier JWT system with RS256 signing, JWKS endpoint (`/.well-known/jwks.json`), risk-tier ceilings, identity verification, instant revocation
- **Company Cards** — Standing multi-action policies with budget, duration, and scope constraints
- **MCP Server Adapter** — 4-tool LLM-native discovery via Streamable HTTP at `/mcp`
- **WebAuthn passkeys** — Phishing-resistant auth for humans. Passkey required for consent approval. Grace period auto-disables password login.
- **Consent UI** — Server-rendered pages for authorization (login, review, approve/decline, activation codes)
- **Company Onboarding Wizard** — OpenAPI spec → guided review → one-click publish
- **Tamper-evident audit log** — SHA-256 hash-chained, every order logged
- **Tech** — Python 3.12 + FastAPI + SQLite + Jinja2 + LiteLLM + MCP SDK
- **License** — MIT

## Project Layout

```
AgentCafe/
├── agentcafe/
│   ├── main.py             # FastAPI app — starts Cafe + demo backends
│   ├── config.py           # Environment-based configuration
│   ├── keys.py             # RS256 key management, JWKS
│   ├── crypto.py           # AES-256-GCM encrypt/decrypt for backend credentials
│   ├── cafe/
│   │   ├── menu.py         # Menu assembly
│   │   ├── router.py       # /cafe/menu, /cafe/order, /cafe/admin/*
│   │   ├── passport.py     # Passport V2: register, validate, revoke
│   │   ├── consent.py      # Consent flow: initiate, approve, token exchange
│   │   ├── human.py        # Human accounts: register, login, passkeys
│   │   ├── cards.py        # Company Cards: request, approve, token, revoke, budget
│   │   ├── mcp_adapter.py  # MCP Server: 4 tools at /mcp
│   │   ├── pages.py        # Jinja2 pages (login, consent, dashboard, tab)
│   │   └── wizard_pages.py # Company wizard pages (onboard, services, admin)
│   ├── wizard/             # Onboarding Wizard (spec parser, AI enricher, publisher)
│   ├── templates/          # Jinja2 templates
│   ├── db/                 # SQLite schema, migrations (0001–0011), seed data
│   ├── demo_backends/      # 3 demo services (hotel, lunch, home)
│   └── demo_agent/         # CLI demo agent (9-step lifecycle)
├── examples/               # Integration snippets (GPT, Claude, sample spec)
├── tests/                  # 335 tests
├── docs/                   # ADRs, specs, planning, strategy, reviews
├── Dockerfile              # Multi-stage build, hardened slim image, non-root
├── docker-compose.yml      # Cafe + 3 demo backends
├── AGENT_CONTEXT.md        # Project context for AI contributors
└── pyproject.toml          # Dependencies and build config
```

## Demo Backends

| Service | Port | Description |
|---------|------|-------------|
| StayRight Hotels | 8001 | Hotel room search + booking |
| QuickBite Delivery | 8002 | Restaurant menu + lunch ordering |
| FixRight Home Services | 8003 | Home service appointment scheduling |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PASSPORT_SIGNING_SECRET` | Yes | HS256 secret for legacy tokens (≥32 bytes) |
| `ISSUER_API_KEY` | Yes | Admin API key for platform endpoints |
| `CAFE_ENCRYPTION_KEY` | Production | AES-256 key for backend credential encryption |
| `PASSPORT_RSA_PRIVATE_KEY` | Production | RS256 private key (PEM). Auto-generated in dev. |
| `OPENAI_API_KEY` | Optional | For wizard AI enrichment (LiteLLM) |
| `CAFE_DB_PATH` | Optional | SQLite path (default: `agentcafe.db`) |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

**Status:** Live at [agentcafe.io](https://agentcafe.io). 335 tests, pylint 10.00/10. CI/CD via GitHub Actions → Fly.io.  
**Built for:** Human-authorized delegation to autonomous agents
