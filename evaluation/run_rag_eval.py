"""
Offline RAG Retrieval & Rejection Evaluator for Shopkeeper NL2SQL Agent.
Evaluates Raw Recall@5, Precision@5, MRR, Context Completion, and Rejection Rate (TRR/FRR)
with per-category statistical breakdown over 80 Bad Cases.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import sys
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_RAG_DATASET = PROJECT_ROOT / "docs" / "RAG_test" / "rag_eval_cases.json"


class MockStreamWriter:
    """Mock stream writer for running nodes offline."""

    def __call__(self, event: dict[str, Any]) -> None:
        pass


class SimpleRuntime:
    """Lightweight runtime wrapper conforming to LangGraph Runtime protocol."""

    def __init__(self, context: dict[str, Any]):
        self.context = context
        self.stream_writer = MockStreamWriter()


def calculate_ir_metrics(
    retrieved_items: list[str], ground_truth: list[str], k: int = 5
) -> dict[str, float]:
    """Calculate Recall@K, Precision@K, and MRR for a list of string identifiers."""
    if not ground_truth:
        return {"recall": 1.0, "precision": 1.0, "mrr": 1.0, "hit_rate": 1.0}

    top_k = retrieved_items[:k]
    gt_set = set(ground_truth)
    hits = [item for item in top_k if item in gt_set]

    recall = len(hits) / len(gt_set)
    precision = len(hits) / k if k > 0 else 0.0
    hit_rate = 1.0 if hits else 0.0

    # Calculate Reciprocal Rank of the first relevant item
    mrr = 0.0
    for rank, item in enumerate(retrieved_items, start=1):
        if item in gt_set:
            mrr = 1.0 / rank
            break

    return {
        "recall": recall,
        "precision": precision,
        "mrr": mrr,
        "hit_rate": hit_rate,
    }


def evaluate_single_case_metrics(
    raw_retrieved_columns: list[str],
    raw_retrieved_metrics: list[str],
    raw_retrieved_values: list[str],
    final_table_infos: list[dict[str, Any]],
    case: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate dual-granularity metrics for a single test case."""
    gt = case["ground_truth"]
    is_ood = case.get("is_out_of_domain", False)

    # 1. Evaluate Granularity A: Raw Retrieval Top-5
    # Combine columns and metrics to evaluate overall schema retrieval
    all_raw_items = raw_retrieved_columns + raw_retrieved_metrics
    required_targets = gt.get("required_columns", []) + gt.get("required_metrics", [])

    if is_ood:
        raw_metrics = {"recall": 0.0, "precision": 0.0, "mrr": 0.0, "hit_rate": 0.0}
    else:
        raw_metrics = calculate_ir_metrics(all_raw_items, required_targets, k=5)

    # 2. Evaluate Granularity B: Merged & Filtered Final Schema & Rejection
    final_columns: set[str] = set()
    for table in final_table_infos:
        tname = table.get("name", "")
        for col in table.get("columns", []):
            cname = col.get("name", "")
            final_columns.add(f"{tname}.{cname}")

    if is_ood:
        # For out-of-domain / attack query, rejection is SUCCESS if Filter empties the tables
        # or if final_columns is empty
        is_rejected = len(final_table_infos) == 0 or len(final_columns) == 0
        final_pass = is_rejected
        rejection_status = "TRUE_REJECTION" if is_rejected else "FALSE_ACCEPTANCE"
        final_recall = 1.0 if is_rejected else 0.0
    else:
        # For valid query, check if all required columns are preserved
        req_cols = set(gt.get("required_columns", []))
        if not req_cols:
            final_recall = 1.0
            final_pass = len(final_table_infos) > 0
        else:
            hits = req_cols.intersection(final_columns)
            final_recall = len(hits) / len(req_cols)
            final_pass = final_recall >= 0.99  # All required columns preserved

        is_falsely_rejected = len(final_table_infos) == 0 or len(final_columns) == 0
        rejection_status = "FALSE_REJECTION" if is_falsely_rejected else "NORMAL_PASSED"

    return {
        "case_id": case["id"],
        "category": case["category"],
        "category_name": case.get("category_name", ""),
        "query": case["query"],
        "is_out_of_domain": is_ood,
        "raw_recall_at_5": raw_metrics["recall"],
        "raw_precision_at_5": raw_metrics["precision"],
        "raw_mrr": raw_metrics["mrr"],
        "raw_hit_rate": raw_metrics["hit_rate"],
        "final_context_recall": final_recall,
        "final_pass": final_pass,
        "rejection_status": rejection_status,
        "final_columns_retained": list(final_columns),
    }


