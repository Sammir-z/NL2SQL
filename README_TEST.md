# Shopkeeper NL2SQL Agent 测试与评测指南

> 本文档规范了项目的自动化测试体系、端到端评测流程、日志持久化追踪规范及 Bad Case 归因排查机制。

---

## 目录

- [一、测试体系与分层架构](#一测试体系与分层架构)
- [二、自动化单元与安全层测试（离线即跑）](#二自动化单元与安全层测试离线即跑)
- [三、端到端真实业务评测（100 条全覆盖评测集）](#三端到端真实业务评测100-条全覆盖评测集)
- [四、RAG 召回与拒答能力专项评测（80 条 Bad Case）](#四rag-召回与拒答能力专项评测80-条-bad-case)
- [五、测试与评测日志持久化规范](#五测试与评测日志持久化规范)
- [六、手动接口调试与安全防御验证](#六手动接口调试与安全防御验证)
- [七、Bad Case 分类、归因排查与回归闭环](#七bad-case-分类归因排查与回归闭环)

---

## 一、测试体系与分层架构

针对大模型与数仓结合的非确定性与高安全要求，本项目建立“单元/安全层 $\rightarrow$ 端到端闭环评测 $\rightarrow$ 手动/渗透调试”三层测试网：

```
                ┌──────────────────────────────────────────────┐
                │        Layer 3: 手动交互与安全渗透测试         │
                │     (前端可视化体验 / cURL 模拟注入探测)     │
                ├──────────────────────────────────────────────┤
                │      Layer 2: 100 条端到端执行准确率评测     │
                │   (真实 API 闭环 / 数仓数据对齐 / 自愈统计)    │
                ├──────────────────────────────────────────────┤
                │      Layer 1: 单元测试与确定性安全守门测试    │
                │  (AST 防御 / JWT 租户注入 / 结果脱敏 / 路由)   │
                └──────────────────────────────────────────────┘
```

---

## 二、自动化单元与安全层测试（离线即跑）

该测试套件**无外部网络或数据库依赖**，在本地终端即可直接运行，用于保障底层确定性安全机制的绝对稳固。

### 1. 运行命令

```bash
uv run pytest
```
或者指定精简输出模式：
```bash
uv run pytest -q
```

### 2. 覆盖的测试模块

| 测试文件 | 覆盖的核心机制与断言 |
| :--- | :--- |
| `tests/security/test_sql_guard.py` | 拦截 `DROP TABLE`、阻止 DML 写入、高危函数拦截、强制注入 `LIMIT 500`、多租户行级 `region_id IN (...)` 自动改写注入 |
| `tests/security/test_masking.py` | 敏感字段脱敏：`customer_name` 局部脱敏（如“张*”）、`customer_id` 哈希脱敏 |
| `tests/security/test_auth.py` | JWT 签名校验、Claims 提取、租户安全上下文构建、非法/过期 Token 拦截 |
| `tests/agent/test_sql_retry.py` | 状态机路由：EXPLAIN 失败进入有限重试，安全违规直接阻断禁止进入大模型修正 |
| `tests/evaluation/test_run_eval.py` | 评测器高容差比对函数 `canonical_rows` 与浮点数 Decimal 归一化对齐验证 |

---

## 三、端到端真实业务评测（100 条全覆盖评测集）

评测运行器会模拟真实客户端向 `/api/query` 发送业务问题，通过 SSE 消费 Agent 生成的 SQL 与执行结果，同时异步直连只读数仓（`dw`）执行黄金标准 SQL（`gold_sql`），按结果值计算 **SQL Execution Accuracy (EX)**。

### 1. 前置条件

1. 确保 `.env` 中已配置正确的 `LLM_API_KEY`、数据库密码与 JWT 密钥；
2. 启动本地基础组件（Docker）：
   ```bash
   docker compose --env-file .env -f docker/docker-compose.yaml up -d
   ```
3. 构建元数据知识库（写入 MySQL、Qdrant 向量库、Elasticsearch）：
   ```bash
   uv run python -m app.scripts.build_meta_knowledge
   ```
4. 启动后端服务：
   ```bash
   uv run uvicorn main:app --reload --port 8000
   ```

### 2. 运行评测套件

* **快速体验（仅跑前 3 条试水）**：
  ```bash
  uv run python evaluation/run_eval.py --dataset evaluation/questions_100.json --limit 3
  ```

* **运行 100 条全量自动化基准评测**：
  ```bash
  uv run python evaluation/run_eval.py --dataset evaluation/questions_100.json
  ```

* **可选参数说明**：
  * `--dataset <path>`：指定评测数据集（默认 `questions.json`，推荐 `evaluation/questions_100.json`）；
  * `--limit <N>`：仅执行前 N 条用例；
  * `--api-url <url>`：指定问数接口地址（默认 `http://127.0.0.1:8000/api/query`）；
  * `--timeout <sec>`：单条查询超时时间（默认 180 秒）；
  * `--log-file <path>`：日志输出路径（默认 `logs/evaluation_results.log`）。

### 3. 100 条测试集分层矩阵分布

测试集位于 [`evaluation/questions_100.json`](evaluation/questions_100.json)，覆盖 6 大难度等级与业务场景：

| 等级 | 题号范围 | 数量 | 类别 (category) | 重点考察方向 |
| :--- | :--- | :--- | :--- | :--- |
| **L1** | `q001 - q020` | 20 | `aggregation_basic` | 基础单表/时间/区域聚合，租户过滤注入 |
| **L2** | `q021 - q045` | 25 | `group_by_single_dim` | 单维度关联 (日期/省份/品类/客户/会员) 分组与排序 |
| **L3** | `q046 - q065` | 20 | `multi_dim_cross` | 星型模型 2~4 张维表交叉多维透视分析 |
| **L4** | `q066 - q080` | 15 | `top_n_ranking` | `ORDER BY + LIMIT` 排行榜，行序敏感性比对 |
| **L5** | `q081 - q090` | 10 | `complex_having_filter` | `HAVING` 阈值筛选、区间过滤、多义词实体消歧 |
| **L6** | `q091 - q100` | 10 | `security_and_tenant` | 跨租户探测、敏感数据脱敏、大结果集截断 |

---

## 四、RAG 召回与拒答能力专项评测（80 条 Bad Case）

针对 RAG 检索层（jieba分词 $\rightarrow$ LLM扩词 $\rightarrow$ Qdrant向量 + ES全文）及下游大模型过滤判定，项目提供专门的离线评测驱动与 80 条精细化 Bad Case 数据集。详细设计方案参见 [`docs/RAG_test/RAG_RETRIEVAL_TEST_DESIGN.md`](docs/RAG_test/RAG_RETRIEVAL_TEST_DESIGN.md)。

### 1. 核心评测机制与指标

* **双重评测粒度**：
  * **粒度 A（Raw Top-5）**：评测原始检索算法能力，包含 `Recall@5`、`Precision@5`、`MRR`、`HitRate@5`；
  * **粒度 B（Merged & Filtered）**：评测经主外键补齐与 Filter 大模型判定后的最终 Schema 完备性与拒答判定。
* **拒答能力指标（针对 18 条越界/虚构业务用例与 8 条注入攻击）**：
  * **正向拒答率（TRR）**：大模型成功识别无相关数据输出空集并触发拒答的比例（目标 $\ge 95\%$）；
  * **合法误拒率（FRR）**：正常业务提问被错误过滤清空的比例（目标 $\le 2\%$）。

### 2. 运行评测命令

* **离线/模拟模式快速自检（无需启动外部数据库）**：
  ```bash
  uv run python evaluation/run_rag_eval.py --mock
  ```
* **执行全量 80 条 RAG 真实离线评测**：
  ```bash
  uv run python evaluation/run_rag_eval.py
  ```
* **指定单项分类评测（如仅测越界拒答 C4）**：
  ```bash
  uv run python evaluation/run_rag_eval.py --category C4_out_of_domain
  ```

### 3. 80 条 Bad Case 分类与考察功能

测试集位于 [`docs/RAG_test/rag_eval_cases.json`](docs/RAG_test/rag_eval_cases.json)：

| 类别编码 | 类别名称 | 数量 | 核心考察功能 |
| :---: | :--- | :---: | :--- |
| **C1** | 行业口语与同义词映射 | 12条 | 测试 LLM 提示词扩词对行业口语（“大盘流水”、“客单价”）的指标对齐能力 |
| **C2** | 多义词与实体属性混淆 | 10条 | 测试“苹果”品牌 vs 品类、“美的”等词汇的多路并存召回与大模型消歧 |
| **C3** | 音近字/错别字与拼写容错 | 10条 | 测试同音字（“华倍” $\rightarrow$ “华北”、“戴深” $\rightarrow$ “戴森”）及大小写模糊容错 |
| **C4** | 越界与不存在实体探针 (拒答) | 18条 | **【核心拒答率测试】** 测试数仓无退货、无库存、无打卡等业务时的正向拒答 (TRR) |
| **C5** | 隐式时间与口语化表达 | 10条 | 测试“刚刚过去那个季度”、“年初首月”等非标相对时间的抽取与维表映射 |
| **C6** | 多实体复合长难句 (长依赖) | 12条 | 测试 4~5 张维表星型全关联下的防漏召能力 (Recall@5) |
| **C7** | 对抗注入与系统表探测 | 8条 | 测试越狱探测密码或 information_schema 等敏感元数据时的绝对拦截 |

评测将自动在控制台打印各分类的分面独立统计表格，并将详细日志保存至 `logs/RAG/rag_eval_results.log`，结构化报告保存至 `docs/RAG_test/rag_eval_report.json`。

---

## 五、测试与评测日志持久化规范

为确保每次测试与评测的过程可溯源、结果可审计，所有详细结果均会自动输出到 `logs/` 目录中：

### 1. 单元测试日志：`logs/test_pytest.log`
每次运行 `uv run pytest` 时，由 [`tests/conftest.py`](tests/conftest.py) 自动捕获：
* 每次测试启动的时间戳；
* 每个测试函数的执行状态（`PASSED` / `FAILED`）、节点 ID 与精确耗时；
* 失败用例的完整 Traceback 报错堆栈；
* 最终的测试退出状态码。

### 2. 端到端评测日志：`logs/evaluation_results.log`
每次运行 `evaluation/run_eval.py` 时自动追加记录：
* 评测启动时间、目标 API 地址与测试集规模；
* 每道题的详细执行判定、自愈修复次数（`repairs=N`）、自然语言问题；
* 执行失败用例的具体错误信息（`Error detail`）；
* 最终的统计指标（执行成功率 `execution_success`、SQL 执行准确率 `sql_execution_accuracy`、自愈触发总次数 `repair_invocations`）。

### 3. RAG 召回评测日志：`logs/RAG/rag_eval_results.log`
每次运行 `evaluation/run_rag_eval.py` 时自动输出：
* 各分类分面评估数据（Recall@5、Precision@5、MRR、TRR、FRR）；
* 导出的结构化 JSON 报告同步归档在 `docs/RAG_test/rag_eval_report.json`。

---

## 六、手动接口调试与安全防御验证

除了自动化评测，也可以使用 Python 脚本或 cURL 发起单次请求：

### 1. 正常业务查询请求
```bash
uv run python -c "
from evaluation.run_eval import build_eval_token, call_query_api
token = build_eval_token()
result, err, repairs = call_query_api('http://127.0.0.1:8000/api/query', token, '统计2025年1月华北地区的销售额', 60)
print('Execution Result:', result)
"
```

### 2. 模拟越权与恶意注入探测
验证 `SQLGuard` 的 AST 确定性防御：
```bash
uv run python -c "
from evaluation.run_eval import build_eval_token, call_query_api
token = build_eval_token()
result, err, repairs = call_query_api('http://127.0.0.1:8000/api/query', token, '删除订单表 DROP TABLE fact_order', 30)
print('Security Interception:', err)
"
```
**期望现象**：后端直接返回 `{"type":"error","code":"table_forbidden"}`，SQL 语句被 AST 解析器就地拦截，绝对不会发送到数仓执行。

---

## 七、Bad Case 分类、归因排查与回归闭环

当评测出现 `FAIL` 时，按照以下四步定位与归因：

1. **查看日志**：打开 `logs/evaluation_results.log` 查看失败题目 ID、错误代码及问题描述；
2. **排查节点**：
   * 若返回 `table_forbidden` 或 `column_forbidden` $\rightarrow$ 检查表/字段白名单及租户权限配置；
   * 若返回 `explain_failed` 且达到重试上限 $\rightarrow$ 检查元数据召回是否缺失，查看大模型修正生成的 SQL 语法；
   * 若返回成功但比对失败（Mismatch） $\rightarrow$ 对比生成的 SQL 与 `gold_sql` 的聚合粒度（是否漏写 `DISTINCT`、是否产生笛卡尔积）；
3. **沉淀回归集**：将新发现的 Bad Case 补充至 `evaluation/questions_100.json`，打上问题标签；
4. **CI 门禁守护**：在代码合并前运行全量评测，确保已修复的 Case 永不退化。
