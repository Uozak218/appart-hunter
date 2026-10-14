#!/usr/bin/env python3
"""Re-score a single listing end-to-end.

Usage (inside the scraper container):
    docker compose exec scraper python scripts/rescore.py <listing_id>
    docker compose exec scraper python scripts/rescore.py --source leboncoin --external 12345

Useful for iterating on prompts or debugging a specific result.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from src.config import get_criteria
from src.models.listing import Listing
from src.notifier.queue import get_notification_queue
from src.scoring.pipeline import ScoringPipeline
from src.storage.database import dispose_engine, get_session_factory
from src.utils.logging import logger, setup_logging


async def _run(listing_id: int | None, source: str | None, external: str | None, notify: bool) -> int:
    factory = get_session_factory()
    async with factory() as session:
        if listing_id is None:
            if not (source and external):
                logger.error("Provide either LISTING_ID or --source + --external")
                return 2
            result = await session.execute(
                select(Listing.id).where(
                    Listing.source == source, Listing.external_id == external
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                logger.error(f"No listing matching source={source} external={external}")
                return 3
            listing_id = int(row)

        pipeline = ScoringPipeline(session, get_criteria())
        scored = await pipeline.process_listing(listing_id)

    if scored is None:
        print("No alert produced (filtered out, below threshold, or budget exhausted).")
        return 0

    print(
        f"Score {scored.score}/100 "
        f"({'URGENT' if scored.is_urgent else 'alert'}) — {scored.raw.title[:80]}"
    )
    for h in scored.highlights:
        print(f"  + {h}")
    for f in scored.red_flags:
        print(f"  ! {f}")

    if notify:
        queue = get_notification_queue()
        await queue.start()
        await queue.enqueue(scored)
        await asyncio.sleep(3)  # let the worker flush
        await queue.stop()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser("rescore")
    parser.add_argument("listing_id", type=int, nargs="?")
    parser.add_argument("--source", help="source name (leboncoin, seloger, ...)")
    parser.add_argument("--external", help="external id on that source")
    parser.add_argument(
        "--notify", action="store_true", help="also push to Telegram + Discord"
    )
    args = parser.parse_args()

    setup_logging("INFO")
    try:
        rc = asyncio.run(_run(args.listing_id, args.source, args.external, args.notify))
    finally:
        asyncio.run(dispose_engine())
    sys.exit(rc)


if __name__ == "__main__":
    main()