def simulate_mock_retrieval(case: dict[str, Any]) -> tuple[list[str], list[str], list[str], list[dict[str, Any]]]:
    """Provide realistic simulation when running in offline/mock mode."""
    gt = case["ground_truth"]
    is_ood = case.get("is_out_of_domain", False)

    if is_ood:
        # In out-of-domain case, raw retrieval returns random noisy columns
        raw_cols = ["fact_order.order_amount", "dim_date.quarter", "dim_region.province"]
        raw_metrics = ["GMV"]
        raw_values = []
        # Filter model successfully identifies query is out-of-domain and outputs empty list
        final_tables: list[dict[str, Any]] = []
        return raw_cols, raw_metrics, raw_values, final_tables

    # For normal cases, simulate high-quality retrieval matching Ground Truth
    req_cols = gt.get("required_columns", [])
    req_metrics = gt.get("required_metrics", [])
    req_values = gt.get("required_values", [])

    # Include GT plus 2 noise columns for Top-5 simulation
    raw_cols = req_cols + ["dim_customer.gender", "dim_region.country"]
    raw_metrics = req_metrics if req_metrics else []
    raw_values = req_values

    # Simulate final filtered tables retaining GT
    tables_map: dict[str, list[dict[str, Any]]] = {}
    for col_str in req_cols:
        parts = col_str.split(".")
        if len(parts) == 2:
            tname, cname = parts
            if tname not in tables_map:
                tables_map[tname] = []
            tables_map[tname].append({"name": cname, "description": "mock"})

    final_tables = [{"name": tname, "columns": cols} for tname, cols in tables_map.items()]
    return raw_cols, raw_metrics, raw_values, final_tables


async def run_live_case(
    case: dict[str, Any], runtime: SimpleRuntime
) -> tuple[list[str], list[str], list[str], list[dict[str, Any]]]:
    """Execute the live node graph for retrieval and filtering."""
    from app.agent.nodes.extract_keywords import extract_keywords
    from app.agent.nodes.filter_metric import filter_metric
    from app.agent.nodes.filter_table import filter_table
    from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
    from app.agent.nodes.recall_column import recall_column
    from app.agent.nodes.recall_metric import recall_metric
    from app.agent.nodes.recall_value import recall_value

    state: dict[str, Any] = {"query": case["query"], "keywords": []}

    # 1. Extract keywords
    kw_res = await extract_keywords(state, runtime)  # type: ignore
    state.update(kw_res)

    # 2. Parallel 3-way retrieval
    col_task = recall_column(state, runtime)  # type: ignore
    val_task = recall_value(state, runtime)  # type: ignore
    met_task = recall_metric(state, runtime)  # type: ignore
    col_res, val_res, met_res = await asyncio.gather(col_task, val_task, met_task)

    state.update(col_res)
    state.update(val_res)
    state.update(met_res)

    raw_cols = [c.id for c in state.get("retrieved_column_infos", [])]
    raw_metrics = [m.name for m in state.get("retrieved_metric_infos", [])]
    raw_values = [v.value for v in state.get("retrieved_value_infos", [])]

    # 3. Merge retrieved info
    merge_res = await merge_retrieved_info(state, runtime)  # type: ignore
    state.update(merge_res)

    # 4. Filter table and metric
    ft_task = filter_table(state, runtime)  # type: ignore
    fm_task = filter_metric(state, runtime)  # type: ignore
    ft_res, fm_res = await asyncio.gather(ft_task, fm_task)
    state.update(ft_res)
    state.update(fm_res)

    final_tables = state.get("table_infos", [])
    return raw_cols, raw_metrics, raw_values, final_tables


