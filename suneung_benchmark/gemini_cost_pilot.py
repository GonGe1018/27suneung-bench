import argparse
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal

import requests

from . import run_budget
from .paths import CSAT_2026_RESULTS, resolve_local_path
from .cost_pilot import exam_parts, make_input, summarize, write_json


API = "https://generativelanguage.googleapis.com/v1beta"
MODEL = "gemini-4-hydrogen"
EFFORT = "xhigh"
OUTPUT_LIMIT = 65_536
INPUT_RATE = Decimal("1.50")
CACHE_RATE = Decimal("0.075")
OUTPUT_RATE = Decimal("7.50")
REFERENCE = CSAT_2026_RESULTS / "cost-pilot-max" / "gpt-6.2-astra"


def load_inputs(parts=None):
    inputs = {}
    for part in exam_parts() if parts is None else parts:
        shared = make_input(part, "image")
        signature = hashlib.sha256(json.dumps(shared, ensure_ascii=False).encode("utf-8")).hexdigest()
        reference = json.loads((REFERENCE / f"{part.name}.json").read_text(encoding="utf-8"))
        if signature != reference["input_sha256"]:
            raise ValueError(f"Shared input differs from the GPT pilot for {part.name}")
        inputs[part.name] = (signature, gemini_input(shared))
    return inputs


def gemini_input(shared):
    prompt, *images = shared[0]["content"]
    return [
        {"type": "text", "text": prompt["text"]},
        *({"type": "image", "data": image["image_url"].split(",", 1)[1],
           "mime_type": "image/png", "resolution": "ultra_high"}
          for image in images),
    ]


def estimate(headers, content, model=MODEL):
    parts = [{"text": content[0]["text"]}]
    parts.extend(
        {"inlineData": {"mimeType": image["mime_type"], "data": image["data"]},
         "mediaResolution": {"level": "MEDIA_RESOLUTION_ULTRA_HIGH"}}
        for image in content[1:]
    )
    url = f"{API}/models/{model}:countTokens"
    response = requests.post(url, headers=headers, json={"contents": [{"role": "user", "parts": parts}]},
                             timeout=(20, 120), allow_redirects=False)
    if not response.ok:
        raise RuntimeError(f"Input token count failed (HTTP {response.status_code})")
    count = response.json()["totalTokens"]
    # The endpoint can omit media-resolution effects. Use the higher count for the budget.
    count = max(count, 2240 * (len(content) - 1))
    return run_budget.estimate_cost(count, OUTPUT_LIMIT, INPUT_RATE, OUTPUT_RATE)


def request_body(part, content, max_tokens=OUTPUT_LIMIT):
    return {
        "model": MODEL,
        "input": content,
        "stream": True,
        "store": False,
        "generation_config": {"thinking_level": EFFORT, "max_output_tokens": max_tokens},
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": {
                "type": "object",
                "properties": {"answers": {"type": "array", "items": {"type": "integer"},
                                           "minItems": part.question_count, "maxItems": part.question_count}},
                "required": ["answers"],
            },
        },
    }


def response_usage(usage):
    if "total_input_tokens" not in usage:
        return {}, None
    input_tokens = usage["total_input_tokens"]
    cached = usage.get("total_cached_tokens", 0)
    thoughts = usage.get("total_thought_tokens", 0)
    output_tokens = usage.get("total_output_tokens", 0) + thoughts
    cost = ((input_tokens - cached) * INPUT_RATE + cached * CACHE_RATE + output_tokens * OUTPUT_RATE) / 1_000_000
    return {
        "input_tokens": input_tokens, "cached_tokens": cached,
        "output_tokens": output_tokens, "reasoning_tokens": thoughts,
    }, float(cost)


def http_error(response):
    code = response.status_code
    details = {"retry_after": response.headers.get("Retry-After")}
    try:
        error = response.json().get("error", {})
        details["provider_status"] = error.get("status")
        for item in error.get("details", []):
            kind = item.get("@type", "")
            if kind.endswith("RetryInfo"):
                details["retry_delay"] = item.get("retryDelay")
            elif kind.endswith("QuotaFailure"):
                details["quota_ids"] = [entry.get("quotaId") for entry in item.get("violations", [])]
    except (ValueError, AttributeError):
        pass
    return {
        "status": "api_error", "http_status": code, "stop_reason": f"HTTP {code}",
        "answered": 0, "billed_usage_unknown": code >= 500 or code == 408, **details,
    }


