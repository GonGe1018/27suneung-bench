import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from urllib.parse import quote

import requests

from . import run_budget
from .paths import CSAT_2026_RESULTS, resolve_local_path
from .cost_pilot import exam_parts, summarize, write_json
from .gemini_cost_pilot import (
    API, EFFORT, INPUT_RATE, MODEL, OUTPUT_LIMIT, OUTPUT_RATE,
    http_error, load_inputs, parse_result, request_body,
)


PILOT = CSAT_2026_RESULTS / "hydrogen-xhigh"
PAID = CSAT_2026_RESULTS / "hydrogen-xhigh-paid"
ACTIVE_STATES = {"queued", "in_progress", "pending"}


def output_limit(part):
    if part.name in {"world-geography", "chemistry-2"}:
        return 2 * OUTPUT_LIMIT
    if part.subject.endswith(("-1", "-2")):
        return OUTPUT_LIMIT + OUTPUT_LIMIT // 2
    return OUTPUT_LIMIT


def reserve(part, original_counts, max_tokens):
    input_count = original_counts[part.name]
    if max_tokens > OUTPUT_LIMIT:
        # Reserve the original prompt and the full preceding output for the second hop.
        input_count = 2 * input_count + OUTPUT_LIMIT
    return run_budget.estimate_cost(input_count, max_tokens, INPUT_RATE, OUTPUT_RATE)


def prepare_body(part, content, max_tokens):
    body = request_body(part, content, max_tokens)
    return {**body, "background": True, "store": True, "stream": False}


def read_interaction(response):
    data = response.json()
    return data.get("interaction", data)


def submit(headers, body, started):
    with requests.post(f"{API}/interactions", headers=headers, json=body, timeout=(25, 120),
                       allow_redirects=False) as response:
        if response.status_code not in (200, 201, 202):
            error = http_error(response)
            error["elapsed_s"] = round(time.monotonic() - started, 2)
            return {}, error
        return read_interaction(response), None


def poll(headers, interaction):
    delay = 10
    deadline = time.monotonic() + 1_800
    url = f"{API}/interactions/{quote(interaction['id'], safe='')}"
    while interaction.get("status") in ACTIVE_STATES:
        if time.monotonic() >= deadline:
            raise TimeoutError("Background interaction is still active")
        time.sleep(delay)
        try:
            with requests.get(url, headers=headers, timeout=(15, 35), allow_redirects=False) as response:
                if response.status_code == 200:
                    interaction = read_interaction(response)
                    delay = 10
                    continue
                if response.status_code not in (408, 429, 500, 502, 503, 504):
                    raise RuntimeError(f"Could not retrieve background interaction (HTTP {response.status_code})")
        except requests.RequestException:
            pass
        delay = min(45, delay * 2)
    return interaction


def finish(headers, body, interaction, started):
    terminal = poll(headers, interaction)
    text = "".join(
        block["text"]
        for step in terminal.get("steps", []) if step.get("type") == "model_output"
        for block in step.get("content", []) if block.get("type") == "text"
    )
    status = terminal.get("status", "unknown")
    reason = terminal.get("finish_reason") or terminal.get("stop_reason")
    errors = terminal.get("errors") or [terminal.get("error") or {}]
    reason = reason or errors[0].get("code")
    return parse_result(body, terminal, status, reason, text, started)


