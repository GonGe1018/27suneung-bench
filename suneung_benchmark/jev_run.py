"""Run Jev 1.14 on the released 2027 CSAT multiple-choice questions."""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
import os
import sys
from time import perf_counter

from PIL import Image
import requests

from . import cost_pilot as csat, run_budget
from .paths import CONFIGS, ROOT


MODEL_ID = "jev-1.14"
MODEL = "typesafe/" + MODEL_ID
API = "https://openrouter.ai/api"
REQUEST_RESERVATION = Decimal(262_144) * Decimal("0.042") / 1_000_000
IMAGE_EDGE = 1568
CHOICES = {f"option_{n}": f"시험지에 표시된 {label}번 선택지"
           for n, label in enumerate("①②③④⑤", 1)}
ANSWERS = {name: n for n, name in enumerate(CHOICES, 1)}


def question_numbers(part):
    last = part.last
    if part.subject == "math":
        last = 15 if part.name == "math-common" else 28
    return range(part.first, last + 1)


def resize_image(item):
    source = base64.b64decode(item["image_url"].split(",", 1)[1], validate=True)
    buffer = BytesIO()
    with Image.open(BytesIO(source)) as image:
        image.thumbnail((IMAGE_EDGE, IMAGE_EDGE), Image.Resampling.LANCZOS)
        width, height = image.size
        if min(width, height) < 280 or max(width, height) > IMAGE_EDGE:
            raise ValueError("Trial image does not fit Jev's input dimensions")
        image.save(buffer, format="PNG", optimize=True)
    data = buffer.getvalue()
    image_url = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    return {"type": "image_url", "image_url": {"url": image_url}}, {
        "sha256": sha256(data).hexdigest(), "width": width, "height": height,
    }


def prepare_request(part, prepared):
    original, metadata = prepared
    content = original[0]["content"]
    state = [f"2027학년도 대학수학능력시험 홀수형 {part.label} 영역의 시험지다. 이미지 순서는 시험지 페이지 순서다."]
    if part.subject == "english":
        state.append(content[0]["text"].split("\n\n", 1)[1])
    images = [resize_image(item) for item in content if item["type"] == "input_image"]
    state.extend(image for image, _ in images)
    questions = {
        f"q{n:02d}": {
            "type": "choice",
            "instructions": f"시험지 이미지에서 {n}번 문항을 풀고 가장 적절한 정답 선택지 하나를 선택하세요.",
            "criteria": CHOICES,
        }
        for n in question_numbers(part)
    }
    body = {"model": MODEL, "state": state, "questions": questions, "provider": {"allow_fallbacks": False}}
    wire = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(wire) >= 32 * 1024 * 1024:
        raise ValueError(f"{part.name}: request exceeds the Jev image upload limit")
    return wire, {
        "label": part.label,
        "subject": part.subject,
        "source_input_sha256": metadata["input_sha256"],
        "request_sha256": sha256(wire).hexdigest(),
        "questions": list(question_numbers(part)),
        "images": [info for _, info in images],
        "request_bytes": len(wire),
    }


def grade_receipt(path, part, key, receipt):
    answers = receipt["payload"].get("answers") or {}
    grading = []
    for n in question_numbers(part):
        choice = answers.get(f"q{n:02d}") or {}
        answer = ANSWERS.get(choice.get("choice")) if choice.get("type") == "choice" else None
        correct, points = key[n - part.first]
        grading.append({"number": n, "answer": answer, "correct": answer == correct, "points": points,
                        "confidence": choice.get("confidence")})
    cost = (receipt["payload"].get("usage") or {}).get("cost")
    csat.write_json(path, {**receipt, "part": part.name, "label": part.label,
                          "subject": part.subject, "cost_usd": cost, "grading": grading})
    print(f"{part.name} HTTP={receipt['http_status']} "
          f"{sum(row['correct'] for row in grading)}/{sum(row['answer'] is not None for row in grading)} "
          f"answered / {len(grading)} planned; cost={cost}", flush=True)


def submit(headers, output, part, key, wire, metadata):
    path = output / f"{part.name}.json"
    with run_budget.record_submission(path):
        csat.write_json(path.with_suffix(".pending"),
                        {"model": MODEL, "request_sha256": metadata["request_sha256"]})
        started = perf_counter()
        status = None
        try:
            response = requests.post(API + "/alpha/decisions", headers=headers, data=wire,
                                     timeout=(15, 120), allow_redirects=False)
            status = response.status_code
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON decision object")
        except (requests.RequestException, ValueError) as error:
            payload = {"error": {"message": type(error).__name__}}
        receipt = {"http_status": status, "seconds": perf_counter() - started, "payload": payload}
        csat.write_json(output / f"{part.name}.raw.json", receipt)
        grade_receipt(path, part, key, receipt)


