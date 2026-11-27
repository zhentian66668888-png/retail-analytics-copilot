# Retail Analytics Copilot

电商 AI 数据分析助手。用中文或英文问净销售额、客单价、复购率，API 返回指标口径、查询计划、带占位符的 SQL、绑定参数和结果。

> 问「上季度各国家的净销售额是多少？」示例数据的答案为法国 **150.00 USD**、英国 **285.00 USD**、美国 **330.00 USD**。示例日期锚点固定为 **2025-07-15**，所以上季度为 2025 年第二季度。

## 快速运行

在项目根目录使用 **Python 3.12**。下面的 PowerShell 命令可直接启动无需 API Key 的规则演示。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m retail_copilot.build
.\.venv\Scripts\python.exe -m uvicorn retail_copilot.api:from_env --factory
```

在本机打开 [Swagger UI](http://127.0.0.1:8000/docs)，选择 `POST /ask` → `Try it out`。也可以在另一个 PowerShell 窗口发送请求。

```powershell
$body = @{ question = '上季度各国家的净销售额是多少？' } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/ask -Method Post `
  -ContentType 'application/json; charset=utf-8' -Body $body |
  ConvertTo-Json -Depth 8
```

试试 `2025-03 US 的客单价是多少？`（该期间只有退款、没有销售订单，结果为 `null`）或 `净销售额是多少？`（缺少时间范围，返回澄清问题）。`GET /health` 检查数仓是否可查询。macOS/Linux 使用 `.venv/bin/python` 代替 Windows 路径。

## 数仓与指标

```text
raw_order_events → stg_order_events → fct_order_lines
                       └───────────→ dim_customers
country_codes ─────────────────────→ dim_countries

问题 → 规则 / LLM 查询计划 → 校验 → 参数化 SQL → 只读 DuckDB → 口径和结果
```

数仓使用 31 行合成订单事件。事实表粒度为每个源事件行，主键是 `line_id`。一个订单可以有多条销售行及后续退款行。dbt 检查主键、维表关系、有效金额、同单属性一致性及退款关联。

| 指标 | 计算口径 |
| --- | --- |
| 净销售额 | 按事件发生日汇总。销售为正，退款为负，取消为零。所有金额使用 USD。 |
| 客单价 | 期间净销售额 ÷ 期间含销售事件的去重订单数。匿名订单计入，退款按发生日冲减，分母为零返回 `null`。 |
| 复购率 | 期间至少有两笔不同销售订单的已识别客户 ÷ 期间有销售订单的已识别客户。返回 0–1 小数，分母为零返回 `null`。 |

每次支持一个指标，可查汇总、按国家或按月分组，可过滤一个国家（FR、GB、US）。查询计划的起始日期包含、结束日期排除。用户明确输入两个日期时，两个日期都计入。数据来自 [合成样例 CSV](warehouse/seeds/raw_order_events.csv)。

## 接入 LLM

配置支持 Chat Completions JSON Schema 输出的服务。例如本地 Ollama 已运行并下载模型后，在启动 API 的 PowerShell 窗口设置变量。

```powershell
ollama pull qwen2.5:1.5b
$env:RETAIL_LLM_BASE_URL = 'http://127.0.0.1:11434/v1'
$env:RETAIL_LLM_MODEL = 'qwen2.5:1.5b'
.\.venv\Scripts\python.exe -m uvicorn retail_copilot.api:from_env --factory
```

服务要求鉴权时，给当前进程设置 `RETAIL_LLM_API_KEY`。应用解析显式分组、国家、日期及「上季度」等相对期间。LLM 接收问题与表结构、指标上下文，输出指标枚举。应用校验后从受控模板生成 SQL，并在禁用外部访问的只读连接执行。部署默认为单机演示，API 无用户鉴权。Ollama 接口说明见 [兼容性文档](https://docs.ollama.com/api/openai-compatibility)。

## 评测

评测集含 30 道中英文题，覆盖汇总、分组、日期边界、同义改写和应澄清的请求。成功答题同时核对指标、日期、分组、国家过滤和结果行。澄清题检查响应状态。报告记录逐题结果、p50 延迟及服务端返回的 token 统计。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m retail_copilot.evaluate --output var/rules-report.json
.\.venv\Scripts\python.exe -m retail_copilot.evaluate --mode llm --output var/llm-report.json
```

`--mode llm` 对同一个模型、题集和 SQL 编译器运行两次。第一组提供表结构和输出约束，第二组额外提供指标定义和业务别名。两组共用日期、过滤和分组解析，以及越界请求预检。评测始终使用固定样例日期。

### 本地实测

| 模式 | 全部样例 | 同义改写 | p50 延迟 | 模型 token |
| --- | ---: | ---: | ---: | ---: |
| 规则 | 26 / 30 | 0 / 4 | 40 ms | — |
| Qwen 1.5B + 表结构 | 30 / 30 | 4 / 4 | 882 ms | 3,697 |
| Qwen 1.5B + 表结构与业务口径 | 30 / 30 | 4 / 4 | 941 ms | 6,297 |

Qwen 通过 CPU 上的 llama.cpp b11865 运行，使用 Qwen2.5-1.5B-Instruct Q4_K_M。每组 24 次模型调用，6 道应澄清的题由共用预检处理。两组在这套小样例上准确率持平，额外口径增加约 70% token。评测使用项目自建的合成数据和回归题，结论限于这套样例。 [模型权重](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/blob/main/qwen2.5-1.5b-instruct-q4_k_m.gguf) · [LLM 逐题报告](reports/qwen-local.json) · [离线规则报告](reports/rules.json)

## 容器运行

```bash
docker build -t retail-analytics-copilot .
docker run --rm -p 127.0.0.1:8000:8000 retail-analytics-copilot
```

容器构建时运行 dbt，运行时使用非 root 用户读取生成的 DuckDB 文件。GitHub Actions 工作流覆盖离线测试与演示评测。架构参考 [WrenAI](https://github.com/Canner/WrenAI) 和 [Text-to-SQL Eval Harness](https://github.com/AnuttaraR/text-to-sql-eval-harness)。
