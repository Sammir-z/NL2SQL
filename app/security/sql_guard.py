"""AST-level SQL validation and tenant filter injection."""

import sqlglot
from sqlglot import exp

from app.security.models import SecurityContext, SecurityViolation


class SQLGuard:
    """Allow only bounded read-only queries within the request policy."""

    _BLOCKED_FUNCTIONS = {
        "benchmark",
        "get_lock",
        "load_file",
        "release_lock",
        "sleep",
        "sys_eval",
        "updatexml",
        "extractvalue",
    }

    def validate_and_rewrite(self, sql: str, context: SecurityContext) -> str:
        try:
            statements = sqlglot.parse(sql, read="mysql")
        except sqlglot.errors.ParseError as exc:
            raise SecurityViolation("invalid_sql", "SQL 解析失败") from exc

        if len(statements) != 1:
            raise SecurityViolation("multiple_statements", "仅允许执行单条 SQL")

        statement = statements[0]
        if not isinstance(statement, exp.Select):
            raise SecurityViolation("readonly_only", "仅允许执行 SELECT 查询")
        if statement.find(exp.Into) is not None:
            raise SecurityViolation("into_not_allowed", "不允许使用 SELECT INTO")
        for function in statement.find_all(exp.Anonymous):
            if function.name.lower() in self._BLOCKED_FUNCTIONS:
                raise SecurityViolation("function_forbidden", "查询包含危险函数")

        tables = list(statement.find_all(exp.Table))
        if not tables:
            raise SecurityViolation("table_required", "查询必须包含受控数据表")

        aliases = {
            table.alias_or_name.lower(): table.name.lower() for table in tables
        }
        table_names = {table.name.lower() for table in tables}
        unauthorized_tables = table_names - context.allowed_tables
        if unauthorized_tables:
            raise SecurityViolation("table_forbidden", "查询包含无权限的数据表")

        self._validate_columns(statement, aliases, table_names, context)
        self._inject_row_filters(statement, tables, context)
        self._apply_limit(statement, context.max_result_rows)

        return statement.sql(dialect="mysql")

    @staticmethod
    def _validate_columns(
        statement: exp.Select,
        aliases: dict[str, str],
        table_names: set[str],
        context: SecurityContext,
    ) -> None:
        for column in statement.find_all(exp.Column):
            if column.is_star:
                continue

            column_name = column.name.lower()
            qualified_table = column.table.lower() if column.table else None
            if qualified_table:
                table_name = aliases.get(qualified_table, qualified_table)
                allowed = context.allowed_columns.get(table_name, frozenset())
                if column_name not in allowed:
                    raise SecurityViolation("column_forbidden", "查询包含无权限的字段")
                continue

            if not any(
                column_name in context.allowed_columns.get(table_name, frozenset())
                for table_name in table_names
            ):
                raise SecurityViolation("column_forbidden", "查询包含无权限的字段")

        masked_columns = set(context.masked_columns)
        for alias in statement.find_all(exp.Alias):
            sensitive_columns = {
                column.name.lower() for column in alias.this.find_all(exp.Column)
            }
            if sensitive_columns & masked_columns:
                raise SecurityViolation(
                    "sensitive_alias_forbidden",
                    "敏感字段不允许通过别名绕过脱敏",
                )

    @staticmethod
    def _inject_row_filters(
        statement: exp.Select,
        tables: list[exp.Table],
        context: SecurityContext,
    ) -> None:
        predicate: exp.Expression | None = None
        for table in tables:
            table_name = table.name.lower()
            qualifier = table.alias_or_name
            for column, values in context.row_filters.get(table_name, {}).items():
                if not values:
                    raise SecurityViolation("tenant_forbidden", "租户没有可访问的数据范围")
                condition = exp.In(
                    this=exp.column(column, table=qualifier),
                    expressions=[exp.Literal.string(value) for value in sorted(values)],
                )
                predicate = condition if predicate is None else exp.and_(predicate, condition)

        if predicate is None:
            return

        where = statement.args.get("where")
        if where is None:
            statement.set("where", exp.Where(this=predicate))
        else:
            statement.set("where", exp.Where(this=exp.and_(where.this, predicate)))

    @staticmethod
    def _apply_limit(statement: exp.Select, max_rows: int) -> None:
        if max_rows <= 0:
            raise SecurityViolation("invalid_limit", "最大结果行数必须大于 0")

        limit = statement.args.get("limit")
        if limit is None:
            statement.limit(max_rows)
            return

        expression = limit.args.get("expression")
        if not isinstance(expression, exp.Literal) or not expression.is_int:
            raise SecurityViolation("invalid_limit", "LIMIT 必须是固定整数")
        if int(expression.this) > max_rows:
            statement.set(
                "limit", exp.Limit(expression=exp.Literal.number(max_rows))
            )
