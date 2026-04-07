-- Migration 0002: Add tables for Passport V2 consent flow.
-- cafe_users: human accounts (Cafe account holders who authorize agents).
-- consents: consent requests initiated by agents, approved by humans.
-- active_tokens: tracks active Tier-2 tokens per policy for concurrent cap enforcement.

CREATE TABLE IF NOT EXISTS cafe_users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT,
    password_hash TEXT NOT NULL DEFAULT '',
    passkey_credential_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cafe_users_email ON cafe_users(email);

CREATE TABLE IF NOT EXISTS consents (
    id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL,
    action_ids TEXT NOT NULL,
    requested_scopes TEXT NOT NULL,
    requested_constraints_json TEXT,
    task_summary TEXT,
    callback_url TEXT,
    cafe_user_id TEXT,
    policy_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_consents_status ON consents(status);
CREATE INDEX IF NOT EXISTS idx_consents_cafe_user ON consents(cafe_user_id);

CREATE TABLE IF NOT EXISTS active_tokens (
    jti TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL REFERENCES policies(id),
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_active_tokens_policy ON active_tokens(policy_id);
