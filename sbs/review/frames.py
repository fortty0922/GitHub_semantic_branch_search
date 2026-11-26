import pandas as pd

from sbs.common.store import Store

STAGES = {
    "repos": "SELECT COUNT(*) FROM repos",
    "readme ok": "SELECT COUNT(*) FROM repos WHERE readme_status = 'ok'",
    "docs_clean": "SELECT COUNT(*) FROM docs_clean",
    "tokens": "SELECT COUNT(*) FROM tokens",
    "dedup kept": "SELECT COUNT(*) FROM dedup WHERE status = 'kept'",
    "labelled": "SELECT COUNT(*) FROM labels WHERE domain NOT IN ('unknown', 'other')",
    "other": "SELECT COUNT(*) FROM labels WHERE domain = 'other'",
    "unknown": "SELECT COUNT(*) FROM labels WHERE domain = 'unknown'",
}

REPOS = """
SELECT r.id, r.full_name, r.stars, r.description, r.topics, r.readme_status,
       d.status AS dedup, d.kept_id, l.domain, l.label_source, l.confidence, t.n_tokens,
       r.readme AS raw, c.text AS clean
FROM repos r
LEFT JOIN docs_clean c ON c.repo_id = r.id
LEFT JOIN tokens t ON t.repo_id = r.id
LEFT JOIN dedup d ON d.repo_id = r.id
LEFT JOIN labels l ON l.repo_id = r.id
"""


def query(store: Store, sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, store.db, params=params)


def has_table(store: Store, name: str) -> bool:
    return store.db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def stage_counts(store: Store) -> pd.DataFrame:
    rows = [(name, store.db.execute(sql).fetchone()[0]) for name, sql in STAGES.items()]
    return pd.DataFrame(rows, columns=["stage", "count"]).set_index("stage")


def repos(store: Store, ids: list[int] | None = None, text: bool = True) -> pd.DataFrame:
    sql = REPOS if text else REPOS.replace(",\n       r.readme AS raw, c.text AS clean", "")
    if ids is None:
        return query(store, sql)
    marks = ",".join("?" * len(ids))
    return query(store, f"{sql} WHERE r.id IN ({marks})", tuple(ids))


def duplicate_groups(store: Store, status: str, limit: int = 10) -> pd.DataFrame:
    groups = query(store, """
        SELECT kept_id, COUNT(*) AS duplicates FROM dedup WHERE status = ?
        GROUP BY kept_id ORDER BY duplicates DESC, kept_id LIMIT ?""", (status, limit))
    rows = []
    for kept_id, n in groups.itertuples(index=False):
        members = query(store, """
            SELECT d.repo_id, r.full_name, r.stars, d.status, substr(c.text, 1, 200) AS preview
            FROM dedup d JOIN repos r ON r.id = d.repo_id LEFT JOIN docs_clean c ON c.repo_id = d.repo_id
            WHERE d.repo_id = ? OR (d.kept_id = ? AND d.status = ?)
            ORDER BY d.status = 'kept' DESC, r.stars DESC""", (kept_id, kept_id, status))
        members.insert(0, "group", kept_id)
        members.insert(1, "group_size", n + 1)
        rows.append(members)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def label_crosstab(store: Store) -> pd.DataFrame:
    df = query(store, "SELECT domain, label_source FROM labels")
    if df.empty:
        return df
    return pd.crosstab(df["domain"], df["label_source"], margins=True)


def label_thresholds(store: Store) -> pd.DataFrame:
    return query(store, "SELECT * FROM label_thresholds ORDER BY domain").set_index("domain")


def domain_agreement(store: Store) -> pd.DataFrame:
    """Crawl domain (repo_domains) vs assigned label, for repositories found in domain mode."""
    if not has_table(store, "repo_domains"):
        return pd.DataFrame()
    df = query(store, """
        SELECT DISTINCT rd.repo_id, rd.domain AS crawl_domain, l.domain AS label
        FROM repo_domains rd JOIN labels l ON l.repo_id = rd.repo_id""")
    if df.empty:
        return df
    return pd.crosstab(df["crawl_domain"], df["label"])


def awesome_coverage(store: Store) -> pd.DataFrame:
    from sbs.corpus.labels import awesome

    lists = query(store, "SELECT list_repo, domain, readme FROM awesome_lists")
    if lists.empty:
        return pd.DataFrame(columns=["list_repo", "domain", "links", "in_corpus", "labelled_awesome"])
    names = {n.lower(): i for i, n in store.db.execute("SELECT id, full_name FROM repos")}
    by_awesome = {i for (i,) in store.db.execute("SELECT repo_id FROM labels WHERE label_source = 'awesome'")}
    rows = []
    for list_repo, domain, readme in lists.itertuples(index=False):
        links = awesome.parse_links(readme, exclude=list_repo)
        found = [names[n] for n in links if n in names]
        rows.append((list_repo, domain, len(links), len(found), sum(i in by_awesome for i in found)))
    return pd.DataFrame(rows, columns=["list_repo", "domain", "links", "in_corpus", "labelled_awesome"])
