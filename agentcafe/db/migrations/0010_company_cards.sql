-- Migration 0010: Company Cards — standing policies for service-level relationships.
-- A card lets a human pre-authorize a class of actions with a service,
-- subject to constraints (budget, duration, scope, excluded actions).
-- See docs/strategy/strategic-review-briefing.md §8.1 and ADR-028.

CREATE TABLE IF NOT EXISTS company_cards (
    id TEXT PRIMARY KEY,
    cafe_user_id TEXT,                   -- NULL for pending cards, set on approval
    service_id TEXT NOT NULL,
    allowed_action_ids TEXT,          -- CSV of allowed actions, NULL = all non-excluded
    excluded_action_ids TEXT,         -- CSV of excluded actions (always require per-action consent)
    max_risk_tier_covered TEXT NOT NULL DEFAULT 'medium',  -- low, medium; high/critical punch through
    budget_limit_cents INTEGER,       -- per-period budget cap, NULL = no cap
    budget_period TEXT,               -- 'daily', 'weekly', 'monthly', NULL = no budget
    budget_spent_cents INTEGER NOT NULL DEFAULT 0,
    budget_period_start TEXT,         -- ISO timestamp for current budget period start
    first_use_confirmation INTEGER NOT NULL DEFAULT 1,     -- 1 = ON (default), 0 = OFF
    first_use_confirmed_at TEXT,      -- NULL until first real action confirmed
    policy_id TEXT,                   -- FK to policies(id), created on approval
    activation_code TEXT UNIQUE,      -- 8-char alphanumeric, same pattern as consents
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, active, revoked, expired
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_company_cards_user ON company_cards(cafe_user_id);
CREATE INDEX IF NOT EXISTS idx_company_cards_service ON company_cards(service_id);
CREATE INDEX IF NOT EXISTS idx_company_cards_status ON company_cards(status);
CREATE INDEX IF NOT EXISTS idx_company_cards_activation ON company_cards(activation_code);
