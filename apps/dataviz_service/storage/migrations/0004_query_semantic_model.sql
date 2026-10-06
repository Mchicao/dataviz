BEGIN;

ALTER TABLE versions ADD COLUMN IF NOT EXISTS semantic_model jsonb;

INSERT INTO schema_migrations(version) VALUES (4)
    ON CONFLICT (version) DO NOTHING;

COMMIT;
