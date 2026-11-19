import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from . import run_budget
from .paths import CSAT_2026_RESULTS
from .cost_pilot import exam_parts, summarize, write_json
from .gemini_cost_pilot import API, estimate, load_inputs
from .gemini_paid_resume import PAID, finish, prepare_body, submit


MODEL = "gemini-4-carbon"
EFFORT = "high"
INPUT_RATE = Decimal("2.00")
CACHE_RATE = Decimal("0.10")
OUTPUT_RATE = Decimal("10.00")
MAX_TOKENS = 262_144
NONSTREAMING_HOP_TOKENS = 32_768
DIRECTORY = CSAT_2026_RESULTS / "carbon-high-sample"


def hydrogen_ranking(parts):
    reference_dir = PAID / "gemini-4-hydrogen"
    dollars = {}
    for part in parts:
        row = json.loads((reference_dir / f"{part.name}.json").read_text(encoding="utf-8"))
        if row["billing_tier"] == "free":
            cost = row["billing_price_equivalent_usd"]
        else:
            cost = row["estimated_usd"]
        dollars[part.name] = Decimal(str(cost))
    return sorted(parts, key=lambda part: dollars[part.name]), dollars


def carbon_cost(usage):
    if "input_tokens" not in usage:
        return None
    cached = usage["cached_tokens"]
    total = (usage["input_tokens"] - cached) * INPUT_RATE + cached * CACHE_RATE
    total += usage["output_tokens"] * OUTPUT_RATE
    return float(total / 1_000_000)


def reserve(input_count):
    # Long decode can repeat the prompt and the accumulated output at each hop.
    hop_input = sum(input_count + offset for offset in range(0, MAX_TOKENS, NONSTREAMING_HOP_TOKENS))
    return run_budget.estimate_cost(hop_input, MAX_TOKENS, INPUT_RATE, OUTPUT_RATE)


