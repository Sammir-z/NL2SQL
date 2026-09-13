import pytest

from app.security.models import Principal, SecurityContext, SecurityViolation
from app.security.sql_guard import SQLGuard


@pytest.fixture
def context() -> SecurityContext:
    return SecurityContext(
        principal=Principal(
            user_id="u1",
            tenant_id="t1",
            roles=frozenset({"analyst"}),
            scopes=frozenset({"query"}),
        ),
        allowed_tables=frozenset({"fact_order", "dim_region"}),
        allowed_columns={
            "fact_order": frozenset({"order_amount", "region_id"}),
            "dim_region": frozenset({"region_id", "region_name"}),
        },
        row_filters={
            "fact_order": {"region_id": frozenset({"R001", "R004"})},
        },
        masked_columns={},
        max_result_rows=2,
        query_timeout_ms=1000,
        max_sql_retries=2,
    )


def test_rewrites_tenant_filter_and_caps_limit(context: SecurityContext):
    sql = SQLGuard().validate_and_rewrite(
        "SELECT order_amount FROM fact_order LIMIT 99", context
    )

    assert "LIMIT 2" in sql
    assert "region_id IN ('R001', 'R004')" in sql


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO fact_order (order_id) VALUES ('x')",
        "UPDATE fact_order SET order_amount = 0",
        "DROP TABLE fact_order",
        "SELECT order_amount FROM fact_order; SELECT region_id FROM fact_order",
    ],
)
def test_rejects_non_readonly_or_multiple_statements(
    context: SecurityContext, sql: str
):
    with pytest.raises(SecurityViolation):
        SQLGuard().validate_and_rewrite(sql, context)


def test_rejects_unauthorized_table_and_column(context: SecurityContext):
    guard = SQLGuard()

    with pytest.raises(SecurityViolation, match="无权限"):
        guard.validate_and_rewrite("SELECT password FROM users", context)
    with pytest.raises(SecurityViolation, match="无权限"):
        guard.validate_and_rewrite("SELECT customer_id FROM fact_order", context)


def test_rejects_dangerous_function_and_sensitive_alias(context: SecurityContext):
    context.masked_columns["region_name"] = "partial"
    guard = SQLGuard()

    with pytest.raises(SecurityViolation):
        guard.validate_and_rewrite("SELECT SLEEP(1) FROM fact_order", context)
    with pytest.raises(SecurityViolation):
        guard.validate_and_rewrite(
            "SELECT region_name AS public_name FROM dim_region", context
        )
