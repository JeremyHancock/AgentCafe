-- Migration 0005: Add hash chain columns to audit_log for tamper detection.
-- prev_hash: hash of the previous entry (NULL for genesis)
-- entry_hash: SHA-256 hash of this entry's contents + prev_hash
ALTER TABLE audit_log ADD COLUMN prev_hash TEXT;
ALTER TABLE audit_log ADD COLUMN entry_hash TEXT;