def call_model(headers, body, accepted):
    start = time.monotonic()
    chunks = []
    interaction = {}
    stop_reason = None
    status = "stream_incomplete"
    try:
        with requests.post(f"{API}/interactions", headers=headers, json=body, stream=True,
                           timeout=(25, 900), allow_redirects=False) as response:
            if response.status_code != 200:
                return {**http_error(response), "elapsed_s": round(time.monotonic() - start, 2)}
            response.encoding = "utf-8"
            for line in response.iter_lines(decode_unicode=True):
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                event = json.loads(data)
                kind = event.get("event_type")
                if kind == "interaction.created":
                    interaction = event["interaction"]
                    accepted.set()
                elif kind == "step.delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text":
                        chunks.append(delta["text"])
                elif kind == "step.stop":
                    stop_reason = event.get("finish_reason") or stop_reason
                elif kind in {"interaction.completed", "interaction.incomplete", "interaction.failed"}:
                    interaction = {**interaction, **event.get("interaction", {})}
                    status = interaction.get("status", kind.removeprefix("interaction."))
                    stop_reason = interaction.get("finish_reason") or interaction.get("stop_reason") or stop_reason
                    accepted.set()
                    break
                elif kind == "error":
                    status = "stream_error"
                    stop_reason = event.get("error", {}).get("code")
                    break
    except (requests.RequestException, ValueError) as error:
        status = "transport_error" if isinstance(error, requests.RequestException) else "stream_error"
        stop_reason = type(error).__name__

    return parse_result(body, interaction, status, stop_reason, "".join(chunks), start)


def parse_result(body, interaction, status, stop_reason, raw, start):
    answers = []
    if raw:
        try:
            payload = json.loads(raw)
            values = payload.get("answers") if isinstance(payload, dict) else None
            expected_count = body["response_format"]["schema"]["properties"]["answers"]["minItems"]
            if isinstance(values, list) and len(values) == expected_count and all(type(value) is int for value in values):
                answers = values
        except ValueError:
            pass
    if status == "completed" and not answers:
        status = "invalid_answers"
    raw_usage = interaction.get("usage") or {}
    usage, cost = response_usage(raw_usage)
    if status == "incomplete" and stop_reason is None and usage.get("output_tokens") == body["generation_config"]["max_output_tokens"]:
        stop_reason = "max_tokens"
    return {
        "status": status, "stop_reason": stop_reason, "answers": answers, "answered": len(answers),
        "raw_output": raw, "response_id": interaction.get("id"), "response_model": interaction.get("model"),
        "elapsed_s": round(time.monotonic() - start, 2), "provider_usage": raw_usage,
        "usage": usage, "estimated_usd": cost, "billed_usage_unknown": cost is None,
    }


