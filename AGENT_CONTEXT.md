# AGENT_CONTEXT.md — AgentCafe
**Project Bible for All AI Contributors — read this first before touching any code.**  
Last Updated: February 26, 2026 (Phase 3 complete)

## 1. Project Vision & Origin
We are building **AgentCafe** — the friendly, trusted Cafe where AI agents discover and safely use services that companies have voluntarily registered.

Services put their offerings on the Menu.  
Agents browse freely.  
When they want to act, they present a valid Passport. The Cafe enforces safety as a mandatory proxy and forwards the request.

Key principles (locked):
- Zero mandatory pre-onboarding for humans (agent handles consent/pre-approvals)
- Company onboarding is the main product: ridiculously easy + completely free + insanely safe
- We are a full proxy — agents never touch backend URLs or long-lived tokens
- Double validation on every order: Human Passport + Company Policy
- The Menu is semantic, lightweight, and future-proof for 2026–2027 agents

## 2. Locked Menu Format (Feb 22 2026)
Agents receive a clean semantic menu (no HTTP methods, no paths, no full schemas).  
Schema policy: **no breaking changes**; additive fields allowed with an ADR (see `DECISIONS.md`).

**Services**:
- service_id (slug, format: `{brand}-{category}`, e.g. `stayright-hotels`)
- name
- category (string, e.g. `"hotels"`, `"food-delivery"`, `"home-services"`)
- capability_tags (array of strings for agent discovery, e.g. `["travel", "booking", "accommodation"]`)
- description

**Actions**:
- action_id (slug)
- description (what it accomplishes)
- example_response (JSON preview)
- cost (object: required_scopes, human_authorization_required, limits)
- required_inputs (array of {name, description, example})

When ordering: POST /cafe/order with service_id, action_id, passport, inputs.

## 3. Core Metaphor & Rules
- Central Cafe/Menu discovery is the core bet — inevitable and agent-first.
- Agents browse freely, order or leave.
- Company Onboarding Wizard is the product we polish until it feels magical.
- Human consent handling is explicitly an agent-side concern.
- Three mocked services for MVP to make the Cafe feel alive immediately.
- Security and company trust first.

## 4. Tech Stack (Locked)
- Python 3.12 + FastAPI
- SQLite (MVP)
- LiteLLM for wizard AI enrichment (optional — rule-based fallback when unavailable)
- Clean-slate (no forking existing gateways)

## 5. Current Status & What Exists

**Phase 0.2 — COMPLETE.** Design deliverables in `docs/design/`:
- Three demo services fully designed (WHY.md, OpenAPI 3.1 specs, locked Menu entries)
- Company Onboarding Wizard fully designed (FLOW.md, UI-SCREENS.md, ARCHITECTURE.md)
- Combined Menu preview at `docs/design/menu/full-menu.json`

**Phase 1 — COMPLETE.** Working codebase in `agentcafe/` (Python package):
- Three demo backends running (hotel, lunch, home services) with realistic mock data
- `GET /cafe/menu` returns the full locked Menu format from the database
- `POST /cafe/order` proxies requests through double validation to the correct backend
- Audit logging on every order

**Phase 3 — COMPLETE.** Company Onboarding Wizard in `agentcafe/wizard/`:
- Spec Parser: OpenAPI 3.0/3.1 ingestion (YAML + JSON), recursive `$ref` resolution, operation extraction, read/write classification, required-only input filtering
- AI Enricher: LiteLLM-based Menu entry generation with rule-based fallback, no parameter truncation
- Review Engine: Draft management, company edits preserved in `company_edits_json` (separate from AI-generated `candidate_menu_json`), preview generation
- Publisher: Atomic one-click publish to `published_services` + `proxy_configs`
- Full wizard API: `/wizard/companies`, `/wizard/specs/parse`, `/wizard/drafts/{id}/review|policy|preview|dry-run|publish`
- JWT session tokens (`Authorization: Bearer <token>`) on all wizard endpoints with draft ownership enforcement
- bcrypt password hashing, Pydantic input validation on company create
- Dry-run endpoint with resolved action path HEAD requests

**Phase 3.1 — COMPLETE.** Code quality and lint cleanup:
- Pylint 10.00/10 — zero warnings across all source and test files
- `_State` class pattern replaces all `global` statements repo-wide
- Proper exception chaining, narrowed exception types, removed dead code
- 77 passing tests (48 existing + 29 wizard tests)

## 6. Codebase Map

