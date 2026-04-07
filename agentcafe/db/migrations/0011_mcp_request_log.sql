-- Migration 0011: MCP request log — analytics for MCP tool usage.
-- Separate from the tamper-evident audit_log (which tracks orders only).
-- Captures all 4 MCP tool calls: search, get_details, request_card, invoke.

CREATE TABLE IF NOT EXISTS mcp_request_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    tool_name TEXT NOT NULL,              -- cafe.search, cafe.get_details, cafe.request_card, cafe.invoke
    -- Request context
    query TEXT,                           -- search query (cafe.search only)
    service_id TEXT,                      -- target service (get_details, request_card, invoke)
    action_id TEXT,                       -- target action (invoke, get_details)
    category TEXT,                        -- category filter (cafe.search only)
    -- Response summary
    result_count INTEGER,                 -- number of results returned (search)
    outcome TEXT NOT NULL DEFAULT 'ok',   -- ok, error, auth_required
    error_code TEXT,                      -- error code if outcome != ok
    -- Passport info (hashed, not raw)
    passport_hash TEXT,                   -- SHA-256 prefix of passport (invoke, request_card)
    -- Timing
    latency_ms INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_mcp_log_timestamp ON mcp_request_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_mcp_log_tool ON mcp_request_log(tool_name);
CREATE INDEX IF NOT EXISTS idx_mcp_log_service ON mcp_request_log(service_id);
CREATE INDEX IF NOT EXISTS idx_mcp_log_outcome ON mcp_request_log(outcome);
