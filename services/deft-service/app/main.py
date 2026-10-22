from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import AUTH_MODE, BACKEND_TYPE, EXECUTION_MODE
from app.routers import fine_tune, profiles, service, workflows
from app.store import run_store

OPENAPI_PATH = Path(__file__).resolve().parent.parent / "openapi.json"

logger = logging.getLogger("deft.service")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    orphans = run_store.recover_orphans()
    if orphans:
        logger.warning("marked %d run(s) failed: they were mid-flight when the previous process exited", orphans)
    if AUTH_MODE == "trusted":
        logger.warning(
            "DEFT_AUTH_MODE=trusted — bearer tokens are trusted verbatim. "
            "Local demos only; set DEFT_AUTH_MODE=static (+DEFT_API_KEYS) or swap in an IdP before deploying."
        )
    logger.info("execution_mode=%s backend=%s", EXECUTION_MODE, BACKEND_TYPE)
    yield


app = FastAPI(title="DEFT", lifespan=_lifespan)

app.include_router(service.router)
app.include_router(workflows.router)
app.include_router(profiles.router)
app.include_router(fine_tune.router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # Routes raise HTTPException with `detail` already shaped as
    # ErrorResponse (or a list of ValidationError dicts for 422) — pass it
    # through verbatim instead of FastAPI's default `{"detail": ...}` wrapper.
    if isinstance(exc.detail, list):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return JSONResponse(status_code=exc.status_code, content=exc.detail)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Matches HTTPValidationError/ValidationError in openapi.json.
    # jsonable_encoder: pydantic model_validator errors carry a raw
    # exception instance in ctx["error"], which plain json.dumps rejects.
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})


_openapi_schema: dict | None = None


def _load_openapi_schema() -> dict:
    global _openapi_schema
    if _openapi_schema is None:
        _openapi_schema = json.loads(OPENAPI_PATH.read_text())
    return _openapi_schema


# openapi.json is the agreed API contract — serve it verbatim instead of
# FastAPI's auto-derived schema, so /docs and /openapi.json always reflect
# exactly what was reviewed and signed off, not an incidental byproduct of
# how the routes happen to be typed.
app.openapi = _load_openapi_schema  # type: ignore[method-assign]
