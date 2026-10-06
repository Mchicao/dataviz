BEGIN;

-- Durable Knowledge Plane. These tables store governance/provenance references
-- to canonical versions; semantic formulas and presentation/interaction content
-- remain exclusively in versions.semantic_model/presentation_ir/interaction_ir.

CREATE TABLE knowledge_assets (
    schema_version text NOT NULL,
    asset_id text NOT NULL CHECK (
        asset_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    ),
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    asset_kind text NOT NULL CHECK (asset_kind IN (
        'source_artifact', 'connection', 'dataset', 'semantic_model',
        'dashboard', 'report_page', 'visual', 'metric', 'column', 'table', 'insight'
    )),
    natural_key text NOT NULL,
    display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 512),
    description text CHECK (description IS NULL OR length(description) <= 4000),
    content_fingerprint text NOT NULL CHECK (content_fingerprint ~ '^[0-9a-f]{64}$'),
    origin_kind text NOT NULL CHECK (origin_kind IN ('tableau', 'power_bi', 'unknown')),
    canonical_version_id text,
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    state text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'deprecated', 'deleted')),
    created_by text NOT NULL CHECK (length(btrim(created_by)) BETWEEN 1 AND 200),
    PRIMARY KEY (tenant_id, asset_id),
    UNIQUE (tenant_id, asset_kind, natural_key),
    FOREIGN KEY (tenant_id, canonical_version_id)
        REFERENCES versions(tenant_id, id) ON DELETE RESTRICT
);

CREATE TABLE knowledge_source_bindings (
    schema_version text NOT NULL,
    binding_id text NOT NULL CHECK (
        binding_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    ),
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    dataset_asset_id text NOT NULL,
    connection_asset_id text NOT NULL,
    connector_id text NOT NULL CHECK (connector_id ~ '^[a-z][a-z0-9_]{0,63}$'),
    capability_snapshot jsonb NOT NULL CHECK (jsonb_typeof(capability_snapshot) = 'object'),
    credential_ref text CHECK (
        credential_ref IS NULL OR credential_ref ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$'
    ),
    mode text NOT NULL CHECK (mode IN ('live', 'extract')),
    freshness_policy jsonb CHECK (
        freshness_policy IS NULL OR jsonb_typeof(freshness_policy) = 'object'
    ),
    created_at timestamptz NOT NULL,
    created_by text NOT NULL CHECK (length(btrim(created_by)) BETWEEN 1 AND 200),
    PRIMARY KEY (tenant_id, binding_id),
    UNIQUE (tenant_id, dataset_asset_id, connection_asset_id),
    FOREIGN KEY (tenant_id, dataset_asset_id)
        REFERENCES knowledge_assets(tenant_id, asset_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, connection_asset_id)
        REFERENCES knowledge_assets(tenant_id, asset_id) ON DELETE RESTRICT,
    CHECK (
        (mode = 'live' AND freshness_policy IS NULL)
        OR (mode = 'extract' AND freshness_policy IS NOT NULL)
    )
);

