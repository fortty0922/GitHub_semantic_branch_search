import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .crawl import Config, Crawler
from .storage import Storage


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m crawler")
    parser.add_argument("--db", default="crawler.db")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="crawl until the time budget is used (or Ctrl+C)")
    budget = run.add_mutually_exclusive_group()
    budget.add_argument("--hours", type=float)
    budget.add_argument("--minutes", type=float)
    run.add_argument("--workers", type=int, default=8, help="concurrent REST README requests")
    run.add_argument("--graphql-workers", type=int, default=4)
    run.add_argument("--batch", type=int, default=50, help="max repos per GraphQL README query")
    run.add_argument("--reserve", type=float, default=0.01, help="fraction of each quota left unused")
    run.add_argument("--max-backlog", type=int, default=50_000)
    run.add_argument("--log-dir", default="logs", help="directory for the summary log file")
    sub.add_parser("stats", help="print database statistics")
    args = parser.parse_args()

    if args.cmd == "stats":
        storage = Storage(args.db)
        print(json.dumps(storage.stats(), indent=2))
        storage.close()
        return

    load_dotenv()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is not set (env or .env)")
    log_path = setup_logging(args.log_dir)
    duration = args.hours * 3600 if args.hours else args.minutes * 60 if args.minutes else None
    cfg = Config(token=token, db_path=args.db, duration=duration, workers=args.workers,
                 graphql_workers=args.graphql_workers, batch_size=args.batch, reserve_ratio=args.reserve, max_backlog=args.max_backlog)
    logging.getLogger("crawler").info("summary log: %s", log_path)
    try:
        asyncio.run(Crawler(cfg).run())
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.getLogger("crawler").exception("crawler stopped by an unhandled error")
        sys.exit(1)


class SummaryFilter(logging.Filter):
    """File log keeps warnings/errors plus records logged with extra=SUMMARY."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING or getattr(record, "summary", False)


def setup_logging(log_dir: str) -> Path:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    path = Path(log_dir) / f"crawler-{datetime.now():%Y%m%d-%H%M%S}.log"
    file = logging.FileHandler(path, encoding="utf-8")
    file.setFormatter(fmt)
    file.addFilter(SummaryFilter())
    logging.basicConfig(level=logging.INFO, handlers=[console, file])
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return path


if __name__ == "__main__":
    main()
