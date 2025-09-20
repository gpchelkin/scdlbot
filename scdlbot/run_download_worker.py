#!/usr/bin/env python
"""Run the Huey download worker."""

import logging
import os
import sys

from huey.consumer import Consumer

from scdlbot.download_worker import huey, HUEY_WORKERS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


def main():
    """Run the Huey consumer for download tasks."""
    logger.info(f"Starting download worker with {HUEY_WORKERS} workers")

    consumer = Consumer(
        huey,
        worker_type="process",
        workers=HUEY_WORKERS,
        periodic=True,
        check_worker_health=True,
        health_check_interval=10,
    )

    try:
        consumer.run()
    except KeyboardInterrupt:
        logger.info("Shutting down download worker...")
        consumer.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
