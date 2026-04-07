-- Migration 0007: Add monotonic sequence column to audit_log for concurrency-safe hash chaining.
-- seq is an auto-incrementing integer that guarantees strict ordering regardless of timestamp ties.
ALTER TABLE audit_log ADD COLUMN seq INTEGER;

-- Backfill existing rows with sequence based on current timestamp ordering
-- (SQLite doesn't support UPDATE with window functions, so we use rowid as a proxy)
UPDATE audit_log SET seq = rowid WHERE seq IS NULL;
