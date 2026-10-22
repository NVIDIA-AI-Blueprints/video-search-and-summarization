"""Shared HTTPException factories, one per ErrorResponse `error` code used
in openapi.json, so every router raises the same shape the spec documents.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.schemas.common import ErrorResponse


def _error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=ErrorResponse(error=error, message=message).model_dump())


def run_not_found(run_id: str) -> HTTPException:
    return _error(404, "run_not_found", f"Fine-tune run {run_id} was not found.")


def workflow_not_found(name: str) -> HTTPException:
    return _error(404, "workflow_not_found", f"Workflow {name} was not found.")


def profile_not_found(name: str) -> HTTPException:
    return _error(404, "profile_not_found", f"Profile {name} was not found.")


def run_not_completed(run_id: str, state: str) -> HTTPException:
    return _error(
        409,
        "run_not_completed",
        f"Results are not available until the run reaches state 'completed' (current state: {state}).",
    )


def idempotency_key_conflict(key: str) -> HTTPException:
    return _error(
        409,
        "idempotency_key_conflict",
        f"Idempotency-Key '{key}' was already used with a different request body. Use a new key for a different request.",
    )


def invalid_request(message: str, loc: list[str | int]) -> HTTPException:
    """422 shaped like FastAPI's own HTTPValidationError, for domain-level
    validation (e.g. unknown profile/workflow) that isn't caught by a
    Pydantic field validator.
    """
    return HTTPException(
        status_code=422,
        detail=[{"loc": loc, "msg": message, "type": "value_error"}],
    )
