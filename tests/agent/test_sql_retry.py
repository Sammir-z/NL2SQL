from app.agent.graph import route_after_guard, route_after_validation


def test_security_failure_stops_before_database_validation():
    assert route_after_guard({"security_error": "table_forbidden"}) == "fail_query"
    assert route_after_guard({"security_error": None}) == "validate_sql"


def test_validation_failure_routes_to_correction_until_retry_limit():
    assert route_after_validation(
        {
            "error": "syntax error",
            "security_error": None,
            "validation_attempt": 1,
            "max_sql_retries": 2,
        }
    ) == "correct_sql"
    assert route_after_validation(
        {
            "error": "syntax error",
            "security_error": None,
            "validation_attempt": 3,
            "max_sql_retries": 2,
        }
    ) == "fail_query"


def test_security_failure_never_routes_to_llm_correction():
    assert route_after_validation(
        {
            "error": "security error",
            "security_error": "table_forbidden",
            "validation_attempt": 0,
            "max_sql_retries": 2,
        }
    ) == "fail_query"