def project_costs(ordered, hydrogen_costs, samples):
    if len(samples) != 3 or any(row.get("estimated_usd") is None for row in samples):
        return None
    anchors_h = [hydrogen_costs[row["part"]] for row in samples]
    anchors_c = [Decimal(str(row["estimated_usd"])) for row in samples]
    projected = []
    for part in ordered:
        hydrogen_cost = hydrogen_costs[part.name]
        segment = int(hydrogen_cost > anchors_h[1])
        fraction = (hydrogen_cost - anchors_h[segment]) / (anchors_h[segment + 1] - anchors_h[segment])
        value = (1 - fraction) * anchors_c[segment] + fraction * anchors_c[segment + 1]
        projected.append({"part": part.name, "label": part.label, "estimated_usd": str(value)})
    rank_estimate = (anchors_c[0] + anchors_c[2]) * Decimal("6.5") + anchors_c[1] * 12
    return {
        "method": "piecewise linear interpolation by measured Hydrogen cost across 25 exam parts",
        "estimated_total_usd": str(sum(Decimal(row["estimated_usd"]) for row in projected)),
        "rank_weighted_total_usd": str(rank_estimate),
        "completed_samples": sum(row["status"] == "completed" for row in samples),
        "projected_parts": projected,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget-usd", type=run_budget.positive_usd, default=Decimal("11"))
    parser.add_argument("--key-stdin", action="store_true")
    args = parser.parse_args()
    api_key = sys.stdin.readline().strip() if args.key_stdin else os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        parser.error("A Gemini API key is required")
    headers = {"x-goog-api-key": api_key, "Api-Revision": "2026-05-20"}
    ordered, hydrogen_costs = hydrogen_ranking(exam_parts())
    selected = [ordered[0], ordered[len(ordered) // 2], ordered[-1]]
    inputs = load_inputs(selected)
    model_dir = DIRECTORY / MODEL

    def path_for(part):
        return model_dir / f"{part.name}.json"

    def run(part):
        path = path_for(part)
        pending = path.with_suffix(".pending")
        signature, content = inputs[part.name]
        body = prepare_body(part, content, MAX_TOKENS)
        body["model"] = MODEL
        body["generation_config"]["thinking_level"] = EFFORT
        started = time.monotonic()
        if pending.exists():
            interaction = json.loads(pending.read_text(encoding="utf-8"))
            result = finish(headers, body, interaction, started)
            row = record(part, signature, len(content) - 1, result)
            pending.unlink()
        else:
            with run_budget.record_submission(path):
                interaction, result = submit(headers, body, started)
                if result is None:
                    write_json(pending, {"id": interaction["id"], "status": interaction["status"]})
                    print(f"{part.name}: accepted", flush=True)
                    result = finish(headers, body, interaction, started)
                row = record(part, signature, len(content) - 1, result)
        print(f"{part.name}: {row['status']} {row['answered']}/{part.question_count}; "
              f"cost={row['estimated_usd']} reason={row.get('stop_reason')}", flush=True)
        return row

    def record(part, signature, image_count, result):
        row = {
            **result, "estimated_usd": carbon_cost(result.get("usage") or {}), "model": MODEL,
            "part": part.name, "subject": part.subject, "label": part.label, "first_question": part.first,
            "question_count": part.question_count, "effort": EFFORT, "max_tokens": MAX_TOKENS,
            "input_format": "image", "input_sha256": signature, "media_resolution": "ultra_high",
            "image_count": image_count, "hydrogen_cost_usd": str(hydrogen_costs[part.name]),
        }
        write_json(path_for(part), row)
        return row

    with run_budget.locked_output(DIRECTORY):
        model_dir.mkdir(exist_ok=True)
        saved = {}
        jobs = []
        total = Decimal(0)
        reference = json.loads((CSAT_2026_RESULTS / "hydrogen-xhigh" / "budget-plan.json").read_text(encoding="utf-8"))
        reference_counts = {row["part"]: row["input_tokens"] for row in reference["requests"]}
        with ThreadPoolExecutor(max_workers=3) as pool:
            measured = list(pool.map(lambda part: estimate(headers, inputs[part.name][1], MODEL), selected))
        plans = []
        for part, count_result in zip(selected, measured):
            count = max(count_result["input_tokens"], reference_counts[part.name])
            reservation = reserve(count)
            path = path_for(part)
            if path.exists():
                row = json.loads(path.read_text(encoding="utf-8"))
                if row["input_sha256"] != inputs[part.name][0] or row["effort"] != EFFORT:
                    raise ValueError(f"Existing result configuration differs for {part.name}")
                saved[part.name] = row
                total += Decimal(reservation["reserved_usd"] if row.get("billed_usage_unknown")
                                 else str(row["estimated_usd"] or 0))
            else:
                total += Decimal(reservation["reserved_usd"])
                if not path.with_suffix(".pending").exists() or path.with_suffix(".pending").stat().st_size:
                    jobs.append(part)
            plans.append({"part": part.name, "label": part.label, **reservation})
        write_json(DIRECTORY / "budget-plan.json", {"requests": plans, "planned_total_usd": str(total),
                                                   "budget_usd": str(args.budget_usd)})
        print(f"Carbon high plan: {[p.name for p in selected]}; maximum ${total:.4f}.", flush=True)
        if total > args.budget_usd:
            raise ValueError(f"Planned exposure exceeds ${args.budget_usd} budget")
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                for part, row in zip(jobs, pool.map(run, jobs)):
                    saved[part.name] = row
            projected = project_costs(ordered, hydrogen_costs, [saved[p.name] for p in selected if p.name in saved])
            if projected:
                write_json(DIRECTORY / "projection.json", projected)
                print(f"Full 25-part projection: ${Decimal(projected['estimated_total_usd']):.2f}.", flush=True)
        finally:
            summarize(DIRECTORY)
        return int(len(saved) != 3 or any(row["status"] != "completed" for row in saved.values()))


if __name__ == "__main__":
    sys.exit(main())
