"""Build the current public exam snapshot from local inputs and scored receipts."""
import argparse
import csv
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from .cost_pilot import exam_parts
from .export_web import MODELS, ROOT, area
from .jev_run import MODEL_ID as JEV_MODEL, public_scores as jev_scores


def write_snapshot(target, data):
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, mode="w", delete=False, encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write("\n")
    Path(file.name).replace(target)


def summarize_progress(parts, models, ready_parts, scored):
    progress = {}
    for group in dict.fromkeys(part["area"] for part in parts.values()):
        group_parts = {name for name, part in parts.items() if part["area"] == group}
        scheduled = [key for key, model in models.items() if group_parts <= set(model["expectedParts"])]
        progress[group] = {
            "readyParts": len(group_parts & ready_parts),
            "totalParts": len(group_parts),
            "scoredModels": sum(all((key, part) in scored for part in group_parts) for key in scheduled),
            "plannedModels": len(scheduled),
        }
    return progress


def part_in_scope(scope, area_name):
    if scope == "korean":
        return area_name == "korean"
    return (scope == "all" or area_name in {"korean", "math", "english"}
            or (scope == "science2" and area_name == "science2"))


def build_snapshot(config, plan, papers, output, previous, released=False, finalize=False):
    definitions = exam_parts()
    parts = {p.name: {"label": p.label, "area": area(p.subject), "count": p.question_count} for p in definitions}
    configured = {m["model"]: m for m in config["models"]}
    roster = {m["id"]: m for m in plan}
    for model_id, m in configured.items():
        known = roster.get(model_id, {})
        name, short, provider = MODELS.get(model_id, (known.get("name", model_id), known.get("name", model_id), known.get("provider", m["provider"])))
        roster[model_id] = {**roster.get(model_id, {}), "name": name, "short": short,
                            "provider": provider, "scope": m["scope"], "effort": m["effort"]}
    models = {}
    for key, m in roster.items():
        coverage = m["scope"]
        expected = [k for k, p in parts.items() if part_in_scope(coverage, p["area"])]
        models[key] = {"name": m["name"], "short": m.get("short", m["name"]), "provider": m["provider"],
                       "effort": m.get("effort", "미정"), "scope": coverage, "expectedParts": expected, "parts": {}}
    scored, hashes = set(), {}
    table = output / "costs.csv"
    if table.exists():
        with table.open(encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                key, part = row["model"], row["part"]
                if key not in models or part not in models[key]["expectedParts"] or not row.get("max_points"):
                    continue
                if (key, part) in scored:
                    raise ValueError(f"Duplicate score for {key}/{part}")
                answered = int(row["answered"] or 0)
                if answered and row["correct"] == "":
                    continue
                if row["status"] in ("api_error", "transport_error", "failed") and not answered:
                    continue
                correct = int(row["correct"] or 0)
                if int(row["question_count"]) != parts[part]["count"] or not 0 <= correct <= answered <= parts[part]["count"]:
                    raise ValueError(f"Invalid score for {key}/{part}")
                digest = row.get("input_sha256")
                if not digest or hashes.setdefault(part, digest) != digest:
                    raise ValueError(f"Different inputs for {part}")
                cost = float(row["estimated_usd"]) if row["estimated_usd"] else None
                score = {"answered": answered, "correct": correct, "cost": cost, "billedEstimate": cost}
                effort = row.get("effort")
                if effort and effort != models[key]["effort"]:
                    score["effort"] = effort
                models[key]["parts"][part] = score
                scored.add((key, part))
    if JEV_MODEL in models:
        jev = models[JEV_MODEL]
        allowed = [part for part in definitions if part.name in jev["expectedParts"]]
        for name, score in jev_scores(output.parent / JEV_MODEL, allowed):
            jev["parts"][name] = score
            scored.add((JEV_MODEL, name))
    ready_parts = {
        part.name for part in definitions
        if any((papers / f"{name}-exam.pdf").is_file() for name in (part.name, part.subject))
    }
    ready_parts.update(part for _, part in scored)
    expected_jobs = {(key, part) for key in configured for part in models[key]["expectedParts"]}
    complete = bool(expected_jobs) and expected_jobs <= scored
    if finalize and not scored:
        raise ValueError("At least one scored bundle is required to finalize.")
    state = "awaiting"
    if ready_parts or released or previous.get("state") in ("running", "complete"):
        state = "running"
    if complete or finalize or previous.get("state") == "complete":
        state = "complete"
    # A public checkout without private receipts must keep already published results.
    for key, record in previous.get("models", {}).items():
        if key in models:
            models[key]["parts"] = {**record["parts"], **models[key]["parts"]}
    scored = {(key, part) for key, model in models.items() for part in model["parts"]}
    snapshot = {"year": config["exam_year"], "exam": "대학수학능력시험", "state": state, "parts": parts, "models": models}
    published = sum(
        sum(part in model["parts"] for part in model["expectedParts"])
        for model in models.values()
    )
    snapshot["completion"] = {
        "plannedBundles": sum(len(model["expectedParts"]) for model in models.values()),
        "scoredBundles": len(scored),
        "publishedBundles": published,
    }
    if state != "complete":
        snapshot["progress"] = summarize_progress(parts, models, ready_parts, scored)
    return snapshot


def refresh(released=False, finalize=False):
    base = ROOT / "configs/2027/csat"
    target = ROOT / "web/public/data/2027/csat/benchmark.json"
    config = json.loads((base / "benchmark.json").read_text(encoding="utf-8"))
    plan = json.loads((base / "planned-models.json").read_text(encoding="utf-8"))
    previous = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    data = build_snapshot(config, plan, ROOT / config["exam_dir"], ROOT / config["output_dir"], previous, released, finalize)
    write_snapshot(target, data)
    return data["state"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--released", action="store_true", help="Record the confirmed official release")
    parser.add_argument("--finalize", action="store_true", help="Publish available results and defer remaining models")
    args = parser.parse_args()
    print(refresh(args.released, args.finalize))
