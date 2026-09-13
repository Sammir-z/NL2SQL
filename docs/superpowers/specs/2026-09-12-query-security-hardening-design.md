# 电商问数安全增强设计

> 状态：设计阶段
>
> 范围：SQL 安全、SQL 修正重试、认证、数据权限、租户隔离和敏感字段脱敏
>
> 原则：最小修改、代码隔离、保持现有问数主链路和前端请求格式兼容

## 1. 背景与目标

当前系统通过 `POST /api/query` 接收自然语言问题，由 LangGraph 生成、校验并执行 SQL。现有流程缺少以下生产安全能力：

1. 查询使用的数据库账号权限过大。
2. 生成 SQL 主要依赖数据库 `EXPLAIN`，缺少 AST 级别的语句、表和字段控制。
3. SQL 修正后直接执行，没有重新进行安全校验和语法校验。
4. 缺少用户认证、数据权限、租户隔离和敏感字段脱敏。

本设计通过新增独立 `app/security` 层解决上述问题，不修改检索节点和 Prompt 主体。

## 2. 方案选择

### 2.1 推荐方案：安全策略层 + LangGraph 最小接入

认证、权限、SQL 校验和脱敏均由独立模块负责，Agent 只接收一次查询级 `SecurityContext`。

优点：

- 安全逻辑集中，便于单元测试和审计。
- 不把权限判断散落在各个 Agent 节点中。
- 现有 `/api/query` 请求体保持兼容。
- 后续可以把策略来源从 YAML 替换为数据库或权限服务。

### 2.2 不采用的方案

- 将安全判断全部写入 Prompt：无法作为强制安全边界。
- 在每个 SQL 节点分别实现权限判断：容易遗漏，重复代码多。
- 先引入独立网关或策略服务：对当前项目改动和部署成本过大。

## 3. 目标架构

```text
HTTP 请求
  -> Bearer Token 认证
  -> 构造 SecurityContext
  -> LangGraph 检索和 SQL 生成
  -> AST 安全校验与 SQL 重写
  -> 数据库 EXPLAIN 校验
  -> 失败时有限次数修正，并重新进入安全校验
  -> 只读数据库执行
  -> 查询结果脱敏
  -> SSE 返回
```

新增目录：

```text
app/security/
├── __init__.py
├── models.py       # Principal、SecurityContext 和策略结构
├── auth.py         # Bearer Token/JWT 认证
├── policy.py       # 用户、角色、租户和数据权限策略
├── sql_guard.py    # SQL AST 校验、权限检查和 SQL 重写
└── masking.py      # 查询结果脱敏
```

## 4. 核心数据结构

### 4.1 用户主体

```python
@dataclass
class Principal:
    user_id: str
    tenant_id: str
    roles: set[str]
    scopes: set[str]
```

`tenant_id` 只能从已验证 Token 中读取，不能从请求体中读取。

### 4.2 查询安全上下文

```python
@dataclass
class SecurityContext:
    principal: Principal
    allowed_tables: set[str]
    allowed_columns: dict[str, set[str]]
    row_filters: dict[str, dict[str, set[str]]]
    masked_columns: dict[str, str]
    max_result_rows: int
    query_timeout_ms: int
    max_sql_retries: int
```

该对象只在一次查询请求内使用，不写入全局状态。

## 5. 认证与 API 设计

### 5.1 认证方式

使用 FastAPI 依赖注入获取 Bearer Token，并验证 JWT。Token 至少包含：

```json
{
  "sub": "user-001",
  "tenant_id": "tenant-001",
  "roles": ["analyst"],
  "scopes": ["query"]
}
```

接口：

```python
async def get_current_principal(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
) -> Principal:
    ...
```

Token 无效返回 `401`，Token 有效但无 `query` 权限返回 `403`。

### 5.2 查询接口

保持现有接口和请求体不变：

```http
POST /api/query
Authorization: Bearer <JWT_TOKEN>
Content-Type: application/json
```

```json
{
  "query": "统计华北地区销售额"
}
```

内部调用由：

```python
query_service.query(query.query)
```

调整为：

```python
query_service.query(query.query, security_context)
```

不新增前端传入的 `tenant_id`、角色或权限字段，避免客户端伪造权限。

## 6. SQL 安全层设计

### 6.1 接口

```python
class SQLGuard:
    def validate_and_rewrite(
        self,
        sql: str,
        context: SecurityContext,
    ) -> str:
        ...
```

建议新增 `sqlglot` 作为 SQL AST 解析器。

### 6.2 校验算法

1. 解析 SQL，解析失败立即拒绝。
2. 只允许单条 `SELECT` 查询。
3. 拒绝 DDL、DML、多语句和危险语法。
4. 检查引用的表和字段是否在 `SecurityContext` 允许范围内。
5. 根据 `row_filters` 自动增加租户或组织范围条件。
6. 没有 `LIMIT` 时补充最大行数；已有 `LIMIT` 超限时压缩到上限。
7. 返回安全重写后的 SQL，后续只允许执行该 SQL。

安全违规不交给大模型修正，直接终止当前查询并返回统一错误码。

