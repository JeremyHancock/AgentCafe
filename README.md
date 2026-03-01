# AgentCafe ☕

**The Cafe for Agents.**

Services voluntarily register their offerings on the Menu.
Agents walk in, browse the clean, semantic Menu, and when they find what they need they present their Passport and order.

Zero mandatory onboarding for humans.
Company onboarding is ridiculously easy, completely free, and insanely safe.

### How it works

You give your personal agent a task.
Your agent visits AgentCafe, looks at the Menu of registered services, and when it finds the right action, it presents your authorization (Passport).

If it has permission, the Cafe safely handles the request behind the scenes (as a secure proxy). If not, the agent simply leaves and finds another way.

**Two-tier Passport system:**
- **Tier 1 (Read):** Agent self-requests. No human involved. Can browse menus and check availability.
- **Tier 2 (Write):** Requires human consent. Your agent requests permission, you review and approve on a Cafe-branded page, and the agent gets a short-lived token to act on your behalf.

### For companies

Onboarding is insanely easy:
Upload your existing API spec → answer a few guided questions → preview how it appears on the Menu → publish.

Completely free to join. You stay in full control of what agents can do, and every call is protected by double validation (human authorization + your policy).

### Quick Start (Docker)

```bash
docker compose up --build
curl http://localhost:8000/cafe/menu
```

### Quick Start (Local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Delete stale DB if you've pulled schema changes
rm -f agentcafe.db

# Start the Cafe (launches 4 servers: Cafe + 3 demo backends)
PASSPORT_SIGNING_SECRET=dev-secret-minimum-32-bytes!! \
ISSUER_API_KEY=admin123 \
python -m agentcafe.main

# Browse the Menu
curl http://localhost:8000/cafe/menu

# Place an order through the proxy
curl -X POST http://localhost:8000/cafe/order \
  -H "Content-Type: application/json" \
  -d '{"service_id":"stayright-hotels","action_id":"search-availability","passport":"demo-passport","inputs":{"city":"Austin","check_in":"2026-03-15","check_out":"2026-03-18","guests":2}}'

# Run tests (177 passing)
pytest tests/ -v
```

### Company Dashboard (Next.js)

```bash
cd dashboard && npm install && npm run dev
# http://localhost:3000 (proxies API to backend on :8000)
```

Pages: `/login`, `/register`, `/onboard` (4-step wizard), `/services` (manage your services), `/admin` (platform admin, requires ISSUER_API_KEY).

A sample spec is included at `dashboard/sample-spec.yaml` for testing the onboarding flow.

### Demo Agent

```bash
python -m agentcafe.demo_agent --headless
# Full lifecycle: browse menu → register → read → consent → approve → write → refresh
```

### Consent Flow (try it locally)

With the Cafe running (`python -m agentcafe.main`):

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

You'll be prompted to create an account (or log in), then you'll see the consent approval page with the service details, risk tier, and duration selector.

### Wizard API (for companies)

```bash
# Create a company account
curl -X POST http://localhost:8000/wizard/companies \
  -H "Content-Type: application/json" \
  -d '{"name": "My Company", "email": "dev@example.com", "password": "secure1234"}'
# → {"company_id": "...", "session_token": "..."}

# Upload an OpenAPI spec (use the session_token from above)
curl -X POST http://localhost:8000/wizard/specs/parse \
  -H "Authorization: Bearer <session_token>" \
  -H "Content-Type: application/json" \
  -d '{"raw_spec": "<your OpenAPI YAML or JSON>"}'
# → {"draft_id": "...", "parsed_spec": {...}, "candidate_menu": {...}}

# Then: review → policy → preview → publish (see /docs for full API)
```

### Architecture

- **Locked Menu format**: semantic, no HTTP methods/paths, agent-friendly
- **Full proxy**: agents never see backend URLs or tokens
- **Double validation**: Human Passport + Company Policy on every order
- **Passport V2**: Two-tier JWT system with human consent flow, risk-tier ceilings, identity verification, and instant policy revocation
- **Consent UI**: Server-rendered pages for human authorization (login, review, approve/decline)
- **Company Onboarding Wizard**: OpenAPI spec → guided review → one-click publish
- **Tech**: Python 3.12 + FastAPI + SQLite (MVP) + Jinja2 + LiteLLM (wizard AI enrichment)

### Project Layout

```
AgentCafe/
├── agentcafe/              # Python package
│   ├── main.py             # FastAPI app — starts Cafe + demo backends
│   ├── config.py           # Environment-based configuration
│   ├── cafe/               # Menu, order routing, passport, consent, human accounts, pages
│   ├── templates/          # Jinja2 templates (login, register, consent approval)
│   ├── wizard/             # Company Onboarding Wizard (spec parser, AI enricher, review, publish)
│   ├── db/                 # SQLite schema, engine, migrations, seed data
│   └── demo_backends/      # 3 demo services (hotel, lunch, home)
├── dashboard/             # Next.js 15 Company Dashboard (React 19, Tailwind 4)
│   └── src/app/           # /login, /register, /onboard, /services, /admin
├── tests/                  # 177 tests (menu, order, passport, consent, policy, wizard, crypto, e2e)
├── docs/
│   ├── design/             # Service specs, menu format, onboarding wizard
│   ├── passport/           # Passport V2 design + threat model
│   └── building-agents-for-agentcafe.md  # Developer guide
├── Dockerfile              # Single image, multi-service
├── docker-compose.yml      # 4 containers: Cafe + 3 demo backends
├── AGENT_CONTEXT.md        # Project bible for AI contributors (read first)
├── DECISIONS.md            # Architectural decisions log
├── DEVELOPMENT-PLAN.md     # Ordered phases with completion status
└── pyproject.toml          # Dependencies and build config
```

### Demo backends

| Service | Port | Description |
|---------|------|-------------|
| StayRight Hotels | 8001 | Hotel room search + booking |
| QuickBite Delivery | 8002 | Restaurant menu + lunch ordering |
| FixRight Home Services | 8003 | Home service appointment scheduling |

---

**Status:** Phase 5 complete. Full stack operational: Passport V2 (two-tier JWT, human consent flow, risk-tier ceilings, identity verification), backend credential encryption (AES-256-GCM), tamper-evident audit logging (SHA-256 hash chain), Company Onboarding Wizard (API + Next.js dashboard), service quarantine & suspension, platform admin dashboard, E2E demo agent. 177 tests passing, pylint 10.00/10.  
**Next:** Phase 6 — Passport signing key management (RS256 + KMS), production Docker hardening, edit-after-publish, open-core split.  
**Built for:** The inevitable agent economy
