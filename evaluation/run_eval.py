"""Run the fixed NL2SQL evaluation set against the real API and DW."""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Support both `python -m evaluation.run_eval` and the README's direct script form.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.conf.app_config import app_config  # noqa: E402

DEFAULT_DATASET = Path(__file__).with_name("questions.json")


def normalize_value(value: Any) -> Any:
    """Normalize JSON/database scalar values for tolerant result comparison."""

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return _normalize_decimal(value)
    if isinstance(value, str):
        try:
            return _normalize_decimal(value)
        except InvalidOperation:
            return value.strip()
    return str(value)


def _normalize_decimal(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000001"))


def canonical_rows(rows: list[dict[str, Any]], order_sensitive: bool) -> list[tuple[Any, ...]]:
    """Compare result values while tolerating aliases and non-semantic row order."""

    normalized = [
        tuple(normalize_value(value) for value in row.values()) for row in rows
    ]
    if not order_sensitive:
        normalized.sort(key=repr)
    return normalized


def load_cases(path: Path = DEFAULT_DATASET) -> list[dict[str, Any]]:
    """Load the fixed questions and their gold SQL definitions."""

    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("evaluation dataset must be a non-empty JSON list")
    required = {"id", "question", "gold_sql", "order_sensitive"}
    for case in cases:
        if not required.issubset(case):
            raise ValueError(f"invalid evaluation case: {case}")
    return cases


def build_eval_token() -> str:
    """Create a local evaluation token for the configured demo tenant."""

    payload = {
        "sub": "eval-user",
        "tenant_id": "tenant-001",
        "roles": ["analyst"],
        "scopes": ["query"],
    }
    return jwt.encode(
        payload,
        app_config.security.jwt_secret,
        algorithm=app_config.security.jwt_algorithm,
    )


def _read_sse_response(response) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None, int]:
    result: list[dict[str, Any]] | None = None
    error: dict[str, Any] | None = None
    repair_count = 0

    for raw_line in response:
        line = raw_line.decode("utf-8").strip()
        if not line.startswith("data: "):
            continue
        event = json.loads(line[6:])
        if event.get("type") == "result":
            result = event.get("data")
        elif event.get("type") == "error":
            error = event
        elif (
            event.get("type") == "progress"
            and event.get("step") == "校正SQL"
            and event.get("status") == "running"
        ):
            repair_count += 1
    return result, error, repair_count


def call_query_api(api_url: str, token: str, question: str, timeout: int) -> tuple[
    list[dict[str, Any]] | None, dict[str, Any] | None, int
]:
    request = Request(
        api_url,
        data=json.dumps({"query": question}, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return _read_sse_response(response)
    except HTTPError as exc:
        raise RuntimeError(f"API returned HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}") from exc
    except URLError as exc:
        raise RuntimeError(f"cannot reach API {api_url}: {exc.reason}") from exc


def dw_url() -> str:
    config = app_config.db_dw
    return (
        f"mysql+asyncmy://{quote_plus(config.user)}:{quote_plus(config.password)}"
        f"@{config.host}:{config.port}/{config.database}?charset=utf8mb4"
    )


async def fetch_gold_rows(session: AsyncSession, sql: str) -> list[dict[str, Any]]:
    result = await session.execute(text(sql))
    return [dict(row) for row in result.mappings().all()]


async def run_evaluation(
    cases: list[dict[str, Any]], api_url: str, timeout: int, log_file: Path | None = None
) -> int:
    token = build_eval_token()
    engine = create_async_engine(dw_url(), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    correct = 0
    executed = 0
    repaired = 0

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as f:
            f.write("\n" + "=" * 80 + "\n")
            f.write(
                f"Evaluation Run Started: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            f.write(f"Target API: {api_url} | Total cases: {len(cases)}\n")
            f.write("=" * 80 + "\n")

    try:
        async with session_factory() as session:
            for index, case in enumerate(cases, start=1):
                gold_rows = await fetch_gold_rows(session, case["gold_sql"])
                actual_rows, error, repair_count = await asyncio.to_thread(
                    call_query_api, api_url, token, case["question"], timeout
                )
                repaired += repair_count
                is_executed = actual_rows is not None
                is_correct = is_executed and not error and canonical_rows(
                    actual_rows, case["order_sensitive"]
                ) == canonical_rows(gold_rows, case["order_sensitive"])
                executed += int(is_executed)
                correct += int(is_correct)
                status = "PASS" if is_correct else "FAIL"
                detail = f"error={error.get('code') if error else '-'}"
                log_line = (
                    f"[{index:02d}/{len(cases):02d}] {status:4} "
                    f"{case['id']} repairs={repair_count} {detail} | Q: {case['question']}"
                )
                print(log_line)
                if log_file:
                    with log_file.open("a", encoding="utf-8") as f:
                        f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {log_line}\n")
                        if not is_correct and error:
                            f.write(f"    Error detail: {error}\n")
    finally:
        await engine.dispose()

    total = len(cases)
    summary = (
        f"\n=== NL2SQL evaluation ===\n"
        f"questions={total}\n"
        f"execution_success={executed}/{total} ({executed / total:.1%})\n"
        f"sql_execution_accuracy={correct}/{total} ({correct / total:.1%})\n"
        f"repair_invocations={repaired}\n"
    )
    print(summary)
    if log_file:
        with log_file.open("a", encoding="utf-8") as f:
            f.write("-" * 80 + "\n")
            f.write(summary)
            f.write(
                f"Evaluation Run Finished at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            f.write("=" * 80 + "\n\n")

    return 0 if correct == total else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url", default="http://127.0.0.1:8000/api/query", help="query SSE endpoint"
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--timeout", type=int, default=180, help="per-query timeout in seconds")
    parser.add_argument("--limit", type=int, help="run only the first N cases")
    parser.add_argument(
        "--log-file",
        type=Path,
        default=Path("logs/evaluation_results.log"),
        help="path to log evaluation results",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = load_cases(args.dataset)
    if args.limit is not None:
        cases = cases[: args.limit]
    if not cases:
        raise SystemExit("no evaluation cases selected")
    return asyncio.run(run_evaluation(cases, args.api_url, args.timeout, args.log_file))


if __name__ == "__main__":
    raise SystemExit(main())
