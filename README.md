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

### Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

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

### Architecture

- **Locked Menu format**: semantic, no HTTP methods/paths, agent-friendly
- **Full proxy**: agents never see backend URLs or tokens
- **Double validation**: Human Passport + Company Policy on every order
- **Tech**: Python 3.12 + FastAPI + SQLite (MVP)

### Project Layout

```
AgentCafe/
├── agentcafe/              # Python package
│   ├── main.py             # FastAPI app — starts Cafe + demo backends
│   ├── config.py           # Environment-based configuration
│   ├── cafe/               # Menu discovery + order routing + passport
│   ├── db/                 # SQLite schema, engine, seed data
│   └── demo_backends/      # 3 demo services (hotel, lunch, home)
├── tests/                  # 27 tests (menu, order, passport)
├── docs/
│   ├── design/             # Service specs, menu format, onboarding wizard
│   └── passport/           # Passport system design + threat model
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

**Status:** Phase 2 complete — Cafe runs end-to-end with 3 demo services, Menu discovery, proxy ordering, and JWT-based Passport validation (behind migration flag). 27 tests passing.
**Next:** Phase 3 (Human accounts with passkey enrollment, activation code flow, standing mandates, Layer 3 async confirmation — see `docs/passport/` for full architecture)
**Built for:** The inevitable agent economy
