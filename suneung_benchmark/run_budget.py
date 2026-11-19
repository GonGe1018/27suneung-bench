import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation


def positive_usd(value):
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Budget must be a positive USD amount") from error
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Budget must be a positive USD amount")
    return amount


def add_options(parser):
    parser.add_argument("--budget-usd", type=positive_usd, help="Whole-run standard token budget, including saved attempts")
    parser.add_argument("--plan-only", action="store_true", help="Count input tokens and show the plan without generating answers")


@contextmanager
def locked_output(output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    lock = output_dir / ".paid-run.lock"
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(fd)
        lock.unlink()


@contextmanager
def record_submission(path):
    # Keep this marker if the request or result write raises. Its cost can be unknown.
    pending = path.with_suffix(".pending")
    pending.touch(exist_ok=False)
    yield
    pending.unlink()


def estimate_cost(input_tokens, output_limit, input_rate, output_rate):
    if type(input_tokens) is not int or input_tokens < 0:
        raise ValueError("Invalid token count from provider")
    padded_input = (input_tokens * 105 + 99) // 100 + 512
    upper = (padded_input * Decimal(str(input_rate)) + output_limit * Decimal(str(output_rate))) / 1_000_000
    return {"input_tokens": input_tokens, "reserved_input_tokens": padded_input,
            "output_token_limit": output_limit, "reserved_usd": str(upper)}


def preflight(output_dir, jobs, estimate, workers, budget):
    existing = [json.loads(path.read_text(encoding="utf-8")) for path in output_dir.glob("*/*.json")]
    if any(output_dir.glob("*/*.pending")) or any(row.get("billed_usage_unknown") for row in existing):
        raise RuntimeError("A previous request has uncertain usage. Reconcile the run with provider billing before continuing.")
    spent = sum((Decimal(str(row.get("estimated_usd") or 0)) for row in existing), Decimal(0))

    def price_job(job):
        model, part = job
        return {"model": model, "part": part.name, **estimate(model, part)}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        requests = list(pool.map(price_job, jobs))
    reserved = sum((Decimal(row["reserved_usd"]) for row in requests), Decimal(0))
    total = spent + reserved
    plan = {"previous_usd": str(spent), "reserved_usd": str(reserved), "planned_total_usd": str(total),
            "budget_usd": str(budget) if budget is not None else None, "requests": requests}
    (output_dir / "budget-plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(f"Budget plan: ${spent:.4f} saved + ${reserved:.4f} reserved for {len(requests)} requests = ${total:.4f}", flush=True)
    if budget is not None and total > budget:
        raise RuntimeError(f"Planned total ${total:.4f} exceeds budget ${budget:.4f}.")
