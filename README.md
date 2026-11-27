# Retail Analytics Copilot

An e-commerce analytics assistant. Ask in English for net sales, average order value, or repeat purchase rate. The API returns the metric definition, the query plan, parameterized SQL, bound parameters, and the result.

> Ask "What were net sales by country last quarter?" On the sample data the answer is France **150.00 USD**, the United Kingdom **285.00 USD**, and the United States **330.00 USD**. The sample date anchor is fixed at **2025-07-15**, so last quarter is the second quarter of 2025.

## Quick start

From the project root, use **Python 3.12**. The PowerShell commands below start the rules demo, which does not need an API key.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m retail_copilot.build
.\.venv\Scripts\python.exe -m uvicorn retail_copilot.api:from_env --factory
```

Open [Swagger UI](http://127.0.0.1:8000/docs) locally, choose `POST /ask`, then `Try it out`. You can also send a request from another PowerShell window.

```powershell
$body = @{ question = 'What were net sales by country last quarter?' } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/ask -Method Post `
  -ContentType 'application/json; charset=utf-8' -Body $body |
  ConvertTo-Json -Depth 8
```

Try `What was the average order value for the US in 2025-03?` (that period has only refunds and no sale orders, so the result is `null`) or `What were net sales?` (no time range, so the API asks for clarification). `GET /health` checks whether the warehouse is queryable. On macOS/Linux, use `.venv/bin/python` instead of the Windows path.

## Warehouse and metrics

```text
raw_order_events → stg_order_events → fct_order_lines
                       └───────────→ dim_customers
country_codes ─────────────────────→ dim_countries

Question → rules / LLM query plan → validation → parameterized SQL → read-only DuckDB → definition and result
```

The warehouse uses 31 synthetic order events. The fact-table grain is one source event row, and the primary key is `line_id`. An order can have several sale lines and later refund lines. dbt checks the primary key, dimension relationships, valid amounts, stable attributes within an order, and refund linkage.

| Metric | Definition |
| --- | --- |
| Net sales | Summed by event date. Sales are positive, refunds are negative, and cancellations are zero. All amounts are in USD. |
| Average order value | Period net sales ÷ distinct orders with a sale in the period. Guest orders are included, refunds reduce the amount on the event date, and a zero denominator returns `null`. |
| Repeat purchase rate | Identified customers with at least two distinct sale orders in the period ÷ identified customers with a sale order in the period. Returned as a 0–1 decimal. A zero denominator returns `null`. |

Each request supports one metric. Results can be a total, grouped by country, or grouped by month, and can filter to one country (FR, GB, or US). In the query plan the start date is inclusive and the end date is exclusive. When the user states two explicit dates, both dates are included. The data is the [synthetic sample CSV](warehouse/seeds/raw_order_events.csv).

## Connect an LLM

Point the app at a service that supports Chat Completions with JSON Schema output. With local Ollama running and the model already pulled, set the variables in the PowerShell window that starts the API.

```powershell
ollama pull qwen2.5:1.5b
$env:RETAIL_LLM_BASE_URL = 'http://127.0.0.1:11434/v1'
$env:RETAIL_LLM_MODEL = 'qwen2.5:1.5b'
.\.venv\Scripts\python.exe -m uvicorn retail_copilot.api:from_env --factory
```

If the service requires authentication, set `RETAIL_LLM_API_KEY` for the current process. The application parses explicit groupings, countries, dates, and relative periods such as "last quarter." The LLM receives the question together with the table schema and metric context, and it returns a metric enum. After validation, the application builds SQL from a controlled template and runs it on a read-only connection with external access disabled. The default deployment is a single-machine demo, and the API does not authenticate users. See the [Ollama compatibility documentation](https://docs.ollama.com/api/openai-compatibility).

## Evaluation

The evaluation set has 30 English questions covering totals, groupings, date boundaries, paraphrases, and requests that should be clarified. A correct answer must match the metric, dates, grouping, country filter, and result rows. Clarification items check the response status. Reports record each question, p50 latency, and the token counts returned by the server.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m retail_copilot.evaluate --output var/rules-report.json
.\.venv\Scripts\python.exe -m retail_copilot.evaluate --mode llm --output var/llm-report.json
```

`--mode llm` runs the same model, question set, and SQL compiler twice. The first run supplies the table schema and output constraints. The second run also supplies metric definitions and business aliases. Both runs share date, filter, and grouping parsing, and the same pre-check for out-of-scope requests. Evaluation always uses the fixed sample date.

### Local results

| Mode | All examples | Paraphrases | p50 latency | Model tokens |
| --- | ---: | ---: | ---: | ---: |
| Rules | 26 / 30 | 0 / 4 | 40 ms | — |
| Qwen 1.5B + schema | 30 / 30 | 4 / 4 | 882 ms | 3,697 |
| Qwen 1.5B + schema and business definitions | 30 / 30 | 4 / 4 | 941 ms | 6,297 |

Qwen ran on CPU through llama.cpp b11865, using Qwen2.5-1.5B-Instruct Q4_K_M. Each run made 24 model calls; the 6 questions that should be clarified were handled by the shared pre-check. On this small sample the two runs tied on accuracy, and the extra definitions increased tokens by about 70%. The evaluation uses this project's synthetic data and regression questions, so the conclusion applies only to this sample. The linked reports are archived from that original run and the original question set. They were not regenerated for these English questions. [Model weights](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/blob/main/qwen2.5-1.5b-instruct-q4_k_m.gguf) · [Per-question LLM report](reports/qwen-local.json) · [Offline rules report](reports/rules.json)

## Run in a container

```bash
docker build -t retail-analytics-copilot .
docker run --rm -p 127.0.0.1:8000:8000 retail-analytics-copilot
```

The image runs dbt during the build. At runtime a non-root user reads the generated DuckDB file. The GitHub Actions workflow covers the offline tests and the demo evaluation. The design follows [WrenAI](https://github.com/Canner/WrenAI) and [Text-to-SQL Eval Harness](https://github.com/AnuttaraR/text-to-sql-eval-harness).
