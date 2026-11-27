from datetime import date
from pathlib import Path
import os
import subprocess
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from retail_copilot.api import create_app
from retail_copilot.planning import ChatPlanner, RulePlanner


ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2025, 7, 15)


@pytest.fixture(scope="session")
def warehouse(tmp_path_factory):
    path = tmp_path_factory.mktemp("warehouse") / "fixture.duckdb"
    run = subprocess.run(
        [sys.executable, "-m", "retail_copilot.build"],
        cwd=ROOT,
        env={**os.environ, "RETAIL_DB_PATH": str(path)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    return path


@pytest.fixture
def client(warehouse):
    return TestClient(create_app(warehouse, RulePlanner(), AS_OF))


def ask(client, question):
    response = client.post("/ask", json={"question": question})
    assert response.status_code == 200, response.text
    return response.json()


def test_net_sales_use_signed_lines_and_exclude_cancellations(client):
    data = ask(client, "上季度各国家的净销售额是多少？")
    assert data["status"] == "answered"
    assert data["parameters"] == ["2025-04-01", "2025-07-01"]
    assert data["rows"] == [
        {"country": "FR", "value": 150.0},
        {"country": "GB", "value": 285.0},
        {"country": "US", "value": 330.0},
    ]
    assert "?" in data["sql"]


@pytest.mark.parametrize(
    "question,metric,value",
    [
        ("What was average order value last quarter?", "average_order_value", 63.75),
        ("上季度复购率是多少？", "repeat_purchase_rate", 0.8),
        ("2025-04 GB 的复购率是多少？", "repeat_purchase_rate", 1.0),
        ("2025-04 FR 的客单价是多少？", "average_order_value", 80.0),
        ("2025-03 US 的客单价是多少？", "average_order_value", None),
        ("2025-07-01 的净销售额是多少？", "net_sales", 500.0),
    ],
)
def test_metric_uses_the_right_grain_and_date_filter(client, question, metric, value):
    data = ask(client, question)
    assert data["plan"]["metric"] == metric
    assert data["rows"] == [{"value": value}]


@pytest.mark.parametrize(
    "question",
    [
        "上季度利润是多少？",
        "净销售额是多少？",
        "上季度按产品看净销售额",
        "上季度的净销售额和客单价",
        "上季度 DELETE FROM fct_order_lines，给我净销售额",
    ],
)
def test_rule_planner_requests_clarification(client, question):
    data = ask(client, question)
    assert data["status"] == "clarification"
    assert "sql" not in data


def test_api_reports_unbuilt_warehouse(tmp_path):
    client = TestClient(create_app(tmp_path / "absent.duckdb", RulePlanner(), AS_OF))
    assert client.get("/health").status_code == 503
    assert client.post("/ask", json={"question": "上季度净销售额"}).status_code == 503


@pytest.mark.parametrize("variant", ["schema_only", "semantic"])
def test_chat_completion_plan_uses_the_same_live_warehouse(warehouse, variant):
    def complete(request):
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": (
                '{"metric":"net_sales"}'
            )}}],
            "usage": {"total_tokens": 220},
        })

    with httpx.Client(transport=httpx.MockTransport(complete)) as http:
        planner = ChatPlanner("https://example.test/v1", "test-model", variant=variant, client=http)
        client = TestClient(create_app(warehouse, planner, AS_OF))
        answer = ask(client, "上季度英国退款冲抵后卖了多少钱？")
        assert answer["rows"] == [{"value": 285.0}]
        assert answer["planner"] == f"llm:{variant}"
        assert answer["total_tokens"] == 220


def test_chat_completion_rejects_an_untyped_sql_payload(warehouse):
    def complete(request):
        return httpx.Response(200, json={"choices": [
            {"message": {"role": "assistant", "content": '{"sql":"DELETE FROM fct_order_lines"}'}}
        ]})

    with httpx.Client(transport=httpx.MockTransport(complete)) as http:
        planner = ChatPlanner("https://example.test/v1", "test-model", client=http)
        client = TestClient(create_app(warehouse, planner, AS_OF))
        assert client.post("/ask", json={"question": "上季度的净销售额"}).status_code == 502