### 6.3 只读账号

查询服务使用单独的只读账号：

```sql
CREATE USER 'shopkeeper_ro' IDENTIFIED BY '...';
GRANT SELECT, SHOW VIEW ON dw.* TO 'shopkeeper_ro';
```

元数据构建脚本使用独立的构建账号，不能复用查询账号。

数据库密码从 `conf/app_config.yaml` 移到环境变量；运行时 `db_dw` 只使用只读账号。

## 7. SQL 修正与重试设计

### 7.1 状态字段

在 `DataAgentState` 中增加：

```python
validation_attempt: int
security_error: str | None
```

`max_sql_retries` 来自 `SecurityContext`。

### 7.2 Graph 流程

```text
generate_sql
  -> guard_sql
  -> validate_sql
      ├─ 成功 -> run_sql
      ├─ 失败且未超过次数 -> correct_sql
      └─ 安全违规或超过次数 -> fail_query

correct_sql -> guard_sql
```

修正后的 SQL 必须重新经过：

1. AST 安全校验；
2. 表、字段和租户过滤检查；
3. `EXPLAIN` 校验；
4. 成功后才允许执行。

默认最多修正 `2` 次，避免无限循环。

## 8. 查询执行与结果限制

调整数仓仓储接口：

```python
async def run(
    self,
    sql: str,
    timeout_ms: int,
    max_rows: int,
) -> list[dict]:
    ...
```

执行层负责：

- 使用只读连接；
- 设置数据库或客户端查询超时；
- 限制最大结果行数；
- 超时后回滚并终止请求；
- 不向客户端暴露原始 SQL 异常和数据库连接信息。

## 9. 数据权限与租户隔离

权限策略至少包含：

```python
allowed_tables: set[str]
allowed_columns: dict[str, set[str]]
row_filters: dict[str, dict[str, set[str]]]
```

例如：

```python
row_filters = {
    "fact_order": {
        "region_id": {"R001", "R004"}
    }
}
```

实现原则：

- 租户范围由 Token 映射到服务端策略。
- SQL AST 检查表和字段权限。
- SQL 执行前强制注入行过滤条件。
- 不允许用户通过自然语言或请求参数绕过租户范围。
- 无权限字段直接拒绝；允许查询但属于敏感字段时进入脱敏流程。

当前项目使用单一 `dw` 数据库，因此第一阶段采用“表/字段白名单 + AST 行过滤”的方式。后续如改为生产环境，可替换为数据库视图、独立 Schema 或原生行级权限方案。

## 10. 敏感字段脱敏

### 10.1 接口

```python
class ResultMasker:
    def mask(
        self,
        rows: list[dict],
        context: SecurityContext,
    ) -> list[dict]:
        ...
```

### 10.2 脱敏策略

```text
full     -> ******
partial  -> 李**
hash     -> 稳定哈希值
```

处理顺序：

```text
数据库结果 -> ResultMasker.mask() -> SSE result
```

脱敏发生在后端，前端不能决定是否脱敏。

## 11. 预计文件变更

### 新增文件

```text
app/security/__init__.py
app/security/models.py
app/security/auth.py
app/security/policy.py
app/security/sql_guard.py
app/security/masking.py
app/agent/nodes/guard_sql.py
app/agent/nodes/fail_query.py
tests/security/test_sql_guard.py
tests/security/test_auth.py
tests/security/test_masking.py
tests/agent/test_sql_retry.py
docker/mysql/permissions.sql
```

### 修改文件

```text
app/api/dependencies.py
app/api/routers/query_router.py
app/services/query_service.py
app/agent/context.py
app/agent/state.py
app/agent/graph.py
app/agent/nodes/validate_sql.py
app/agent/nodes/run_sql.py
app/repositories/mysql/dw/dw_mysql_repository.py
conf/app_config.yaml
pyproject.toml
```

不修改字段/指标/取值检索节点、Prompt 和前端请求格式。

## 12. 测试要求

实现前先编写失败测试，覆盖：

- 拒绝 `INSERT`、`UPDATE`、`DELETE`、`DROP`；
- 拒绝多条 SQL；
- 拒绝无权限表和字段；
- 自动添加租户行过滤；
- 自动限制最大结果行数；
- 修正 SQL 后重新进行安全和语法校验；
- 超过最大重试次数后终止；
- 无 Token 返回 `401`；
- 无权限返回 `403`；
- 敏感字段按策略脱敏；
- 查询超时返回统一错误。

## 13. 配置建议

```yaml
security:
  max_sql_retries: 2
  query_timeout_ms: 5000
  max_result_rows: 500
  jwt_algorithm: HS256
  jwt_secret: ${oc.env:JWT_SECRET}
```

生产环境不在 YAML 中保存明文密钥、数据库密码或用户权限明细。

## 14. 非目标

本阶段不实现以下内容：

- 独立认证中心或用户管理后台；
- 多租户数据库拆分；
- 复杂 RBAC 管理界面；
- Prompt 重写和检索策略重构；
- 前端页面重做；
- 独立网关或策略服务。

