-- Migration 0012: Service Integration Standard — jointly-verified mode.
-- Adds infrastructure for per-request authorization artifacts, identity binding,
-- and authorization grant tracking. Standard-mode services are unaffected.

-- New column on proxy_configs: NULL or 'standard' = current behavior,
-- 'jointly_verified' = extended proxy path with artifact signing + identity binding.
ALTER TABLE proxy_configs ADD COLUMN integration_mode TEXT DEFAULT NULL;

-- Identity binding between AC humans and service-side accounts.
-- One row per (human, service) pair. Outlives individual grants.
CREATE TABLE IF NOT EXISTS human_service_accounts (
    id TEXT PRIMARY KEY,
    ac_human_id TEXT NOT NULL REFERENCES cafe_users(id),
    service_id TEXT NOT NULL,
    service_account_id TEXT,
    binding_method TEXT NOT NULL,
    binding_status TEXT NOT NULL DEFAULT 'active',
    identity_binding TEXT NOT NULL,
    linked_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(ac_human_id, service_id)
);

CREATE INDEX IF NOT EXISTS idx_hsa_human ON human_service_accounts(ac_human_id);
CREATE INDEX IF NOT EXISTS idx_hsa_service ON human_service_accounts(service_id);
CREATE INDEX IF NOT EXISTS idx_hsa_status ON human_service_accounts(binding_status);

-- Per-consent authorization tracking. One row per (consent_ref, service) pair.
-- consent_ref is policy_id (single-action consent) or card_id (Company Card).
CREATE TABLE IF NOT EXISTS authorization_grants (
    id TEXT PRIMARY KEY,
    ac_human_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    consent_ref TEXT NOT NULL,
    grant_status TEXT NOT NULL DEFAULT 'active',
    granted_at TEXT NOT NULL,
    revoked_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(consent_ref, service_id)
);

CREATE INDEX IF NOT EXISTS idx_ag_human_service ON authorization_grants(ac_human_id, service_id);
CREATE INDEX IF NOT EXISTS idx_ag_status ON authorization_grants(grant_status);
CREATE INDEX IF NOT EXISTS idx_ag_consent_ref ON authorization_grants(consent_ref);

-- Tracks revocation push delivery to services. Used by PR 2 (revocation delivery),
-- but table created now so migration numbering stays clean.
CREATE TABLE IF NOT EXISTS revocation_deliveries (
    id TEXT PRIMARY KEY,
    consent_ref TEXT NOT NULL,
    service_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    delivered_at TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rd_status ON revocation_deliveries(status);
