"""Shared security data structures."""

from dataclasses import dataclass


class SecurityViolation(Exception):
    """A query violated a non-negotiable security rule."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Principal:
    """Authenticated identity extracted from a verified token."""

    user_id: str
    tenant_id: str
    roles: frozenset[str]
    scopes: frozenset[str]


@dataclass(frozen=True)
class SecurityContext:
    """Request-scoped authorization and execution policy."""

    principal: Principal
    allowed_tables: frozenset[str]
    allowed_columns: dict[str, frozenset[str]]
    row_filters: dict[str, dict[str, frozenset[str]]]
    masked_columns: dict[str, str]
    max_result_rows: int
    query_timeout_ms: int
    max_sql_retries: int
