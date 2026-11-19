from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs"
CSAT_2026_EXAMS = ROOT / "exams" / "2026" / "csat"
CSAT_2026_RESULTS = ROOT / "results" / "2026" / "csat"

LEGACY_CONFIGS = {
    "benchmark_2027.json": CONFIGS / "2027" / "csat" / "benchmark.json",
    "benchmark_2026_sonnet_terra.json": CONFIGS / "2026" / "csat" / "sonnet-terra-128k.json",
}


def resolve_local_path(path):
    """Accept paths from before the CSAT inputs and runs were grouped by year."""
    path = Path(path)
    if path.is_file():
        return path
    try:
        parts = path.resolve().relative_to(ROOT).parts
    except ValueError:
        return path

    if len(parts) == 1:
        return LEGACY_CONFIGS.get(parts[0], path)
    if len(parts) != 2:
        return path

    directory, name = parts
    if directory == "exams" and name in {"2026", "2027"}:
        return ROOT / directory / name / "csat"
    year, _, run_id = name.partition("-")
    if directory == "results" and year in {"2026", "2027"} and run_id:
        return ROOT / directory / year / "csat" / run_id
    return path
