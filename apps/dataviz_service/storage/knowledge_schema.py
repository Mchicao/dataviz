"""SQLite schema and migration helper for the knowledge plane (Wave K1).

Implements KP-CONTRACT-001's storage deliverable: the smallest DDL for the
knowledge-plane tables (Sections 2.1-2.5 of
``PLAN_DATAVIZ_DATA_KNOWLEDGE_PLANE_2026-09-04.md``), written to be
structurally Postgres-compatible:

* ``TEXT``/``INTEGER``/``REAL`` columns map 1:1 to ``text``/``integer``/
  ``double precision``;
* JSON payloads are stored as JSON text in SQLite and ``jsonb`` in Postgres
  (column comment documents the mapping);
* ``TEXT CHECK (x IN (...))`` enforces the same closed enums the Pydantic
  contracts enforce;
* all tables are tenant-scoped and append-friendly; ``lineage_edges`` has no
  UPDATE or DELETE path — corrections are new edges with ``supersedes_edge_id``
  and asset FKs are ON DELETE RESTRICT (soft deletion preserves history), with
  a tenant-scoped self-FK keeping ``supersedes_edge_id`` inside one tenant.

The search projection (Section 2.6) is deliberately absent: it is a disposable
FTS projection owned by KP-SEARCH-001, not authoritative schema.

Migrations are forward-only and recorded in ``knowledge_schema_migrations``;
each migration's DDL and its version marker commit in one explicit
transaction, so a failure rolls both back together. The helper is schema-only:
it never executes queries against tenant data and never reads or writes
credentials.
"""

from __future__ import annotations

import sqlite3

KNOWLEDGE_SCHEMA_VERSION = 5
"""Current knowledge-plane DDL version (integer, additive per wave)."""

#: Per-table column documentation for maintainers; keys are stable table names.
TABLE_NOTES: dict[str, str] = {
    "knowledge_assets": "Section 2.1 asset registry. natural_key semantics validated in core.contracts.knowledge.",
    "knowledge_source_bindings": "Section 2.2. jsonb columns stored as JSON text in SQLite.",
    "knowledge_lineage_edges": (
        "Section 2.3, append-only. Neither UPDATE nor DELETE paths exist; asset "
        "FKs use ON DELETE RESTRICT so hard asset deletion never cascades into "
        "lineage history (soft deletion is the supported lifecycle), and the "
        "tenant-scoped self-FK on (tenant_id, supersedes_edge_id) keeps "
        "corrections pointed at same-tenant edges only (migration 5)."
    ),
    "knowledge_semantic_entities": "Section 2.4 projection of Semantic IR versions.",
    "knowledge_metric_registry": "Section 2.4 metric occurrences; formulas stay in the IR version.",
    "knowledge_business_rules": "Section 2.4 approved rule statements; RLS referenced read-only.",
    "knowledge_insights": "Section 2.5 grounded insight records.",
}

_MIGRATION_0001 = """
CREATE TABLE IF NOT EXISTS knowledge_schema_migrations (
    version integer PRIMARY KEY,
    applied_at text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE knowledge_assets (
    schema_version text NOT NULL,
    asset_id text NOT NULL,
    tenant_id text NOT NULL,  -- tenant FK added by the platform schema (Postgres: tenants(id))
    asset_kind text NOT NULL CHECK (asset_kind IN (
        'source_artifact', 'connection', 'dataset', 'semantic_model',
        'dashboard', 'report_page', 'visual', 'metric', 'column', 'table',
        'insight')),
    natural_key text NOT NULL,
    display_name text NOT NULL,
    description text,
    content_fingerprint text NOT NULL CHECK (content_fingerprint GLOB
        '[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*'),
    origin_kind text NOT NULL CHECK (origin_kind IN ('tableau', 'power_bi', 'unknown')),
    first_seen_at text NOT NULL,
    last_seen_at text NOT NULL,
    state text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'deprecated', 'deleted')),
    created_by text NOT NULL,
    PRIMARY KEY (tenant_id, asset_id),
    UNIQUE (tenant_id, asset_kind, natural_key)
);
"""

