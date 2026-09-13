import asyncio

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.conf.app_config import app_config
from app.security.auth import get_current_principal
from app.security.models import Principal
from app.security.policy import build_security_context


def _credentials(payload: dict) -> HTTPAuthorizationCredentials:
    token = jwt.encode(
        payload,
        app_config.security.jwt_secret,
        algorithm=app_config.security.jwt_algorithm,
    )
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_decodes_principal_claims():
    principal = asyncio.run(
        get_current_principal(
            _credentials(
                {
                    "sub": "user-1",
                    "tenant_id": "tenant-001",
                    "roles": ["analyst"],
                    "scopes": ["query"],
                }
            )
        )
    )

    assert principal.user_id == "user-1"
    assert principal.tenant_id == "tenant-001"
    assert "query" in principal.scopes


def test_rejects_missing_credentials():
    with pytest.raises(HTTPException) as error:
        asyncio.run(get_current_principal(None))

    assert error.value.status_code == 401


def test_rejects_missing_required_claim():
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            get_current_principal(
                _credentials({"sub": "user-1", "tenant_id": "tenant-001"})
            )
        )

    assert error.value.status_code == 401


def test_policy_binds_tenant_filter_from_verified_principal():
    context = build_security_context(
        Principal(
            user_id="user-1",
            tenant_id="tenant-001",
            roles=frozenset({"analyst"}),
            scopes=frozenset({"query"}),
        )
    )

    assert context.row_filters["fact_order"]["region_id"] == frozenset(
        {"R001", "R004"}
    )


def test_policy_rejects_unknown_tenant():
    with pytest.raises(HTTPException) as error:
        build_security_context(
            Principal(
                user_id="user-1",
                tenant_id="tenant-unknown",
                roles=frozenset({"analyst"}),
                scopes=frozenset({"query"}),
            )
        )

    assert error.value.status_code == 403
