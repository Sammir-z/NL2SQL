from app.security.masking import ResultMasker
from app.security.models import Principal, SecurityContext


def test_masks_full_partial_and_hash_values():
    context = SecurityContext(
        principal=Principal(
            user_id="u1",
            tenant_id="t1",
            roles=frozenset(),
            scopes=frozenset(),
        ),
        allowed_tables=frozenset(),
        allowed_columns={},
        row_filters={},
        masked_columns={
            "secret": "full",
            "customer_name": "partial",
            "customer_id": "hash",
        },
        max_result_rows=10,
        query_timeout_ms=1000,
        max_sql_retries=1,
    )

    rows = ResultMasker().mask(
        [{"secret": "abc", "customer_name": "李伟", "customer_id": "C001"}],
        context,
    )

    assert rows[0]["secret"] == "******"
    assert rows[0]["customer_name"] == "李*"
    assert rows[0]["customer_id"] != "C001"
    assert len(rows[0]["customer_id"]) == 16
