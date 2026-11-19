import argparse
import getpass
import hashlib
import json
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import anthropic
import httpx
import pymupdf
import requests

from suneung_benchmark import claude_cost_pilot as claude
from suneung_benchmark import cost_pilot as openai
from suneung_benchmark import gemini_cost_pilot as gemini
from suneung_benchmark import run_budget
from suneung_benchmark.gemini_paid_resume import finish, submit
from suneung_benchmark.paths import CONFIGS, ROOT, resolve_local_path

CORE = {"korean", "math", "english"}
SUBJECT_GROUPS = CORE | {"science-1", "science-2", "social"}
PAPER_PAGES = {"korean": 20, "math": 20, "english": 8}
KEY_NAMES = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
PROVIDER_MODELS = {
    "gemini": {"gemini-4-carbon", "gemini-4-hydrogen"},
    "openai": openai.RATES.keys(),
    "anthropic": claude.RATES.keys(),
}


@dataclass(frozen=True)
class Model:
    model: str
    provider: str
    effort: str
    scope: str
    max_output_tokens: int


@dataclass(frozen=True)
class Job:
    model: Model
    part: openai.ExamPart

    def path(self, output):
        return output / self.model.model / f"{self.part.name}.json"


def subject_group(part):
    if part.subject in CORE:
        return part.subject
    if part.subject.endswith(("-1", "-2")):
        return f"science{part.subject[-2:]}"
    return "social"


def selected_jobs(parts, models, priorities=None):
    if priorities:
        parts = sorted(parts, key=lambda part: priorities[subject_group(part)])
    return [Job(model, part) for part in parts for model in models
            if model.scope == "all" or part.subject in CORE]


def earliest_priorities(jobs, priorities):
    earliest = {}
    for job in jobs:
        level = priorities[subject_group(job.part)]
        earliest[job.model] = min(level, earliest.get(job.model, level))
    return earliest


def defer_lower_priority(jobs, blockers, priorities):
    if not priorities:
        return jobs
    earliest = earliest_priorities(blockers, priorities)
    return [job for job in jobs
            if job.model not in earliest or priorities[subject_group(job.part)] <= earliest[job.model]]


def odd_form_offset(source, expected_pages):
    with pymupdf.open(source) as document:
        count = document.page_count
        if count not in {expected_pages, 2 * expected_pages}:
            raise ValueError(f"expected {expected_pages} or {2 * expected_pages} pages, got {count}")
        first = "".join(document[0].get_text().split())
        if count == expected_pages:
            if "짝수형" in first and "홀수형" not in first:
                raise ValueError("even-numbered form provided")
            return 0
        second = "".join(document[expected_pages].get_text().split())
        if "홀수형" in first and "짝수형" in second:
            return 0
        if "짝수형" in first and "홀수형" in second:
            return expected_pages
        raise ValueError("cannot identify the odd-numbered form from the two paper headers")


def prepare_exams(parts, exam_dir, year):
    prepared = {}
    missing = {}
    offsets = {}
    for part in parts:
        combined = exam_dir / f"{part.subject}-exam.pdf"
        separate = exam_dir / f"{part.name}-exam.pdf"
        split = separate != combined and separate.is_file()
        source = separate if split else combined
        if not source.is_file():
            missing[part.name] = source.name
            continue
        if part.subject == "english" and not (exam_dir / "english-transcript.pdf").is_file():
            missing[part.name] = "english-transcript.pdf"
            continue
        first, last = part.pages or (1, 4)
        if split:
            first, last = 1, last - first + 1
        expected = last if split else PAPER_PAGES.get(part.subject, 4)
        try:
            if source not in offsets:
                offsets[source] = odd_form_offset(source, expected)
            offset = offsets[source]
            part_for_input = replace(part, pages=(first + offset, last + offset))
            shared = openai.make_input(part_for_input, "image", root=exam_dir, exam_year=year, source=source,
                                       image_group=part.name if split else None)
        except (OSError, ValueError, RuntimeError) as error:
            missing[part.name] = f"{source.name} ({error})"
            continue
        signature = hashlib.sha256(json.dumps(shared, ensure_ascii=False).encode("utf-8")).hexdigest()
        prepared[part.name] = shared, {
            "source": source.name, "input_sha256": signature, "image_count": len(shared[0]["content"]) - 1,
            "first_question": part.first, "last_question": part.last,
        }
    return prepared, missing


