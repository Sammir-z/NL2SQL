# 从自然语言到安全 SQL：电商问数 NL2SQL Agent 项目实践

> GitHub 项目地址：
> https://github.com/Sammir-z/NL2SQL

## 一、项目背景

在真实企业中，业务人员经常需要查询销售额、订单量、商品销量、地区排名等数据，但大多数业务人员并不熟悉 SQL。

传统做法通常依赖数据分析师手动编写 SQL，存在以下问题：

- 需求响应速度慢；
- 不同人员对指标口径理解不一致；
- 数据库表结构复杂，字段含义不直观；
- 直接让大模型生成 SQL，容易出现字段幻觉、表选择错误和权限越界；
- 多租户和敏感数据场景下，错误 SQL 可能造成数据泄露。

因此，本项目构建了一个面向电商数仓的安全增强型 NL2SQL Agent，让用户可以通过自然语言完成数据查询，同时保证 SQL 经过权限、安全和执行校验。

## 二、项目核心出发点

本项目不是简单调用一次大模型接口生成 SQL，而是围绕真实企业数据分析场景，构建完整的：

```text
自然语言问题
→ 元数据检索
→ SQL 生成
→ SQL 安全校验
→ 数据库校验
→ SQL 自动修正
→ 只读执行
→ 结果脱敏
→ 流式返回
```

项目重点解决三个问题：

1. 大模型是否理解企业数据库中的表、字段和指标含义；
2. 生成的 SQL 是否符合当前用户的数据权限和租户范围；
3. SQL 执行失败后，系统能否自动修正并重新校验。

## 三、项目整体架构

```text
用户自然语言问题
        ↓
JWT 用户认证
        ↓
关键词抽取
        ↓
字段 / 指标 / 字段值混合召回
        ↓
候选表和指标过滤
        ↓
上下文补全
        ↓
SQL 生成
        ↓
SQLGuard AST 安全校验
        ↓
EXPLAIN 数据库校验
        ├── 校验失败 → Repair → 重新安全校验
        └── 校验成功 → 只读数仓执行
        ↓
敏感字段脱敏
        ↓
FastAPI + SSE 流式返回
```

## 四、核心技术设计

### 1. 元数据知识库与混合检索

项目使用 MySQL 保存表、字段、指标和字段取值等结构化元数据。

同时结合：

- Qdrant：字段和指标的向量检索；
- Elasticsearch：字段真实取值的全文检索；
- MySQL：保存权威元数据和数据库信息。

通过三路召回，系统可以将“销售额”“订单金额”“成交总额”等不同表达映射到真实数据库字段和业务指标。

### 2. LangGraph 多阶段 Agent 工作流

项目使用 LangGraph 对 Agent 进行流程编排，将复杂查询拆分为多个可观测节点：

- 关键词抽取；
- 字段信息召回；
- 指标信息召回；
- 字段值召回；
- 表和指标过滤；
- 上下文补全；
- SQL 生成；
- SQL 校验；
- SQL 修正；
- SQL 执行。

相比单次 Prompt 调用，这种设计更方便进行调试、重试、状态管理和错误定位。

### 3. SQL 安全与数据权限

为了避免模型生成危险 SQL，项目增加了独立的 SQL 安全层：

- 只允许执行单条 `SELECT`；
- 禁止 `INSERT`、`UPDATE`、`DELETE`、`DROP` 等 DDL/DML；
- 禁止多语句执行；
- 禁止 `SELECT INTO` 和危险函数；
- 使用表名和字段名白名单；
- 自动注入当前租户的行级过滤条件；
- 限制查询超时和最大返回行数；
- 使用只读数据库账号执行查询；
- 对客户名称、客户 ID 等敏感字段进行脱敏。

例如，模型生成以下 SQL 时：

```sql
DROP TABLE fact_order;
```

或者：

```sql
SELECT * FROM users;
```

系统会在 SQL 进入数据库之前直接拒绝，并通过 SSE 返回错误事件。

### 4. Generate → Guard → Validate → Repair → Execute 闭环

项目构建了 SQL 自校验闭环：