_MIGRATION_0002 = """
CREATE TABLE knowledge_source_bindings (
    schema_version text NOT NULL,
    binding_id text NOT NULL,
    tenant_id text NOT NULL,
    dataset_asset_id text NOT NULL,
    connection_asset_id text NOT NULL,
    connector_id text NOT NULL,
    capability_snapshot text NOT NULL,  -- jsonb on Postgres
    credential_ref text,                -- opaque id only; validated by contract
    mode text NOT NULL CHECK (mode IN ('live', 'extract')),
    freshness_policy text,              -- jsonb on Postgres
    created_at text NOT NULL,
    created_by text NOT NULL,
    PRIMARY KEY (tenant_id, binding_id),
    FOREIGN KEY (tenant_id, dataset_asset_id)
        REFERENCES knowledge_assets(tenant_id, asset_id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, connection_asset_id)
        REFERENCES knowledge_assets(tenant_id, asset_id) ON DELETE CASCADE
);

CREATE TABLE knowledge_lineage_edges (
    schema_version text NOT NULL,
    edge_id text NOT NULL,
    tenant_id text NOT NULL,
    from_asset_id text NOT NULL,
    to_asset_id text NOT NULL,
    edge_kind text NOT NULL CHECK (edge_kind IN (
        'derived_from', 'compiled_from', 'binds_to', 'references',
        'migrated_from', 'origin_of', 'grounded_by')),
    observed_at text NOT NULL,
    evidence_ref text NOT NULL,
    supersedes_edge_id text,
    PRIMARY KEY (tenant_id, edge_id),
    FOREIGN KEY (tenant_id, from_asset_id)
        REFERENCES knowledge_assets(tenant_id, asset_id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, to_asset_id)
        REFERENCES knowledge_assets(tenant_id, asset_id) ON DELETE CASCADE
);

CREATE TRIGGER knowledge_lineage_edges_no_update
BEFORE UPDATE ON knowledge_lineage_edges
BEGIN
    SELECT RAISE(ABORT, 'knowledge_lineage_edges is append-only');
END;
"""

#: Forward-only fix (adversarial review KP-CONTRACT-001): make lineage
#: append-only at the persistence level for BOTH UPDATE and DELETE, and
#: rebuild the table with ON DELETE RESTRICT FKs so hard-deleting an asset
#: cannot cascade-delete lineage history. Soft deletion (state='deleted')
#: remains the supported asset lifecycle and preserves history by design.
_MIGRATION_0004 = """
CREATE TABLE knowledge_lineage_edges_v4 (
    schema_version text NOT NULL,
    edge_id text NOT NULL,
    tenant_id text NOT NULL,
    from_asset_id text NOT NULL,
    to_asset_id text NOT NULL,
    edge_kind text NOT NULL CHECK (edge_kind IN (
        'derived_from', 'compiled_from', 'binds_to', 'references',
        'migrated_from', 'origin_of', 'grounded_by')),
    observed_at text NOT NULL,
    evidence_ref text NOT NULL,
    supersedes_edge_id text,
    PRIMARY KEY (tenant_id, edge_id),
    FOREIGN KEY (tenant_id, from_asset_id)
        REFERENCES knowledge_assets(tenant_id, asset_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, to_asset_id)
        REFERENCES knowledge_assets(tenant_id, asset_id) ON DELETE RESTRICT
);

INSERT INTO knowledge_lineage_edges_v4
    SELECT schema_version, edge_id, tenant_id, from_asset_id, to_asset_id,
           edge_kind, observed_at, evidence_ref, supersedes_edge_id
    FROM knowledge_lineage_edges;

DROP TABLE knowledge_lineage_edges;
ALTER TABLE knowledge_lineage_edges_v4 RENAME TO knowledge_lineage_edges;

CREATE TRIGGER knowledge_lineage_edges_no_update
BEFORE UPDATE ON knowledge_lineage_edges
BEGIN
    SELECT RAISE(ABORT, 'knowledge_lineage_edges is append-only');
END;

CREATE TRIGGER knowledge_lineage_edges_no_delete
BEFORE DELETE ON knowledge_lineage_edges
BEGIN
    SELECT RAISE(ABORT, 'knowledge_lineage_edges is append-only');
END;
"""

