import asyncio
import os
import sys

from dotenv import load_dotenv

from crawler.client import GitHubClient
from sbs.common.store import Store
from sbs.corpus import awesome_fetch, clean, dedup, manual, split, tokens
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
        clean.run(store, experiment["corpus"]["clean"])
    elif step == "tokenize":
        tokens.run(store)
    elif step == "dedup":
        dedup.run(store, experiment["corpus"]["dedup"], manual.read_excluded(experiment["paths"]["manual_exclude"]))
    elif step == "fetch-awesome":
        asyncio.run(fetch_awesome(store, settings))
    elif step == "label":
        assign.run(store, settings, manual.read_labels(experiment["paths"]["manual_labels"]))
    elif step == "split":
        split.run(store, settings)