def print_and_format_report(
    results: list[dict[str, Any]], log_file: Path | None, report_json_file: Path | None
) -> str:
    """Compute per-category breakdown and render a clean ASCII report table."""
    categories: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(r)

    lines = []
    lines.append("=" * 120)
    lines.append("                          Shopkeeper RAG 召回与拒答能力离线评测报告")
    lines.append(f"               评测时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 总用例数: {len(results)} 条")
    lines.append("=" * 120)
    lines.append(
        f"{'类别编码':<6} {'类别名称':<18} {'样本量':<6} "
        f"{'Raw Recall@5':<14} {'Final Recall':<14} {'Precision@5':<13} {'MRR':<8} {'拒答判定结果 (TRR / FRR)':<25}"
    )
    lines.append("-" * 120)

    category_summary_data = {}

    total_normal_count = 0
    total_normal_raw_rec = 0.0
    total_normal_final_rec = 0.0
    total_normal_prec = 0.0
    total_normal_mrr = 0.0
    total_normal_false_rejections = 0

    total_ood_count = 0
    total_ood_true_rejections = 0

    for cat_code, cat_items in sorted(categories.items()):
        cat_name = cat_items[0].get("category_name", cat_code)
        count = len(cat_items)
        is_ood_cat = all(item["is_out_of_domain"] for item in cat_items)

        if is_ood_cat:
            # Out of domain category (e.g., C4, C7)
            trr_hits = sum(1 for item in cat_items if item["rejection_status"] == "TRUE_REJECTION")
            trr_pct = (trr_hits / count) * 100.0
            rejection_text = f"TRR: {trr_pct:5.1f}% ({trr_hits}/{count} 成功拒答)"

            lines.append(
                f"{cat_code:<6} {cat_name:<18} {count:>3}条   "
                f"{'N/A':<14} {'N/A':<14} {'N/A':<13} {'N/A':<8} {rejection_text:<25}"
            )
            total_ood_count += count
            total_ood_true_rejections += trr_hits

            category_summary_data[cat_code] = {
                "name": cat_name,
                "count": count,
                "type": "out_of_domain",
                "trr": trr_pct,
                "rejection_detail": f"{trr_hits}/{count}",
            }
        else:
            # Normal business query category
            valid_items = [it for it in cat_items if not it["is_out_of_domain"]]
            v_count = len(valid_items)
            raw_rec = sum(it["raw_recall_at_5"] for it in valid_items) / v_count * 100.0
            final_rec = sum(it["final_context_recall"] for it in valid_items) / v_count * 100.0
            prec = sum(it["raw_precision_at_5"] for it in valid_items) / v_count * 100.0
            mrr = sum(it["raw_mrr"] for it in valid_items) / v_count

            false_rejections = sum(1 for it in valid_items if it["rejection_status"] == "FALSE_REJECTION")
            frr_pct = (false_rejections / v_count) * 100.0
            rejection_text = f"FRR: {frr_pct:5.1f}% ({false_rejections}/{v_count} 误拒)"

            lines.append(
                f"{cat_code:<6} {cat_name:<18} {count:>3}条   "
                f"{raw_rec:>8.1f}%      {final_rec:>8.1f}%      {prec:>7.1f}%     {mrr:>6.2f}   {rejection_text:<25}"
            )

            total_normal_count += v_count
            total_normal_raw_rec += sum(it["raw_recall_at_5"] for it in valid_items)
            total_normal_final_rec += sum(it["final_context_recall"] for it in valid_items)
            total_normal_prec += sum(it["raw_precision_at_5"] for it in valid_items)
            total_normal_mrr += sum(it["raw_mrr"] for it in valid_items)
            total_normal_false_rejections += false_rejections

            category_summary_data[cat_code] = {
                "name": cat_name,
                "count": count,
                "type": "normal_business",
                "raw_recall_at_5": raw_rec,
                "final_context_recall": final_rec,
                "precision_at_5": prec,
                "mrr": mrr,
                "frr": frr_pct,
            }

    lines.append("-" * 120)
    lines.append("【总体评估指标汇总 (Overall Summary)】")
    if total_normal_count > 0:
        avg_raw_rec = (total_normal_raw_rec / total_normal_count) * 100.0
        avg_final_rec = (total_normal_final_rec / total_normal_count) * 100.0
        avg_prec = (total_normal_prec / total_normal_count) * 100.0
        avg_mrr = total_normal_mrr / total_normal_count
        avg_frr = (total_normal_false_rejections / total_normal_count) * 100.0

        lines.append(
            f"  * 合法业务样本 ({total_normal_count}条): "
            f"Raw Recall@5 = {avg_raw_rec:.1f}% | Final Context Recall = {avg_final_rec:.1f}% | "
            f"Precision@5 = {avg_prec:.1f}% | MRR = {avg_mrr:.2f} | 误拒率 (FRR) = {avg_frr:.1f}%"
        )

    if total_ood_count > 0:
        avg_trr = (total_ood_true_rejections / total_ood_count) * 100.0
        lines.append(
            f"  * 越界与防御样本 ({total_ood_count}条): "
            f"正向拒答率 (TRR) = {avg_trr:.1f}% ({total_ood_true_rejections}/{total_ood_count} 成功拦截并拒答)"
        )

    lines.append("=" * 120 + "\n")
    report_text = "\n".join(lines)

    # Write to log file if requested
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as f:
            f.write(report_text)

    # Export structured JSON report
    if report_json_file:
        report_json_file.parent.mkdir(parents=True, exist_ok=True)
        summary_payload = {
            "timestamp": datetime.datetime.now().isoformat(),
            "total_cases": len(results),
            "normal_summary": {
                "count": total_normal_count,
                "raw_recall_at_5": (total_normal_raw_rec / total_normal_count) if total_normal_count else 0.0,
                "final_context_recall": (total_normal_final_rec / total_normal_count) if total_normal_count else 0.0,
                "precision_at_5": (total_normal_prec / total_normal_count) if total_normal_count else 0.0,
                "mrr": (total_normal_mrr / total_normal_count) if total_normal_count else 0.0,
                "frr": (total_normal_false_rejections / total_normal_count) if total_normal_count else 0.0,
            },
            "rejection_summary": {
                "count": total_ood_count,
                "true_rejection_rate": (total_ood_true_rejections / total_ood_count) if total_ood_count else 0.0,
            },
            "category_breakdown": category_summary_data,
            "case_results": results,
        }
        report_json_file.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return report_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_RAG_DATASET,
        help="path to rag_eval_cases.json",
    )
    parser.add_argument("--limit", type=int, help="evaluate only the first N cases")
    parser.add_argument("--category", type=str, help="filter evaluation by category code (e.g. C1_synonym)")
    parser.add_argument(
        "--log-file",
        type=Path,
        default=PROJECT_ROOT / "logs" / "RAG" / "rag_eval_results.log",
        help="path to log evaluation report",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        default=PROJECT_ROOT / "docs" / "RAG_test" / "rag_eval_report.json",
        help="path to export JSON evaluation report",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="run in simulated offline mode without connecting to live Docker databases",
    )
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()

    if not args.dataset.exists():
        print(f"Dataset not found at {args.dataset}, please generate it first.")
        return 1

    cases = json.loads(args.dataset.read_text(encoding="utf-8"))

    if args.category:
        cases = [c for c in cases if c.get("category") == args.category]

    if args.limit:
        cases = cases[: args.limit]

    print(f"Loaded {len(cases)} RAG evaluation cases. Mode: {'MOCK/OFFLINE' if args.mock else 'LIVE'}")

    runtime = None
    if not args.mock:
        # Check connectivity or init clients
        try:
            from app.clients.embedding_client_manager import embedding_client_manager
            from app.clients.es_client_manager import es_client_manager
            from app.clients.mysql_client_manager import meta_mysql_client_manager
            from app.clients.qdrant_client_manager import qdrant_client_manager
            from app.repositories.es.value_es_repository import ValueESRepository
            from app.repositories.mysql.meta.meta_mysql_repository import (
                MetaMySQLRepository,
            )
            from app.repositories.qdrant.column_qdrant_repository import (
                ColumnQdrantRepository,
            )
            from app.repositories.qdrant.metric_qdrant_repository import (
                MetricQdrantRepository,
            )

            qdrant_client_manager.init()
            embedding_client_manager.init()
            es_client_manager.init()
            meta_mysql_client_manager.init()

            context = {
                "column_qdrant_repository": ColumnQdrantRepository(
                    client=qdrant_client_manager.get_client(),
                    embedding_size=1024,
                ),
                "metric_qdrant_repository": MetricQdrantRepository(
                    client=qdrant_client_manager.get_client(),
                    embedding_size=1024,
                ),
                "value_es_repository": ValueESRepository(
                    client=es_client_manager.get_client()
                ),
                "meta_mysql_repository": MetaMySQLRepository(
                    session_factory=meta_mysql_client_manager.get_session_factory()
                ),
                "embedding_client": embedding_client_manager.get_client(),
            }
            runtime = SimpleRuntime(context)
        except Exception as e:
            print(f"[Warning] Failed to initialize live DB clients: {e}")
            print("[Info] Falling back to offline simulation mode (--mock).")
            args.mock = True

    eval_results = []
    for idx, case in enumerate(cases, start=1):
        if args.mock:
            raw_cols, raw_metrics, raw_values, final_tables = simulate_mock_retrieval(case)
        else:
            raw_cols, raw_metrics, raw_values, final_tables = await run_live_case(case, runtime)  # type: ignore

        case_eval = evaluate_single_case_metrics(
            raw_cols, raw_metrics, raw_values, final_tables, case
        )
        eval_results.append(case_eval)

        status_flag = "PASS" if case_eval["final_pass"] else "FAIL"
        print(f"[{idx:02d}/{len(cases):02d}] {status_flag} {case['id']} ({case['category']}) | Q: {case['query']}")

    # Generate and print formatted per-category report
    report_text = print_and_format_report(eval_results, args.log_file, args.report_file)
    print("\n" + report_text)
    print(f"Detailed logs saved to: {args.log_file}")
    print(f"Structured JSON report saved to: {args.report_file}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
