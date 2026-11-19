
"""Run the selected OpenRouter models on the shared 2027 CSAT images."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
import getpass
import json
import os
from pathlib import Path
import sys

import requests

from run_benchmark import prepare_exams
from . import cost_pilot as csat
from . import run_budget
from .export_web import area
from .paths import CONFIGS, ROOT, resolve_local_path
from .web_status import part_in_scope


BASE = "https://openrouter.ai/api/v1"
ROUTES = {
    "kimi-k3.1-pro": ("moonshotai/kimi-k3.1-pro", "wafer/us"),
    "qwen4-max": ("qwen/qwen4-max", "alibaba"),
    "glm-5.3-flashx": ("z-ai/glm-5.3-flashx", "z-ai/fp8"),
    "deepseek-v4.1-flash-1017": ("deepseek/deepseek-v4.1-flash-1017", "morph/fp8"),
    "qwen3.8-flash-next": ("qwen/qwen3.8-flash-20260826", "alibaba/us-east-1"),
    "qwen3.8-27b": ("qwen/qwen3.8-27b", "wafer/fp8"),
    "muse-glimmer-30b": ("meta/muse-glimmer-30b", "modelrun/bf16"),
    "gemma-4-31b-it": ("google/gemma-4-31b-it", "coreweave/fp4"),
}


def endpoint(model):
    slug, tag = ROUTES[model]
    response = requests.get(f"{BASE}/models/{slug}/endpoints", timeout=15)
    response.raise_for_status()
    listing = response.json()["data"]
    route = next((entry for entry in listing["endpoints"] if entry["tag"] == tag), None)
    if "image" not in listing["architecture"]["input_modalities"] or not route:
        raise ValueError(f"{model}: pinned image endpoint unavailable")
    if not {"reasoning", "structured_outputs"} <= set(route["supported_parameters"]):
        raise ValueError(f"{model}: pinned endpoint lacks required parameters")
    return route


def request_body(model, effort, part, shared, limit):
    slug, tag = ROUTES[model]
    content = [
        {"type": "text", "text": item["text"]} if item["type"] == "input_text"
        else {"type": "image_url", "image_url": {"url": item["image_url"]}}
        for item in shared[0]["content"]
    ]
    schema = csat.request_body(slug, part, shared, effort, limit)["text"]["format"]
    reasoning = {"enabled": True} if effort == "thinking on" else {"effort": effort}
    response_format = {
        "type": "json_schema",
        "json_schema": {key: schema[key] for key in ("name", "schema", "strict")},
    }
    if model == "glm-5.3-flashx":
        response_format = {"type": "json_object"}
    return {
        "model": slug,
        "messages": [{"role": "user", "content": content}],
        "reasoning": {**reasoning, "exclude": True},
        "max_tokens": limit,
        "response_format": response_format,
        "provider": {"only": [tag], "allow_fallbacks": False, "require_parameters": True},
        "stream": False,
    }


def reservation(route, limit, image_count):
    def rate(name):
        return Decimal(route["pricing"].get(name) or "0")

    # Use the endpoint's full context as an upper bound for input charges.
    return (route["context_length"] * rate("prompt") + limit * rate("completion")
            + image_count * rate("image") + rate("request")) * Decimal("1.10")


def generate(headers, model, effort, part, shared, metadata, limit, reserve, output, keys, prior=None):
    path = output / model / f"{part.name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with run_budget.record_submission(path):
        csat.write_json(path.with_suffix(".pending"), {"reservation_usd": str(reserve), **metadata})
        status, http = "transport_error", None
        usage, record, stop_reason, completion, answers = {}, {}, None, None, []
        try:
            response = requests.post(
                BASE + "/chat/completions", headers=headers,
                json=request_body(model, effort, part, shared, limit), timeout=(20, 900),
            )
            http = response.status_code
            status = "api_error"
            record = response.json()
            usage = record.get("usage") or {}
            choices = record.get("choices") or []
            if response.ok and choices:
                stop_reason = choices[0].get("finish_reason")
                status = "invalid_answers"
                completion = (choices[0].get("message") or {}).get("content")
                try:
                    value = json.loads(completion)["answers"]
                    if isinstance(value, list) and len(value) == part.question_count and all(type(a) is int for a in value):
                        answers = value
                        status = "completed"
                except (KeyError, TypeError, ValueError):
                    pass
        except (requests.RequestException, ValueError):
            pass

        cost = usage.get("cost")
        if cost is None and http in {400, 401, 402, 403, 404, 413, 422, 429}:
            cost = 0
        row = {
            **metadata, "model": model, "subject": part.subject, "part": part.name,
            "label": part.label, "question_count": part.question_count,
            "input_format": "image", "effort": effort, "max_tokens": limit,
            "response_model": record.get("model"), "response_id": record.get("id"),
            "endpoint": ROUTES[model][1], "stop_reason": stop_reason,
            "status": status, "http_status": http, "answers": answers, "answered": len(answers),
            "reservation_usd": str(reserve), "billed_usage_unknown": cost is None,
            "estimated_usd": float(cost) if cost is not None else None,
            "usage": {"input_tokens": usage.get("prompt_tokens"),
                      "output_tokens": usage.get("completion_tokens"),
                      "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")},
        }
        if status != "completed" and isinstance(completion, str):
            row["raw_completion"] = completion
        if prior is not None:
            prior_cost = Decimal(str(prior["estimated_usd"]))
            row["prior_attempt"] = prior
            row["estimated_usd"] = float(prior_cost + Decimal(str(cost or 0)))
            row["reservation_usd"] = str(prior_cost + reserve)
        csat.write_json(path, row)
    grade = csat.score_row(row, keys[part.name]).get("correct") if part.name in keys else None
    print(f"{model}/{part.name}: {status} HTTP={http} {len(answers)}/{part.question_count} "
          f"correct={grade} cost={row['estimated_usd']}", flush=True)



def retry_unanswered(headers, model, name, exam, output, year, budget, effort=None):
    if model not in ROUTES:
        raise ValueError("Unknown OpenRouter model")
    parts = [part for part in csat.exam_parts() if part.name == name]
    if len(parts) != 1:
        raise ValueError("Unknown exam bundle")
    part = parts[0]
    with run_budget.locked_output(output / ".openrouter-retry" / model / name):
        path = output / model / f"{name}.json"
        prior = json.loads(path.read_text(encoding="utf-8"))
        if (prior["status"] != "invalid_answers" or prior["answered"] or prior.get("prior_attempt")
                or prior["estimated_usd"] is None or path.with_suffix(".pending").exists()):
            raise ValueError("Manual retry requires a completed first attempt with no valid answers and recorded cost")
        prepared, _ = prepare_exams(parts, exam, year)
        shared, metadata = prepared[name]
        route = endpoint(model)
        limit = prior["max_tokens"]
        if prior["input_sha256"] != metadata["input_sha256"] or limit > route["max_completion_tokens"]:
            raise ValueError("The original input or token limit is unavailable")
        reserve = reservation(route, limit, metadata["image_count"])
        exposure = Decimal(0)
        for model_id in ROUTES:
            directory = output / model_id
            records = {file.stem: file for file in directory.glob("*.pending")}
            records.update({file.stem: file for file in directory.glob("*.json")})
            for file in records.values():
                if not file.stat().st_size:
                    raise RuntimeError("A submission is being recorded")
                row = json.loads(file.read_text(encoding="utf-8"))
                cost = row.get("estimated_usd")
                exposure += Decimal(str(row["reservation_usd"] if cost is None else cost))
        if exposure + reserve > budget:
            raise ValueError(f"Manual retry would exceed the OpenRouter budget of USD {budget:.2f}")
        keys = csat.score_keys(exam, parts, allow_missing=True)
        print(f"Submitting one manual retry for {model}/{name}; additional USD {reserve:.2f} reserved.", flush=True)
        generate(headers, model, effort or prior["effort"], part, shared, metadata, limit, reserve, output, keys, prior)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget-usd", type=run_budget.positive_usd, required=True)
    parser.add_argument("--key-stdin", action="store_true")
    parser.add_argument("--retry", nargs=2, metavar=("MODEL", "BUNDLE"))
    parser.add_argument("--retry-effort", choices=("low", "medium", "high", "xhigh", "max"))
    args = parser.parse_args()
    if args.retry_effort and not args.retry:
        parser.error("--retry-effort requires --retry")
    key = sys.stdin.readline().strip() if args.key_stdin else os.environ.get("OPENROUTER_API_KEY")
    if not key and not args.key_stdin:
        key = getpass.getpass("OPENROUTER_API_KEY: ")
    if not key:
        parser.error("OPENROUTER_API_KEY is required")
    headers = {"Authorization": "Bearer " + key}
    auth = requests.get(BASE + "/key", headers=headers, timeout=12)
    if not auth.ok:
        parser.error(f"OpenRouter key check returned HTTP {auth.status_code}")
    remaining = auth.json().get("data", {}).get("limit_remaining")
    budget = args.budget_usd if remaining is None else min(args.budget_usd, Decimal(str(remaining)))

    base = CONFIGS / "2027" / "csat"
    config = json.loads((base / "benchmark.json").read_text(encoding="utf-8"))
    plan = json.loads((base / "planned-models.json").read_text(encoding="utf-8"))
    exam = resolve_local_path(ROOT / config["exam_dir"])
    output = resolve_local_path(ROOT / config["output_dir"])
    if args.retry:
        retry_unanswered(headers, *args.retry, exam, output, config["exam_year"], budget, args.retry_effort)
        return
    with run_budget.locked_output(output / ".openrouter"):
        parts = csat.exam_parts()
        prepared, _ = prepare_exams(parts, exam, config["exam_year"])
        keys = csat.score_keys(exam, parts, allow_missing=True)
        with ThreadPoolExecutor(max_workers=len(ROUTES)) as pool:
            info = dict(zip(ROUTES, pool.map(endpoint, ROUTES)))
        jobs = []
        exposure = Decimal(0)
        for entry in plan:
            model = entry["id"]
            if model not in ROUTES:
                continue
            route = info[model]
            limit = min(262_144, route["max_completion_tokens"])
            for part in parts:
                name = part.name
                if name not in prepared or not part_in_scope(entry["scope"], area(part.subject)):
                    continue
                shared, metadata = prepared[name]
                path = output / model / f"{name}.json"
                saved = path if path.exists() else path.with_suffix(".pending")
                if saved.exists():
                    previous = json.loads(saved.read_text(encoding="utf-8"))
                    if previous["input_sha256"] != metadata["input_sha256"]:
                        raise ValueError(f"{model}/{name}: saved input differs")
                    cost = previous.get("estimated_usd")
                    exposure += Decimal(str(previous["reservation_usd"] if cost is None else cost))
                    continue
                reserve = reservation(route, limit, metadata["image_count"])
                if exposure + reserve <= budget:
                    exposure += reserve
                    jobs.append((model, entry["effort"], part, shared, metadata, limit, reserve))
        print(f"Submitting {len(jobs)} OpenRouter requests in parallel; "
              f"reserved USD {exposure:.2f} of {budget:.2f}; zero automatic retries.", flush=True)
        if jobs:
            with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                tasks = [pool.submit(generate, headers, *job, output, keys) for job in jobs]
                for task in as_completed(tasks):
                    task.result()


if __name__ == "__main__":
    main()
