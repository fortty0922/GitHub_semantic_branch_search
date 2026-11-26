import re
from pathlib import Path

import pandas as pd

from sbs.common.store import Store
from sbs.corpus import split

RESIDUE = {
    "url": re.compile(r"https?://|www\.", re.I),
    "html_tag": re.compile(r"</?[a-zA-Z][a-zA-Z0-9-]*\b[^>]*>"),
    "table_pipe": re.compile(r"\|"),
    "backtick": re.compile(r"`"),
    "heading": re.compile(r"^#{1,6}\s", re.M),
    "link_syntax": re.compile(r"\]\(|\]\["),
}


def residue(store: Store, examples: int = 5, context: int = 40) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Counts per residue kind, and example snippets around the first match in each example doc."""
    counts = {name: 0 for name in RESIDUE}
    snippets = []
    for repo_id, text in store.iter_rows("SELECT repo_id, text FROM docs_clean"):
        for name, pattern in RESIDUE.items():
            m = pattern.search(text)
            if not m:
                continue
            counts[name] += 1
            if counts[name] <= examples:
                start, end = max(0, m.start() - context), m.end() + context
                snippets.append((name, repo_id, " / ".join(text[start:end].splitlines())))
    summary = pd.DataFrame({"docs": counts})
    return summary, pd.DataFrame(snippets, columns=["kind", "repo_id", "context"])


def split_summary(store: Store, path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {"frozen": False}
    try:
        test = split.test_ids(path)
    except split.TamperedError as e:
        return {"frozen": True, "hash_ok": False, "error": str(e)}
    train = split.train_ids(store, path)
    labelled = store.labelled()
    rows = [(labelled.get(i, "not labelled"), "test") for i in test] + [(labelled[i], "train") for i in train]
    per_domain = pd.DataFrame(rows, columns=["domain", "set"]).value_counts().unstack(fill_value=0)
    return {
        "frozen": True,
        "hash_ok": True,
        "disjoint": not (test & train),
        "test": len(test),
        "train": len(train),
        "test_not_labelled_now": sum(i not in labelled for i in test),
        "per_domain": per_domain,
    }
