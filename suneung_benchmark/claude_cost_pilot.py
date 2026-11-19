import argparse
import getpass
import hashlib
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
import httpx

from . import run_budget
from .paths import CSAT_2026_RESULTS, resolve_local_path
from .cost_pilot import exam_parts, make_input, summarize, write_json


RATES = {
    "claude-fable-5-2": (8.0, 40.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
}
PILOT_MODELS = ("claude-fable-5-2", "claude-opus-5-5")
EFFORT = "max"
MAX_TOKENS = 64_000
RETRY_MAX_TOKENS = 128_000
ANSWER_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {"answers": {"type": "array", "items": {"type": "integer"}}},
        "required": ["answers"],
        "additionalProperties": False,
    },
}
THINKING = {"type": "adaptive"}
OUTPUT_CONFIG = {"effort": EFFORT, "format": ANSWER_FORMAT}


def message_input(part, shared=None):
    if shared is None:
        shared = make_input(part, "image")
    digest = hashlib.sha256(json.dumps(shared, ensure_ascii=False).encode("utf-8")).hexdigest()
    prompt, *images = shared[0]["content"]
    blocks = [{"type": "text", "text": prompt["text"]}]
    for image in images:
        data = image["image_url"].split(",", 1)[1]
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}})
    return [{"role": "user", "content": blocks}], digest, len(images)


def charge(model, usage):
    input_rate, output_rate = RATES[model]
    cache = usage.get("cache_creation") or {}
    read = usage.get("cache_read_input_tokens") or 0
    written = usage.get("cache_creation_input_tokens") or 0
    long_write = cache.get("ephemeral_1h_input_tokens") or 0
    read_factor = 0.05 if model == "claude-sonnet-5-5" else 0.10
    input_cost = input_rate * (
        usage["input_tokens"] + read * read_factor + (written - long_write) * 1.25 + long_write * 2)
    return round((input_cost + usage["output_tokens"] * output_rate) / 1_000_000, 8)


def retry_delay(error, attempt):
    try:
        return max(1, float(error.response.headers["retry-after"])) + random.uniform(0, 1)
    except (KeyError, ValueError):
        return min(60, 2 ** (attempt + 2)) + random.uniform(0, 1)


def estimate_claude(client, model, messages, limit, *, effort=EFFORT):
    output_config = {**OUTPUT_CONFIG, "effort": effort}
    count = client.messages.count_tokens(model=model, messages=messages, thinking=THINKING, output_config=output_config)
    return run_budget.estimate_cost(count.input_tokens, limit, *RATES[model])


def call_model(client, model, part, messages, max_tokens, *, max_retries=7, effort=EFFORT):
    started = time.monotonic()
    refusal_category = None
    for attempt in range(max_retries + 1):
        received_headers = False
        try:
            with client.messages.stream(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                thinking=THINKING,
                output_config={**OUTPUT_CONFIG, "effort": effort},
                service_tier="standard_only",
            ) as stream:
                received_headers = True
                for event in stream:
                    if event.type == "message_delta" and event.delta.stop_reason == "refusal":
                        details = event.delta.model_dump().get("stop_details") or {}
                        refusal_category = details.get("category")
                message = stream.get_final_message()
            break
        except anthropic.APIStatusError as error:
            if not received_headers and error.status_code == 429 and attempt < max_retries:
                delay = retry_delay(error, attempt)
                print(f"{model} {part.name}: HTTP {error.status_code}, retrying in {delay:.0f}s", flush=True)
                time.sleep(delay)
                continue
            body = error.body if isinstance(error.body, dict) else {}
            details = body.get("error") or body
            return {"status": "api_error", "http_status": error.status_code, "error": details.get("type"),
                    "billed_usage_unknown": received_headers or error.status_code >= 500}
        except (anthropic.APIError, httpx.HTTPError) as error:
            return {"status": type(error).__name__, "billed_usage_unknown": True}

    provider_usage = message.usage.model_dump(exclude_none=True)
    read = provider_usage.get("cache_read_input_tokens") or 0
    written = provider_usage.get("cache_creation_input_tokens") or 0
    usage = {
        "input_tokens": provider_usage["input_tokens"] + read + written,
        "output_tokens": provider_usage["output_tokens"],
        "input_tokens_details": {"cached_tokens": read, "cache_write_tokens": written},
    }
    answer_text = "".join(block.text for block in message.content if block.type == "text")
    answers = []
    try:
        candidate = json.loads(answer_text)["answers"]
        if isinstance(candidate, list) and all(type(item) is int for item in candidate):
            answers = candidate
    except (TypeError, KeyError, ValueError):
        pass
    completed = message.stop_reason == "end_turn" and len(answers) == part.question_count
    return {
        "status": "completed" if completed else "incomplete",
        "stop_reason": message.stop_reason,
        "refusal_category": refusal_category,
        "response_id": message.id,
        "response_model": message.model,
        "provider_usage": provider_usage,
        "usage": usage,
        "estimated_usd": charge(model, provider_usage),
        "elapsed_s": round(time.monotonic() - started, 2),
        "answers": answers,
        "answered": len(answers),
        "max_tokens": max_tokens,
    }


