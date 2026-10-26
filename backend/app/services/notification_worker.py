"""Bounded in-app worker and standalone command: python -m backend.app.services.notification_worker."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from .notification_outbox import activate_scheduled_alerts, deliver_one
from ..security import database_url

log = logging.getLogger(__name__)


async def notification_worker(stop: asyncio.Event) -> None:
    if not database_url() or os.getenv("ECOPULSE_NOTIFICATION_WORKER_ENABLED", "true").lower() != "true":
        return
    try:
        poll = max(1, min(60, float(os.getenv("ECOPULSE_NOTIFICATION_POLL_SECONDS", "5"))))
    except ValueError:
        poll = 5
    scheduled_check = 0.0
    while not stop.is_set():
        try:
            # Claim one job immediately before sending, so queued work never loses
            # a lease while waiting behind a slow provider request in a batch.
            if time.monotonic() >= scheduled_check:
                await asyncio.to_thread(activate_scheduled_alerts)
                scheduled_check = time.monotonic() + poll
            processed = await asyncio.to_thread(deliver_one)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # DB exceptions can contain credentials, SQL parameters or private text.
            log.warning("Notification worker unavailable (%s); will retry", type(error).__name__)
            processed = False
        if not processed:
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll)
            except asyncio.TimeoutError:
                pass


async def _main() -> None:
    stop = asyncio.Event()
    await notification_worker(stop)


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