```
AgentCafe/
├── agentcafe/                      # Python package
│   ├── main.py                     # Entry point — starts Cafe (port 8000) + 3 demo backends (8001-8003)
│   ├── config.py                   # Env-based config (CafeConfig dataclass)
│   ├── db/
│   │   ├── models.py               # SQLite schema: companies, published_services, proxy_configs, audit_log, draft_services
│   │   ├── engine.py               # DB connection singleton (aiosqlite)
│   │   └── seed.py                 # Loads Menu entries from docs/design JSON files + seeds proxy configs on startup
│   ├── cafe/
│   │   ├── menu.py                 # Assembles the locked Menu from published_services
│   │   ├── passport.py             # JWT Passport: issuance, validation, revocation (Phase 2)
│   │   ├── policy.py               # Company Policy: rate limiting + input type validation (Phase 2.3)
│   │   └── router.py               # GET /cafe/menu + POST /cafe/order (proxy + double validation + audit)
│   ├── wizard/                     # Company Onboarding Wizard (Phase 3)
│   │   ├── models.py               # Pydantic models for all wizard data
│   │   ├── spec_parser.py          # OpenAPI 3.x parsing + validation + operation extraction
│   │   ├── ai_enricher.py          # LiteLLM enrichment with rule-based fallback
│   │   ├── review_engine.py        # Draft management, edits, preview generation
│   │   ├── publisher.py            # Atomic publish to Menu + proxy configs
│   │   └── router.py               # FastAPI router for all /wizard/* endpoints
│   └── demo_backends/
│       ├── hotel.py                # StayRight Hotels — 4 endpoints, in-memory data
│       ├── lunch.py                # QuickBite Delivery — 4 endpoints, in-memory data
│       └── home_service.py         # FixRight Home — 4 endpoints, in-memory data
├── tests/
│   ├── conftest.py                 # Shared fixtures: in-memory DB, ASGI test client
│   ├── test_menu.py                # 7 tests: format compliance, actions, auth requirements
│   ├── test_order.py               # 8 tests: rejection + input validation + happy-path proxy (MVP mode)
│   ├── test_passport.py            # 12 tests: JWT issuance, scope/wildcard/authorization validation, revocation
│   ├── test_policy.py              # 21 tests: rate limiting (unit + integration), input type validation
│   └── test_wizard.py              # 29 tests: spec parsing, enrichment, hotel spec $ref, full wizard API flow, ownership, auth, dry-run
├── docs/
│   ├── design/                     # Service specs, menu format, onboarding wizard design
│   └── passport/                   # Passport system design + threat model (v1.4, locked)
├── Dockerfile                      # Single image, multi-service (Python 3.12-slim)
├── docker-compose.yml              # 4 containers: Cafe + 3 demo backends on shared network
├── pyproject.toml                  # Dependencies and build config
├── AGENT_CONTEXT.md                # This file
├── DECISIONS.md                    # Architectural decisions log
└── DEVELOPMENT-PLAN.md             # Ordered phases with completion status
```

## 7. What's Real vs. MVP Placeholder

| Component | Status | Notes |
|-----------|--------|-------|
| Menu format | **LOCKED & REAL** | No breaking changes. Additive fields allowed with an ADR. Includes `category` and `capability_tags` (ADR-009). |
| Menu discovery (`GET /cafe/menu`) | **Real** | Reads from SQLite, assembles locked format |
| Proxy (`POST /cafe/order`) | **Real** | Routes to correct backend, resolves path params, returns response |
| Demo backends (hotel, lunch, home) | **Real mock data** | In-memory, realistic responses, no persistence across restarts |
| Passport validation | **Real (behind flag)** | `USE_REAL_PASSPORT=true` enables JWT validation (HS256, scopes, expiry, revocation). Default: MVP mode (`"demo-passport"`). |
| Human authorization check | **Real (behind flag)** | JWT `authorizations` array with per-action mandates and `valid_until` enforcement. Default: MVP mode. |
| Rate limiting | **Real** | Sliding-window per passport+action using audit_log. Enforces `rate_limit` from proxy_configs (e.g., `60/minute`). |
| Company Onboarding Wizard | **Real** | Spec parser, AI enricher (LiteLLM + rule-based fallback), review engine, publisher. Full API at `/wizard/*`. |
| Audit log | **Real** | Every order writes to `audit_log` table (hashed passport, hashed inputs, outcome, latency) |
| DB encryption for backend creds | **Not implemented** | Backend auth headers stored as plaintext in MVP. Phase 4. |

## 8. How to Run

**Docker (recommended):**
```bash
docker compose up --build          # Builds image, starts 4 containers
# Menu:  http://localhost:8000/cafe/menu
# Order: POST http://localhost:8000/cafe/order
docker compose down                # Stop and remove containers
```

