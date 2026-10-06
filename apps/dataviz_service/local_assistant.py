"""Servicio local del asistente; reutiliza el adaptador y los contratos del servicio."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from apps.dataviz_service.agent_tools.zai import (
    AssistantError,
    AssistantRequest,
    ZaiSettings,
    propose,
)


def create_app(settings: ZaiSettings) -> Starlette:
    """Expone configuración sin secretos y generación de propuestas para uso local."""

    async def config(request: Request) -> JSONResponse:
        return JSONResponse(settings.public_config(), headers={"Cache-Control": "no-store"})

    async def proposal(request: Request) -> JSONResponse:
        # Dev tool local: cualquier puerto de localhost es un dev server del
        # propio proyecto (3000 por convención, 3001+ cuando quedó ocupado).
        origin = request.headers.get("origin")
        allowed_origins = {
            None,
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            *(f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in range(3000, 3010)),
        }
        if origin not in allowed_origins:
            return JSONResponse({"error": "Origen no permitido."}, status_code=403)
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return JSONResponse({"error": "Se requiere JSON."}, status_code=415)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 256_000:
                return JSONResponse(
                    {"error": "El contexto del dashboard supera el límite."}, status_code=413
                )
        try:
            payload = AssistantRequest.model_validate_json(bytes(raw))
            result = await run_in_threadpool(propose, payload, settings)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except (ValidationError, ValueError, json.JSONDecodeError):
            return JSONResponse(
                {"error": "Solicitud inválida: revisa modelo, razonamiento y contexto."},
                status_code=400,
            )
        except AssistantError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)

    return Starlette(
        routes=[
            Route("/api/assistant/config", config, methods=["GET"]),
            Route("/api/assistant/proposals", proposal, methods=["POST"]),
        ]
    )


def main() -> None:
    """Carga sólo el .env del proyecto y escucha exclusivamente en loopback."""
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    uvicorn.run(create_app(ZaiSettings.from_environment()), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
