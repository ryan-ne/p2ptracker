import asyncio
import logging
import time

from .db import Database

log = logging.getLogger("retention")


async def retention_loop(db: Database, raw_retention_days: int, every_sec: int = 3600) -> None:
    """Hourly: roll raw snapshots older than the retention window into 1h OHLC rows, then delete them."""
    while True:
        try:
            removed = await db.rollup_and_prune(int(time.time()) - raw_retention_days * 86400)
            if removed:
                log.info("rolled up and pruned %d raw rows", removed)
        except Exception:
            log.exception("retention job failed")
        await asyncio.sleep(every_sec)
