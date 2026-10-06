BEGIN;

-- Human no-code authoring produces the same canonical durable versions as
-- migration/agent governance. These columns are audit/lineage metadata only;
-- SemanticModel + PresentationIR + InteractionIR remain the content authority.
ALTER TABLE versions
    ADD COLUMN IF NOT EXISTS parent_version_id text,
    ADD COLUMN IF NOT EXISTS created_by text,
    ADD COLUMN IF NOT EXISTS message text;

ALTER TABLE versions
    ADD CONSTRAINT versions_parent_version_fk
    FOREIGN KEY (tenant_id, parent_version_id)
    REFERENCES versions(tenant_id, id)
    ON DELETE RESTRICT
    NOT VALID;

ALTER TABLE versions
    ADD CONSTRAINT versions_message_length
    CHECK (message IS NULL OR length(message) <= 500);

-- Durable idempotency ledger for authoring requests. It stores no semantic
-- payload and therefore cannot become a parallel model; request_sha256 binds
-- the opaque idempotency key to the exact typed operation batch.
CREATE TABLE authoring_mutations (
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 255),
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    base_version integer NOT NULL CHECK (base_version > 0),
    base_checksum text NOT NULL CHECK (base_checksum ~ '^[0-9a-f]{64}$'),
    result_version_id text NOT NULL,
    created_by text NOT NULL CHECK (length(created_by) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, project_id, idempotency_key),
    FOREIGN KEY (tenant_id, project_id) REFERENCES projects(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, result_version_id) REFERENCES versions(tenant_id, id) ON DELETE RESTRICT
);

ALTER TABLE authoring_mutations ENABLE ROW LEVEL SECURITY;
ALTER TABLE authoring_mutations FORCE ROW LEVEL SECURITY;
CREATE POLICY authoring_mutations_tenant_policy ON authoring_mutations
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

INSERT INTO schema_migrations(version) VALUES (10)
    ON CONFLICT (version) DO NOTHING;

COMMIT;
