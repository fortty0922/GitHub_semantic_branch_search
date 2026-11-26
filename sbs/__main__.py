import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from crawler.client import GitHubClient
from sbs.common import settings as settings_mod
from sbs.common.store import Store
from sbs.corpus import awesome_fetch, clean, dedup, split, tokens
from sbs.corpus.labels import assign

CORPUS_STEPS = ("clean", "tokenize", "dedup", "fetch-awesome", "label", "split", "all")
ALL = ("clean", "tokenize", "dedup", "label", "split")


async def fetch_awesome(store: Store, settings: dict) -> None:
    load_dotenv()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is not set (env or .env)")
    client = GitHubClient(token)
    try:
        await client.init_limits()
        await awesome_fetch.run(store, settings["awesome_lists"], client)
    finally:
        await client.close()


def run_step(step: str, store: Store, settings: dict) -> None:
    experiment = settings["experiment"]
    if step == "clean":
        clean.run(store)
    elif step == "tokenize":
        tokens.run(store)
    elif step == "dedup":
        dedup.run(store, experiment["corpus"]["dedup"])
    elif step == "fetch-awesome":
        asyncio.run(fetch_awesome(store, settings))
    elif step == "label":
        assign.run(store, settings)
    elif step == "split":
        split.run(store, settings)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sbs")
    parser.add_argument("--db", default="crawler.db")
    parser.add_argument("--config", default="config")
    sub = parser.add_subparsers(dest="stage", required=True)
    corpus = sub.add_parser("corpus", help="data preparation")
    corpus.add_argument("step", choices=CORPUS_STEPS)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings = settings_mod.load(args.config)
    store = Store(args.db)
    try:
        for step in ALL if args.step == "all" else (args.step,):
            try:
                run_step(step, store, settings)
            except split.FrozenError as e:
                logging.getLogger("sbs").info("split skipped: %s", e)
    finally:
        store.close()


if __name__ == "__main__":
    main()
