"""
CLI entrypoint for the offline jobs.

Usage:
    python -m jobs seed                 # load tickers.json -> instruments
    python -m jobs ingest [--period 5y] [--ticker SYM ...]
    python -m jobs analyze [--min-observations 30] [--retain 5]

Requires DATABASE_URL (via env or settings). Jobs are TensorFlow-free; the
ingestion job lazily imports yfinance only when its default source is used.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys


def _init_db_or_exit() -> None:
    import db

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        try:
            from settings import get_settings

            url = get_settings().database_url.strip()
        except Exception:
            url = ""
    if not url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        raise SystemExit(2)
    db.init_engine(url)


def main(argv=None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(prog="jobs", description="Offline analytics jobs")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed", help="Seed instruments from tickers.json")

    p_ingest = sub.add_parser("ingest", help="Ingest OHLCV into price_series")
    p_ingest.add_argument("--period", default="5y", help="History window (default 5y)")
    p_ingest.add_argument("--ticker", action="append", dest="tickers", help="Limit to specific ticker(s)")

    p_analyze = sub.add_parser("analyze", help="Compute metrics + covariance, publish a version")
    p_analyze.add_argument("--min-observations", type=int, default=30)
    p_analyze.add_argument("--retain", type=int, default=None)

    args = parser.parse_args(argv)
    _init_db_or_exit()

    if args.command == "seed":
        from jobs.seed import seed_from_tickers_json

        result = seed_from_tickers_json()
    elif args.command == "ingest":
        from jobs.ingest import ingest

        result = ingest(period=args.period, tickers=args.tickers)
    elif args.command == "analyze":
        from jobs.analyze import analyze

        result = analyze(min_observations=args.min_observations, retain=args.retain)
    else:  # pragma: no cover - argparse enforces a valid command
        parser.error("unknown command")
        return 2

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