**Local (all-in-one process):**
```bash
source .venv/bin/activate          # venv already created (at repo root)
rm -f agentcafe.db                 # IMPORTANT: delete stale DB after schema changes
python -m agentcafe.main           # Starts all 4 servers in one process
# Menu:  http://127.0.0.1:8000/cafe/menu
# Order: POST http://127.0.0.1:8000/cafe/order
# Passport: POST http://127.0.0.1:8000/passport/issue + POST /cafe/revoke
# Wizard: POST http://127.0.0.1:8000/wizard/companies + /wizard/specs/parse + ...
# API docs: http://127.0.0.1:8000/docs
pytest tests/ -v                   # 77 tests passing
python -m pylint agentcafe/ tests/ --disable=C,R  # 10.00/10
```

**⚠️ Stale DB caveat:** SQLite uses `CREATE TABLE IF NOT EXISTS`, so if `agentcafe.db` exists from a previous run with an older schema, new columns (like `password_hash`) won't be added. Always `rm -f agentcafe.db` after schema changes. Tests use in-memory DBs and are unaffected.

**Wizard onboarding walkthrough (curl):**
```bash
# 1. Create company account
curl -s -X POST http://localhost:8000/wizard/companies \
  -H "Content-Type: application/json" \
  -d '{"name":"My Company","email":"dev@example.com","password":"secure1234"}'
# → {"company_id": "...", "session_token": "<JWT>"}

# 2. Parse OpenAPI spec (use session_token from step 1)
curl -s -X POST http://localhost:8000/wizard/specs/parse \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{\"raw_spec\": $(python3 -c "import json; print(json.dumps(open('docs/design/services/hotel-booking/openapi.yaml').read()))")}"
# → {"draft_id": "...", "parsed_spec": {...}, "candidate_menu": {...}}

# 3. Review — MUST include actions array (see Known Limitations below)
curl -s -X PUT http://localhost:8000/wizard/drafts/<DRAFT_ID>/review \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"service_id":"my-service","name":"My Service","actions":[...]}'

# 4. Policy — set scopes, rate limits, backend URL
# 5. Preview — GET .../preview to see final Menu entry
# 6. Publish — POST .../publish to go live on GET /cafe/menu
```

## 9. Architecture Notes

- **`_State` class pattern**: All module-level mutable state uses a `_State` class instead of `global` statements. Access via `_state.attribute`. Tests monkeypatch via `monkeypatch.setattr(module._state, "attr", value)`. Applied in `engine.py`, `passport.py`, `cafe/router.py`, `wizard/router.py`.
- **Shared httpx.AsyncClient**: `cafe/router.py` uses `_state.http_client` (`get_http_client()`) for proxying — reuses TCP connections. Closed on shutdown via `close_http_client()` in `main.py`.
- **Named row access**: All `aiosqlite.Row` results use `row["column_name"]` (not positional `row[0]`). `row_factory = aiosqlite.Row` is set in `engine.py`.
- **Audit log indexes**: `audit_log` has indexes on `timestamp`, `(service_id, action_id)`, and `passport_hash` for future query performance.
- **Wizard auth**: JWT session tokens signed with `PASSPORT_SIGNING_SECRET`, 8-hour expiry, `iss=agentcafe-wizard`. All draft endpoints validate token + check `draft.company_id == token.sub`.
- **Test mocking pattern**: Happy-path order tests use a `_MultiBackendTransport` class that routes httpx requests to the correct demo backend via ASGI transport — no running servers needed. See `test_order.py`.
- **Decisions log**: See `DECISIONS.md` for rationale behind architectural choices.

## 9.1 Known Limitations (from live testing)

- **Review replaces, doesn't merge**: `PUT /wizard/drafts/{id}/review` stores company edits as a complete replacement of the candidate menu. Submitting a review with no `actions` array results in an empty preview. The Phase 5 dashboard must pre-populate the form with AI-generated values and merge partial edits.
- **MVP passport vs wizard-published services**: `demo-passport` has hardcoded scopes for the 3 seeded services only. Orders to wizard-published services are correctly rejected with `scope_missing`. To test wizard-published services end-to-end, enable `USE_REAL_PASSPORT=true` and issue a JWT with the wizard-assigned scopes.
- **LLM enrichment model hardcoded**: `ENRICHMENT_MODEL = "gpt-4o-mini"` in `ai_enricher.py` line 27. Not configurable via env var yet. Rule-based fallback works without any LLM.

## 10. Rules for All AI Contributors
- Always respect the locked Menu format and proxy architecture.
- **Menu entries in `docs/design/services/*/menu-entry.json` are the single source of truth.** Edit those files to change what agents see; `seed.py` loads them at startup.
- Read the codebase map (Section 6) before making changes — know what exists.
- Check Section 7 to understand what's real vs. placeholder before building on top of it.
- Produce clean, readable, well-commented code.
- Include tests with meaningful changes.
- Security first — every call must go through double validation.
- Never expose backend URLs, auth headers, or internal paths to agents.
- When in doubt, ask rather than assume.
