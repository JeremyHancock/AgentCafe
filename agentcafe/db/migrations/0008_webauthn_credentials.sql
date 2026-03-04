-- Migration 0008: Add WebAuthn passkey tables.
-- webauthn_credentials: stores registered passkey credentials per user (supports multiple per user).
-- webauthn_challenges: short-lived challenge storage for registration/authentication ceremonies.

CREATE TABLE IF NOT EXISTS webauthn_credentials (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES cafe_users(id),
    credential_id TEXT NOT NULL UNIQUE,
    public_key TEXT NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    device_name TEXT,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_webauthn_credentials_user ON webauthn_credentials(user_id);
CREATE INDEX IF NOT EXISTS idx_webauthn_credentials_cred ON webauthn_credentials(credential_id);

CREATE TABLE IF NOT EXISTS webauthn_challenges (
    id TEXT PRIMARY KEY,
    challenge TEXT NOT NULL,
    user_id TEXT,
    email TEXT,
    display_name TEXT,
    type TEXT NOT NULL CHECK(type IN ('register', 'login')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_webauthn_challenges_expires ON webauthn_challenges(expires_at);