def gemini_pricing(model):
    if model == "gemini-4-hydrogen":
        return gemini.INPUT_RATE, gemini.CACHE_RATE, gemini.OUTPUT_RATE, 65_536
    if date.today() < date(2026, 12, 1):
        return Decimal("2"), Decimal("0.10"), Decimal("10"), 32_768
    return Decimal("4"), Decimal("0.20"), Decimal("20"), 32_768


def prepare_request(job, shared):
    model = job.model
    if model.provider == "openai":
        return openai.request_body(model.model, job.part, shared, model.effort, model.max_output_tokens)
    if model.provider == "anthropic":
        return claude.message_input(job.part, shared)[0]
    body = gemini.request_body(job.part, gemini.gemini_input(shared), model.max_output_tokens)
    body["model"] = model.model
    body["generation_config"]["thinking_level"] = model.effort
    return {**body, "stream": False, "background": True, "store": True}


def estimate_request(job, body, clients):
    model = job.model
    client = clients[model.provider]
    if model.provider == "openai":
        return openai.estimate_openai(client, body)
    if model.provider == "anthropic":
        return claude.estimate_claude(client, model.model, body, model.max_output_tokens, effort=model.effort)
    initial = gemini.estimate(client, body["input"], model.model)["input_tokens"]
    input_rate, _, output_rate, hop = gemini_pricing(model.model)
    cumulative = sum(initial + offset for offset in range(0, model.max_output_tokens, hop))
    return {**run_budget.estimate_cost(cumulative, model.max_output_tokens, input_rate, output_rate),
            "initial_input_tokens": initial}


def generate(job, body, clients, marker, admission, admitted):
    model = job.model
    client = clients[model.provider]
    if model.provider == "openai":
        return openai.call_model(client, body, max_retries=0)
    if model.provider == "anthropic":
        return claude.call_model(client, model.model, job.part, body, model.max_output_tokens,
                                 max_retries=0, effort=model.effort)
    started = time.monotonic()
    if "id" in admission:
        interaction = admission
    else:
        interaction, error = submit(client, body, started)
        if error is not None:
            return error
        openai.write_json(marker, {**admission, "id": interaction["id"], "status": interaction["status"]})
        print(f"{model.model}/{job.part.name}: accepted", flush=True)
    if admitted is not None:
        admitted.set()
    result = finish(client, body, interaction, started)
    usage = result.get("usage") or {}
    if usage:
        input_rate, cache_rate, output_rate, _ = gemini_pricing(model.model)
        cached = usage["cached_tokens"]
        cost = (usage["input_tokens"] - cached) * input_rate + cached * cache_rate
        cost += usage["output_tokens"] * output_rate
        result["estimated_usd"] = float(cost / 1_000_000)
    return result


def run_job(job, output, shared_info, clients, amount, admission=None, admitted=None):
    path = job.path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = path.with_suffix(".pending")
    shared, metadata = shared_info
    body = prepare_request(job, shared)

    def complete(request_admission):
        result = generate(job, body, clients, marker, request_admission, admitted)
        if result.get("http_status") == 408:
            result["billed_usage_unknown"] = True
        answers = result.get("answers")
        valid = (isinstance(answers, list) and len(answers) == job.part.question_count
                 and all(type(answer) is int for answer in answers))
        if not valid:
            result["answers"] = []
            result["answered"] = 0
            if result.get("status") == "completed":
                result["status"] = "invalid_answers"
        row = {
            **result, **metadata, "model": job.model.model, "subject": job.part.subject,
            "label": job.part.label, "part": job.part.name, "question_count": job.part.question_count,
            "effort": job.model.effort, "input_format": "image", "max_tokens": job.model.max_output_tokens,
            "reservation_usd": str(amount),
        }
        openai.write_json(path, row)
        return row

    if admission is not None:
        row = complete(admission)
        marker.unlink()
        return row
    with run_budget.record_submission(path):
        admission = {"reservation_usd": str(amount), "input_sha256": metadata["input_sha256"],
                     "effort": job.model.effort, "max_tokens": job.model.max_output_tokens}
        openai.write_json(marker, admission)
        return complete(admission)


