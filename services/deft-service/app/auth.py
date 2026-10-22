"""Bearer-token auth with two modes (DEFT_AUTH_MODE):

  trusted (default) — the bearer token's content IS the caller's identity.
      This is the original demo/identity-wiring mode: it exists so the
      ownership/authorization rules in openapi.json (RunStatus.created_by,
      the `deft:admin` scope, 403s on cross-identity access) are exercised
      end-to-end without an IdP. Token convention:
          Authorization: Bearer alice        -> subject="alice", admin=False
          Authorization: Bearer admin:alice  -> subject="alice", admin=True
      Do NOT deploy trusted mode anywhere that isn't a local demo.

  static — the bearer token must match a configured API key. Keys come from
      DEFT_API_KEYS: "<token>:<subject>[,<token>:<subject>...]"; prefix the
      subject with "admin:" to grant the deft:admin scope, e.g.
          DEFT_API_KEYS="s3cr3t-a:alice,s3cr3t-r:admin:root"
      This is a reasonable baseline for an internal single-tenant service.
      For multi-tenant / external deployments, swap `parse_principal` for
      real JWT/IdP verification — everything else is agnostic to it.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException

from app.config import API_KEYS_SPEC, AUTH_MODE
from app.schemas.common import ErrorResponse


@dataclass(frozen=True)
class Principal:
    subject: str
    is_admin: bool


def _parse_api_keys(spec: str) -> dict[str, Principal]:
    keys: dict[str, Principal] = {}
    for entry in filter(None, (part.strip() for part in spec.split(","))):
        token, _, subject = entry.partition(":")
        if not token or not subject:
            raise ValueError(f"DEFT_API_KEYS entry {entry!r} must be '<token>:<subject>'")
        if subject.startswith("admin:"):
            keys[token] = Principal(subject=subject[len("admin:"):], is_admin=True)
        else:
            keys[token] = Principal(subject=subject, is_admin=False)
    return keys


_API_KEYS: dict[str, Principal] = _parse_api_keys(API_KEYS_SPEC) if AUTH_MODE == "static" else {}
if AUTH_MODE == "static" and not _API_KEYS:
    raise ValueError("DEFT_AUTH_MODE=static requires a non-empty DEFT_API_KEYS")


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=ErrorResponse(error="unauthorized", message="Missing or invalid Authorization header.").model_dump(),
    )


def parse_principal(token: str) -> Principal:
    if AUTH_MODE == "static":
        principal = _API_KEYS.get(token)
        if principal is None:
            raise _unauthorized()
        return principal
    # trusted (demo) mode
    if token.startswith("admin:"):
        subject = token[len("admin:"):]
        if not subject:
            raise _unauthorized()
        return Principal(subject=subject, is_admin=True)
    return Principal(subject=token, is_admin=False)


async def get_principal(authorization: str | None = Header(default=None)) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized()
    token = authorization[len("bearer "):].strip()
    if not token:
        raise _unauthorized()
    return parse_principal(token)


def forbidden(run_id: str) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail=ErrorResponse(
            error="forbidden",
            message=f"Fine-tune run {run_id} belongs to a different identity.",
        ).model_dump(),
    )


def require_owner_or_admin(principal: Principal, created_by: str, run_id: str) -> None:
    if principal.is_admin or principal.subject == created_by:
        return
    raise forbidden(run_id)


def resolve_owner_filter(principal: Principal, owner: str | None) -> str | None:
    """Turns the `owner` query param into a store filter, enforcing that
    non-admin callers can only ever see their own runs.

    Returns None for "no filter" (admin browsing every run); otherwise the
    identity to filter on.
    """
    if owner is None:
        return None if principal.is_admin else principal.subject
    if owner == "*":
        if not principal.is_admin:
            raise HTTPException(
                status_code=403,
                detail=ErrorResponse(
                    error="forbidden",
                    message="The `deft:admin` scope is required to list runs across all identities.",
                ).model_dump(),
            )
        return None
    if owner != principal.subject and not principal.is_admin:
        raise HTTPException(
            status_code=403,
            detail=ErrorResponse(
                error="forbidden",
                message=f"The `deft:admin` scope is required to list runs owned by '{owner}'.",
            ).model_dump(),
        )
    return owner
