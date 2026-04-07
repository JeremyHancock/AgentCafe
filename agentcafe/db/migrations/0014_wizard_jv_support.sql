-- Migration 0014: Wizard JV support.
-- Adds integration_mode and integration_config_json columns to draft_services
-- for jointly-verified service onboarding, and creates the
-- service_integration_configs table for published JV service settings.

ALTER TABLE draft_services ADD COLUMN integration_mode TEXT DEFAULT NULL;
ALTER TABLE draft_services ADD COLUMN integration_config_json TEXT DEFAULT NULL;

CREATE TABLE IF NOT EXISTS service_integration_configs (
    service_id TEXT PRIMARY KEY REFERENCES published_services(service_id),
    integration_base_url TEXT NOT NULL DEFAULT '',
    integration_auth_header TEXT NOT NULL DEFAULT '',
    identity_matching TEXT NOT NULL DEFAULT 'opaque_id',
    has_direct_signup INTEGER NOT NULL DEFAULT 0,
    cap_account_check INTEGER NOT NULL DEFAULT 0,
    cap_account_create INTEGER NOT NULL DEFAULT 0,
    cap_link_complete INTEGER NOT NULL DEFAULT 0,
    cap_unlink INTEGER NOT NULL DEFAULT 0,
    cap_revoke INTEGER NOT NULL DEFAULT 1,
    cap_grant_status INTEGER NOT NULL DEFAULT 0,
    path_revoke TEXT,
    configured_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
