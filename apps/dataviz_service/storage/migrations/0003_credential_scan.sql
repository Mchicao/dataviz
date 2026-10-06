BEGIN;

ALTER TABLE versions ADD COLUMN IF NOT EXISTS credential_scan jsonb;

INSERT INTO schema_migrations(version) VALUES (3);
COMMIT;
