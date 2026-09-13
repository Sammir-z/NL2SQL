"""Bearer token authentication for query endpoints."""

from typing import Annotated

import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.conf.app_config import app_config
from app.security.models import Principal

bearer_scheme = HTTPBearer(auto_error=False)


def _authentication_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _claim_set(value: object) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset(item for item in value.split() if item)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return frozenset(value)
    return frozenset()


async def get_current_principal(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(bearer_scheme)
    ],
) -> Principal:
    """Decode a JWT and expose only the claims needed by the query policy."""

    if credentials is None:
        raise _authentication_error()

    try:
        payload = jwt.decode(
            credentials.credentials,
            app_config.security.jwt_secret,
            algorithms=[app_config.security.jwt_algorithm],
            options={"require": ["sub", "tenant_id", "scopes"]},
        )
    except jwt.InvalidTokenError as exc:
        raise _authentication_error() from exc

    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    if not isinstance(user_id, str) or not user_id:
        raise _authentication_error()
    if not isinstance(tenant_id, str) or not tenant_id:
        raise _authentication_error()

    return Principal(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=_claim_set(payload.get("roles", [])),
        scopes=_claim_set(payload.get("scopes", [])),
    )
