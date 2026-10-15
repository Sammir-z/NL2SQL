"""Unit tests for RAG retrieval and rejection evaluator."""

from evaluation.run_rag_eval import (
    calculate_ir_metrics,
    evaluate_single_case_metrics,
)


def test_calculate_ir_metrics_full_hit():
    retrieved = ["fact_order.order_amount", "dim_date.quarter", "dim_region.province"]
    gt = ["fact_order.order_amount", "dim_date.quarter"]

    metrics = calculate_ir_metrics(retrieved, gt, k=5)
    assert metrics["recall"] == 1.0
    assert metrics["precision"] == 2.0 / 5.0
    assert metrics["mrr"] == 1.0
    assert metrics["hit_rate"] == 1.0


def test_calculate_ir_metrics_partial_hit():
    retrieved = ["dim_customer.gender", "dim_date.month", "dim_region.province"]
    gt = ["fact_order.order_amount", "dim_date.month"]

    metrics = calculate_ir_metrics(retrieved, gt, k=5)
    assert metrics["recall"] == 0.5
    assert metrics["precision"] == 1.0 / 5.0
    assert metrics["mrr"] == 0.5  # First hit is at rank 2 -> 1/2 = 0.5
    assert metrics["hit_rate"] == 1.0


def test_out_of_domain_true_rejection():
    case = {
        "id": "rag_033",
        "category": "C4_out_of_domain",
        "category_name": "越界与不存在实体探针",
        "query": "统计退款总金额",
        "is_out_of_domain": True,
        "ground_truth": {
            "required_tables": [],
            "required_columns": [],
            "required_metrics": [],
        },
    }
    # When Filter model clears tables for OOD case, it is a True Rejection
    result = evaluate_single_case_metrics(
        raw_retrieved_columns=["fact_order.order_amount"],
        raw_retrieved_metrics=["GMV"],
        raw_retrieved_values=[],
        final_table_infos=[],  # Filter outputs empty
        case=case,
    )
    assert result["rejection_status"] == "TRUE_REJECTION"
    assert result["final_pass"] is True


def test_out_of_domain_false_acceptance():
    case = {
        "id": "rag_033",
        "category": "C4_out_of_domain",
        "category_name": "越界与不存在实体探针",
        "query": "统计退款总金额",
        "is_out_of_domain": True,
        "ground_truth": {
            "required_tables": [],
            "required_columns": [],
            "required_metrics": [],
        },
    }
    # If Filter fails to reject and keeps order_amount, it's False Acceptance
    result = evaluate_single_case_metrics(
        raw_retrieved_columns=["fact_order.order_amount"],
        raw_retrieved_metrics=["GMV"],
        raw_retrieved_values=[],
        final_table_infos=[
            {"name": "fact_order", "columns": [{"name": "order_amount"}]}
        ],
        case=case,
    )
    assert result["rejection_status"] == "FALSE_ACCEPTANCE"
    assert result["final_pass"] is False
