"""Build a request policy from the authenticated principal."""

from fastapi import HTTPException, status

from app.conf.app_config import app_config
from app.security.models import Principal, SecurityContext


def _forbidden(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)


def build_security_context(principal: Principal) -> SecurityContext:
    """Resolve the tenant and role policy without trusting request data."""

    if "query" not in principal.scopes:
        raise _forbidden("Missing query scope")

    tenant_filters = app_config.security.tenant_filters.get(principal.tenant_id)
    if tenant_filters is None:
        raise _forbidden("Tenant is not configured")

    masked_columns = dict(app_config.security.masked_columns)
    if "pii:read" in principal.scopes:
        masked_columns = {}

    return SecurityContext(
        principal=principal,
        allowed_tables=frozenset(
            table.lower() for table in app_config.security.allowed_tables
        ),
        allowed_columns={
            table.lower(): frozenset(column.lower() for column in columns)
            for table, columns in app_config.security.allowed_columns.items()
        },
        row_filters={
            table.lower(): {
                column.lower(): frozenset(str(value) for value in values)
                for column, values in columns.items()
            }
            for table, columns in tenant_filters.items()
        },
        masked_columns={column.lower(): strategy for column, strategy in masked_columns.items()},
        max_result_rows=app_config.security.max_result_rows,
        query_timeout_ms=app_config.security.query_timeout_ms,
        max_sql_retries=app_config.security.max_sql_retries,
    )
