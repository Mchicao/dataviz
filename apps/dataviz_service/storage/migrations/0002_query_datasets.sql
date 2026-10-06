BEGIN;

ALTER TABLE versions
    ADD COLUMN IF NOT EXISTS datasets jsonb NOT NULL DEFAULT '{}'::jsonb;

INSERT INTO schema_migrations(version) VALUES (2)
    ON CONFLICT (version) DO NOTHING;

COMMIT;
