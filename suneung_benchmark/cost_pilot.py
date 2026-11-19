import argparse
import base64
import csv
import getpass
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import pymupdf
import requests

from . import run_budget
from .paths import CSAT_2026_RESULTS, resolve_local_path
from .fetch_2026 import ROOT, SUBJECTS


API_URL = "https://api.openai.com/v1/responses"
IMAGE_DPI = 180
MAX_REASONING_OUTPUT_TOKENS = 25_000
RATES = {
    # Standard short-context USD per million tokens: input, cache read, cache write, output.
    "gpt-6.2-astra": (8.0, 0.40, 10.0, 40.0),
    "gpt-6.2-sol": (2.0, 0.10, 2.50, 10.0),
    "gpt-6.1-terra": (1.0, 0.05, 1.25, 5.0),
}
PILOT_MODELS = ("gpt-6.2-astra", "gpt-6.2-sol")
ELECTIVE_ANSWER_COLUMNS = {
    "korean-common": (0, 3),
    "korean-speech": (6,),
    "korean-language": (9,),
    "math-common": (0, 3),
    "math-statistics": (6,),
    "math-calculus": (9,),
    "math-geometry": (12,),
}
CIRCLED_CHOICES = dict(zip("①②③④⑤", (1, 2, 3, 4, 5)))


@dataclass(frozen=True)
class ExamPart:
    subject: str
    name: str
    label: str
    first: int
    last: int
    pages: tuple[int, int] | None = None

    @property
    def question_count(self):
        return self.last - self.first + 1


def exam_parts():
    parts = [
        ExamPart("korean", "korean-common", "국어 공통", 1, 34, (1, 12)),
        ExamPart("korean", "korean-speech", "화법과 작문", 35, 45, (13, 16)),
        ExamPart("korean", "korean-language", "언어와 매체", 35, 45, (17, 20)),
        ExamPart("math", "math-common", "수학 공통", 1, 22, (1, 8)),
        ExamPart("math", "math-statistics", "확률과 통계", 23, 30, (9, 12)),
        ExamPart("math", "math-calculus", "미적분", 23, 30, (13, 16)),
        ExamPart("math", "math-geometry", "기하", 23, 30, (17, 20)),
        ExamPart("english", "english", "영어", 1, 45, (1, 8)),
    ]
    return parts + [
        ExamPart(subject, subject, label, 1, 20)
        for subject, label in list(SUBJECTS.items())[3:]
    ]


def pdf_input(part, source):
    if part.pages:
        start, end = part.pages
        with pymupdf.open(source) as original, pymupdf.open() as subset:
            subset.insert_pdf(original, from_page=start - 1, to_page=end - 1)
            pdf_data = subset.tobytes(garbage=4, deflate=True)
    else:
        pdf_data = source.read_bytes()
    return {
        "type": "input_file",
        "filename": f"{part.name}.pdf",
        "file_data": "data:application/pdf;base64," + base64.b64encode(pdf_data).decode("ascii"),
        "detail": "high",
    }


def image_input(part, source, *, root=ROOT, image_group=None):
    directory = root / "images" / (image_group or part.subject)
    directory.mkdir(parents=True, exist_ok=True)
    items = []
    with pymupdf.open(source) as original:
        start, end = part.pages or (1, original.page_count)
        for number in range(start, end + 1):
            path = directory / f"page-{number:02d}.png"
            page = original[number - 1].get_pixmap(dpi=IMAGE_DPI, colorspace=pymupdf.csRGB, alpha=False)
            data = page.tobytes("png")
            if not path.exists() or path.read_bytes() != data:
                temp = path.with_suffix(".tmp")
                temp.write_bytes(data)
                temp.replace(path)
            items.append({
                "type": "input_image",
                "image_url": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
                "detail": "original",
            })
    return items


def make_input(part, input_format, *, root=ROOT, exam_year=2026, source=None, image_group=None):
    source = source or root / f"{part.subject}-exam.pdf"

    directions = (
        f"{exam_year}학년도 대학수학능력시험 홀수형 {part.label} 영역의 "
        f"{part.first}번부터 {part.last}번까지 각 문항을 풀어라. "
        "answers 배열에 문제 번호 순서대로 최종 답 하나씩만 담아라. "
        "객관식은 선택지 번호 1~5, 수학 단답형은 계산한 정수로 기입하라."
    )
    if part.subject == "english":
        with pymupdf.open(root / "english-transcript.pdf") as transcript:
            script = "\n".join(page.get_text(sort=True) for page in transcript)
        directions += "\n\n영어 1~17번 공식 듣기 대본:\n" + script

    files = (image_input(part, source, root=root, image_group=image_group)
             if input_format == "image" else [pdf_input(part, source)])
    return [{"role": "user", "content": [{"type": "input_text", "text": directions}, *files]}]


