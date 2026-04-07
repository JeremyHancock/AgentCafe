# Company Cards Implementation Plan (Phase 8.1)

**Source:** `docs/strategy/strategic-review-briefing.md` §8.1, ADR-028

## Architecture Summary

A Company Card is a **standing policy** for a service. Once a human approves a card, the agent can repeatedly request tokens without human interaction — until the card expires, is revoked, or budget is exceeded.

**Key design decisions:**
- Cards are per `service_id` (not per company_id) — aligns with existing policy/token architecture
- Cards create long-lived policies with broader scope than per-action consent
- Agent uses `POST /cards/{card_id}/token` to get Tier-2 tokens (no consent ceremony per-use)
- Risk tier overrides: low/medium covered by card, high requires per-action, critical always single-use
- First-use confirmation: first real action under a new card triggers a lightweight human confirm
- Budget tracking: optional, per-period spending cap

**Agent flow with cards:**
1. Agent calls `POST /cafe/order` → gets 403 `human_auth_required` (with `card_suggestion: true`)
2. Agent calls `POST /cards/request` with service_id + suggested constraints
3. Cafe returns `card_id`, `consent_url`, `activation_code` (mirrors consent flow)
4. Human visits consent_url, sets constraints, approves with passkey
5. Agent calls `POST /cards/{card_id}/token` → gets Tier-2 write token
6. Agent uses token in `POST /cafe/order` → success
7. Token expires → agent calls `/cards/{card_id}/token` again → new token, no human needed
8. Card expires or is revoked → agent must request new card

## Sprint 1: Schema + Card Request/Approve/Token API

**Migration 0010:** `company_cards` table
- id, cafe_user_id, service_id
- allowed_action_ids (CSV, NULL = all non-excluded)
- excluded_action_ids (CSV)
- max_risk_tier_covered (low/medium — high/critical punch through)
- budget_limit_cents, budget_period, budget_spent_cents, budget_period_start
- first_use_confirmation (default ON), first_use_confirmed_at
- status (pending/active/revoked/expired)
- activation_code, expires_at, revoked_at, created_at, updated_at

**New module:** `cafe/cards.py`
- `POST /cards/request` — agent requests card (requires Tier-1 passport)
- `GET /cards/{card_id}/status` — agent polls card status
- `POST /cards/{card_id}/approve` — human approves (requires session + passkey)
- `POST /cards/{card_id}/token` — agent gets Tier-2 token from active card

**Wire:** main.py imports + configure_cards()

**Tests:** test_cards.py — request, approve, token exchange, revoked card, expired card, risk tier override

## Sprint 2: Card Management + Tab Dashboard

- `GET /cards` — human lists their cards (the "Tab")
- `POST /cards/{card_id}/revoke` — human revokes a card (kills all tokens)
- `PATCH /cards/{card_id}` — human edits constraints (budget, scope, excluded actions)
- First-use confirmation flow (lightweight confirm on first real action)
- Card approval page template (Jinja2, similar to consent page)
- Tab page template (the human's card dashboard)
- Tests

## Sprint 3: Order Integration + Smart Suggestions

- Modify 403 response in router.py to include `card_suggestion: true` when appropriate
- Card suggestion logic: after 3+ per-action approvals for same service, suggest card on consent page
- Budget tracking at order time (check + decrement)
- Budget period reset logic
- Integration tests (full flow: request → approve → token → order → budget check)

## Sprint 4: Polish + Docs

- Edge cases: expired cards, budget exceeded mid-order, concurrent token limits per card
- Update AGENT_CONTEXT.md, development-plan.md, decisions.md
- Update strategic briefing with implementation notes
