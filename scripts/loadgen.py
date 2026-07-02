"""Load generator.

Fires a stream of synthetic exceptions at a running Waybill service so the
Grafana dashboards fill with realistic data. Uses the same generator as the app,
so the mix of exception types (and thus the auto-resolve/escalate split) is
representative.

Usage:
    python scripts/loadgen.py http://localhost:8080 --rate 5 --duration 300
"""
from __future__ import annotations

import argparse
import time

import httpx

from waybill.data.generator import SyntheticGenerator


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("base_url")
    p.add_argument("--rate", type=float, default=5.0, help="requests per second")
    p.add_argument("--duration", type=int, default=300, help="seconds to run")
    args = p.parse_args()

    gen = SyntheticGenerator(seed=int(time.time()))
    interval = 1.0 / args.rate
    deadline = time.time() + args.duration
    sent = 0

    with httpx.Client(base_url=args.base_url, timeout=10.0) as client:
        while time.time() < deadline:
            shipment, event = gen.exception()
            try:
                client.post("/exceptions", json={
                    "tracking_number": event.tracking_number,
                    "carrier": event.carrier,
                    "raw_message": event.raw_message,
                    "origin": shipment.origin,
                    "destination": shipment.destination,
                    "value_usd": shipment.value_usd,
                    "customer": shipment.customer,
                    "true_type": event.true_type.value if event.true_type else None,
                    "true_severity": event.true_severity.value if event.true_severity else None,
                })
                sent += 1
                if sent % 20 == 0:
                    print(f"sent {sent} exceptions")
            except Exception as exc:  # keep going on transient errors
                print(f"request failed: {exc}")
            time.sleep(interval)

    print(f"done — sent {sent} exceptions")


if __name__ == "__main__":
    main()