def previous_jobs(jobs, output, prepared):
    known = set()
    pending = []
    for job in jobs:
        path = job.path(output)
        marker = path.with_suffix(".pending")
        if not path.exists() and not marker.exists():
            continue
        known.add(job)
        record = path if path.exists() else marker
        row = json.loads(record.read_text(encoding="utf-8")) if record.stat().st_size else {}
        if not row:
            # Submission writes its reservation before any outbound call.
            print(f"{job.model.model}/{job.part.name}: an empty submission marker needs review", flush=True)
            continue
        expected = prepared[job.part.name][1]["input_sha256"]
        saved_limit = row.get("max_tokens")
        if row["input_sha256"] != expected or row["effort"] != job.model.effort or saved_limit != job.model.max_output_tokens:
            raise ValueError(f"Saved input or model settings differ for {job.model.model}/{job.part.name}")
        if record == path:
            continue
        if job.model.provider == "gemini" and row.get("id"):
            pending.append((job, Decimal(row["reservation_usd"]), row))
        else:
            print(f"{job.model.model}/{job.part.name}: submission state uncertain. Kept its reservation.", flush=True)
    return known, pending


def schedule(queue, prepared, clients, output, budget, exposure, provider_limit, priorities=None):
    in_flight = {}
    by_provider = Counter()
    gemini_admitted = {job.model.model: threading.Event() for job, _, _ in queue
                       if job.model.provider == "gemini"}
    paused_models = set()
    paused_providers = set()
    with ThreadPoolExecutor(max_workers=provider_limit * len(clients)) as pool:
        while queue or in_flight:
            earliest = (earliest_priorities((job for job, _, admission in queue if admission is None), priorities)
                        if priorities else {})
            for index in range(len(queue) - 1, -1, -1):
                job, amount, admission = queue[index]
                provider = job.model.provider
                if by_provider[provider] >= provider_limit:
                    continue
                admitted = gemini_admitted.get(job.model.model)
                if admission is None and (provider in paused_providers or
                                          job.model.model in paused_models or exposure + amount > budget):
                    continue
                if admission is None and priorities and priorities[subject_group(job.part)] != earliest[job.model]:
                    continue
                queue.pop(index)
                if admission is None:
                    exposure += amount
                future = pool.submit(run_job, job, output, prepared[job.part.name], clients, amount, admission, admitted)
                in_flight[future] = job, amount
                by_provider[provider] += 1
            if not in_flight:
                break
            done, _ = wait(in_flight, timeout=1 if queue else None, return_when=FIRST_COMPLETED)
            for future in done:
                job, amount = in_flight.pop(future)
                by_provider[job.model.provider] -= 1
                admitted = gemini_admitted.get(job.model.model)
                label = f"{job.model.model}/{job.part.name}"
                try:
                    row = future.result()
                except (requests.RequestException, OSError, RuntimeError, ValueError, KeyError) as error:
                    print(f"{label}: {type(error).__name__}. Kept its reservation and submission record.", flush=True)
                    if admitted is not None and not admitted.is_set():
                        paused_models.add(job.model.model)
                    continue
                if not row.get("billed_usage_unknown"):
                    exposure += Decimal(str(row.get("estimated_usd") or 0)) - amount
                if row.get("http_status") in {401, 402, 403}:
                    paused_providers.add(job.model.provider)
                elif row.get("http_status") == 429:
                    paused_models.add(job.model.model)
                elif admitted is not None and not admitted.is_set() and row["status"] != "completed":
                    paused_models.add(job.model.model)
                print(f"{label}: {row['status']} {row['answered']}/{job.part.question_count}; "
                      f"cost={row.get('estimated_usd')} current exposure=${exposure:.4f}", flush=True)
    return len(queue)


