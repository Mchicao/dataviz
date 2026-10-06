BEGIN;

-- Finalize PostgreSQL authority fences introduced by 0011. This migration is
-- intentionally fail-closed: historical FK violations abort the transaction
-- instead of being downgraded to NOTICE while still marking the schema ready.

ALTER TABLE schema_migrations
    ADD COLUMN IF NOT EXISTS sha256 text;

DO $constraint$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'schema_migrations_sha256_format'
          AND conrelid = 'schema_migrations'::regclass
    ) THEN
        ALTER TABLE schema_migrations
            ADD CONSTRAINT schema_migrations_sha256_format
            CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$');
    END IF;
END
$constraint$;

-- Constraints created NOT VALID for online rollout must become real authority
-- before readiness can succeed.
ALTER TABLE jobs VALIDATE CONSTRAINT jobs_version_in_project_fk;
ALTER TABLE authoring_mutations
    VALIDATE CONSTRAINT authoring_mutations_result_in_project_fk;
ALTER TABLE versions VALIDATE CONSTRAINT versions_parent_version_fk;

-- A parent version must belong to the same project, not merely the same tenant.
ALTER TABLE versions
    ADD CONSTRAINT versions_parent_in_project_fk
    FOREIGN KEY (tenant_id, project_id, parent_version_id)
    REFERENCES versions(tenant_id, project_id, id)
    ON DELETE RESTRICT
    NOT VALID;
ALTER TABLE versions VALIDATE CONSTRAINT versions_parent_in_project_fk;

-- Tighten the 0011 UPDATE fence. New canonical content may only be born as the
-- complete Semantic + Presentation + Interaction triple. Legacy partial rows
-- remain readable/frozen, but cannot be silently completed or rewritten.
CREATE OR REPLACE FUNCTION guard_versions_write_once() RETURNS trigger AS $$
BEGIN
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

    IF NEW.semantic_model IS DISTINCT FROM OLD.semantic_model
       OR NEW.presentation_ir IS DISTINCT FROM OLD.presentation_ir
       OR NEW.interaction_ir IS DISTINCT FROM OLD.interaction_ir THEN
        IF NEW.semantic_model IS NULL
           OR NEW.presentation_ir IS NULL
           OR NEW.interaction_ir IS NULL THEN
            RAISE EXCEPTION 'canonical version IRs must be written all-or-none';
        END IF;
    END IF;

    IF OLD.semantic_model IS NOT NULL
       OR OLD.presentation_ir IS NOT NULL
       OR OLD.interaction_ir IS NOT NULL THEN
        IF NEW.semantic_model IS DISTINCT FROM OLD.semantic_model
           OR NEW.presentation_ir IS DISTINCT FROM OLD.presentation_ir
           OR NEW.interaction_ir IS DISTINCT FROM OLD.interaction_ir
           OR NEW.checksum IS DISTINCT FROM OLD.checksum THEN
            RAISE EXCEPTION 'canonical three-IR version content is write-once';
        END IF;
    ELSE
        IF NEW.checksum IS DISTINCT FROM OLD.checksum
           AND NEW.semantic_model IS NULL THEN
            RAISE EXCEPTION
                'version checksum cannot change before canonical materialization';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Direct INSERTs cannot create semantic-only or otherwise partial canonical
-- authority. Empty staging rows remain valid for the durable import job flow.
CREATE FUNCTION guard_versions_insert_canonical() RETURNS trigger AS $$
BEGIN
    IF NOT (
        (NEW.semantic_model IS NULL
         AND NEW.presentation_ir IS NULL
         AND NEW.interaction_ir IS NULL)
        OR
        (NEW.semantic_model IS NOT NULL
         AND NEW.presentation_ir IS NOT NULL
         AND NEW.interaction_ir IS NOT NULL)
    ) THEN
        RAISE EXCEPTION 'canonical version IRs must be inserted all-or-none';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER versions_insert_canonical_fence
    BEFORE INSERT ON versions
    FOR EACH ROW EXECUTE FUNCTION guard_versions_insert_canonical();

-- Canonical versions are durable history. Product flows use publication
-- pointers/status rather than deleting history; administrative repair requires
-- an explicit trigger-disable operation outside runtime credentials.
CREATE FUNCTION reject_versions_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'durable versions are append-only and cannot be deleted';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER versions_delete_fence
    BEFORE DELETE ON versions
    FOR EACH ROW EXECUTE FUNCTION reject_versions_delete();

INSERT INTO schema_migrations(version) VALUES (12)
    ON CONFLICT (version) DO NOTHING;

COMMIT;
