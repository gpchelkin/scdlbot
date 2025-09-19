#!/usr/bin/env python3
"""Run Huey worker for ffmpeg tasks with concurrency=2."""

import logging
import os
import sys

from huey.consumer import Consumer

from scdlbot.ffmpeg_worker import HUEY_WORKERS, huey

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


def main():
    """Run the ffmpeg Huey worker."""
    logger.info(f"Starting ffmpeg worker with {HUEY_WORKERS} workers")

    consumer = Consumer(
        huey,
        workers=HUEY_WORKERS,  # Default is 2, controlled by env var
        periodic=True,
        initial_delay=0.1,
        backoff=1.15,
        max_delay=10.0,
        scheduler_interval=1,
        worker_type="thread",  # Use threads for I/O-bound ffmpeg operations
        check_worker_health=True,
        health_check_interval=10,
    )

    try:
        consumer.run()
    except KeyboardInterrupt:
        logger.info("Shutting down ffmpeg worker...")
        consumer.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()