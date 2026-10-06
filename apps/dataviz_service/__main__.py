"""CLI productiva de DataVIZ."""

from __future__ import annotations

import argparse
import logging

import uvicorn

from apps.dataviz_service.bootstrap import (
    MigrationSettings,
    ServiceSettings,
    WorkerSettings,
    build_application,
    build_migration_store,
    build_worker,
)
from apps.dataviz_service.job_worker import run_worker_loop


def main() -> None:
    """Sirve la API, aplica migraciones o ejecuta el loop de jobs."""
    parser = argparse.ArgumentParser(description="DataVIZ production service")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Serve the authenticated ASGI API")
    serve.add_argument("--port", type=int)
    commands.add_parser("migrate", help="Apply pending PostgreSQL schema migrations")
    commands.add_parser(
        "worker", help="Run the self-hosted job worker over configured tenants"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "worker":
        worker_settings = WorkerSettings.from_environment()
        store, handler = build_worker(worker_settings)
        try:
            run_worker_loop(
                store,
                handler,
                worker_settings.tenants,
                idle_seconds=worker_settings.idle_seconds,
                lease_seconds=worker_settings.lease_seconds,
                heartbeat_seconds=worker_settings.heartbeat_seconds,
            )
        finally:
            store.close()
        return

    if args.command == "migrate":
        migration_settings = MigrationSettings.from_environment()
        store = build_migration_store(migration_settings)
        try:
            store.migrate()
        finally:
            store.close()
        return

    settings = ServiceSettings.from_environment()
    port = args.port or settings.port
    uvicorn.run(build_application(settings), host="0.0.0.0", port=port, access_log=False)


if __name__ == "__main__":
    main()
