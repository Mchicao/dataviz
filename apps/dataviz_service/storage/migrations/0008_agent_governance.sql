BEGIN;

ALTER TABLE agent_proposals
    ADD COLUMN IF NOT EXISTS project_id text,
    ADD COLUMN IF NOT EXISTS proposal_type text,
    ADD COLUMN IF NOT EXISTS draft_version_id text;

ALTER TABLE agent_proposals
    ADD CONSTRAINT agent_proposals_proposal_type_check
    CHECK (proposal_type IS NULL OR proposal_type IN ('authoring'));

CREATE UNIQUE INDEX IF NOT EXISTS agent_proposals_draft_version_unique
    ON agent_proposals (tenant_id, draft_version_id)
    WHERE draft_version_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS permission_assignments (
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    principal_id text NOT NULL,
    scope text NOT NULL CHECK (scope IN ('organization','project','version')),
    resource_id text NOT NULL,
    permission text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, principal_id, scope, resource_id, permission)
);

ALTER TABLE permission_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE permission_assignments FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON permission_assignments
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

INSERT INTO schema_migrations(version) VALUES (8);
COMMIT;
