BEGIN;

-- 0012 made versions append-only for product runtime, but an unconditional
-- DELETE trigger also blocked privileged administrative cleanup and tenant
-- teardown. Keep runtime fail-closed while allowing the separate migrator/
-- owner credential to perform explicit repair/retention operations.
CREATE OR REPLACE FUNCTION reject_versions_delete() RETURNS trigger AS $$
DECLARE
    admin_authority boolean := false;
BEGIN
    SELECT (
        r.rolsuper
        OR r.rolbypassrls
        OR r.rolcreaterole
        OR r.rolcreatedb
        OR r.rolreplication
        OR EXISTS (
            SELECT 1 FROM pg_namespace n
            WHERE n.nspname = 'public' AND n.nspowner = r.oid
        )
        OR EXISTS (
            SELECT 1
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = 'versions'
              AND c.relowner = r.oid
        )
    )
    INTO admin_authority
    FROM pg_roles r
    WHERE r.rolname = session_user;

    IF NOT COALESCE(admin_authority, false) THEN
        RAISE EXCEPTION 'durable versions are append-only for runtime roles';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

INSERT INTO schema_migrations(version) VALUES (13)
    ON CONFLICT (version) DO NOTHING;

COMMIT;
