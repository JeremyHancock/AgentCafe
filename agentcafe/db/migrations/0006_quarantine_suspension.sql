-- Migration 0006: Add quarantine and suspension columns to proxy_configs (ADR-025).
-- quarantine_until: ISO timestamp; actions under quarantine force Tier-2 consent for all risk tiers.
-- suspended_at: ISO timestamp; suspended services return 503 on all orders.
ALTER TABLE proxy_configs ADD COLUMN quarantine_until TEXT;
ALTER TABLE proxy_configs ADD COLUMN suspended_at TEXT;
