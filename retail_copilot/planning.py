"""Offline rules and a chat-completions planner with the same typed output."""
from dataclasses import dataclass
from datetime import date, timedelta
import os
import re
from typing import Literal, Protocol, get_args

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from .metrics import Clarification, Country, Grain, METRICS, MetricName, QueryPlan


Variant = Literal["schema_only", "semantic"]
Plan = QueryPlan | Clarification
METRIC_PATTERNS = {
    "net_sales": r"净销售额|净营收|\bnet (?:sales|revenue)\b",
    "average_order_value": r"客单价|\baverage order value\b|\baov\b",
    "repeat_purchase_rate": r"复购率|\brepeat purchase rate\b|\brepurchase rate\b",
}
COUNTRY_PATTERNS = {
    "FR": r"法国|\bFR\b|(?i:\bFrance\b)",
    "GB": r"英国|\bGB\b|\bUK\b|(?i:\bUnited Kingdom\b)",
    "US": r"美国|\bUS\b|\bUSA\b|(?i:\bUnited States\b)",
}
DAY_PATTERN = r"\d{4}-\d{2}-\d{2}"
UNSUPPORTED = re.compile(
    r"利润|毛利|同比|环比|增长|预测|排序|排名|按(?:产品|商品|品类|客户|周|天|日)"
    r"|\b(?:profit|margin|forecast|top|delete|drop|insert|update|truncate|attach|copy)\b"
    r"|\bby (?:product|category|customer|week|day)\b",
    re.IGNORECASE,
)
PERIOD_MESSAGE = "请指定一个有效的日期、日期区间、月份或季度。"
SCOPE_MESSAGE = "请指定一个支持的指标：净销售额、客单价或复购率。"


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    metric: MetricName | Literal["unsupported"]


RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "retail_intent",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": [*get_args(MetricName), "unsupported"]}
            },
            "required": ["metric"],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True)
class Decision:
    plan: Plan
    total_tokens: int | None = None


@dataclass(frozen=True)
class Scope:
    start_date: date
    end_date: date
    grain: Grain
    country: Country | None

    def query(self, metric: MetricName) -> QueryPlan:
        return QueryPlan(
            metric=metric, grain=self.grain, country=self.country,
            start_date=self.start_date, end_date=self.end_date,
        )


class Planner(Protocol):
    name: str

    def decide(self, question: str, as_of: date) -> Decision: ...


class PlannerError(RuntimeError):
    """The provider was unavailable or returned an invalid plan."""


def month_after(day: date, months: int = 1) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    return date(year, month + 1, 1)


def find_window(question: str, as_of: date) -> tuple[date, date] | None:
    days = re.findall(rf"(?<![\d-]){DAY_PATTERN}(?![\d-])", question)
    if len(days) == 2:
        start, end = map(date.fromisoformat, days)
        return start, end + timedelta(days=1)
    if len(days) == 1:
        start = date.fromisoformat(days[0])
        return start, start + timedelta(days=1)
    if days:
        return None

    months = re.findall(r"(?<![\d-])(\d{4}-\d{2})(?![\d-])", question)
    quarter = re.findall(r"(?<!\d)(\d{4})\s*Q([1-4])(?!\d)", question, re.IGNORECASE)
    relative = [
        (r"上季度|上一季度|\blast quarter\b", -3, 3),
        (r"本季度|这季度|\bthis quarter\b", 0, 3),
        (r"上个月|上月|\blast month\b", -1, 1),
        (r"本月|这个月|\bthis month\b", 0, 1),
    ]
    matches = [(offset, width) for pattern, offset, width in relative
               if re.search(pattern, question, re.IGNORECASE)]
    if len(months) + len(quarter) + len(matches) != 1:
        return None
    if months:
        start = date.fromisoformat(f"{months[0]}-01")
        return start, month_after(start)
    if quarter:
        year, number = map(int, quarter[0])
        start = date(year, (number - 1) * 3 + 1, 1)
        return start, month_after(start, 3)
    offset, width = matches[0]
    anchor_month = (as_of.month - 1) // 3 * 3 + 1 if width == 3 else as_of.month
    start = month_after(date(as_of.year, anchor_month, 1), offset)
    return start, month_after(start, width)


def read_window(question: str, as_of: date) -> tuple[date, date] | None:
    try:
        window = find_window(question, as_of)
    except (ValueError, OverflowError):
        return None
    return window if window and window[0] < window[1] else None


