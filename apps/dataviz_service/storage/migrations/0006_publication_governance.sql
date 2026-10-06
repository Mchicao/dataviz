BEGIN;

-- Gobernanza de publicación durable: propuestas, candidatos, aprobaciones e
-- historial en tablas tenant-scoped con RLS forzada (mismo patrón que 0001).

-- Propuestas agénticas: identidad (tenant, proposal_id) y decisión única vía
-- CAS sobre status. proposal_id sigue el contrato de Proposal (uuid4 hex, 32).
CREATE TABLE agent_proposals (
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    proposal_id text NOT NULL CHECK (proposal_id ~ '^[0-9a-f]{32}$'),
    doc_id text NOT NULL CHECK (doc_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    base_version integer NOT NULL CHECK (base_version > 0),
    base_checksum text CHECK (base_checksum IS NULL OR base_checksum ~ '^[0-9a-f]{64}$'),
    head_checksum text CHECK (head_checksum IS NULL OR head_checksum ~ '^[0-9a-f]{64}$'),
    proposal_payload jsonb NOT NULL,
    proposal_sha256 text NOT NULL CHECK (proposal_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    author text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    decided_by text,
    decided_at timestamptz,
    PRIMARY KEY (tenant_id, proposal_id)
);
CREATE INDEX agent_proposals_doc_status_idx
    ON agent_proposals (tenant_id, doc_id, status);

-- Candidatos de publicación inmutables (espejo de PublicationCandidate).
CREATE TABLE publication_candidates (
    id text NOT NULL CHECK (id ~ '^[A-Za-z0-9_-]{1,128}$'),
    tenant_id text NOT NULL,
    organization_id text NOT NULL CHECK (organization_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    project_id text NOT NULL CHECK (project_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    target_version integer NOT NULL CHECK (target_version > 0),
    expected_published_version integer CHECK (
        expected_published_version IS NULL OR expected_published_version > 0
    ),
    kind text NOT NULL CHECK (kind IN ('publish','rollback')),
    target_checksum text NOT NULL CHECK (target_checksum ~ '^[0-9a-f]{64}$'),
    diff_sha256 text NOT NULL CHECK (diff_sha256 ~ '^[0-9a-f]{64}$'),
    requested_by text NOT NULL CHECK (length(requested_by) BETWEEN 1 AND 200),
    requested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES projects(tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX publication_candidates_project_idx
    ON publication_candidates (tenant_id, project_id, requested_at);

-- Aprobación humana ligada a un candidato exacto; una por candidato.
CREATE TABLE publication_approvals (
    id text NOT NULL CHECK (id ~ '^[A-Za-z0-9_-]{1,128}$'),
    candidate_id text NOT NULL CHECK (candidate_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    tenant_id text NOT NULL,
    organization_id text NOT NULL,
    project_id text NOT NULL,
    target_version integer NOT NULL CHECK (target_version > 0),
    kind text NOT NULL CHECK (kind IN ('publish','rollback')),
    target_checksum text NOT NULL CHECK (target_checksum ~ '^[0-9a-f]{64}$'),
    diff_sha256 text NOT NULL CHECK (diff_sha256 ~ '^[0-9a-f]{64}$'),
    approved_by text NOT NULL CHECK (length(approved_by) BETWEEN 1 AND 200),
    approved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, candidate_id),
    FOREIGN KEY (tenant_id, candidate_id)
        REFERENCES publication_candidates(tenant_id, id) ON DELETE CASCADE
);

-- Historial de publicaciones inmutable: la aprobación se consume una sola vez
-- (UNIQUE tenant_id, approval_id) y el registro es append-only por auditoría.
CREATE TABLE publication_records (
    id text NOT NULL CHECK (id ~ '^[A-Za-z0-9_-]{1,128}$'),
    candidate_id text NOT NULL,
    approval_id text NOT NULL CHECK (approval_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    tenant_id text NOT NULL,
    organization_id text NOT NULL,
    project_id text NOT NULL,
    previous_version integer CHECK (previous_version IS NULL OR previous_version > 0),
    target_version integer NOT NULL CHECK (target_version > 0),
    kind text NOT NULL CHECK (kind IN ('publish','rollback')),
    target_checksum text NOT NULL CHECK (target_checksum ~ '^[0-9a-f]{64}$'),
    diff_sha256 text NOT NULL CHECK (diff_sha256 ~ '^[0-9a-f]{64}$'),
    approved_by text NOT NULL CHECK (length(approved_by) BETWEEN 1 AND 200),
    approved_at timestamptz NOT NULL,
    published_by text NOT NULL CHECK (length(published_by) BETWEEN 1 AND 200),
    published_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, approval_id),
    FOREIGN KEY (tenant_id, candidate_id) REFERENCES publication_candidates(tenant_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, approval_id) REFERENCES publication_approvals(tenant_id, id)
        ON DELETE RESTRICT
);
CREATE INDEX publication_records_project_idx
    ON publication_records (tenant_id, project_id, published_at);

-- Puntero durable de publicación actual sobre la tabla projects existente;
-- ambos campos se fijan juntos y apuntan al registro de auditoría real.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS published_version integer
    CHECK (published_version IS NULL OR published_version > 0);
ALTER TABLE projects ADD COLUMN IF NOT EXISTS published_record_id text
    CHECK (published_record_id ~ '^[A-Za-z0-9_-]{1,128}$');
ALTER TABLE projects ADD CONSTRAINT projects_published_pointer_together
    CHECK ((published_version IS NULL) = (published_record_id IS NULL));
ALTER TABLE projects ADD CONSTRAINT projects_published_record_fk
    FOREIGN KEY (tenant_id, published_record_id) REFERENCES publication_records(tenant_id, id);

-- Aislamiento por tenant: RLS forzada en todas las tablas nuevas.
ALTER TABLE agent_proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE publication_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE publication_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE publication_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_proposals FORCE ROW LEVEL SECURITY;
ALTER TABLE publication_candidates FORCE ROW LEVEL SECURITY;
ALTER TABLE publication_approvals FORCE ROW LEVEL SECURITY;
ALTER TABLE publication_records FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON agent_proposals
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON publication_candidates
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON publication_approvals
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON publication_records
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- El historial publicable es append-only por contrato de auditoría.
CREATE FUNCTION block_publication_record_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'publication_records is append-only';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER publication_records_append_only
    BEFORE UPDATE OR DELETE ON publication_records
    FOR EACH ROW EXECUTE FUNCTION block_publication_record_mutation();

INSERT INTO schema_migrations(version) VALUES (6)
    ON CONFLICT (version) DO NOTHING;

COMMIT;