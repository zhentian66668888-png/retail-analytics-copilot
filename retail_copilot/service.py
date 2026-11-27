"""Shared question-to-result path for the API and evaluator."""
from datetime import date
from pathlib import Path

import duckdb

from .metrics import Clarification, METRICS, compile_query
from .planning import Planner


DEMO_AS_OF = date(2025, 7, 15)


def connect(path: Path):
    return duckdb.connect(str(path), read_only=True, config={"enable_external_access": "false"})


def answer(question: str, planner: Planner, path: Path, as_of: date) -> dict:
    decision = planner.decide(question, as_of)
    base = {"planner": planner.name, "as_of": as_of, "total_tokens": decision.total_tokens}
    if isinstance(decision.plan, Clarification):
        return {**base, "status": "clarification", "message": decision.plan.clarification}

    plan = decision.plan
    sql, parameters = compile_query(plan)
    with connect(path) as connection:
        cursor = connection.execute(sql, parameters)
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    unit, definition, _ = METRICS[plan.metric]
    return {
        **base,
        "status": "answered",
        "plan": plan.model_dump(mode="json"),
        "definition": definition,
        "unit": unit,
        "sql": sql,
        "parameters": [str(parameter) for parameter in parameters],
        "rows": rows,
    }