```text
Generate
→ Guard
→ Validate
→ Repair
→ Guard
→ Validate
→ Execute
```

如果 SQL 只是语法错误或字段错误，系统会将数据库 `EXPLAIN` 返回的错误信息反馈给模型，进行有限次数的自动修正。

修正后的 SQL 不会直接执行，而是必须重新通过 SQL 安全层和数据库校验，避免 Repair 过程引入新的安全问题。

## 五、主要 API

项目提供查询接口：

```http
POST /api/query
```

请求示例：

```bash
curl -N http://localhost:8000/api/query \
  -H "Authorization: Bearer <JWT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"query":"统计华北地区本月销售额"}'
```

接口使用 SSE 流式返回 Agent 执行进度：

```text
data: {"type":"progress","step":"SQL安全校验","status":"success"}

data: {"type":"result","data":[...]}

data: {"type":"error","code":"table_forbidden","message":"查询未通过安全校验"}
```

## 六、为什么说它是企业级项目型实践

本项目按照真实企业数据应用的研发思路进行设计，具备以下工程特征：

- 分层架构：API、Service、Repository、Agent、Security；
- 依赖注入和运行时上下文管理；
- 多数据源协同：MySQL、Qdrant、Elasticsearch；
- 请求级用户认证和租户安全上下文；
- SQL AST 安全校验；
- 只读数据库权限；
- 查询超时和结果限制；
- 自动修正和重试机制；
- 敏感字段脱敏；
- 单元测试和评测数据集；
- FastAPI SSE 流式接口。

需要说明的是，当前版本是可本地运行的工程实践版本，已经覆盖查询链路中的基础企业级安全能力，但企业生产环境还需要继续接入统一身份认证、策略中心、审计平台、监控告警和链路追踪系统。

## 七、评测设计

项目补充了 20 条固定测试问题和对应的 Gold SQL，覆盖：

- 聚合查询；
- 时间筛选；
- 地区分析；
- 多表关联；
- Top-N 查询；
- 商品和客户维度分析；
- 租户数据权限。

评测运行器会：

1. 调用真实 `/api/query`；
2. 获取 Agent 最终返回结果；
3. 使用只读数据库执行 Gold SQL；
4. 对比两者结果；
5. 输出 SQL Execution Accuracy、执行成功率和 Repair 次数。

运行命令：

```bash
uv run python evaluation/run_eval.py --limit 3
```

当前已完成 3 条真实端到端试跑，结果为：

```text
SQL Execution Accuracy: 3/3
Execution Success: 3/3
Repair Invocations: 0
```

由于样本量较小，该结果暂时只用于验证运行链路，不作为最终模型效果结论。

## 八、适合哪些人学习

这个项目适合以下方向的同学：

- 想学习 LangGraph Agent 工作流；
- 想了解 NL2SQL 的完整实现；
- 想学习 RAG 和混合检索；
- 想做大模型与数据库结合的项目；
- 想了解 SQL 安全和数据权限；
- 想准备 Agent、后端或大模型应用开发面试；
- 想寻找可以深入扩展的企业级项目案例。

## 九、项目运行

项目依赖：

- Python 3.14+
- MySQL
- Qdrant
- Elasticsearch
- Embedding 服务
- LLM API

安装依赖：

```bash
uv sync --dev
Copy-Item .env.example .env
```

启动基础设施：

```bash
docker compose --env-file .env -f docker/docker-compose.yaml up -d
```

启动 API：

```bash
uv run uvicorn main:app --reload
```

## 十、项目地址与参考

GitHub：

https://github.com/Sammir-z/NL2SQL

本项目的基础问数流程参考了：

- [didilili/shopkeeper-agent](https://github.com/didilili/shopkeeper-agent)
- [ai-agents-from-zero](https://github.com/didilili/ai-agents-from-zero)

上游项目主要展示了 LangGraph、多路召回、SQL 生成和 FastAPI 流式接口。本项目在此基础上，进一步加入了 JWT 认证、数据权限、租户隔离、AST SQL 白名单、只读执行、SQL 修正重试和结果脱敏等安全能力。
