"""SQL security guard node."""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
from app.security.models import SecurityViolation


async def guard_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Validate and rewrite generated SQL before database validation."""

    writer = runtime.stream_writer
    step = "SQL安全校验"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        sql_guard = runtime.context["sql_guard"]
        security_context = runtime.context["security_context"]
        safe_sql = sql_guard.validate_and_rewrite(state["sql"], security_context)
        writer({"type": "progress", "step": step, "status": "success"})
        return {
            "sql": safe_sql,
            "validated_sql": safe_sql,
            "security_error": None,
        }
    except SecurityViolation as exc:
        logger.warning(f"SQL安全校验拒绝查询: {exc.code}")
        writer({"type": "progress", "step": step, "status": "error"})
        return {
            "security_error": exc.code,
            "error": exc.message,
        }
    except Exception as exc:
        logger.error(f"{step} failed: {exc}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