_MIGRATION_0003 = """
CREATE TABLE knowledge_semantic_entities (
    schema_version text NOT NULL,
    entity_id text NOT NULL,
    tenant_id text NOT NULL,
    ir_version text NOT NULL,
    node_path text NOT NULL,
    entity_type text NOT NULL CHECK (entity_type IN ('table', 'column', 'relationship')),
    grain text,
    created_at text NOT NULL,
    created_by text NOT NULL,
    PRIMARY KEY (tenant_id, entity_id),
    UNIQUE (tenant_id, ir_version, node_path)
);

CREATE TABLE knowledge_metric_registry (
    schema_version text NOT NULL,
    metric_id text NOT NULL,
    tenant_id text NOT NULL,
    asset_id text NOT NULL,
    ir_version text NOT NULL,
    node_path text NOT NULL,
    metric_checksum text NOT NULL,
    owner text NOT NULL,
    certification text NOT NULL DEFAULT 'none' CHECK (certification IN (
        'none', 'in_review', 'certified', 'deprecated')),
    certified_by text,
    certified_at text,
    grain text,
    default_filter_context text,        -- jsonb on Postgres
    created_at text NOT NULL,
    created_by text NOT NULL,
    PRIMARY KEY (tenant_id, metric_id),
    UNIQUE (tenant_id, ir_version, node_path),
    FOREIGN KEY (tenant_id, asset_id)
        REFERENCES knowledge_assets(tenant_id, asset_id) ON DELETE CASCADE
);

CREATE TABLE knowledge_business_rules (
    schema_version text NOT NULL,
    rule_id text NOT NULL,
    tenant_id text NOT NULL,
    ir_version text NOT NULL,
    node_path text NOT NULL,
    statement text NOT NULL CHECK (length(statement) BETWEEN 1 AND 2000),
    references_rls integer NOT NULL DEFAULT 0 CHECK (references_rls IN (0, 1)),
    owner text NOT NULL,
    created_at text NOT NULL,
    created_by text NOT NULL,
    PRIMARY KEY (tenant_id, rule_id),
    UNIQUE (tenant_id, ir_version, node_path)
);

CREATE TABLE knowledge_insights (
    schema_version text NOT NULL,
    insight_id text NOT NULL,
    tenant_id text NOT NULL,
    statement text NOT NULL CHECK (length(statement) BETWEEN 1 AND 2000),
    metric_ref text NOT NULL,
    grain text NOT NULL,
    filter_context text NOT NULL DEFAULT '{}',  -- jsonb on Postgres
    time_range text NOT NULL,                    -- jsonb on Postgres
    value_refs text NOT NULL DEFAULT '[]',       -- jsonb on Postgres
    ir_version text NOT NULL,
    query_plan_version text NOT NULL,
    evidence text NOT NULL,                      -- jsonb on Postgres, non-empty array enforced by contract
    certification text NOT NULL DEFAULT 'draft' CHECK (certification IN (
        'draft', 'reviewed', 'certified', 'rejected')),
    certified_by text,
    certified_at text,
    as_of text NOT NULL,
    created_at text NOT NULL,
    created_by text NOT NULL,
    PRIMARY KEY (tenant_id, insight_id),
    FOREIGN KEY (tenant_id, metric_ref)
        REFERENCES knowledge_metric_registry(tenant_id, metric_id) ON DELETE RESTRICT
);
"""

#: Forward-only fix (P1-3/P1-8): rebuild ``knowledge_lineage_edges`` again,
#: adding a tenant-scoped SELF foreign key so ``supersedes_edge_id`` can only
#: reference an edge of the same tenant. All v4 guarantees are preserved: both
#: asset FKs stay ON DELETE RESTRICT and both append-only triggers (no UPDATE,
#: no DELETE) are recreated. The self-FK is declared against the table's
#: temporary name so ``ALTER TABLE ... RENAME TO`` rewrites it to the final
#: name (SQLite rename semantics), and ``PRAGMA defer_foreign_keys`` lets the
#: backfill copy superseding rows regardless of source order before the
#: deferred check runs at COMMIT.
_MIGRATION_0005 = """
PRAGMA defer_foreign_keys = ON;

CREATE TABLE knowledge_lineage_edges_v5 (
    schema_version text NOT NULL,
    edge_id text NOT NULL,
    tenant_id text NOT NULL,
    from_asset_id text NOT NULL,
    to_asset_id text NOT NULL,
    edge_kind text NOT NULL CHECK (edge_kind IN (
        'derived_from', 'compiled_from', 'binds_to', 'references',
        'migrated_from', 'origin_of', 'grounded_by')),
    observed_at text NOT NULL,
    evidence_ref text NOT NULL,
    supersedes_edge_id text,
    PRIMARY KEY (tenant_id, edge_id),
    FOREIGN KEY (tenant_id, from_asset_id)
        REFERENCES knowledge_assets(tenant_id, asset_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, to_asset_id)
        REFERENCES knowledge_assets(tenant_id, asset_id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, supersedes_edge_id)
        REFERENCES knowledge_lineage_edges_v5(tenant_id, edge_id) ON DELETE RESTRICT
);

INSERT INTO knowledge_lineage_edges_v5
    SELECT schema_version, edge_id, tenant_id, from_asset_id, to_asset_id,
           edge_kind, observed_at, evidence_ref, supersedes_edge_id
    FROM knowledge_lineage_edges;

DROP TABLE knowledge_lineage_edges;

ALTER TABLE knowledge_lineage_edges_v5 RENAME TO knowledge_lineage_edges;

CREATE TRIGGER knowledge_lineage_edges_no_update
BEFORE UPDATE ON knowledge_lineage_edges
BEGIN
    SELECT RAISE(ABORT, 'knowledge_lineage_edges is append-only');
END;

CREATE TRIGGER knowledge_lineage_edges_no_delete
BEFORE DELETE ON knowledge_lineage_edges
BEGIN
    SELECT RAISE(ABORT, 'knowledge_lineage_edges is append-only');
END;
"""

