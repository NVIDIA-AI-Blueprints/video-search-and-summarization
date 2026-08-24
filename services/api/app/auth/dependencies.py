import json
from typing import Any

import jwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

_bearer_scheme = HTTPBearer(auto_error=False)


def _get_signing_key(token: str):
    settings = get_settings()
    jwks_client = PyJWKClient(settings.cognito_jwks_url)
    return jwks_client.get_signing_key_from_jwt(token).key


def verify_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            _get_signing_key(token),
            algorithms=["RS256"],
            audience=settings.cognito_app_client_id or None,
            options={"verify_aud": bool(settings.cognito_app_client_id)},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        ) from exc
    return claims


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict[str, Any]:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    claims = verify_token(credentials.credentials)
    if claims.get("token_use") not in ("id", "access"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unexpected token type",
        )
    return {
        "user_id": claims.get("sub"),
        "username": claims.get("username") or claims.get("cognito:username"),
        "email": claims.get("email"),
        "claims": claims,
    }


async def require_admin(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    groups = user["claims"].get("cognito:groups", [])
    if "admins" not in groups:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


def local_dev_user() -> dict[str, Any]:
    settings = get_settings()
    if settings.environment != "local":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return {"user_id": "local-dev-user", "username": "dev", "email": None, "claims": {}}


def dumps(value: Any) -> str:
    return json.dumps(value, default=str)
