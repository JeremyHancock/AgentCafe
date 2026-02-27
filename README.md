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
- **Company Onboarding Wizard**: OpenAPI spec → guided review → one-click publish
- **Tech**: Python 3.12 + FastAPI + SQLite (MVP) + LiteLLM (wizard AI enrichment)

### Project Layout

```
AgentCafe/
├── agentcafe/              # Python package
│   ├── main.py             # FastAPI app — starts Cafe + demo backends
│   ├── config.py           # Environment-based configuration
│   ├── cafe/               # Menu discovery + order routing + passport
│   ├── wizard/             # Company Onboarding Wizard (spec parser, AI enricher, review, publish)
│   ├── db/                 # SQLite schema, engine, seed data
│   └── demo_backends/      # 3 demo services (hotel, lunch, home)
├── tests/                  # 77 tests (menu, order, passport, policy, wizard)
├── docs/
│   ├── design/             # Service specs, menu format, onboarding wizard
│   └── passport/           # Passport system design + threat model
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

**Status:** Phase 3 complete — Cafe runs end-to-end with 3 demo services, Menu discovery, proxy ordering, JWT Passport validation, rate limiting, input type validation, and a fully functional Company Onboarding Wizard (spec parsing, AI enrichment, review, policy, dry-run, publish). JWT session auth with bcrypt passwords, draft ownership enforcement. 77 tests passing, pylint 10.00/10.
**Next:** Phase 4 (Security & Guardrails) then Phase 5 (Wizard Dashboard UI, end-to-end agent demo)
**Built for:** The inevitable agent economy