def recovery_plan(output_dir, jobs, saved, budget):
    original = json.loads((output_dir / "budget-plan.json").read_text(encoding="utf-8"))
    reserves = {row["part"]: Decimal(row["reserved_usd"]) for row in original["requests"]}
    # The original run may still be finishing other parts. Keep their full reserves.
    exposure = sum((reserve for part, reserve in reserves.items() if part not in saved), Decimal(0))
    for part, row in saved.items():
        while row:
            exposure += reserves[part] if row.get("billed_usage_unknown") else Decimal(str(row.get("estimated_usd") or 0))
            row = row.get("prior_attempt")
    settled_exposure = exposure
    selected = []
    for model, part in jobs:
        reserved = reserves[part.name]
        if exposure + reserved <= budget:
            selected.append((model, part))
            exposure += reserved
    plan = {
        "previous_cost_plus_uncertain_reserves_usd": str(settled_exposure),
        "planned_total_usd": str(exposure), "budget_usd": str(budget),
        "parts": [part.name for _, part in selected],
    }
    write_json(output_dir / "recovery-plan.json", plan)
    print(f"Recovery budget: ${settled_exposure:.4f} prior exposure, ${exposure:.4f} total for "
          f"{len(selected)}/{len(jobs)} candidate requests.", flush=True)
    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=resolve_local_path, default=CSAT_2026_RESULTS / "hydrogen-xhigh")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--key-stdin", action="store_true")
    parser.add_argument("--retry-rejected", action="store_true",
                        help="Retry only explicit upfront HTTP 429 or short HTTP 503, keeping uncertain reserves")
    parser.add_argument("--submit-interval", type=float, default=0)
    run_budget.add_options(parser)
    args = parser.parse_args()
    if args.budget_usd is None and not args.plan_only:
        parser.error("--budget-usd is required for generation")
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.submit_interval < 0 or (args.retry_rejected and args.budget_usd is None):
        parser.error("Recovery requires a budget and the submission interval must be nonnegative")
    api_key = sys.stdin.readline().strip() if args.key_stdin else os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        parser.error("A Gemini API key is required")

    headers = {"x-goog-api-key": api_key, "Api-Revision": "2026-05-20"}
    parts = exam_parts()
    inputs = load_inputs()
    print(f"Verified {len(parts)} matching inputs, {sum(len(items) - 1 for _, items in inputs.values())} pages, "
          f"{sum(part.question_count for part in parts)} questions.", flush=True)
    directory = args.output / MODEL
    throttle = threading.Lock()
    quota_reset = threading.Event()
    next_submission = 0.0
    saved = {}

    def path_for(part):
        return directory / f"{part.name}.json"

    def run(part, accepted):
        nonlocal next_submission
        signature, content = inputs[part.name]
        path = path_for(part)
        with throttle:
            if quota_reset.wait(max(0, next_submission - time.monotonic())):
                return None
            next_submission = time.monotonic() + args.submit_interval
        with run_budget.record_submission(path):
            received = call_model(headers, request_body(part, content), accepted)
            row = {
                **received, "model": MODEL, "part": part.name, "subject": part.subject, "label": part.label,
                "first_question": part.first, "question_count": part.question_count, "effort": EFFORT,
                "input_format": "image", "image_count": len(content) - 1, "media_resolution": "ultra_high",
                "input_sha256": signature, "max_tokens": OUTPUT_LIMIT,
            }
            if part.name in saved:
                row["prior_attempt"] = saved[part.name]
            write_json(path, row)
        retry_after = row.get("retry_after") or ""
        if row.get("http_status") == 429 and retry_after.isdecimal() and int(retry_after) >= 3600:
            quota_reset.set()
            print(f"Paused remaining submissions until the provider's quota resets in {retry_after}s.", flush=True)
        print(f"{part.name}: {row['status']} {row['answered']}/{part.question_count}; "
              f"cost={row.get('estimated_usd')} reason={row.get('stop_reason')}", flush=True)
        return row

    lock_dir = args.output / ".recovery" if args.retry_rejected else args.output
    with run_budget.locked_output(lock_dir):
        for part in parts:
            path = path_for(part)
            if path.exists():
                row = json.loads(path.read_text(encoding="utf-8"))
                if row["input_sha256"] != inputs[part.name][0] or row["effort"] != EFFORT:
                    raise ValueError(f"Existing result configuration differs for {part.name}")
                saved[part.name] = row
        if args.retry_rejected:
            retryable = [
                part for part in parts
                if part.name in saved and not path_for(part).with_suffix(".pending").exists()
                and saved[part.name]["status"] == "api_error" and not saved[part.name]["answered"]
                and not saved[part.name].get("prior_attempt")
                and (saved[part.name].get("http_status") == 429 or
                     (saved[part.name].get("http_status") == 503 and saved[part.name]["elapsed_s"] < 15))
            ]
            priority = {"korean-speech", "math-calculus", "math-geometry", "biology-2", "physics-2", "earth-science-2"}
            retryable.sort(key=lambda part: (part.name not in priority, saved[part.name]["http_status"]))
            jobs = recovery_plan(args.output, [(MODEL, part) for part in retryable], saved, args.budget_usd)
        else:
            jobs = [(MODEL, part) for part in parts if part.name not in saved]
            if jobs or not (args.output / "budget-plan.json").exists():
                run_budget.preflight(args.output, jobs, lambda _model, part: estimate(headers, inputs[part.name][1]),
                                     args.workers, args.budget_usd)
        if args.plan_only or not jobs:
            return 0
        directory.mkdir(parents=True, exist_ok=True)
        failed = False
        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                accepted = threading.Event()
                submitted = []
                if not args.retry_rejected:
                    _, first_part = jobs.pop(0)
                    first = pool.submit(run, first_part, accepted)
                    # Avoid submitting all parts if the API rejects the shared request format.
                    while not first.done() and not accepted.wait(0.25):
                        pass
                    if not accepted.is_set():
                        first.result()
                        print("Stopped before submitting remaining parts because the first request was not accepted.",
                              flush=True)
                        return 1
                    submitted.append(first)
                submitted.extend(pool.submit(run, part, accepted) for _, part in jobs)
                for future in as_completed(submitted):
                    row = future.result()
                    failed |= row is None or row["status"] != "completed"
        finally:
            summarize(args.output)
        return int(failed)


if __name__ == "__main__":
    sys.exit(main())
