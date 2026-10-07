"""
Seed the ``instruments`` table from tickers.json (idempotent).

The group label in tickers.json becomes the instrument's ``asset_class``.
Lightweight metadata is inferred where cheap and safe:
- country: 'IN' for ``.NS`` symbols, else null (filled by reference data later)
- currency: 'INR' for ``.NS`` symbols, else null

Everything else (sector, esg/shariah flags, liquidity tier) defaults to the
schema defaults and is expected to be enriched later from a reference-data
source (Phase 3/4). Re-running the seed updates asset_class/metadata for known
tickers and inserts any new ones, without duplicating rows.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


def _infer_country_currency(ticker: str) -> Tuple[str | None, str | None]:
    if ticker.endswith(".NS"):
        return "IN", "INR"
    return None, None


def seed_instruments(groups: Dict[str, List[str]]) -> dict:
    """
    Upsert instruments from a {asset_class_label: [ticker, ...]} mapping.

    Returns a summary dict: {"inserted": n, "updated": m, "total": k}.
    Idempotent: calling twice with the same input performs 0 inserts the 2nd time.
    """
    import db
    from db.models import Instrument

    if not db.is_enabled():
        raise RuntimeError("Database is not configured (set DATABASE_URL).")

    session = db.get_session()
    inserted = updated = 0

    for asset_class, tickers in groups.items():
        for ticker in tickers:
            country, currency = _infer_country_currency(ticker)
            existing = session.get(Instrument, ticker)
            if existing is None:
                session.add(
                    Instrument(
                        ticker=ticker,
                        asset_class=asset_class,
                        country=country,
                        currency=currency,
                        active=True,
                    )
                )
                inserted += 1
            else:
                # Keep the universe definition authoritative for asset_class +
                # inferred metadata; never clobber a ticker into existence twice.
                existing.asset_class = asset_class
                if country and not existing.country:
                    existing.country = country
                if currency and not existing.currency:
                    existing.currency = currency
                updated += 1

    session.commit()
    total = session.query(Instrument).count()
    logger.info("Seeded instruments: inserted=%d, updated=%d, total=%d.", inserted, updated, total)
    return {"inserted": inserted, "updated": updated, "total": total}


def seed_from_tickers_json(path: str | None = None) -> dict:
    """
    Load groups from tickers.json and seed them.

    Reads the JSON directly (does NOT import investment_strategy, which would
    pull in TensorFlow) so the seed/analytics jobs stay TF-free.
    """
    import json
    import os

    if path is None:
        # tickers.json lives next to the backend modules (one level up from jobs/).
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.environ.get("TICKERS_CONFIG_PATH", os.path.join(here, "tickers.json"))

    with open(path, "r") as f:
        raw = json.load(f)
    groups = raw.get("groups", {})
    cleaned: Dict[str, List[str]] = {}
    for label, tickers in groups.items():
        if isinstance(label, str) and isinstance(tickers, list):
            syms = [str(t).strip() for t in tickers if str(t).strip()]
            if syms:
                cleaned[label] = syms
    if not cleaned:
        raise ValueError(f"No valid ticker groups found in {path}")
    return seed_instruments(cleaned)