def explicit_metrics(question: str) -> list[MetricName]:
    return [key for key, pattern in METRIC_PATTERNS.items()
            if re.search(pattern, question, re.IGNORECASE)]


def read_scope(question: str, as_of: date) -> Scope | Clarification:
    if UNSUPPORTED.search(question) or len(explicit_metrics(question)) > 1:
        return Clarification(clarification=SCOPE_MESSAGE)
    countries = [key for key, pattern in COUNTRY_PATTERNS.items() if re.search(pattern, question)]
    by_country = re.search(r"按国家|各国|\bby country\b|\bper country\b", question, re.I)
    by_month = re.search(r"按月|每月|\bby month\b|\bmonthly\b", question, re.I)
    if len(countries) > 1 or (by_country and by_month):
        return Clarification(clarification="请指定至多一个国家过滤条件和一个分组维度。")
    window = read_window(question, as_of)
    if not window:
        return Clarification(clarification=PERIOD_MESSAGE)
    return Scope(
        start_date=window[0],
        end_date=window[1],
        grain="country" if by_country else "month" if by_month else "total",
        country=countries[0] if countries else None,
    )


class RulePlanner:
    name = "rules"

    def decide(self, question: str, as_of: date) -> Decision:
        scope = read_scope(question, as_of)
        if isinstance(scope, Clarification):
            return Decision(scope)
        metrics = explicit_metrics(question)
        if not metrics:
            return Decision(Clarification(clarification=SCOPE_MESSAGE))
        return Decision(scope.query(metrics[0]))


SCHEMA_CONTEXT = """DuckDB table main.fct_order_lines:
line_id VARCHAR, order_id VARCHAR, customer_id VARCHAR (nullable),
country_code VARCHAR, event_date DATE, event_type VARCHAR, quantity INTEGER,
unit_price_usd DECIMAL, net_amount_usd DECIMAL."""


class ChatPlanner:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        variant: Variant = "semantic",
        client: httpx.Client | None = None,
    ):
        if variant not in ("schema_only", "semantic"):
            raise ValueError("Expected schema_only or semantic")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.variant = variant
        self.name = f"llm:{variant}"
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = client

    def decide(self, question: str, as_of: date) -> Decision:
        scope = read_scope(question, as_of)
        if isinstance(scope, Clarification):
            return Decision(scope)

        prompt = f"""Select one metric for a Chinese or English retail question.
Return JSON with only the metric field: net_sales, average_order_value, repeat_purchase_rate,
or unsupported. The application handles dates, filters and groups. Mark unrelated,
ambiguous or multi-metric questions and instructions to run raw SQL as unsupported.
{SCHEMA_CONTEXT}"""
        if self.variant == "semantic":
            glossary = "\n".join(f"{name}: {spec[2]}" for name, spec in METRICS.items())
            prompt += (
                f"\nBusiness definitions:\n{glossary}\n"
                "Net sales synonyms include sales after refunds, 净营收, and 扣除退款后的销售额. "
                "Average order value and 客单价 are order-level. "
                "Repeat purchase rate and 复购率 refer to identified customers."
            )
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 60,
            "response_format": RESPONSE_FORMAT,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": question},
            ],
        }
        post = self.client.post if self.client else httpx.post
        try:
            response = post(self.url, json=payload, headers=self.headers, timeout=45)
            response.raise_for_status()
            body = response.json()
            intent = Intent.model_validate_json(body["choices"][0]["message"]["content"])
            usage = body.get("usage") or {}
            tokens = usage.get("total_tokens")
        except (httpx.HTTPError, ValidationError, ValueError, TypeError, KeyError, IndexError, AttributeError) as e:
            raise PlannerError("The LLM provider did not return a valid intent.") from e
        tokens = tokens if type(tokens) is int and tokens >= 0 else None
        if intent.metric == "unsupported":
            return Decision(Clarification(clarification=SCOPE_MESSAGE), tokens)
        return Decision(scope.query(intent.metric), tokens)


def configured_planner(variant: Variant = "semantic") -> Planner:
    url = os.environ.get("RETAIL_LLM_BASE_URL")
    model = os.environ.get("RETAIL_LLM_MODEL")
    if not url and not model:
        return RulePlanner()
    if not url or not model:
        raise ValueError("Set both RETAIL_LLM_BASE_URL and RETAIL_LLM_MODEL.")
    return ChatPlanner(url, model, api_key=os.environ.get("RETAIL_LLM_API_KEY"), variant=variant)