def copy_free_results(parts, output, inputs):
    model_dir = output / MODEL
    model_dir.mkdir(parents=True, exist_ok=True)
    for part in parts:
        path = model_dir / f"{part.name}.json"
        original = json.loads((PILOT / MODEL / path.name).read_text(encoding="utf-8"))
        if original["input_sha256"] != inputs[part.name][0] or original["effort"] != EFFORT:
            raise ValueError(f"Free-tier input differs for {part.name}")
        if not path.exists() and original.get("answered") == part.question_count:
            row = {**original, "billing_tier": "free", "billing_price_equivalent_usd": original.get("estimated_usd"),
                   "estimated_usd": 0, "billed_usage_unknown": False}
            write_json(path, row)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=resolve_local_path, default=PAID)
    parser.add_argument("--budget-usd", required=True, type=run_budget.positive_usd)
    parser.add_argument("--key-stdin", action="store_true")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--submit-interval", type=float, default=2)
    args = parser.parse_args()
    api_key = sys.stdin.readline().strip() if args.key_stdin else os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        parser.error("A Gemini API key is required")
    if args.workers < 1 or args.submit_interval < 0:
        parser.error("Use a positive worker count and a nonnegative submission interval")
    headers = {"x-goog-api-key": api_key, "Api-Revision": "2026-05-20"}

    parts = exam_parts()
    inputs = load_inputs()
    original_plan = json.loads((PILOT / "budget-plan.json").read_text(encoding="utf-8"))
    original_counts = {row["part"]: row["input_tokens"] for row in original_plan["requests"]}
    directory = args.output / MODEL
    throttle = threading.Lock()
    paused = threading.Event()
    next_submission = 0.0

    def result_path(part):
        return directory / f"{part.name}.json"

    def run(part, first_admitted):
        nonlocal next_submission
        signature, content = inputs[part.name]
        limit = output_limit(part)
        body = prepare_body(part, content, limit)
        path = result_path(part)
        marker = path.with_suffix(".pending")
        started = time.monotonic()
        with throttle:
            if paused.wait(max(0, next_submission - time.monotonic())):
                return False
            next_submission = time.monotonic() + args.submit_interval
        if marker.exists():
            interaction = json.loads(marker.read_text(encoding="utf-8"))
            first_admitted.set()
            received = finish(headers, body, interaction, started)
            write_receipt(path, part, signature, len(content) - 1, limit, received)
            marker.unlink()
        else:
            with run_budget.record_submission(path):
                interaction, received = submit(headers, body, started)
                if received is None:
                    write_json(marker, {"id": interaction["id"], "status": interaction["status"]})
                    first_admitted.set()
                    print(f"{part.name}: accepted", flush=True)
                    received = finish(headers, body, interaction, started)
                write_receipt(path, part, signature, len(content) - 1, limit, received)
        retry_after = received.get("retry_after") or ""
        if received.get("http_status") == 429 and retry_after.isdecimal() and int(retry_after) >= 3600:
            paused.set()
        print(f"{part.name}: {received['status']} {received['answered']}/{part.question_count}; "
              f"cost={received.get('estimated_usd')} reason={received.get('stop_reason')}", flush=True)
        return received["status"] == "completed"

    with run_budget.locked_output(args.output):
        copy_free_results(parts, args.output, inputs)
        exposure = Decimal(0)
        pending = []
        jobs = []
        reservations = {}
        for part in parts:
            path = result_path(part)
            marker = path.with_suffix(".pending")
            if path.exists():
                row = json.loads(path.read_text(encoding="utf-8"))
                if row["input_sha256"] != inputs[part.name][0] or row["effort"] != EFFORT:
                    raise ValueError(f"Existing result configuration differs for {part.name}")
                if row.get("billed_usage_unknown"):
                    exposure += Decimal(reserve(part, original_counts, row["max_tokens"])["reserved_usd"])
                else:
                    exposure += Decimal(str(row.get("estimated_usd") or 0))
                continue
            reservation = reserve(part, original_counts, output_limit(part))
            reservations[part.name] = reservation
            exposure += Decimal(reservation["reserved_usd"])
            if marker.exists():
                if marker.stat().st_size:
                    pending.append(part)
                else:
                    print(f"{part.name}: admission uncertain, keeping the budget reserve", flush=True)
            else:
                jobs.append(part)
        if exposure > args.budget_usd:
            raise RuntimeError(f"Planned exposure ${exposure:.4f} exceeds the budget ${args.budget_usd:.4f}")
        write_json(args.output / "budget-plan.json", {
            "planned_total_usd": str(exposure), "budget_usd": str(args.budget_usd),
            "new_parts": reservations,
        })
        print(f"Paid-tier plan: {len(jobs)} new parts, {len(pending)} to retrieve, ${exposure:.4f} maximum exposure.",
              flush=True)
        jobs.sort(key=lambda part: -output_limit(part))
        successful = True
        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                accepted = threading.Event()
                futures = {pool.submit(run, part, accepted): part for part in pending}
                if jobs and not pending:
                    first = jobs.pop(0)
                    initial = pool.submit(run, first, accepted)
                    futures[initial] = first
                    while not initial.done() and not accepted.wait(0.25):
                        pass
                    if not accepted.is_set():
                        if initial.exception():
                            print(f"{first.name}: {type(initial.exception()).__name__} before admission", flush=True)
                        print("The first request was not accepted. Remaining submissions were paused.", flush=True)
                        return 1
                futures.update({pool.submit(run, part, accepted): part for part in jobs})
                for future in as_completed(futures):
                    try:
                        successful &= future.result()
                    except (requests.RequestException, ValueError, KeyError, TimeoutError, RuntimeError) as error:
                        print(f"{futures[future].name}: {type(error).__name__}, submission marker retained.", flush=True)
                        successful = False
        finally:
            summarize(args.output)
        return int(not successful)


def write_receipt(path, part, signature, image_count, limit, received):
    write_json(path, {
        **received, "model": MODEL, "part": part.name, "subject": part.subject, "label": part.label,
        "first_question": part.first, "question_count": part.question_count, "effort": EFFORT,
        "input_format": "image", "image_count": image_count, "media_resolution": "ultra_high",
        "input_sha256": signature, "max_tokens": limit, "billing_tier": "paid",
        "execution": "background",
    })


if __name__ == "__main__":
    sys.exit(main())
