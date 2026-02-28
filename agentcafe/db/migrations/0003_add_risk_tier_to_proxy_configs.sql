-- Migration 0003: Add risk_tier column to proxy_configs.
-- Drives token lifetime ceilings per v2-spec.md §6.2.
-- Risk tiers are set per-action during onboarding (seed data for demos).

ALTER TABLE proxy_configs ADD COLUMN risk_tier TEXT NOT NULL DEFAULT 'medium';
