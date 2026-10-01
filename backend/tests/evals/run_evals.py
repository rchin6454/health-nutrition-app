"""Run the eval suite against the real Groq models (architecture §12, implementation plan §5.1).

Usage (from backend/):
    uv run python -m tests.evals.run_evals                  # every case
    uv run python -m tests.evals.run_evals --smoke          # the small subset CI runs
    uv run python -m tests.evals.run_evals --slice nutrition --slice scope
    uv run python -m tests.evals.run_evals --case fs-rice-overnight
    uv run python -m tests.evals.run_evals --resume tests/evals/results/<file>.json
    uv run python -m tests.evals.run_evals --judge          # add the LLM-as-judge rubric
    uv run python -m tests.evals.run_evals --recheck tests/evals/results/<file>.json

--recheck re-runs only the deterministic checks on a saved results file, with the current
cases.yaml, after a check or an expectation was corrected. It makes no model call; cases whose
question changed are marked skipped, to be re-run with --resume.

Needs GROQ_API_KEY, and EVAL_DATABASE_URL (or DATABASE_URL) pointing at a database with the
knowledge tables loaded. The run only reads that database: conversations are not stored, and
failures are captured into the results file instead of the `failures` table, so production
review queries never count eval traffic.

Each case runs the real pipeline (`run_turn`) in-process: the same gates, knowledge lookup,
prompts and validation as POST /api/chat. Calls are paced by the same per-model Groq budget as
production, with a longer wait (`LLM_MAX_WAIT_S`, default 75 s here). A case refused by a Groq
limit is re-run once the limit allows; if the daily limit is reached the run stops, saves what
it has, and can be continued the next day with --resume. Groq limits are shared with the live
app when the same API key is used.

Results are written to tests/evals/results/{date}_{PROMPT_VERSION}[_smoke|_subset].json and a
summary table is printed. The exit code is 0 only if every exit-criteria target is met.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

# Before any app import reads the settings: wait up to 75 s for a per-minute Groq window.
os.environ.setdefault("LLM_MAX_WAIT_S", "75")

from app import db
from app.config import get_settings
from app.knowledge import embeddings
from app.llm.client import LLMCallError, LLMResult
from app.pipeline import answer as answer_module
from app.pipeline import orchestrator
from app.pipeline import understanding as understanding_module
from app.prompts import PROMPT_VERSION
from app.schemas.answer import ChatResponse
from app.store.conversations import StoredMessage
from tests.evals.cases import Case, load_cases
from tests.evals.checks import CheckResult, Outcome, run_checks
from tests.evals.judge import JUDGE_PROMPT_VERSION, JudgeScore, judge

RESULTS_DIR = Path(__file__).with_name("results")
# A Groq limit that frees up within this long is waited out; a longer one stops the run.
MAX_LIMIT_WAIT_S = 300.0
MAX_ATTEMPTS = 4

# (metric, the check it is computed from, target) — the Phase 5 exit criteria.
TARGETS: list[tuple[str, str, float]] = [
    ("schema_parse_rate", "", 1.0),  # model outputs that parsed / model outputs
    ("response_schema_rate", "schema", 1.0),
    ("source_null_rate", "source_null", 1.0),
    ("scope_blocked_in_code_rate", "blocked_in_code", 1.0),
    ("classification_accuracy", "category", 0.90),
    ("nutrition_numeric_accuracy", "numbers", 0.90),
    ("food_safety_verdict_accuracy", "verdict", 0.95),
]


# --- Observing the pipeline ---


@dataclass
class Observed:
    """What the current case did, captured by the hooks below."""

    groq_requests: dict[str, int] = field(default_factory=dict)
    model_outputs: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    limit_error: LLMCallError | None = None


_observed = Observed()

StructuredCall = Callable[..., Awaitable[LLMResult]]


def _counting(label: str, call: StructuredCall) -> StructuredCall:
    async def wrapper(**kwargs: Any) -> LLMResult:
        try:
            result = await call(**kwargs)
        except LLMCallError as exc:
            if exc.kind in ("rate_limited", "budget_exceeded"):
                _observed.limit_error = exc
            if exc.kind not in ("budget_exceeded", "config_error"):  # these never reach Groq
                _observed.groq_requests[label] = _observed.groq_requests.get(label, 0) + 1
            raise
        _observed.groq_requests[label] = _observed.groq_requests.get(label, 0) + 1
        if result.content is not None:
            _observed.model_outputs += 1
        return result

    return wrapper


async def _capture_failure(**kwargs: Any) -> None:
    _observed.failures.append(kwargs)


def install_hooks() -> None:
    """Count Groq requests per call and capture failures instead of writing them to the DB."""
    for label, module in (("understanding", understanding_module), ("answer", answer_module)):
        setattr(module, "structured_call", _counting(label, module.structured_call))  # noqa: B010
    setattr(orchestrator, "record_failure", _capture_failure)  # noqa: B010


# --- Running cases ---


def history_for(case: Case) -> list[StoredMessage]:
    now = datetime.now(UTC)
    messages: list[StoredMessage] = []
    for turn in case.history:
        request_id = str(uuid4())
        messages.append(StoredMessage(len(messages), now, request_id, "user", {"text": turn.user}))
        messages.append(
            StoredMessage(
                len(messages),
                now,
                request_id,
                "assistant",
                {"answer_type": "answer", "answer": turn.assistant},
            )
        )
    return messages


class DailyLimitReached(Exception):
    pass


async def run_case(case: Case, *, use_judge: bool) -> dict[str, Any]:
    global _observed
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _observed = Observed()
        start = time.perf_counter()
        turn = await orchestrator.run_turn(
            request_id=str(uuid4()),
            conversation_id=str(uuid4()),
            message=case.question,
            history=history_for(case),
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        limit = _observed.limit_error
        if limit is None:
            break
        wait = limit.retry_after_s
        if wait is None or wait > MAX_LIMIT_WAIT_S:
            raise DailyLimitReached(f"{limit.kind}: {limit.detail}")
        if attempt == MAX_ATTEMPTS:
            raise DailyLimitReached(f"still rate limited after {attempt} attempts: {limit.detail}")
        print(f"    rate limited ({limit.kind}); waiting {wait:.0f}s", flush=True)
        await asyncio.sleep(wait + 1)

    response = turn.response.model_dump(mode="json")
    outcome = Outcome(
        response=response,
        analysis_category=turn.analysis.category if turn.analysis else None,
        groq_requests=dict(_observed.groq_requests),
        model_outputs=_observed.model_outputs,
        failures=_observed.failures,
    )
    checks = run_checks(case, outcome)
    result: dict[str, Any] = {
        "id": case.id,
        "slice": case.slice,
        "question": case.question,
        "status": "passed" if all(c.passed for c in checks) else "failed",
        "checks": [asdict(c) for c in checks],
        "response": response,
        "analysis": turn.analysis.model_dump(mode="json") if turn.analysis else None,
        "context": turn.context.model_dump(mode="json") if turn.context else None,
        "failures": [_jsonable(f) for f in _observed.failures],
        "groq_requests": outcome.groq_requests,
        "model_outputs": outcome.model_outputs,
        "parse_failures": outcome.parse_failures,
        "usage": turn.usage,
        "latency_ms": elapsed_ms,
        "stage_ms": turn.stage_ms,
    }
    if use_judge and turn.response.answer_type == "answer":
        score = await judge(case.question, ChatResponse.model_validate(response), turn.context)
        result["judge"] = score.model_dump() if isinstance(score, JudgeScore) else {"error": score}
    return result


def _jsonable(failure: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in failure.items() if k not in ("request_id", "conversation_id")}


# --- Summary ---


def summarize(results: list[dict[str, Any]], total_cases: int) -> dict[str, Any]:
    done = [r for r in results if r["status"] in ("passed", "failed")]
    checks: dict[str, list[bool]] = {}
    for r in done:
        for c in r["checks"]:
            checks.setdefault(c["name"], []).append(c["passed"])

    def rate(values: list[bool]) -> float | None:
        return sum(values) / len(values) if values else None

    outputs = sum(r["model_outputs"] for r in done)
    parse_failures = sum(r["parse_failures"] for r in done)
    metrics: dict[str, Any] = {}
    for name, check, target in TARGETS:
        if check:
            values = checks.get(check, [])
            value, n = rate(values), len(values)
        else:
            value = (outputs - parse_failures) / outputs if outputs else None
            n = outputs
        metrics[name] = {
            "value": value,
            "n": n,
            "target": target,
            "met": value is not None and value >= target - 1e-9,
        }

    slices: dict[str, dict[str, int]] = {}
    for r in results:
        s = slices.setdefault(r["slice"], {"cases": 0, "passed": 0, "failed": 0, "skipped": 0})
        s["cases"] += 1
        s[r["status"]] += 1

    judged = [r["judge"] for r in done if "judge" in r and "error" not in r["judge"]]
    judge_summary = None
    if judged:
        judge_summary = {
            "cases": len(judged),
            "clarity_avg": round(sum(j["clarity"] for j in judged) / len(judged), 2),
            "relevance_avg": round(sum(j["relevance"] for j in judged) / len(judged), 2),
            "with_unsupported_claims": sum(1 for j in judged if j["unsupported_claims"]),
        }

    warnings: dict[str, int] = {}
    for r in done:
        for f in r["failures"]:
            key = f"{f.get('severity', 'error')}:{f['failure_type']}"
            warnings[key] = warnings.get(key, 0) + 1

    return {
        "cases_total": total_cases,
        "cases_run": len(done),
        "cases_passed": sum(1 for r in done if r["status"] == "passed"),
        "complete": len(done) == total_cases,
        "metrics": metrics,
        "checks": {name: {"passed": sum(v), "n": len(v)} for name, v in sorted(checks.items())},
        "slices": slices,
        "recorded_failures": dict(sorted(warnings.items())),
        "judge": judge_summary,
        "latency_ms_p50": _percentile([r["latency_ms"] for r in done], 0.5),
        "latency_ms_p95": _percentile([r["latency_ms"] for r in done], 0.95),
    }


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def print_summary(summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    print()
    print(f"{'slice':<16}{'cases':>7}{'passed':>8}{'failed':>8}{'skipped':>9}")
    for name, s in sorted(summary["slices"].items()):
        row = f"{name:<16}{s['cases']:>7}{s['passed']:>8}{s['failed']:>8}{s['skipped']:>9}"
        print(row)
    print()
    print(f"{'metric':<32}{'value':>8}{'n':>6}{'target':>8}  ")
    for name, m in summary["metrics"].items():
        value = "-" if m["value"] is None else f"{100 * m['value']:.1f}%"
        mark = "ok" if m["met"] else ("--" if m["value"] is None else "MISSED")
        print(f"{name:<32}{value:>8}{m['n']:>6}{100 * m['target']:>7.0f}%  {mark}")
    if summary["judge"]:
        j = summary["judge"]
        print(
            f"\njudge ({j['cases']} answers): clarity {j['clarity_avg']}/5, relevance "
            f"{j['relevance_avg']}/5, {j['with_unsupported_claims']} with unsupported claims"
        )
    print(f"\nlatency p50 {summary['latency_ms_p50']} ms, p95 {summary['latency_ms_p95']} ms")
    if summary["recorded_failures"]:
        print("recorded failures/warnings:", summary["recorded_failures"])
    failed = [r for r in results if r["status"] == "failed"]
    if failed:
        print(f"\n{len(failed)} failed case(s):")
        for r in failed:
            bad = [f"{c['name']} ({c['detail']})" for c in r["checks"] if not c["passed"]]
            print(f"  {r['id']}: " + "; ".join(bad))


# --- CLI ---


def select_cases(cases: list[Case], args: argparse.Namespace) -> list[Case]:
    if args.case:
        unknown = set(args.case) - {c.id for c in cases}
        if unknown:
            sys.exit(f"unknown case id(s): {sorted(unknown)}")
        cases = [c for c in cases if c.id in args.case]
    if args.slice:
        cases = [c for c in cases if c.slice in args.slice]
    if args.smoke:
        cases = [c for c in cases if c.smoke]
    if args.limit:
        cases = cases[: args.limit]
    return cases


def results_path(args: argparse.Namespace, subset: bool) -> Path:
    if args.resume:
        return Path(args.resume)
    suffix = "_smoke" if args.smoke else ("_subset" if subset else "")
    return RESULTS_DIR / f"{date.today().isoformat()}_{PROMPT_VERSION}{suffix}.json"


def save(path: Path, run: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


async def main(args: argparse.Namespace) -> int:
    all_cases = load_cases()
    cases = select_cases(all_cases, args)
    path = results_path(args, subset=len(cases) < len(all_cases))

    previous: dict[str, dict[str, Any]] = {}
    if args.resume:
        stored = json.loads(path.read_text(encoding="utf-8"))
        if stored["prompt_version"] != PROMPT_VERSION:
            sys.exit(f"{path} was run with {stored['prompt_version']}, not {PROMPT_VERSION}")
        previous = {r["id"]: r for r in stored["results"] if r["status"] != "skipped"}

    dsn = os.environ.get("EVAL_DATABASE_URL") or get_settings().database_url
    if not dsn:
        sys.exit("set EVAL_DATABASE_URL or DATABASE_URL")
    if not get_settings().groq_api_key:
        sys.exit("GROQ_API_KEY is not set")
    await db.open_pool(dsn)
    settings = get_settings()
    await asyncio.to_thread(embeddings.load, settings.embedding_model, settings.embedding_cache_dir)
    install_hooks()

    run: dict[str, Any] = {
        "prompt_version": PROMPT_VERSION,
        "judge_prompt_version": JUDGE_PROMPT_VERSION if args.judge else None,
        "models": {
            "understanding": settings.model_understanding,
            "answer": settings.model_answer,
            "reasoning_effort": {
                "understanding": settings.reasoning_effort_understanding,
                "answer": settings.reasoning_effort_answer,
            },
        },
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "selection": {"smoke": args.smoke, "slices": args.slice, "cases": args.case},
        "results": [],
    }
    results: list[dict[str, Any]] = []
    stopped: str | None = None
    try:
        for i, case in enumerate(cases, 1):
            if case.id in previous:
                results.append(previous[case.id])
                continue
            if stopped:
                results.append(_skipped(case, stopped))
                continue
            print(f"[{i}/{len(cases)}] {case.id} ...", end=" ", flush=True)
            try:
                result = await run_case(case, use_judge=args.judge)
            except DailyLimitReached as exc:
                stopped = f"Groq limit reached: {exc}"
                print("SKIPPED (Groq limit reached; continue later with --resume)")
                results.append(_skipped(case, stopped))
                continue
            results.append(result)
            failed = [c.name for c in map(_check, result["checks"]) if not c.passed]
            status = "PASS" if not failed else "FAIL " + ",".join(failed)
            print(f"{status} ({result['latency_ms'] / 1000:.1f}s)", flush=True)
            run["results"] = results
            save(path, run)  # after every case, so an interrupted run keeps its results
    finally:
        await db.close_pool()

    run["results"] = results
    run["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    run["summary"] = summarize(results, len(cases))
    save(path, run)
    print_summary(run["summary"], results)
    print(f"\nresults: {path}")
    if stopped:
        print(f"stopped early: {stopped}")
        return 2
    return (
        0 if all(m["met"] or m["value"] is None for m in run["summary"]["metrics"].values()) else 1
    )


def recheck(path: Path) -> int:
    """Re-run the checks on stored outcomes with the current cases.yaml (no model calls)."""
    run = json.loads(path.read_text(encoding="utf-8"))
    cases = {c.id: c for c in load_cases()}
    for r in run["results"]:
        if r["status"] == "skipped":
            continue
        case = cases.get(r["id"])
        if case is None or case.question != r["question"]:
            r.update(status="skipped", reason="case removed or question changed; re-run")
            continue
        outcome = Outcome(
            response=r["response"],
            analysis_category=(r["analysis"] or {}).get("category"),
            groq_requests=r["groq_requests"],
            model_outputs=r["model_outputs"],
            failures=r["failures"],
        )
        checks = run_checks(case, outcome)
        r["checks"] = [asdict(c) for c in checks]
        r["status"] = "passed" if all(c.passed for c in checks) else "failed"
    total = run.get("summary", {}).get("cases_total", len(run["results"]))
    run["summary"] = summarize(run["results"], total)
    run["rechecked_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    save(path, run)
    print_summary(run["summary"], run["results"])
    print(f"\nrechecked: {path}")
    return (
        0 if all(m["met"] or m["value"] is None for m in run["summary"]["metrics"].values()) else 1
    )


def _skipped(case: Case, reason: str) -> dict[str, Any]:
    return {"id": case.id, "slice": case.slice, "question": case.question, "status": "skipped",
            "reason": reason}  # fmt: skip


def _check(c: dict[str, Any]) -> CheckResult:
    return CheckResult(c["name"], c["passed"], c["detail"])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--smoke", action="store_true", help="only cases marked smoke: true")
    parser.add_argument("--slice", action="append", help="only this slice (repeatable)")
    parser.add_argument("--case", action="append", help="only this case id (repeatable)")
    parser.add_argument("--limit", type=int, help="at most this many cases")
    parser.add_argument("--resume", help="continue a results file: re-run its skipped cases")
    parser.add_argument("--judge", action="store_true", help="add the LLM-as-judge rubric")
    parser.add_argument("--recheck", help="re-run the checks on a results file (no model calls)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    cli_args = parse_args()
    if cli_args.recheck:
        sys.exit(recheck(Path(cli_args.recheck)))
    sys.exit(asyncio.run(main(cli_args)))
