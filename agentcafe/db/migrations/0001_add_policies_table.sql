-- Migration 0001: Add policies table for Passport V2 consent flow.
-- Also adds revoked_at for instant policy revocation (v2-spec.md §6.5).

CREATE TABLE IF NOT EXISTS policies (
    id TEXT PRIMARY KEY,
    cafe_user_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    allowed_action_ids TEXT NOT NULL,
    scopes TEXT NOT NULL,
    constraints_json TEXT,
    risk_tier TEXT NOT NULL DEFAULT 'medium',
    max_token_lifetime_seconds INTEGER NOT NULL DEFAULT 900,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_policies_user ON policies(cafe_user_id);
CREATE INDEX IF NOT EXISTS idx_policies_service ON policies(service_id);
