-- Migration 0013: Add correlation_id to revocation_deliveries.
-- Required by Service Contract §B.3 for delivery tracking and idempotency.

ALTER TABLE revocation_deliveries ADD COLUMN correlation_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_rd_correlation ON revocation_deliveries(correlation_id);
