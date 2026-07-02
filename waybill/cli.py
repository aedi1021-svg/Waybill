"""Waybill CLI.

Two commands for Phase 1:

    python -m waybill.cli demo        # run the agent over a few exceptions, print decisions
    python -m waybill.cli eval        # run the eval harness, print the report

Later phases add a `serve` command (FastAPI) and a queue consumer; this CLI stays
the fastest way to see the core working.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

from waybill.agent.agent import WaybillAgent
from waybill.agent.llm import OllamaClient
from waybill.data.generator import SyntheticGenerator
from waybill.eval.harness import run_eval


def _demo(n: int) -> None:
    llm = OllamaClient()
    mode = "LLM (Ollama)" if llm.available() else "heuristic fallback (Ollama not reachable)"
    print(f"Classifier mode: {mode}\n")

    gen = SyntheticGenerator()
    agent = WaybillAgent()
    for shipment, event in gen.dataset(n):
        res = agent.handle(shipment, event)
        print(f"- {event.tracking_number} | {event.carrier}")
        print(f"  msg: {event.raw_message}")
        print(
            f"  -> {res.classification.exception_type.value} "
            f"({res.classification.severity.value}, conf={res.confidence:.2f}) "
            f"=> {res.disposition.value}"
        )
        for a in res.actions:
            print(f"     action: {a.kind} — {a.summary}")
        print()


def _eval(n: int) -> None:
    report = run_eval(n=n)
    print(json.dumps(report.to_dict(), indent=2))


def _migrate() -> None:
    """Apply Alembic migrations. Thin wrapper so `waybill migrate` just works
    inside the container without needing the alembic CLI in PATH knowledge."""
    rc = subprocess.call([sys.executable, "-m", "alembic", "upgrade", "head"])
    sys.exit(rc)


def _seed(n: int) -> None:
    """Generate n exceptions, run them through the agent, and persist every
    decision to the append-only journal. Proves the DB path end to end."""
    from waybill.db.repository import Journal

    journal = Journal()
    llm = OllamaClient()
    model_name = llm.model if llm.available() else "heuristic"
    agent = WaybillAgent(journal=journal, model_name=model_name)

    gen = SyntheticGenerator()
    for shipment, event in gen.dataset(n):
        agent.handle(shipment, event)
    print(f"Persisted {n} handled exceptions to the journal.")

    recent = journal.recent_resolutions(limit=5)
    print("\nMost recent decisions:")
    for r in recent:
        print(f"  {r.decided_at:%Y-%m-%d %H:%M} | {r.exception_type} "
              f"({r.severity}, conf={r.confidence:.2f}) -> {r.disposition}")


def _serve(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run("waybill.api.app:app", host=host, port=port, log_level="info")


def _train() -> None:
    """Train the classifier, log to MLflow, and eval-gate promotion to Staging."""
    from waybill.ml.train_register import main as train_main

    rc = train_main()
    sys.exit(rc)


def _drift(window: int) -> None:
    from waybill.ml.drift import detect_drift

    print(json.dumps(detect_drift(window=window).to_dict(), indent=2))


def _retrain(force: bool) -> None:
    from waybill.ml.retrain import retrain

    sys.exit(retrain(force=force))


def main() -> None:
    parser = argparse.ArgumentParser(prog="waybill")
    sub = parser.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="run the agent over sample exceptions")
    d.add_argument("-n", type=int, default=8)

    e = sub.add_parser("eval", help="run the eval harness")
    e.add_argument("-n", type=int, default=200)

    sub.add_parser("migrate", help="apply database migrations")

    s = sub.add_parser("seed", help="persist a batch of handled exceptions to the DB")
    s.add_argument("-n", type=int, default=50)

    sv = sub.add_parser("serve", help="run the FastAPI service")
    sv.add_argument("--host", default="0.0.0.0")
    sv.add_argument("--port", type=int, default=8000)

    sub.add_parser("train", help="train the classifier, log to MLflow, gate promotion")

    dr = sub.add_parser("drift", help="check the journal for model drift")
    dr.add_argument("--window", type=int, default=500)

    rt = sub.add_parser("retrain", help="drift-triggered retrain (--force to always)")
    rt.add_argument("--force", action="store_true")

    args = parser.parse_args()
    if args.cmd == "demo":
        _demo(args.n)
    elif args.cmd == "eval":
        _eval(args.n)
    elif args.cmd == "migrate":
        _migrate()
    elif args.cmd == "seed":
        _seed(args.n)
    elif args.cmd == "serve":
        _serve(args.host, args.port)
    elif args.cmd == "train":
        _train()
    elif args.cmd == "drift":
        _drift(args.window)
    elif args.cmd == "retrain":
        _retrain(args.force)


if __name__ == "__main__":
    main()