def request_body(model, part, content, effort, output_limit=None):
    return {
        "model": model,
        "input": content,
        "reasoning": {"effort": effort},
        "max_output_tokens": output_limit or (MAX_REASONING_OUTPUT_TOKENS if effort == "max" else max(2300, part.question_count * 220)),
        "service_tier": "default",
        "store": False,
        "text": {"format": {
            "type": "json_schema",
            "name": "exam_answers",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"answers": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": part.question_count,
                    "maxItems": part.question_count,
                }},
                "required": ["answers"],
                "additionalProperties": False,
            },
        }},
    }


def estimate_openai(api_key, body):
    payload = {key: body[key] for key in ("model", "input", "reasoning", "text")}
    response = requests.post(f"{API_URL}/input_tokens", headers={"Authorization": f"Bearer {api_key}"},
                             json=payload, timeout=(20, 90), allow_redirects=False)
    response.raise_for_status()
    token_count = response.json()["input_tokens"]
    _, _, write_rate, output_rate = RATES[body["model"]]
    ceiling = run_budget.estimate_cost(token_count, body["max_output_tokens"], 2 * write_rate, output_rate)
    if ceiling["reserved_input_tokens"] > 272_000:
        raise ValueError("Prompt crosses the short-context pricing limit")
    return ceiling


def charge(model, usage):
    input_rate, cache_rate, write_rate, output_rate = RATES[model]
    details = usage.get("input_tokens_details") or {}
    cached = details.get("cached_tokens") or 0
    written = details.get("cache_write_tokens") or 0
    uncached = usage["input_tokens"] - cached - written
    long_write = (details.get("cache_write_tokens_details") or {}).get("ttl_12h_tokens") or 0
    price = uncached * input_rate + cached * cache_rate + (written + long_write) * write_rate
    return round((price + usage["output_tokens"] * output_rate) / 1_000_000, 8)


def call_model(api_key, body, *, max_retries=3):
    headers = {"Authorization": f"Bearer {api_key}"}
    started = time.monotonic()
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(API_URL, headers=headers, json=body, timeout=(20, 600), allow_redirects=False)
            data = response.json()
        except (requests.RequestException, ValueError) as error:
            return {"status": type(error).__name__, "billed_usage_unknown": True}
        error = data.get("error") or {}
        if response.status_code == 429 and error.get("code") == "rate_limit_exceeded" and attempt < max_retries:
            try:
                delay = float(response.headers.get("Retry-After", 4 * (2 ** attempt)))
            except ValueError:
                delay = 4 * (2 ** attempt)
            time.sleep(min(60, max(1, delay)))
            continue
        break

    row = {"http_status": response.status_code, "elapsed_s": round(time.monotonic() - started, 2)}
    if not response.ok:
        return {**row, "status": "api_error", "billed_usage_unknown": response.status_code >= 500,
                "error": {k: error.get(k) for k in ("code", "type", "param")}}

    usage = data.get("usage") or {}
    answer_text = "".join(
        item.get("text", "")
        for output in data.get("output", [])
        if output.get("type") == "message"
        for item in output.get("content", [])
        if item.get("type") == "output_text"
    )
    answers = []
    try:
        answers = json.loads(answer_text)["answers"]
    except (ValueError, KeyError, TypeError):
        pass
    return {
        **row,
        "status": data.get("status"),
        "incomplete_details": data.get("incomplete_details"),
        "stop_reason": (data.get("incomplete_details") or {}).get("reason"),
        "response_id": data.get("id"),
        "response_model": data.get("model"),
        "response_effort": (data.get("reasoning") or {}).get("effort"),
        "service_tier": data.get("service_tier"),
        "usage": usage,
        "estimated_usd": charge(body["model"], usage) if usage else None,
        "billed_usage_unknown": not usage,
        "answers": answers,
        "answered": len(answers),
    }


def write_json(path, row):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def score_keys(root=ROOT, parts=None, allow_missing=False):
    tables = {}
    parts = exam_parts() if parts is None else parts
    for subject in dict.fromkeys(part.subject for part in parts):
        path = root / f"{subject}-answer.pdf"
        if allow_missing and not path.exists():
            continue
        with pymupdf.open(path) as document:
            tables[subject] = document[0].find_tables().tables[0].extract()

    keys = {}
    for part in parts:
        if part.subject not in tables:
            continue
        table = tables[part.subject]
        columns = ELECTIVE_ANSWER_COLUMNS.get(part.name, range(0, len(table[0]), 3))
        key = {}
        for column in columns:
            for row in table:
                number, answer, points = row[column:column + 3]
                if number and number.strip().isdigit():
                    answer = answer.strip()
                    value = CIRCLED_CHOICES[answer] if answer in CIRCLED_CHOICES else int(answer)
                    key[int(number)] = (value, int(points))
        if sorted(key) != list(range(part.first, part.last + 1)):
            raise ValueError(f"Unexpected answer key for {part.name}")
        keys[part.name] = [key[number] for number in sorted(key)]
    return keys


def score_row(row, answer_key):
    maximum = sum(points for _, points in answer_key)
    answers = row.get("answers", [])
    if len(answers) != len(answer_key):
        return {"max_points": maximum}
    earned = [points for actual, (expected, points) in zip(answers, answer_key) if actual == expected]
    return {"correct": len(earned), "raw_points": sum(earned), "max_points": maximum}