CREATE TABLE knowledge_lineage_edges (
    schema_version text NOT NULL,
    edge_id text NOT NULL CHECK (
        edge_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    ),
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    from_asset_id text NOT NULL,
    to_asset_id text NOT NULL,
    edge_kind text NOT NULL CHECK (edge_kind IN (
        'derived_from', 'compiled_from', 'binds_to', 'references',
        'migrated_from', 'origin_of', 'grounded_by'
    )),
    observed_at timestamptz NOT NULL,
    evidence_ref text NOT NULL CHECK (evidence_ref ~ '^(ir:)?[0-9a-f]{64}$'),
    supersedes_edge_id text CHECK (
        supersedes_edge_id IS NULL OR supersedes_edge_id ~
            '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    ),
    PRIMARY KEY (tenant_id, edge_id),
    UNIQUE NULLS NOT DISTINCT (
        tenant_id, edge_kind, from_asset_id, to_asset_id, supersedes_edge_id
    ),
    FOREIGN KEY (tenant_id, from_asset_id)
        REFERENCES knowledge_assets(tenant_id, asset_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, to_asset_id)
        REFERENCES knowledge_assets(tenant_id, asset_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, supersedes_edge_id)
        REFERENCES knowledge_lineage_edges(tenant_id, edge_id) ON DELETE RESTRICT,
    CHECK (from_asset_id <> to_asset_id),
    CHECK (supersedes_edge_id IS NULL OR supersedes_edge_id <> edge_id)
);

CREATE TABLE knowledge_semantic_entities (
    schema_version text NOT NULL,
    entity_id text NOT NULL,
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    canonical_version_id text NOT NULL,
    ir_version text NOT NULL CHECK (ir_version ~ '^[0-9a-f]{64}$'),
    node_path text NOT NULL CHECK (length(node_path) BETWEEN 1 AND 512),
    entity_type text NOT NULL CHECK (entity_type IN ('table', 'column', 'relationship')),
    grain text CHECK (grain IS NULL OR length(grain) <= 200),
    created_at timestamptz NOT NULL,
    created_by text NOT NULL CHECK (length(btrim(created_by)) BETWEEN 1 AND 200),
    PRIMARY KEY (tenant_id, entity_id),
    UNIQUE (tenant_id, canonical_version_id, node_path),
    FOREIGN KEY (tenant_id, canonical_version_id)
        REFERENCES versions(tenant_id, id) ON DELETE RESTRICT
);

CREATE TABLE knowledge_metric_registry (
    schema_version text NOT NULL,
    metric_id text NOT NULL,
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    asset_id text NOT NULL,
    canonical_version_id text NOT NULL,
    ir_version text NOT NULL CHECK (ir_version ~ '^[0-9a-f]{64}$'),
    node_path text NOT NULL CHECK (length(node_path) BETWEEN 1 AND 512),
    metric_checksum text NOT NULL CHECK (metric_checksum ~ '^[0-9a-f]{64}$'),
    owner text NOT NULL CHECK (length(btrim(owner)) BETWEEN 1 AND 200),
    certification text NOT NULL DEFAULT 'none' CHECK (
        certification IN ('none', 'in_review', 'certified', 'deprecated')
    ),
    certified_by text CHECK (certified_by IS NULL OR length(btrim(certified_by)) BETWEEN 1 AND 200),
    certified_at timestamptz,
    grain text CHECK (grain IS NULL OR length(grain) <= 200),
    default_filter_context jsonb,
    created_at timestamptz NOT NULL,
    created_by text NOT NULL CHECK (length(btrim(created_by)) BETWEEN 1 AND 200),
    PRIMARY KEY (tenant_id, metric_id),
    UNIQUE (tenant_id, canonical_version_id, node_path),
    FOREIGN KEY (tenant_id, asset_id)
        REFERENCES knowledge_assets(tenant_id, asset_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, canonical_version_id)
        REFERENCES versions(tenant_id, id) ON DELETE RESTRICT,
    CHECK (
        (certification = 'certified' AND certified_by IS NOT NULL AND certified_at IS NOT NULL)
        OR (certification <> 'certified' AND certified_by IS NULL AND certified_at IS NULL)
    )
);

CREATE TABLE knowledge_business_rules (
    schema_version text NOT NULL,
    rule_id text NOT NULL,
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    canonical_version_id text NOT NULL,
    ir_version text NOT NULL CHECK (ir_version ~ '^[0-9a-f]{64}$'),
    node_path text NOT NULL CHECK (length(node_path) BETWEEN 1 AND 512),
    statement text NOT NULL CHECK (length(btrim(statement)) BETWEEN 1 AND 2000),
    references_rls boolean NOT NULL DEFAULT false,
    owner text NOT NULL CHECK (length(btrim(owner)) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL,
    created_by text NOT NULL CHECK (length(btrim(created_by)) BETWEEN 1 AND 200),
    PRIMARY KEY (tenant_id, rule_id),
    UNIQUE (tenant_id, canonical_version_id, node_path),
    FOREIGN KEY (tenant_id, canonical_version_id)
        REFERENCES versions(tenant_id, id) ON DELETE RESTRICT
);

CREATE TABLE knowledge_insights (
    schema_version text NOT NULL,
    insight_id text NOT NULL,
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    statement text NOT NULL CHECK (length(btrim(statement)) BETWEEN 1 AND 2000),
    metric_ref text NOT NULL,
    grain text NOT NULL CHECK (length(btrim(grain)) BETWEEN 1 AND 200),
    filter_context jsonb NOT NULL CHECK (jsonb_typeof(filter_context) = 'object'),
    time_range jsonb NOT NULL CHECK (jsonb_typeof(time_range) = 'object'),
    value_refs jsonb NOT NULL CHECK (jsonb_typeof(value_refs) = 'array'),
    canonical_version_id text NOT NULL,
    ir_version text NOT NULL CHECK (ir_version ~ '^[0-9a-f]{64}$'),
    query_plan_version text NOT NULL CHECK (length(btrim(query_plan_version)) BETWEEN 1 AND 128),
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'array' AND jsonb_array_length(evidence) > 0),
    certification text NOT NULL DEFAULT 'draft' CHECK (
        certification IN ('draft', 'reviewed', 'certified', 'rejected')
    ),
    certified_by text CHECK (certified_by IS NULL OR length(btrim(certified_by)) BETWEEN 1 AND 200),
    certified_at timestamptz,
    as_of timestamptz NOT NULL,
    created_at timestamptz NOT NULL,
    created_by text NOT NULL CHECK (length(btrim(created_by)) BETWEEN 1 AND 200),
    PRIMARY KEY (tenant_id, insight_id),
    FOREIGN KEY (tenant_id, metric_ref)
        REFERENCES knowledge_metric_registry(tenant_id, metric_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, canonical_version_id)
        REFERENCES versions(tenant_id, id) ON DELETE RESTRICT,
    CHECK (
        (certification = 'certified' AND certified_by IS NOT NULL AND certified_at IS NOT NULL)
        OR (certification <> 'certified' AND certified_by IS NULL AND certified_at IS NULL)
    )
);

CREATE INDEX knowledge_assets_version_idx
    ON knowledge_assets (tenant_id, canonical_version_id)
    WHERE canonical_version_id IS NOT NULL;
CREATE INDEX knowledge_source_bindings_dataset_idx
    ON knowledge_source_bindings (tenant_id, dataset_asset_id);
CREATE INDEX knowledge_source_bindings_connection_idx
    ON knowledge_source_bindings (tenant_id, connection_asset_id);
CREATE INDEX knowledge_lineage_edges_from_idx
    ON knowledge_lineage_edges (tenant_id, from_asset_id);
CREATE INDEX knowledge_lineage_edges_to_idx
    ON knowledge_lineage_edges (tenant_id, to_asset_id);
CREATE INDEX knowledge_metric_registry_asset_idx
    ON knowledge_metric_registry (tenant_id, asset_id);
CREATE INDEX knowledge_insights_metric_idx
    ON knowledge_insights (tenant_id, metric_ref, as_of DESC);

CREATE OR REPLACE FUNCTION knowledge_assets_guard_immutable()
RETURNS trigger AS $$
BEGIN
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.asset_id IS DISTINCT FROM OLD.asset_id
       OR NEW.asset_kind IS DISTINCT FROM OLD.asset_kind
       OR NEW.natural_key IS DISTINCT FROM OLD.natural_key
       OR NEW.content_fingerprint IS DISTINCT FROM OLD.content_fingerprint
       OR NEW.origin_kind IS DISTINCT FROM OLD.origin_kind
       OR NEW.canonical_version_id IS DISTINCT FROM OLD.canonical_version_id
       OR NEW.first_seen_at IS DISTINCT FROM OLD.first_seen_at
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.schema_version IS DISTINCT FROM OLD.schema_version THEN
        RAISE EXCEPTION 'knowledge_assets immutable identity or provenance fields cannot be modified';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER knowledge_assets_guard_immutable_trigger
BEFORE UPDATE ON knowledge_assets
FOR EACH ROW EXECUTE FUNCTION knowledge_assets_guard_immutable();

CREATE OR REPLACE FUNCTION knowledge_lineage_edges_prevent_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'knowledge_lineage_edges is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER knowledge_lineage_edges_no_update
BEFORE UPDATE ON knowledge_lineage_edges
FOR EACH ROW EXECUTE FUNCTION knowledge_lineage_edges_prevent_mutation();

CREATE TRIGGER knowledge_lineage_edges_no_delete
BEFORE DELETE ON knowledge_lineage_edges
FOR EACH ROW EXECUTE FUNCTION knowledge_lineage_edges_prevent_mutation();

ALTER TABLE knowledge_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_source_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_lineage_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_semantic_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_metric_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_business_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_insights ENABLE ROW LEVEL SECURITY;

ALTER TABLE knowledge_assets FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge_source_bindings FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge_lineage_edges FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge_semantic_entities FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge_metric_registry FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge_business_rules FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge_insights FORCE ROW LEVEL SECURITY;

CREATE POLICY knowledge_assets_tenant ON knowledge_assets
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY knowledge_source_bindings_tenant ON knowledge_source_bindings
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY knowledge_lineage_edges_tenant ON knowledge_lineage_edges
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY knowledge_semantic_entities_tenant ON knowledge_semantic_entities
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY knowledge_metric_registry_tenant ON knowledge_metric_registry
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY knowledge_business_rules_tenant ON knowledge_business_rules
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY knowledge_insights_tenant ON knowledge_insights
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

INSERT INTO schema_migrations(version) VALUES (9)
    ON CONFLICT (version) DO NOTHING;
COMMIT;