def execute(jobs, prepared, output, budget, provider_limit, supplied_keys=None, priorities=None):
    ready = [job for job in jobs if job.part.name in prepared]
    known, pending = previous_jobs(ready, output, prepared)
    # Apply priorities to available papers so unpublished subjects cannot hold up a release.
    new = [job for job in ready if job not in known]
    providers = dict.fromkeys(job.model.provider for job in new + [job for job, _, _ in pending])
    if not providers:
        return 0
    with ExitStack() as stack:
        clients = {}
        for provider in providers:
            name = KEY_NAMES[provider]
            key = (supplied_keys or {}).get(provider) or os.environ.get(name)
            if not key and supplied_keys is None:
                key = getpass.getpass(f"{name}: ")
            if not key:
                raise ValueError(f"{name} is required")
            if provider == "anthropic":
                clients[provider] = stack.enter_context(anthropic.Anthropic(
                    api_key=key, base_url="https://api.anthropic.com", max_retries=0,
                    timeout=httpx.Timeout(900, connect=20)))
            elif provider == "gemini":
                clients[provider] = {"x-goog-api-key": key, "Api-Revision": "2026-05-20"}
            else:
                clients[provider] = key

        def price(job):
            body = prepare_request(job, prepared[job.part.name][0])
            return estimate_request(job, body, clients)

        reservations = {}
        count_failures = []
        with ThreadPoolExecutor(max_workers=provider_limit * len(clients)) as pool:
            estimates = {pool.submit(price, job): job for job in new}
            for future in as_completed(estimates):
                job = estimates[future]
                try:
                    reservations[job] = future.result()
                except (requests.RequestException, anthropic.APIError, ValueError, KeyError, RuntimeError) as error:
                    print(f"{job.model.model}/{job.part.name}: token count failed ({type(error).__name__})", flush=True)
                    count_failures.append(job)
        existing_cost = Decimal(0)
        for path in output.glob("*/*.json"):
            row = json.loads(path.read_text(encoding="utf-8"))
            field = "reservation_usd" if row.get("billed_usage_unknown") else "estimated_usd"
            existing_cost += Decimal(str(row.get(field) or 0))
        for marker in output.glob("*/*.pending"):
            if marker.with_suffix(".json").exists() or not marker.stat().st_size:
                continue
            saved = json.loads(marker.read_text(encoding="utf-8"))
            existing_cost += Decimal(saved["reservation_usd"])
        openai.write_json(output / "budget-plan.json", {
            "budget_usd": str(budget), "previous_exposure_usd": str(existing_cost),
            "requests": [{"model": job.model.model, "part": job.part.name, **info}
                         for job, info in reservations.items()],
        })
        priced = [job for job in new if job in reservations]
        eligible = defer_lower_priority(priced, count_failures, priorities)
        queue = pending + [(job, Decimal(reservations[job]["reserved_usd"]), None) for job in eligible]
        deferred = schedule(list(reversed(queue)), prepared, clients, output, budget, existing_cost, provider_limit,
                            priorities)
        deferred += len(priced) - len(eligible)
        print(f"Deferred requests: {deferred}; input count failures: {len(count_failures)}.", flush=True)
        return deferred + len(count_failures)


