import argparse
import logging

from sbs.common import settings as settings_mod
from sbs.common.store import Store
from sbs.corpus import split
from sbs.corpus.steps import ALL, CORPUS_STEPS, run_step


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
