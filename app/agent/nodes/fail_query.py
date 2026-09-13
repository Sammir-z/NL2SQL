"""Terminal query failure node."""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState


async def fail_query(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Emit a safe terminal error without executing SQL."""

    writer = runtime.stream_writer
    code = state.get("security_error") or "sql_validation_failed"
    message = (
        "查询未通过安全校验"
        if state.get("security_error")
        else "SQL 多次校验失败，未执行查询"
    )
    writer({"type": "error", "code": code, "message": message})