def summarize_usage(row):
    totals = {}
    attempts = 0
    while row:
        attempts += 1
        usage = row.get("usage") or {}
        fields = {**usage, **(usage.get("input_tokens_details") or {}), **(usage.get("output_tokens_details") or {})}
        for key in ("input_tokens", "cached_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens"):
            if isinstance(fields.get(key), int):
                totals[key] = totals.get(key, 0) + fields[key]
        row = row.get("prior_attempt")
    return {**totals, "attempts": attempts}


def summarize(output_dir, *, exam_root=ROOT, parts=None, allow_unscored=False):
    rows = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(output_dir.glob("*/*.json"))]
    try:
        keys = score_keys(exam_root, parts, allow_unscored)
    except (OSError, IndexError, ValueError, RuntimeError) as error:
        if not allow_unscored:
            raise
        print(f"Answer keys could not be read ({type(error).__name__}). Saved usage and responses.", flush=True)
        keys = {}
    fields = [
        "model", "subject", "part", "label", "question_count", "answered", "status", "elapsed_s",
        "effort", "response_effort", "input_format", "image_count", "input_sha256",
        "attempts", "max_tokens", "stop_reason", "refusal_category",
        "input_tokens", "cached_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens", "estimated_usd",
        "correct", "raw_points", "max_points",
    ]
    temporary = output_dir / f".costs-{os.getpid()}.tmp"
    with temporary.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            score = score_row(row, keys[row["part"]]) if row["part"] in keys else {}
            writer.writerow({**row, **summarize_usage(row), **score})
    temporary.replace(output_dir / "costs.csv")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=resolve_local_path, default=CSAT_2026_RESULTS / "cost-pilot-max")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default="max")
    parser.add_argument("--input-format", choices=["pdf", "image"], default="image")
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--key-stdin", action="store_true")
    run_budget.add_options(parser)
    args = parser.parse_args()
    if args.budget_usd is None and not args.plan_only:
        parser.error("--budget-usd is required for generation")
    if args.max_output_tokens is not None and not 16 <= args.max_output_tokens <= 128_000:
        parser.error("--max-output-tokens must be between 16 and 128000")
    api_key = sys.stdin.readline().strip() if args.key_stdin else os.environ.get("OPENAI_API_KEY")
    if not api_key:
        api_key = getpass.getpass("OpenAI API key: ")
    if not api_key:
        parser.error("An API key is required")

    parts = exam_parts()
    assert len(parts) == 25 and sum(part.question_count for part in parts) == 487
    inputs = {part.name: make_input(part, args.input_format) for part in parts}
    signatures = {name: hashlib.sha256(json.dumps(content, ensure_ascii=False).encode("utf-8")).hexdigest()
                  for name, content in inputs.items()}
    print(f"Prepared {len(parts)} parts with input={args.input_format} and effort={args.effort}.", flush=True)

    def result_path(model, part):
        return args.output / model / f"{part.name}.json"

    def get_body(model, part):
        return request_body(model, part, inputs[part.name], args.effort, args.max_output_tokens)

    def run(model, part):
        path = args.output / model / f"{part.name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = inputs[part.name]
        body = get_body(model, part)
        with run_budget.record_submission(path):
            result = call_model(api_key, body)
            row = {**result, "model": model, "subject": part.subject, "part": part.name, "label": part.label,
                   "question_count": part.question_count, "first_question": part.first, "effort": args.effort,
                   "input_format": args.input_format, "input_sha256": signatures[part.name],
                   "max_tokens": body["max_output_tokens"],
                   "image_count": sum(item["type"] == "input_image" for item in content[0]["content"])}
            write_json(path, row)
        return row

    with run_budget.locked_output(args.output):
        jobs = []
        for part in parts:
            for model in PILOT_MODELS:
                path = result_path(model, part)
                if path.exists():
                    saved = json.loads(path.read_text(encoding="utf-8"))
                    if saved["effort"] != args.effort or saved.get("input_format", "pdf") != args.input_format:
                        raise ValueError("Choose a new --output directory for a different reasoning effort or input format")
                    if args.input_format == "image" and saved.get("input_sha256") != signatures[part.name]:
                        raise ValueError(f"Input changed for {part.name}; use a new --output directory")
                else:
                    jobs.append((model, part))
        run_budget.preflight(args.output, jobs, lambda model, part: estimate_openai(api_key, get_body(model, part)),
                             args.workers, args.budget_usd)
        if args.plan_only:
            return 0
        failed = False
        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                for future in as_completed(pool.submit(run, model, part) for model, part in jobs):
                    row = future.result()
                    failed |= row["status"] != "completed" or row.get("answered") != row["question_count"]
                    print(f"{row['model']} {row['part']}: {row['status']} "
                          f"{row.get('answered', 0)}/{row['question_count']}; ${row.get('estimated_usd')}", flush=True)
        finally:
            summarize(args.output)
        return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
