BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version integer PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tenants (
    id text PRIMARY KEY CHECK (id ~ '^[A-Za-z0-9_-]{1,128}$'),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    quotas jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE projects (
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    id text NOT NULL,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    description text CHECK (length(description) <= 500),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE versions (
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    id text NOT NULL,
    version_number integer NOT NULL CHECK (version_number > 0),
    checksum text NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    is_active boolean NOT NULL DEFAULT true,
    render_plan jsonb,
    runtime_results jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, project_id, version_number),
    FOREIGN KEY (tenant_id, project_id) REFERENCES projects(tenant_id, id) ON DELETE CASCADE
);

CREATE TABLE jobs (
    tenant_id text NOT NULL,
    id text NOT NULL,
    project_id text NOT NULL,
    version_id text NOT NULL,
    job_type text NOT NULL,
    input jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('PENDING','RUNNING','COMPLETED','FAILED','CANCELLED')),
    idempotency_key text NOT NULL,
    lease_token text,
    lease_expires_at timestamptz,
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    checkpoint jsonb,
    result jsonb,
    error_code text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, project_id) REFERENCES projects(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, version_id) REFERENCES versions(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX jobs_claim_idx ON jobs (created_at)
    WHERE status = 'PENDING';

CREATE TABLE role_assignments (
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    principal_id text NOT NULL,
    scope text NOT NULL CHECK (scope IN ('organization','project','version')),
    resource_id text NOT NULL,
    role text NOT NULL CHECK (role IN ('viewer','editor','owner')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, principal_id, scope, resource_id)
);

CREATE TABLE api_quotas (
    tenant_id text PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    requests_per_minute integer NOT NULL DEFAULT 120 CHECK (requests_per_minute >= 0),
    requests_per_day integer NOT NULL DEFAULT 10000 CHECK (requests_per_day >= 0),
    concurrent_jobs integer NOT NULL DEFAULT 3 CHECK (concurrent_jobs >= 0)
);

CREATE TABLE api_usage (
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    bucket_kind text NOT NULL CHECK (bucket_kind IN ('minute','day')),
    bucket_start timestamptz NOT NULL,
    request_count integer NOT NULL CHECK (request_count >= 0),
    PRIMARY KEY (tenant_id, bucket_kind, bucket_start)
);

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE role_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_quotas ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
ALTER TABLE projects FORCE ROW LEVEL SECURITY;
ALTER TABLE versions FORCE ROW LEVEL SECURITY;
ALTER TABLE jobs FORCE ROW LEVEL SECURITY;
ALTER TABLE role_assignments FORCE ROW LEVEL SECURITY;
ALTER TABLE api_quotas FORCE ROW LEVEL SECURITY;
ALTER TABLE api_usage FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON tenants
    USING (id = current_setting('app.tenant_id', true))
    WITH CHECK (id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON projects
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON versions
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON jobs
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON role_assignments
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON api_quotas
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON api_usage
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

INSERT INTO schema_migrations(version) VALUES (1);
COMMIT;
