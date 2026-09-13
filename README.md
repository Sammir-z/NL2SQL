# NL2SQL｜安全增强型电商问数 Agent

> 面向电商数仓的自然语言问数系统：把业务问题转换为可控、可审计、可流式返回的 SQL 查询。

本仓库是在上游电商问数项目基础上的独立安全增强实现。项目保留“元数据检索 + LangGraph 编排 + SQL 生成”的主链路，并将认证、数据权限、租户隔离、SQL 安全和结果脱敏纳入查询闭环。

![AI Agent](https://img.shields.io/badge/AI-Agent-00c853?style=flat)
![Python](https://img.shields.io/badge/Python-3.14-3776AB.svg?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-API-009688.svg?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-Workflow-1C3C3C.svg)

## 项目定位

系统服务于电商运营、销售和数据分析场景。用户只需输入自然语言问题，例如“统计华北地区本月销售额”，Agent 会检索相关元数据，生成 SQL，经过安全校验和数据库校验后执行，并通过 SSE 返回进度与结果。

核心目标不是让模型直接执行 SQL，而是让模型生成的 SQL 必须经过确定性安全层和权限策略约束。

## 核心设计

```text
自然语言问题
      ↓
关键词抽取 → 字段 / 指标 / 字段值三路召回
      ↓
结果合并 → 表与指标过滤 → 上下文补全 → SQL 生成
      ↓
JWT 认证 → 数据权限与租户策略
      ↓
SQLGuard(AST 白名单、只读、单语句、LIMIT、租户条件)
      ↓
EXPLAIN 校验 ──失败→ 有限次数修正 → 再次 SQL 安全校验
      ↓
只读数仓执行(超时/行数限制) → 敏感字段脱敏 → SSE 返回
```

### 1. 混合元数据检索

- MySQL 保存权威的表、字段、指标和数据库信息。
- Qdrant 负责字段和指标的语义召回。
- Elasticsearch 负责字段取值的全文检索。
- LangGraph 将抽取、召回、过滤、上下文补全、生成和执行拆成可观测节点。

### 2. SQL 安全执行闭环

- 数据库运行账号使用 `shopkeeper_ro`，只授予 `dw.*` 的 `SELECT` 和 `SHOW VIEW` 权限。
- 使用 `sqlglot` 解析 MySQL AST，只允许单条 `SELECT`。
- 拒绝 DDL、DML、多语句、`SELECT INTO` 和危险函数。
- 表名、字段名按白名单校验；系统自动注入当前租户的行级过滤条件。
- 固定查询超时和最大结果行数，避免慢查询和大结果集拖垮服务。
- SQL 经数据库 `EXPLAIN` 校验失败时，仅允许配置的最大次数重试修正；修正后的 SQL 必须重新经过安全层。
- 返回结果统一执行敏感字段脱敏；具备 `pii:read` scope 的用户才可按策略读取明文。

### 3. 认证与数据权限

请求必须携带 Bearer JWT。Token 至少包含 `sub`、`tenant_id` 和 `scopes`，服务端根据租户和 scope 构建请求级安全上下文，不信任客户端提交的租户或权限参数。

默认安全策略位于 [`conf/app_config.yaml`](conf/app_config.yaml)，包括允许访问的表/字段、租户过滤条件、脱敏策略、超时、最大行数和最大重试次数。

## 主要 API

### `POST /api/query`

请求体：

```json
{"query": "统计华北地区本月销售额"}
```

请求示例：

```bash
curl -N http://localhost:8000/api/query \
  -H "Authorization: Bearer <JWT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"query":"统计华北地区本月销售额"}'
```

响应为 `text/event-stream`，事件示例：

```text
data: {"type":"progress","step":"SQL安全校验","status":"success"}

data: {"type":"result","data":[...]}

data: {"type":"error","code":"table_forbidden","message":"查询未通过安全校验"}
```

危险 SQL 不会进入数据库。例如模型生成 `DROP TABLE fact_order`、`DELETE FROM fact_order`、多条 SQL 或访问未授权表时，`SQLGuard` 会直接拒绝并返回 SSE 错误事件；数据库不会收到该语句。若只是 SQL 语法错误，则会进入有限次“修正 → 安全校验 → EXPLAIN”闭环，超过次数后终止执行。

## 目录结构

```text
app/
├── agent/                 # LangGraph 状态、上下文和工作流节点
├── api/                   # FastAPI 路由、依赖和请求模型
├── clients/               # MySQL、Qdrant、Elasticsearch 等客户端
├── repositories/          # 元数据、数仓和检索仓储
├── services/              # 查询服务和元数据知识库服务
└── security/              # JWT、策略、AST SQLGuard、结果脱敏
conf/                      # 应用配置和安全白名单
docker/mysql/              # 元数据、数仓及只读权限初始化脚本
tests/                     # 安全层和 Agent 流程测试
```

## 快速开始

### 环境要求

- Python 3.14+
- Docker Compose
- 可用的 LLM API Key
- 本地运行 MySQL、Qdrant、Elasticsearch 和 Embedding 服务

### 安装依赖

```bash
uv sync --dev
Copy-Item .env.example .env
```

修改 `.env` 中的 LLM、JWT 和数据库密码；不要把真实密钥提交到 Git。首次部署前请替换 `docker/mysql/permissions.sql` 和示例配置中的占位密码，并确保应用只使用只读数仓账号执行查询。

### 启动基础设施

```bash
docker compose -f docker/docker-compose.yaml up -d
```

初始化元数据知识库后启动 API：

```bash
uv run python -m app.scripts.build_meta_knowledge
uv run uvicorn app.main:app --reload
```

### 测试与检查

```bash
uv run python -m pytest -q
uv run ruff check app tests
```

## 当前实现边界

当前版本已经覆盖查询入口所需的基础认证、数据权限、租户隔离、SQL 安全和脱敏能力，但仍建议在生产环境继续补充：

- 接入企业级 IdP、密钥轮换和 Token 吊销机制。
- 将静态白名单和租户策略迁移到统一策略中心。
- 增加 SQL 审计日志、调用链追踪、限流、缓存和自动化评测。
- 对多租户数据库、视图权限和不同 SQL 方言做专项验证。

## 上游参考与致谢

本项目的基础问数流程参考了：

- [didilili/shopkeeper-agent](https://github.com/didilili/shopkeeper-agent)
- [ai-agents-from-zero](https://github.com/didilili/ai-agents-from-zero) 教程中的[电商问数实战项目](https://github.com/didilili/ai-agents-from-zero/tree/main/%E5%AE%9E%E6%88%98%E9%A1%B9%E7%9B%AE-%E7%94%B5%E5%95%86%E9%97%AE%E6%95%B0)

上游项目重点展示 LangGraph、多路召回、SQL 生成和 FastAPI 流式接口；本仓库在此基础上围绕安全执行和企业数据隔离进行了独立设计与实现。感谢上游项目及其教程作者提供的学习基础。

## License

请遵循上游项目及依赖组件的许可证要求；本仓库主要用于学习、研究和工程实践。
