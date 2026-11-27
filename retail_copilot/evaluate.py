"""Run the same ordered question set against each selected planner."""
import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
from statistics import median
from time import perf_counter

from .build import DEFAULT_DB, ROOT
from .planning import Planner, PlannerError, RulePlanner, configured_planner
from .service import DEMO_AS_OF, answer


QUESTIONS = ROOT / "eval" / "questions.jsonl"


def compare(expected: dict, actual: dict) -> str:
    if expected["status"] != actual["status"]:
        return "wrong_status"
    if expected["status"] == "clarification":
        return "correct"
    if expected["plan"] != actual.get("plan"):
        return "wrong_plan"
    if expected["rows"] != actual.get("rows"):
        return "wrong_result"
    return "correct"


def evaluate(planner: Planner, examples: list[dict], path: Path) -> dict:
    details = []
    counts = Counter()
    breakdown: dict[str, Counter] = {}
    for example in examples:
        start = perf_counter()
        try:
            actual = answer(example["question"], planner, path, DEMO_AS_OF)
            actual["as_of"] = actual["as_of"].isoformat()
            verdict = compare(example["expected"], actual)
        except PlannerError as e:
            actual = {"error": str(e)}
            verdict = "provider_error"
        elapsed_ms = round((perf_counter() - start) * 1000, 2)
        category = example["category"]
        details.append({**example, "actual": actual, "verdict": verdict, "latency_ms": elapsed_ms})
        counts[verdict] += 1
        breakdown.setdefault(category, Counter())[verdict] += 1

    tokens = [row["actual"].get("total_tokens") for row in details]
    reported_tokens = [value for value in tokens if value is not None]
    return {
        "planner": planner.name,
        "total": len(details),
        "accuracy": round(counts["correct"] / len(details), 4),
        "counts": dict(counts),
        "by_category": dict(breakdown),
        "median_latency_ms": round(median(row["latency_ms"] for row in details), 2),
        "token_reporting": {
            "requests_with_usage": len(reported_tokens),
            "total_tokens": sum(reported_tokens) if reported_tokens else None,
        },
        "examples": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["rules", "llm"], default="rules")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    path = Path(os.environ.get("RETAIL_DB_PATH", DEFAULT_DB))
    if not path.is_file():
        parser.error("Run python -m retail_copilot.build before evaluating.")

    planners: list[Planner] = [RulePlanner()]
    model = None
    if args.mode == "llm":
        model = os.environ.get("RETAIL_LLM_MODEL")
        if not model or not os.environ.get("RETAIL_LLM_BASE_URL"):
            parser.error("Set RETAIL_LLM_BASE_URL and RETAIL_LLM_MODEL for --mode llm.")
        planners = [configured_planner("schema_only"), configured_planner("semantic")]

    fixture = QUESTIONS.read_bytes()
    examples = [json.loads(line) for line in fixture.decode("utf-8").splitlines() if line.strip()]
    report = {
        "model": model,
        "as_of": DEMO_AS_OF.isoformat(),
        "questions_sha256": sha256(fixture).hexdigest(),
        "warehouse_sha256": sha256(path.read_bytes()).hexdigest(),
        "runs": [],
    }
    for planner in planners:
        run = evaluate(planner, examples, path)
        report["runs"].append(run)
        print(f'{planner.name}: {run["counts"].get("correct", 0)}/{run["total"]} correct, '
              f'p50 {run["median_latency_ms"]} ms, counts={run["counts"]}', flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return int(any(run["counts"].get("provider_error") == run["total"] for run in report["runs"]))


if __name__ == "__main__":
    raise SystemExit(main())
