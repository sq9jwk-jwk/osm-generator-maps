"""Worker process: `python -m app.jobs.worker` (systemd: osm-tilegen-worker)."""

from __future__ import annotations

import logging
import signal
import time

from .. import db
from . import queue
from .handlers import run_job

log = logging.getLogger("osm-tilegen.worker")
_running = True


def _stop(*_: object) -> None:
    global _running
    _running = False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    db.init_db()
    n = queue.requeue_interrupted()
    if n:
        log.info("requeued %d interrupted job(s)", n)
    log.info("worker started")
    while _running:
        job = queue.claim_next()
        if job is None:
            time.sleep(1.0)
            continue
        log.info("running job %s (%s)", job["id"], job["kind"])
        run_job(job)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
