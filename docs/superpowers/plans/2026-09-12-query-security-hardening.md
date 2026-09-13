# Query Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a minimal, isolated security layer for SQL safety, bounded SQL correction, authentication, tenant/data policy enforcement, and result masking.

**Architecture:** Add pure security modules under `app/security`, then inject one request-scoped `SecurityContext` into the existing query service and LangGraph context. Add a `guard_sql` node before database validation and route correction back through the guard; mask rows immediately before the SSE result event.

**Tech Stack:** FastAPI dependencies, PyJWT, SQLGlot, SQLAlchemy async sessions, LangGraph, pytest.

**Spec:** `docs/superpowers/specs/2026-09-12-query-security-hardening-design.md`

## Global Constraints

- Keep the existing `POST /api/query` request body unchanged.
- Do not modify retrieval nodes, prompts, or the frontend request format.
- SQL execution must use a read-only DW account configuration.
- Security violations must not be sent back to the LLM for correction.
- Corrected SQL must return to AST and database validation before execution.
- User requested implementation before testing; tests are added and run after implementation.

---

### Task 1: Add security models, policy, JWT authentication, SQL guard, and masking

**Files:**
- Create: `app/security/__init__.py`
- Create: `app/security/models.py`
- Create: `app/security/auth.py`
- Create: `app/security/policy.py`
- Create: `app/security/sql_guard.py`
- Create: `app/security/masking.py`
- Modify: `app/conf/app_config.py`
- Modify: `conf/app_config.yaml`
- Modify: `.env.example`
- Modify: `pyproject.toml`

**Interfaces:**
- `Principal`, `SecurityContext`, and `SecurityViolation` are the shared security types.
- `get_current_principal()` validates a Bearer JWT and returns `Principal`.
- `build_security_context(principal)` returns the request policy and limits.
- `SQLGuard.validate_and_rewrite(sql, context)` returns safe SQL or raises `SecurityViolation`.
- `ResultMasker.mask(rows, context)` returns sanitized rows.

- [x] Implement the data classes and default policy values.
- [x] Implement JWT decoding with required `sub`, `tenant_id`, and `scopes` claims.
- [x] Implement SQLGlot parsing, single-SELECT enforcement, table/column allowlists, row-filter injection, and LIMIT clamping.
- [x] Implement full, partial, and hash result masking.
- [x] Add security configuration and environment variable placeholders.
- [x] Add `sqlglot`, `PyJWT`, and `pytest` dependency declarations.

### Task 2: Wire authentication and request security context into the API and Agent

**Files:**
- Modify: `app/api/dependencies.py`
- Modify: `app/api/routers/query_router.py`
- Modify: `app/services/query_service.py`
- Modify: `app/agent/context.py`
- Modify: `app/agent/state.py`

**Interfaces:**
- `/api/query` receives `principal: Principal = Depends(get_current_principal)`.
- `QueryService.query(query, security_context)` passes the context to LangGraph.
- `DataAgentContext.security_context` is available to all SQL-related nodes.
- `DataAgentState` adds `validation_attempt`, `security_error`, and `validated_sql`.

- [x] Add FastAPI dependencies for principal and security context.
- [x] Keep the JSON body unchanged while requiring authentication.
- [x] Pass security context through `QueryService` and `DataAgentContext`.
- [x] Add retry and terminal-error state fields with safe defaults.

### Task 3: Add SQL guard node, bounded correction loop, execution limits, and masking

**Files:**
- Create: `app/agent/nodes/guard_sql.py`
- Create: `app/agent/nodes/fail_query.py`
- Modify: `app/agent/graph.py`
- Modify: `app/agent/nodes/validate_sql.py`
- Modify: `app/agent/nodes/correct_sql.py`
- Modify: `app/agent/nodes/run_sql.py`
- Modify: `app/repositories/mysql/dw/dw_mysql_repository.py`

**Interfaces:**
- `guard_sql(state, runtime)` writes the rewritten safe SQL or a terminal security error.
- `fail_query(state, runtime)` emits one SSE `error` event without executing SQL.
- `DWMySQLRepository.run(sql, timeout_ms, max_rows)` enforces execution bounds.

- [x] Insert `guard_sql` between SQL generation/correction and database validation.
- [x] Route valid SQL to execution, ordinary validation errors to correction until the configured retry limit, and security/final failures to `fail_query`.
- [x] Route `correct_sql` back to `guard_sql`, never directly to `run_sql`.
- [x] Pass timeout and row limits to validation/execution repository methods.
- [x] Apply `ResultMasker` before emitting the SSE `result` event.

### Task 4: Add read-only database grant script

**Files:**
- Create: `docker/mysql/permissions.sql`

**Interfaces:**
- The script creates `shopkeeper_ro` with only `SELECT` and `SHOW VIEW` on `dw.*`.

- [x] Add idempotent read-only user creation and grants.
- [x] Keep metadata/build credentials separate from runtime query credentials.

### Task 5: Add tests after implementation

**Files:**
- Create: `tests/security/test_sql_guard.py`
- Create: `tests/security/test_auth.py`
- Create: `tests/security/test_masking.py`
- Create: `tests/agent/test_sql_retry.py`

- [x] Test DDL, DML, multi-statement, unauthorized table/column, row filter, and LIMIT behavior.
- [x] Test JWT missing/invalid/valid claims and 401 behavior.
- [x] Test full, partial, and hash masking.
- [x] Test correction returns through guard/validation and stops at the retry limit.
- [x] Run the focused tests, then the complete pytest suite and Ruff checks.
