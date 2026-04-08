-- MCP OAuth 2.0 support (backlog 1.18)
-- Stores OAuth clients, authorization codes, and tokens for MCP SDK compatibility.

CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    client_secret_hash TEXT,
    redirect_uris TEXT NOT NULL,          -- JSON array
    client_name TEXT,
    scopes TEXT NOT NULL DEFAULT '',      -- space-separated
    token_endpoint_auth_method TEXT NOT NULL DEFAULT 'client_secret_post',
    grant_types TEXT NOT NULL DEFAULT '["authorization_code","refresh_token"]',
    response_types TEXT NOT NULL DEFAULT '["code"]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS oauth_auth_codes (
    code TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    scopes TEXT NOT NULL DEFAULT '',
    code_challenge TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    redirect_uri_provided_explicitly INTEGER NOT NULL DEFAULT 1,
    resource TEXT,
    expires_at REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS oauth_access_tokens (
    token TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    scopes TEXT NOT NULL DEFAULT '',
    resource TEXT,
    expires_at REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
    token TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    scopes TEXT NOT NULL DEFAULT '',
    expires_at REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