#: Ordered forward-only migrations: (version, sql). Additive edits append here;
#: editing an applied migration is forbidden.
MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, _MIGRATION_0001),
    (2, _MIGRATION_0002),
    (3, _MIGRATION_0003),
    (4, _MIGRATION_0004),
    (5, _MIGRATION_0005),
)


def _split_statements(sql: str) -> list[str]:
    """Split a migration script into individually executable statements.

    Drops ``--``/``/* */`` comments, keeps ``'...'`` literals intact, tracks
    ``BEGIN...END`` depth so ``CREATE TRIGGER`` bodies stay whole, and splits
    on ``;`` only at depth zero.

    ponytail: mini-scanner, not a SQL tokenizer — it assumes no dialect
    constructs beyond comments, literals and trigger blocks (true for every
    shipped migration); switch to a real tokenizer if a future migration ever
    needs semicolons inside identifiers or nested block constructs.
    """
    statements: list[str] = []
    current: list[str] = []
    depth = 0
    i = 0
    length = len(sql)
    while i < length:
        ch = sql[i]
        if ch == "-" and sql[i : i + 2] == "--":
            end = sql.find("\n", i)
            i = length if end == -1 else end
            continue
        if ch == "/" and sql[i : i + 2] == "/*":
            end = sql.find("*/", i + 2)
            i = length if end == -1 else end + 2
            continue
        if ch == "'":
            end = sql.find("'", i + 1)
            stop = length if end == -1 else end + 1
            current.append(sql[i:stop])
            i = stop
            continue
        if ch == ";":
            if depth == 0:
                statements.append("".join(current))
                current = []
            else:
                current.append(ch)
            i += 1
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < length and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            word = sql[i:j].lower()
            if word == "begin":
                depth += 1
            elif word == "end" and depth > 0:
                depth -= 1
            current.append(sql[i:j])
            i = j
            continue
        current.append(ch)
        i += 1
    statements.append("".join(current))
    return [statement.strip() for statement in statements if statement.strip()]


def apply_migrations(connection: sqlite3.Connection) -> list[int]:
    """Apply pending forward-only knowledge-plane migrations to ``connection``.

    The migrations table is created first (idempotent), then each migration
    whose version is greater than the applied maximum runs in ONE explicit
    transaction together with its version-marker INSERT: a failed migration
    rolls back both its DDL and its version marker together; the schema is
    never left versioned-without-schema or schema-without-version. Returns
    the list of versions applied by this call (empty when already up to
    date).

    Args:
        connection: an open SQLite connection (typically ``:memory:`` in tests
            or a tenant database file owned by the service storage layer). It
            must be transaction-idle: each migration opens its own explicit
            ``BEGIN`` scope.

    Raises:
        sqlite3.Error: if any migration fails; the failed migration's
            transaction is rolled back and previously applied migrations in
            this call stay committed (forward-only recovery: re-run this
            function).
    """
    applied: list[int] = []
    connection.execute(
        "CREATE TABLE IF NOT EXISTS knowledge_schema_migrations ("
        "version integer PRIMARY KEY, "
        "applied_at text NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))"
    )
    connection.commit()
    (max_version,) = connection.execute(
        "SELECT coalesce(max(version), 0) FROM knowledge_schema_migrations"
    ).fetchone()
    for version, sql in MIGRATIONS:
        if version <= max_version:
            continue
        # executescript() commits implicitly and cannot share a transaction
        # with the version marker, so every statement runs via execute()
        # inside one explicit BEGIN...COMMIT scope. The explicit BEGIN works
        # in both isolation modes and makes DDL and the version-marker INSERT
        # commit (or roll back) atomically together.
        try:
            connection.execute("BEGIN")
            for statement in _split_statements(sql):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO knowledge_schema_migrations(version) VALUES (?)", (version,)
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
        applied.append(version)
    return applied


def current_version(connection: sqlite3.Connection) -> int:
    """Return the highest applied knowledge schema version (0 when fresh)."""
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'knowledge_schema_migrations'"
    ).fetchone()
    if not exists:
        return 0
    (version,) = connection.execute(
        "SELECT coalesce(max(version), 0) FROM knowledge_schema_migrations"
    ).fetchone()
    return version


__all__ = [
    "KNOWLEDGE_SCHEMA_VERSION",
    "MIGRATIONS",
    "TABLE_NOTES",
    "apply_migrations",
    "current_version",
]