def run_part(client, path, model, part, prepared, existing, limit):
    messages, digest, image_count = prepared
    path.parent.mkdir(parents=True, exist_ok=True)
    with run_budget.record_submission(path):
        result = call_model(client, model, part, messages, limit)
        row = {**result, "model": model, "subject": part.subject, "part": part.name, "label": part.label,
               "question_count": part.question_count, "first_question": part.first, "effort": EFFORT,
               "input_format": "image", "input_sha256": digest, "image_count": image_count}
        if existing:
            row["prior_attempt"] = existing
            row["estimated_usd"] = round((existing.get("estimated_usd") or 0) + (result.get("estimated_usd") or 0), 8)
        write_json(path, row)
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=resolve_local_path, default=CSAT_2026_RESULTS / "claude-max")
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--key-stdin", action="store_true")
    parser.add_argument("--retry-capped", action="store_true")
    parser.add_argument("--retry-job", action="append", metavar="MODEL/PART")
    parser.add_argument("--max-output-tokens", type=int)
    run_budget.add_options(parser)
    args = parser.parse_args()
    if args.budget_usd is None and not args.plan_only:
        parser.error("--budget-usd is required for generation")
    if args.retry_capped != bool(args.retry_job):
        parser.error("--retry-capped and explicit --retry-job MODEL/PART selections must be used together")
    if args.max_output_tokens is not None and not 16 <= args.max_output_tokens <= RETRY_MAX_TOKENS:
        parser.error("--max-output-tokens must be between 16 and 128000")
    key = sys.stdin.readline().strip() if args.key_stdin else os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        key = getpass.getpass("Claude API key: ")
    if not key:
        parser.error("An API key is required")

    parts = exam_parts()
    if len(parts) != 25 or sum(part.question_count for part in parts) != 487:
        raise ValueError("Unexpected exam coverage")
    chosen = set(args.retry_job or ())
    failed = 0
    with run_budget.locked_output(args.output):
        jobs = {}
        for part in parts:
            for model in PILOT_MODELS:
                path = args.output / model / f"{part.name}.json"
                old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
                if args.retry_capped:
                    if f"{model}/{part.name}" not in chosen:
                        continue
                    limit = args.max_output_tokens or RETRY_MAX_TOKENS
                    if not old or old.get("stop_reason") != "max_tokens" or limit <= old["max_tokens"]:
                        parser.error(f"{model}/{part.name} is not a capped request eligible for the chosen new limit")
                else:
                    if old:
                        continue
                    limit = args.max_output_tokens or MAX_TOKENS
                jobs[(model, part)] = path, old, limit
        if args.retry_capped and len(jobs) != len(chosen):
            parser.error("Unknown Claude model or exam part in --retry-job")
        unique_parts = {part.name: part for _, part in jobs}
        prepared = {name: message_input(part) for name, part in unique_parts.items()}
        for (model, part), (_, old, _) in jobs.items():
            if old and old.get("input_sha256") != prepared[part.name][1]:
                raise ValueError(f"Input changed for {model}/{part.name}; a retry would use different data")
        with anthropic.Anthropic(api_key=key, base_url="https://api.anthropic.com", max_retries=0,
                                 timeout=httpx.Timeout(300.0, connect=20.0)) as client:
            run_budget.preflight(args.output, jobs, lambda model, part: estimate_claude(
                client, model, prepared[part.name][0], jobs[(model, part)][2]), args.workers, args.budget_usd)
            if args.plan_only:
                return 0
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = [pool.submit(run_part, client, path, model, part, prepared[part.name], old, limit)
                           for (model, part), (path, old, limit) in jobs.items()]
                for future in as_completed(futures):
                    row = future.result()
                    failed += row["status"] != "completed"
                    print(f"{row['model']} {row['part']}: {row['status']} {row.get('answered', 0)}/{row['question_count']}; "
                          f"${row.get('estimated_usd')} {row.get('stop_reason') or row.get('error') or ''}", flush=True)
        summarize(args.output)
    print(f"Finished with {failed} incomplete requests.", flush=True)
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
