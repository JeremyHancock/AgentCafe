# Design — Demo Backend APIs + Company Onboarding Wizard

**Phase:** 0.2 (Discovery & Design) — **COMPLETE**
**Author:** Cascade (AI contributor)

---

## Summary

This deliverable contains the complete design for:

1. **Three demo backend services** — realistic services that represent what real companies would voluntarily register with AgentCafe
2. **Company Onboarding Wizard** — the main product we obsess over making insanely easy and delightful

All designs respect the locked constraints from `AGENT_CONTEXT.md`:
- Locked Menu format (service_id/action_id slugs, cost object, required_inputs array)
- Full proxy architecture (agents never see backend URLs)
- Double validation (Human Passport + Company Policy)
- Zero mandatory human pre-onboarding
- Company onboarding is completely free, full company control, maximum safety

---

## Deliverables

### Demo Services

| Service | Directory | Why They'd Join |
|---------|-----------|-----------------|
| **HotelBookingService** | `services/hotel-booking/` | Hotels get a new distribution channel — AI agents booking rooms for millions of users without needing to build agent integrations themselves |
| **LunchDeliveryService** | `services/lunch-delivery/` | Restaurants/delivery companies capture the "agent orders lunch for busy professional" use case — frictionless demand |
| **HomeServiceAppointmentService** | `services/home-service-appointment/` | Home service marketplaces let agents schedule plumbers, electricians, cleaners — capturing intent at the moment of need |

Each service directory contains:
- `WHY.md` — Plain-English business rationale
- `openapi.yaml` — Full internal OpenAPI 3.1 spec (backend implementation)
- `menu-entry.json` — Exact preview of the locked AgentCafe Menu format

### Combined Menu

- `menu/full-menu.json` — The complete Menu as agents would see it (all three services)

### Company Onboarding Wizard

- `onboarding-wizard/FLOW.md` — Complete step-by-step user flow
- `onboarding-wizard/UI-SCREENS.md` — Sample UI text and screen descriptions
- `onboarding-wizard/ARCHITECTURE.md` — Technical design: spec ingestion, question engine, live preview, one-click publish

---

## Locked Constraints Respected

- **Menu format**: Every action has `action_id` (slug), `description`, `example_response`, `cost` (with `required_scopes`, `human_authorization_required`, `limits`), and `required_inputs` (array of `{name, description, example}`)
- **Proxy**: All OpenAPI specs define internal backend routes. Agents never see these. The Cafe proxies everything.
- **Double validation**: Every action's `cost` object specifies scopes and whether human authorization is required
- **Zero human pre-onboarding**: The Menu is browsable by any agent. Authorization is handled at order time.
- **Company control**: Each service defines its own policy (scopes, limits, authorization requirements)

---

## Next Step

Phase 1: Bootstrap the repo, implement these three backends in FastAPI, and wire up the Menu discovery endpoint.
