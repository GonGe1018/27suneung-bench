import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from urllib.parse import quote

import requests

from . import run_budget
from .paths import resolve_local_path
from .cost_pilot import exam_parts, summarize, write_json
from .gemini_cost_pilot import API, INPUT_RATE, MODEL, OUTPUT_LIMIT, OUTPUT_RATE
from .gemini_paid_resume import PAID, finish, prepare_body, read_interaction, submit


LONG_OUTPUT_LIMIT = 262_144


def continuation_reserve(previous_limit, input_tokens):
    hop_inputs = sum(input_tokens + offset for offset in range(previous_limit, LONG_OUTPUT_LIMIT, OUTPUT_LIMIT))
    return Decimal(run_budget.estimate_cost(hop_inputs, LONG_OUTPUT_LIMIT - previous_limit,
                                           INPUT_RATE, OUTPUT_RATE)["reserved_usd"])


def get_previous(headers, interaction_id):
    url = f"{API}/interactions/{quote(interaction_id, safe='')}"
    with requests.get(url, headers=headers, timeout=(15, 35), allow_redirects=False) as response:
        if response.status_code != 200:
            raise RuntimeError(f"Could not read previous interaction (HTTP {response.status_code})")
        return read_interaction(response)


def continue_part(headers, path, part, previous, reserved):
    started = time.monotonic()
    base = prepare_body(part, [], LONG_OUTPUT_LIMIT)
    base.pop("input")
    pending = path.with_suffix(".continuing")
    if pending.exists():
        interaction = json.loads(pending.read_text(encoding="utf-8"))
    else:
        previous_interaction = get_previous(headers, previous["response_id"])
        token = previous_interaction.get("continuation_token")
        if previous_interaction.get("status") not in {"incomplete", "requires_continuation"} or not token:
            print(f"{part.name}: no usable continuation token", flush=True)
            return False
        body = {**base, "previous_interaction_id": previous["response_id"], "continuation_token": token}
        pending.touch(exist_ok=False)
        interaction, error = submit(headers, body, started)
        if error is not None:
            print(f"{part.name}: continuation rejected ({error['stop_reason']})", flush=True)
            if not error["billed_usage_unknown"]:
                pending.unlink()
            return False
        write_json(pending, {"id": interaction["id"], "status": interaction["status"]})
        print(f"{part.name}: continuation accepted", flush=True)
    result = finish(headers, base, interaction, started)
    cost = result.get("estimated_usd")
    previous_cost = previous.get("estimated_usd") or 0
    if result["response_id"] == previous["response_id"]:
        # The same interaction ID reports one cumulative usage receipt.
        history = None
        added_cost = cost - previous_cost if cost is not None else None
        combined = cost if cost is not None else previous_cost
    else:
        history = previous
        added_cost = cost
        combined = previous_cost + (cost or 0)
    row = {
        **previous, **result,
        "estimated_usd": combined, "continuation_estimated_usd": added_cost,
        "previous_interaction_id": previous["response_id"], "prior_attempt": history,
        "max_tokens": LONG_OUTPUT_LIMIT, "continuation_reserved_usd": str(reserved),
    }
    write_json(path, row)
    pending.unlink()
    print(f"{part.name}: {row['status']} {row['answered']}/{part.question_count}; "
          f"additional cost={added_cost}", flush=True)
    return row["status"] == "completed"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget-usd", type=run_budget.positive_usd, required=True)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--output", type=resolve_local_path, default=PAID)
    args = parser.parse_args()
    api_key = sys.stdin.readline().strip()
    if not api_key:
        parser.error("Provide the Gemini API key on stdin")
    headers = {"x-goog-api-key": api_key, "Api-Revision": "2026-05-20"}
    directory = args.output / MODEL
    original_plan = json.loads((args.output / "budget-plan.json").read_text(encoding="utf-8"))["new_parts"]

    with run_budget.locked_output(args.output / ".continuations"):
        candidates = []
        exposure = Decimal(0)
        for part in exam_parts():
            path = directory / f"{part.name}.json"
            marker = path.with_suffix(".continuing")
            if not path.exists():
                exposure += Decimal(original_plan[part.name]["reserved_usd"])
                continue
            row = json.loads(path.read_text(encoding="utf-8"))
            exposure += Decimal(str(row.get("estimated_usd") or 0))
            if row.get("billed_usage_unknown"):
                exposure += Decimal(row.get("continuation_reserved_usd") or original_plan[part.name]["reserved_usd"])
            if row["status"] not in {"incomplete", "requires_continuation"} or row["answered"]:
                continue
            if not row.get("response_id") or row["max_tokens"] >= LONG_OUTPUT_LIMIT:
                continue
            input_tokens = row["provider_usage"].get("initial_input_tokens")
            if input_tokens is None:
                input_tokens = original_plan[part.name]["input_tokens"]
            reserved = continuation_reserve(row["max_tokens"], input_tokens)
            if marker.exists():
                exposure += reserved
                if not marker.stat().st_size:
                    print(f"{part.name}: continuation admission uncertain, keeping the budget reserve", flush=True)
                    continue
            candidates.append((part, path, row, reserved, marker.exists()))

        jobs = []
        for part, path, row, reserved, submitted in candidates:
            if not submitted and exposure + reserved > args.budget_usd:
                print(f"{part.name}: additional continuation exceeds the budget", flush=True)
                continue
            if not submitted:
                exposure += reserved
            jobs.append((part, path, row, reserved))
        print(f"Continuation plan: {len(jobs)} parts at {LONG_OUTPUT_LIMIT:,} tokens, "
              f"${exposure:.4f} maximum overall exposure.", flush=True)
        if not jobs:
            return 0

        failed = False
        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(continue_part, headers, path, part, row, reserved): part
                           for part, path, row, reserved in jobs}
                for future in as_completed(futures):
                    try:
                        failed |= not future.result()
                    except (requests.RequestException, ValueError, KeyError, TimeoutError, RuntimeError) as error:
                        print(f"{futures[future].name}: {type(error).__name__} during continuation", flush=True)
                        failed = True
        finally:
            summarize(args.output)
        return int(failed)


if __name__ == "__main__":
    sys.exit(main())
