"""Publish scores and recorded cost estimates from local pilots."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ("cost-pilot-max", "claude-max", "sonnet-terra-max", "hydrogen-xhigh-paid", "carbon-high-sample")
MODELS = {
    "gpt-6.2-astra": ("GPT-6.2 Astra", "Astra", "OpenAI"),
    "gpt-6.2-sol": ("GPT-6.2 Sol", "Sol", "OpenAI"),
    "gpt-6.1-terra": ("GPT-6.1 Terra", "Terra", "OpenAI"),
    "claude-fable-5-2": ("Claude Fable 5.2", "Fable", "Anthropic"),
    "claude-opus-5-5": ("Claude Opus 5.5", "Opus", "Anthropic"),
    "claude-sonnet-5-5": ("Claude Sonnet 5.5", "Sonnet", "Anthropic"),
    "gemini-4-hydrogen": ("Gemini 4 Hydrogen", "Hydrogen", "Google"),
    "gemini-4-carbon": ("Gemini 4 Carbon", "Carbon", "Google"),
}


def area(subject):
    if subject in ("korean", "math", "english"):
        return subject
    if subject.endswith("-1"):
        return "science1"
    if subject.endswith("-2"):
        return "science2"
    return "social"


def published_points(row):
    maximum, earned = row.get("max_points"), row.get("raw_points")
    if not maximum or earned is None or (earned == "" and int(row["answered"] or 0)):
        return {}
    maximum, earned = int(maximum), int(earned or 0)
    if maximum <= 0 or not 0 <= earned <= maximum:
        raise ValueError(f"Invalid points for {row['model']}/{row['part']}")
    return {"points": {"earned": earned, "max": maximum}}


def export():
    exam = ROOT / "results/2026/csat"
    parts, models, hashes = {}, {}, {}
    for run in RUNS:
        folder = exam / run
        with (folder / "costs.csv").open(encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                model, part = row["model"], row["part"]
                digest = row["input_sha256"]
                if not digest or hashes.setdefault(part, digest) != digest:
                    raise ValueError(f"Different exam images for {part}")
                count, answered = int(row["question_count"]), int(row["answered"])
                if answered and row["correct"] == "":
                    raise ValueError(f"Unscored answers for {model}/{part}")
                correct = int(row["correct"] or 0)
                if not 0 <= correct <= answered <= count:
                    raise ValueError(f"Invalid score for {model}/{part}")
                definition = {"label": row["label"], "area": area(row["subject"]), "count": count}
                if parts.setdefault(part, definition) != definition:
                    raise ValueError(f"Inconsistent part definition for {part}")
                name, short, provider = MODELS[model]
                record = models.setdefault(model, {"name": name, "short": short, "provider": provider,
                                                   "effort": row["effort"], "parts": {}})
                if part in record["parts"] or record["effort"] != row["effort"]:
                    raise ValueError(f"Conflicting result for {model}/{part}")
                raw = json.loads((folder / model / f"{part}.json").read_text(encoding="utf-8"))
                paid = float(row["estimated_usd"]) if row["estimated_usd"] and not raw.get("billed_usage_unknown") else None
                equivalent = raw.get("billing_price_equivalent_usd") if raw.get("billing_tier") == "free" else paid
                record["parts"][part] = {"answered": answered, "correct": correct,
                                         "cost": equivalent, "billedEstimate": paid,
                                         **published_points(row)}
    path = ROOT / "web/public/data/2026/csat/pilot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"year": 2026, "exam": "대학수학능력시험", "parts": parts, "models": models}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(f"{len(models)} models, {len(parts)} bundles -> {path.relative_to(ROOT)}")


if __name__ == "__main__":
    export()
