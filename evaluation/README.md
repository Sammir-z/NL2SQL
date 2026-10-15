# NL2SQL 评测

`questions.json` 包含 20 条与 `docker/mysql/dw.sql` 样例数据匹配的问题和 `gold_sql`。运行器会调用真实的 `/api/query`，再用只读数仓执行 `gold_sql`，按结果值计算 SQL Execution Accuracy。

## 运行一次

先确保 `.env` 已配置真实 `LLM_API_KEY` 和数据库密码，并启动基础设施：

```bash
docker compose --env-file .env -f docker/docker-compose.yaml up -d
uv run python -m app.scripts.build_meta_knowledge
uv run uvicorn main:app --reload
```

另开终端执行完整评测：

```bash
uv run python evaluation/run_eval.py
```

先试跑 3 条：

```bash
uv run python evaluation/run_eval.py --limit 3
```

输出中的 `sql_execution_accuracy` 才是可以写入简历的实测值；`repair_invocations` 用于观察自动修正触发次数。当前运行器评估最终闭环结果，若要计算“从首次生成到最终结果”的提升，需要额外保存无 Repair 模式的对照结果。
