import logging
import re

from sbs.common.store import Store

log = logging.getLogger("sbs")

TOKEN = re.compile(r"\.?[a-z0-9]+(?:[-.'_][a-z0-9]+)*(?:\+\+|#)?")
SENTENCE = re.compile(r"[.!?;:]+(?=\s|$)|\n")


def tokenize(text: str) -> list[list[str]]:
    sentences = []
    for part in SENTENCE.split(text.lower()):
        tokens = [t for t in TOKEN.findall(part) if not t.startswith(".") or t[1:].isalpha()]
        if tokens:
            sentences.append(tokens)
    return sentences


def serialize(sentences: list[list[str]]) -> str:
    return "\n".join(" ".join(s) for s in sentences)


def deserialize(text: str) -> list[list[str]]:
    return [line.split(" ") for line in text.split("\n") if line]


def run(store: Store) -> int:
    def rows():
        for repo_id, text in store.iter_rows("SELECT repo_id, text FROM docs_clean"):
            sentences = tokenize(text)
            if sentences:
                yield repo_id, serialize(sentences), sum(map(len, sentences))

    count = store.replace_all("tokens", ("repo_id", "text", "n_tokens"), rows())
    log.info("tokenize: %d documents", count)
    return count