def public_scores(directory, parts):
    for part in parts:
        path = directory / f"{part.name}.json"
        if not path.is_file():
            continue
        receipt = json.loads(path.read_text(encoding="utf-8"))
        model = receipt["payload"].get("model") or ""
        if receipt["http_status"] != 200 or not (model == MODEL or model.startswith(MODEL + "-")):
            continue
        grading = receipt["grading"]
        if [row["number"] for row in grading] != list(question_numbers(part)):
            raise ValueError(f"Unexpected Jev question numbers for {part.name}")
        answered = sum(row["answer"] in ANSWERS.values() for row in grading)
        correct = sum(row["correct"] for row in grading)
        if not 0 <= correct <= answered <= len(grading):
            raise ValueError(f"Invalid Jev score for {part.name}")
        cost = receipt["cost_usd"]
        points = {"earned": sum(row["points"] for row in grading if row["correct"]),
                  "max": sum(row["points"] for row in grading)}
        score = {"answered": answered, "correct": correct, "cost": cost,
                 "billedEstimate": cost, "points": points}
        if part.subject == "math":
            score["coverage"] = {"kind": "multiple-choice", "count": len(grading)}
        yield part.name, score


def main():
    from run_benchmark import prepare_exams, subject_group
    from .web_status import refresh

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area", action="append",
                        choices=("korean", "math", "english", "science-2", "science-1", "social"))
    parser.add_argument("--run", action="store_true", help="Send each available unsent request once")
    parser.add_argument("--key-stdin", action="store_true")
    parser.add_argument("--budget-usd", type=run_budget.positive_usd)
    args = parser.parse_args()
    if args.run and args.budget_usd is None:
        parser.error("--run requires --budget-usd")
    base = CONFIGS / "2027" / "csat"
    config = json.loads((base / "benchmark.json").read_text(encoding="utf-8"))
    exam = ROOT / config["exam_dir"]
    output = (ROOT / config["output_dir"]).parent / MODEL_ID
    parts = [p for p in csat.exam_parts() if not args.area or subject_group(p) in args.area]
    paths = {part.name: output / f"{part.name}.json" for part in parts}
    needed = [part for part in parts if not paths[part.name].is_file()]
    prepared, _ = prepare_exams(needed, exam, config["exam_year"])
    ready = [part for part in needed if part.name in prepared]
    keys = csat.score_keys(exam, ready, allow_missing=True)
    ready = [part for part in ready if part.name in keys]
    awaiting = len(needed) - len(ready)
    print(f"Jev: {len(parts) - len(needed)} saved, {len(ready)} ready, {awaiting} awaiting official files.", flush=True)
    if not args.run or not ready:
        return

    with run_budget.locked_output(output):
        jobs = []
        for part in ready:
            path = paths[part.name]
            if path.is_file():
                continue
            raw = output / f"{part.name}.raw.json"
            if raw.is_file():
                grade_receipt(path, part, keys[part.name], json.loads(raw.read_text(encoding="utf-8")))
                path.with_suffix(".pending").unlink(missing_ok=True)
            elif not path.with_suffix(".pending").is_file():
                wire, metadata = prepare_request(part, prepared[part.name])
                jobs.append((part, wire, metadata))
            else:
                print(f"{part.name}: previous submission awaits reconciliation", flush=True)

        reserve = REQUEST_RESERVATION * len(jobs)
        if reserve > args.budget_usd:
            parser.error(f"New requests could cost ${reserve:.4f}, above the supplied budget")
        if jobs:
            key = sys.stdin.readline().strip() if args.key_stdin else os.environ.get("OPENROUTER_API_KEY")
            if not key:
                parser.error("Provide an OpenRouter key")
            headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
            response = requests.get(API + "/v1/key", headers=headers, timeout=10)
            response.raise_for_status()
            remaining = response.json()["data"].get("limit_remaining")
            if remaining is not None and reserve > Decimal(str(remaining)):
                parser.error("The OpenRouter key has insufficient remaining balance")
            manifest_path = output / "inputs.json"
            manifest = (json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file()
                        else {"model": MODEL, "exam_year": config["exam_year"], "form": "odd",
                              "maximum_image_edge": IMAGE_EDGE, "automatic_retries": 0, "parts": {}})
            manifest["parts"].update({part.name: metadata for part, _, metadata in jobs})
            csat.write_json(manifest_path, manifest)
            print(f"Sending {len(jobs)} Jev requests. Reserved ${reserve:.4f}. Automatic retries: 0.", flush=True)
            with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
                tasks = [pool.submit(submit, headers, output, part, keys[part.name], wire, meta)
                         for part, wire, meta in jobs]
                for task in as_completed(tasks):
                    task.result()
        refresh()


if __name__ == "__main__":
    main()
