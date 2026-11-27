"""HTTP API and interactive OpenAPI demo."""
from datetime import date
import os
from pathlib import Path

import duckdb
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .build import DEFAULT_DB
from .planning import Planner, PlannerError, configured_planner
from .service import DEMO_AS_OF, answer, connect


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500, examples=["What were net sales by country last quarter?"])

    @field_validator("question", mode="before")
    @classmethod
    def strip_whitespace(cls, value):
        return value.strip() if isinstance(value, str) else value


def create_app(path: Path, planner: Planner, as_of: date) -> FastAPI:
    app = FastAPI(title="Retail Analytics Copilot", version="1.0.0")

    @app.get("/health")
    def health():
        try:
            with connect(path) as connection:
                connection.execute("SELECT 1 FROM main.fct_order_lines LIMIT 1").fetchone()
        except duckdb.Error as e:
            raise HTTPException(503, "Warehouse unavailable. Run python -m retail_copilot.build.") from e
        return {"status": "ok", "planner": planner.name, "as_of": as_of}

    @app.post("/ask")
    def ask(request: Question):
        if not path.is_file():
            raise HTTPException(503, "Warehouse unavailable. Run python -m retail_copilot.build.")
        try:
            return answer(request.question, planner, path, as_of)
        except PlannerError as e:
            raise HTTPException(502, str(e)) from e
        except duckdb.Error as e:
            raise HTTPException(503, "Warehouse unavailable. Run python -m retail_copilot.build.") from e

    return app


def from_env() -> FastAPI:
    path = Path(os.environ.get("RETAIL_DB_PATH", DEFAULT_DB)).resolve()
    as_of = date.fromisoformat(os.environ.get("RETAIL_AS_OF", DEMO_AS_OF.isoformat()))
    return create_app(path, configured_planner(), as_of)
