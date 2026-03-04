-- Migration 0009: Add activation_code column to consents table.
-- Activation codes are 8-char alphanumeric codes for the cold-start UX flow.
-- Agents share these codes with humans who don't yet have a Cafe account.
-- The human enters the code at /activate to register + approve in one step.

ALTER TABLE consents ADD COLUMN activation_code TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_consents_activation_code
    ON consents(activation_code) WHERE activation_code IS NOT NULL;
