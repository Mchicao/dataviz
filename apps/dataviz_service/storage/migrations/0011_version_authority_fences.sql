BEGIN;

-- P0 authority fences for canonical versions (LUNA2 remediation).
--
-- The runtime/worker role is a restricted LOGIN (NOSUPERUSER NOBYPASSRLS);
-- migrations and administrative repair run through a separate admin DSN.
-- The database, not only the Python layer, must guarantee:
--   * version identity is immutable after insert,
--   * the canonical three-IR content and its checksum are write-once once
--     born, while derived state (render_plan, runtime_results, datasets,
--     credential_scan, is_active) keeps supporting the materialization
--     lifecycle,
--   * jobs and the authoring ledger cannot mix versions across projects.
--
-- The trigger fires for every role including the table owner, so a runtime
-- SQL writer cannot rewrite canonical history even before RLS is considered.

CREATE FUNCTION guard_versions_write_once() RETURNS trigger AS $$
BEGIN
    -- Identity of a durable version never changes after insert.
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.version_number IS DISTINCT FROM OLD.version_number
       OR NEW.parent_version_id IS DISTINCT FROM OLD.parent_version_id
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.message IS DISTINCT FROM OLD.message
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'versions identity columns are immutable after insert';
    END IF;

    -- The publication triple is all-or-none: writing presentation_ir or
    -- interaction_ir requires the complete canonical triple. A semantic
    -- model alone remains a valid legacy analytics state (0004-era flow),
    -- so only checked when an IR column actually changes; derived-only
    -- updates of legacy partial rows keep passing.
    IF NEW.semantic_model IS DISTINCT FROM OLD.semantic_model
       OR NEW.presentation_ir IS DISTINCT FROM OLD.presentation_ir
       OR NEW.interaction_ir IS DISTINCT FROM OLD.interaction_ir THEN
        IF (NEW.presentation_ir IS NOT NULL OR NEW.interaction_ir IS NOT NULL)
           AND (NEW.semantic_model IS NULL
                OR NEW.presentation_ir IS NULL
                OR NEW.interaction_ir IS NULL) THEN
            RAISE EXCEPTION 'canonical version IRs must be written all-or-none';
        END IF;
    END IF;

    IF OLD.semantic_model IS NOT NULL
       OR OLD.presentation_ir IS NOT NULL
       OR OLD.interaction_ir IS NOT NULL THEN
        -- Canonical content already exists: content and checksum are frozen.
        IF NEW.semantic_model IS DISTINCT FROM OLD.semantic_model
           OR NEW.presentation_ir IS DISTINCT FROM OLD.presentation_ir
           OR NEW.interaction_ir IS DISTINCT FROM OLD.interaction_ir
           OR NEW.checksum IS DISTINCT FROM OLD.checksum THEN
            RAISE EXCEPTION 'canonical three-IR version content is write-once';
        END IF;
    ELSE
        -- Not canonical yet: the checksum may only move together with the
        -- canonical birth (all three IRs arriving in the same UPDATE).
        IF NEW.checksum IS DISTINCT FROM OLD.checksum
           AND NEW.semantic_model IS NULL THEN
            RAISE EXCEPTION
                'version checksum cannot change before canonical materialization';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER versions_write_once_fence
    BEFORE UPDATE ON versions
    FOR EACH ROW EXECUTE FUNCTION guard_versions_write_once();

-- Jobs previously had independent FKs for (tenant, project) and
-- (tenant, version), allowing crossed combinations inside one tenant. The
-- composite FK closes that gap. The explicit UNIQUE key is implied by the
-- existing PRIMARY KEY (tenant_id, id) but PostgreSQL requires it to exist
-- as a constraint before it can be referenced. NOT VALID preserves
-- historical rows; the explicit VALIDATE attempt below keeps it NOT VALID
-- only when legacy rows actually violate it. New writes are enforced
-- immediately either way.
ALTER TABLE versions
    ADD CONSTRAINT versions_tenant_project_id_unique
    UNIQUE (tenant_id, project_id, id);

ALTER TABLE jobs
    ADD CONSTRAINT jobs_version_in_project_fk
    FOREIGN KEY (tenant_id, project_id, version_id)
    REFERENCES versions(tenant_id, project_id, id)
    ON DELETE CASCADE
    NOT VALID;

DO $validate$
BEGIN
    ALTER TABLE jobs VALIDATE CONSTRAINT jobs_version_in_project_fk;
EXCEPTION
    WHEN foreign_key_violation THEN
        RAISE NOTICE 'jobs_version_in_project_fk kept NOT VALID: %', SQLERRM;
END
$validate$;

-- Same project scoping for the authoring idempotency ledger: its result
-- version must belong to the ledger row's project.
ALTER TABLE authoring_mutations
    ADD CONSTRAINT authoring_mutations_result_in_project_fk
    FOREIGN KEY (tenant_id, project_id, result_version_id)
    REFERENCES versions(tenant_id, project_id, id)
    ON DELETE RESTRICT
    NOT VALID;

DO $validate$
BEGIN
    ALTER TABLE authoring_mutations
        VALIDATE CONSTRAINT authoring_mutations_result_in_project_fk;
EXCEPTION
    WHEN foreign_key_violation THEN
        RAISE NOTICE 'authoring_mutations_result_in_project_fk kept NOT VALID: %', SQLERRM;
END
$validate$;

INSERT INTO schema_migrations(version) VALUES (11)
    ON CONFLICT (version) DO NOTHING;

COMMIT;
