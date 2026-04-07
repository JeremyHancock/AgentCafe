-- Migration 0004: Add human_identifier_field to proxy_configs.
-- Drives identity verification per v2-spec.md §7.
-- NULL means no identity check required for this action.

ALTER TABLE proxy_configs ADD COLUMN human_identifier_field TEXT;
