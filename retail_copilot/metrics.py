"""Business definitions, query plan contract, and deterministic SQL."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MetricName = Literal["net_sales", "average_order_value", "repeat_purchase_rate"]
Grain = Literal["total", "country", "month"]
Country = Literal["FR", "GB", "US"]

METRICS = {
    "net_sales": (
        "USD",
        "按事件发生日期汇总，销售为正，退款为负，取消为零。匿名订单计入。",
        "Sum sales less refunds by event date. Exclude cancellations and include guest orders.",
    ),
    "average_order_value": (
        "USD",
        "期间净销售额除以期间有销售事件的不同订单数。退款按发生日冲减，匿名订单计入。",
        "Divide period net sales by distinct orders with a sale in the period. Include guest orders.",
    ),
    "repeat_purchase_rate": (
        "ratio",
        "期间至少下两笔不同订单的已识别客户数，除以期间至少下一笔订单的已识别客户数。",
        "Share of identified purchasing customers with at least two distinct sale orders in the period.",
    ),
}


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: MetricName
    grain: Grain
    start_date: date
    end_date: date
    country: Country | None

    @model_validator(mode="after")
    def check_window(self):
        if self.start_date >= self.end_date:
            raise ValueError("start_date must precede end_date")
        return self


class Clarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clarification: str = Field(min_length=1, max_length=400)


def compile_query(plan: QueryPlan) -> tuple[str, list[date | str]]:
    group = {
        "total": "",
        "country": "country_code AS country, ",
        "month": "strftime(event_date, '%Y-%m') AS month, ",
    }[plan.grain]
    params: list[date | str] = [plan.start_date, plan.end_date]
    country_filter = ""
    if plan.country:
        country_filter = "\n      AND country_code = ?"
        params.append(plan.country)

    base = (
        f"WITH filtered AS (\n"
        f"    SELECT {group}order_id, customer_id, event_type, net_amount_usd\n"
        f"    FROM main.fct_order_lines\n"
        f"    WHERE event_date >= ? AND event_date < ?{country_filter}\n"
        f")"
    )
    group_col = f"{plan.grain}, " if group else ""
    grouping = f"\nGROUP BY {plan.grain}\nORDER BY {plan.grain}" if group else ""

    if plan.metric == "repeat_purchase_rate":
        sales_by_customer = (
            ", purchases AS (\n"
            f"    SELECT {group_col}customer_id, count(DISTINCT order_id) AS orders\n"
            "    FROM filtered\n"
            "    WHERE event_type = 'sale' AND customer_id IS NOT NULL\n"
            f"    GROUP BY {group_col}customer_id\n"
            ")"
        )
        sql = (
            f"{base}{sales_by_customer}\nSELECT {group_col}"
            "round(count(*) FILTER (WHERE orders >= 2)::DOUBLE / nullif(count(*), 0), 4) AS value\n"
            f"FROM purchases{grouping}"
        )
        return sql, params

    amount = "coalesce(sum(net_amount_usd), 0)"
    if plan.metric == "average_order_value":
        amount += " / nullif(count(DISTINCT order_id) FILTER (WHERE event_type = 'sale'), 0)"
    sql = (
        f"{base}\nSELECT {group_col}round({amount}, 2)::DOUBLE AS value\n"
        f"FROM filtered{grouping}"
    )
    return sql, params