def main():
    parser = argparse.ArgumentParser(description="Run the configured models on shared CSAT image inputs.")
    parser.add_argument("--config", type=Path, default=CONFIGS / "2027" / "csat" / "benchmark.json")
    parser.add_argument("--exam-dir", type=Path)
    parser.add_argument("--exam-year", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", action="append")
    parser.add_argument("--budget-usd", type=run_budget.positive_usd)
    parser.add_argument("--keys-stdin", action="store_true", help="Read a JSON object of provider keys from stdin")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prepare", action="store_true", help="Render available papers and verify hashes locally")
    modes.add_argument("--run", action="store_true", help="Submit available parts, using the supplied total budget")
    modes.add_argument("--score", action="store_true", help="Score saved answers using the supplied answer PDFs")
    args = parser.parse_args()
    if args.run and args.budget_usd is None:
        parser.error("--run requires --budget-usd")
    config = json.loads(resolve_local_path(args.config).read_text(encoding="utf-8"))
    if config["generation_retries"] != 0:
        parser.error("This preset sends each paid generation at most once")
    parallelism = config["parallelism_per_provider"]
    if type(parallelism) is not int or parallelism < 1:
        parser.error("parallelism_per_provider must be a positive integer")
    order = config.get("subject_priority")
    if order is not None and (not isinstance(order, list) or len(order) != len(SUBJECT_GROUPS)
                              or set(order) != SUBJECT_GROUPS):
        parser.error("subject_priority must list each subject group once")
    priorities = {subject: index for index, subject in enumerate(order)} if order else None
    models = [Model(**entry) for entry in config["models"]]
    if any(model.model not in PROVIDER_MODELS.get(model.provider, ()) or model.scope not in {"all", "core"}
           for model in models) or len({model.model for model in models}) != len(models):
        parser.error("Each configured model must be supported, unique and have a valid scope")
    if args.model:
        models = [model for model in models if model.model in args.model]
        if {model.model for model in models} != set(args.model):
            parser.error("Unknown model in --model")
    parts = openai.exam_parts()
    jobs = selected_jobs(parts, models, priorities)
    if order:
        print(f"Submission priority: {' -> '.join(order)}.")
    for model in models:
        included = [job.part for job in jobs if job.model == model]
        print(f"{model.model} {model.effort}: {len(included)} parts, "
              f"{sum(part.question_count for part in included)} questions, 0 automatic generation retries.")
    if not (args.prepare or args.run or args.score):
        print(f"{len(jobs)} generation requests in the full plan. No API calls made.", flush=True)
        return 0

    year = args.exam_year or config["exam_year"]
    exam_dir = resolve_local_path(args.exam_dir or ROOT / config["exam_dir"])
    output = resolve_local_path(args.output or ROOT / config["output_dir"])
    def refresh_public_status():
        if (resolve_local_path(args.config).resolve() == (CONFIGS / "2027/csat/benchmark.json").resolve()
                and year == 2027 and output.resolve() == (ROOT / config["output_dir"]).resolve()
                and exam_dir.resolve() == (ROOT / config["exam_dir"]).resolve()):
            from suneung_benchmark.web_status import refresh
            try:
                refresh()
            except (OSError, ValueError, KeyError) as error:
                print(f"Public leaderboard update failed: {error}", file=sys.stderr)

    with run_budget.locked_output(output):
        if args.score:
            openai.summarize(output, exam_root=exam_dir, parts=parts, allow_unscored=True)
            refresh_public_status()
            return 0
        unique_parts = list(dict.fromkeys(job.part for job in jobs))
        prepared, missing = prepare_exams(unique_parts, exam_dir, year)
        openai.write_json(output / "inputs.json", {
            "exam_year": year, "exam_dir": str(exam_dir.resolve()),
            "prepared": {name: info for name, (_, info) in prepared.items()},
            "awaiting_files": missing,
        })
        print(f"Prepared {len(prepared)}/{len(unique_parts)} parts from {exam_dir}.", flush=True)
        if missing:
            print(f"Awaiting {', '.join(dict.fromkeys(missing.values()))}.", flush=True)
        refresh_public_status()
        if args.prepare or not prepared:
            return int(bool(missing))
        supplied_keys = None
        if args.keys_stdin:
            try:
                supplied_keys = json.load(sys.stdin)
            except ValueError:
                parser.error("Expected a JSON object of provider keys on stdin")
            if not isinstance(supplied_keys, dict) or any(
                name not in KEY_NAMES or not isinstance(key, str) for name, key in supplied_keys.items()
            ):
                parser.error("Expected provider names and string keys on stdin")
        try:
            execute(jobs, prepared, output, args.budget_usd, parallelism, supplied_keys, priorities)
        finally:
            openai.summarize(output, exam_root=exam_dir, parts=parts, allow_unscored=True)
        refresh_public_status()
        receipts = [json.loads(job.path(output).read_text(encoding="utf-8"))
                    for job in jobs if job.path(output).is_file()]
        completed = sum(row["status"] == "completed" for row in receipts)
        print(f"Completed: {completed}/{len(jobs)}. Error or incomplete: {len(receipts)-completed}. "
              f"Awaiting result or unsubmitted: {len(jobs)-len(receipts)}.", flush=True)
        return int(completed != len(jobs))


if __name__ == "__main__":
    raise SystemExit(main())
