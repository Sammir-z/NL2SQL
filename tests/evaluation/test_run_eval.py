from evaluation.run_eval import canonical_rows, normalize_value


def test_normalize_value_compares_numeric_json_values_consistently():
    assert normalize_value("8999.0") == normalize_value(8999)


def test_canonical_rows_ignores_row_order_for_unordered_results():
    rows = [{"region": "华北", "amount": "10.0"}, {"region": "华南", "amount": 20}]
    shuffled = [{"region": "华南", "amount": 20.0}, {"region": "华北", "amount": 10}]

    assert canonical_rows(rows, order_sensitive=False) == canonical_rows(
        shuffled, order_sensitive=False
    )
