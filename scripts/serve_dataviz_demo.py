"""Servidor HTTP local para validar el contrato runtime de DataVIZ."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# Direct script execution starts with ``scripts/`` on sys.path, not the repo root.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.dataviz_service.runtime.executor import QueryExecutor
from core.contracts.dataset import Dataset
from core.contracts.query_ast import QuerySpec
from core.contracts.semantic_ir import SemanticModel


def execute_query(
    raw_query: Mapping[str, Any], query_context: Mapping[str, Any]
) -> dict[str, object]:
    """Ejecuta una Query AST contra el contexto local generado por la demo."""

    query = QuerySpec.from_dict(raw_query)
    raw_datasets = query_context.get("datasets", {})
    if not isinstance(raw_datasets, Mapping) or query.from_datasource not in raw_datasets:
        raise ValueError("query datasource is unavailable")
    raw_dataset = raw_datasets[query.from_datasource]
    if not isinstance(raw_dataset, Mapping):
        raise ValueError("query dataset is invalid")
    model_payload = query_context.get("semantic_model")
    if not isinstance(model_payload, Mapping):
        raise ValueError("semantic model is unavailable")
    dataset = Dataset(
        name=str(raw_dataset["name"]),
        columns=tuple(str(column) for column in raw_dataset["columns"]),
        rows=tuple(dict(row) for row in raw_dataset["rows"]),
    )
    result = QueryExecutor(SemanticModel.from_dict(model_payload)).execute(query, dataset)
    return {"name": result.name, "columns": list(result.columns), "rows": list(result.rows)}


class Handler(BaseHTTPRequestHandler):
    payload: dict[str, object] = {}
    query_context: dict[str, object] = {}
    cache_enabled = False
    response_cache: dict[str, bytes] = {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/healthz", "/readyz"}:
            self._send(200, {"status": "ok" if self.path == "/healthz" else "ready"})
            return
        if self.path == "/api/versions/tableau-dashboards/runtime":
            self._send_cached("GET:" + self.path, 200, self.payload)
            return
        self._send(404, {"error": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/versions/tableau-dashboards/query":
            self._send(404, {"error": "NOT_FOUND"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError("invalid query body size")
            raw_query = json.loads(self.rfile.read(length))
            if not isinstance(raw_query, dict):
                raise ValueError("query body must be an object")
            cache_key = "POST:" + self.path + ":" + json.dumps(
                raw_query, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            # El productor debe ser perezoso: evaluar ``execute_query`` como
            # argumento reejecuta la consulta incluso cuando la respuesta ya
            # está en caché (el encabezado HIT sería entonces engañoso).
            self._send_cached(
                cache_key,
                200,
                lambda: execute_query(raw_query, self.query_context),
            )
        except (KeyError, TypeError, ValueError) as exc:
            self._send(400, {"error": "INVALID_QUERY", "detail": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_cached(self, key: str, status: int, body: object | Callable[[], object]) -> None:
        if not self.cache_enabled:
            value = body() if callable(body) else body
            self._send(status, value)
            return
        raw = self.response_cache.get(key)
        cache_status = "HIT" if raw is not None else "MISS"
        if raw is None:
            value = body() if callable(body) else body
            raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.response_cache[key] = raw
        self._send_raw(status, raw, cache_status=cache_status)

    def _send(self, status: int, body: object) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self._send_raw(status, raw)

    def _send_raw(self, status: int, raw: bytes, *, cache_status: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        if cache_status is not None:
            self.send_header("X-Cache", cache_status)
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(raw)

    def _send_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin in {"http://127.0.0.1:5173", "http://localhost:5173"}:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Accept, Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--query-context", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--enable-cache",
        action="store_true",
        help="Enable the exact-request in-memory cache for local benchmark evidence",
    )
    args = parser.parse_args()
    Handler.payload = json.loads(args.payload.read_text(encoding="utf-8"))
    if args.query_context is not None:
        Handler.query_context = json.loads(args.query_context.read_text(encoding="utf-8"))
    Handler.cache_enabled = args.enable_cache
    Handler.response_cache = {}
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"DataVIZ demo API listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
