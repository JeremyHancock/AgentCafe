"""SQLite table definitions for AgentCafe."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    website TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS published_services (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id),
    service_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    menu_entry_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'live',
    published_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proxy_configs (
    id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL REFERENCES published_services(service_id),
    action_id TEXT NOT NULL,
    backend_url TEXT NOT NULL,
    backend_path TEXT NOT NULL,
    backend_method TEXT NOT NULL,
    backend_auth_header TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL,
    human_auth_required INTEGER NOT NULL DEFAULT 0,
    rate_limit TEXT NOT NULL DEFAULT '60/minute',
    created_at TEXT NOT NULL,
    UNIQUE(service_id, action_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    service_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    passport_hash TEXT NOT NULL,
    inputs_hash TEXT NOT NULL,
    outcome TEXT NOT NULL,
    response_code INTEGER,
    latency_ms INTEGER
);

CREATE TABLE IF NOT EXISTS revoked_jtis (
    jti TEXT PRIMARY KEY,
    revoked_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_log_service_action ON audit_log(service_id, action_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_passport ON audit_log(passport_hash);
"""
