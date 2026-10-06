BEGIN;

-- La versión durable de publicación contiene exactamente los tres IR canónicos.
-- RenderPlan y runtime_results continúan siendo derivados y no participan como
-- autoridad de publicación.
ALTER TABLE versions
    ADD COLUMN IF NOT EXISTS presentation_ir jsonb,
    ADD COLUMN IF NOT EXISTS interaction_ir jsonb;

ALTER TABLE versions
    ADD CONSTRAINT versions_presentation_ir_object
    CHECK (presentation_ir IS NULL OR jsonb_typeof(presentation_ir) = 'object');
ALTER TABLE versions
    ADD CONSTRAINT versions_interaction_ir_object
    CHECK (interaction_ir IS NULL OR jsonb_typeof(interaction_ir) = 'object');

-- Los candidatos nuevos sólo pueden apuntar a una versión real del mismo
-- tenant/proyecto. NOT VALID preserva filas históricas sintéticas, pero el FK
-- se aplica inmediatamente a inserts/updates futuros.
ALTER TABLE publication_candidates
    ADD CONSTRAINT publication_candidates_target_version_fk
    FOREIGN KEY (tenant_id, project_id, target_version)
    REFERENCES versions(tenant_id, project_id, version_number)
    ON DELETE RESTRICT
    NOT VALID;

INSERT INTO schema_migrations(version) VALUES (7)
    ON CONFLICT (version) DO NOTHING;

COMMIT;
