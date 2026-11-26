import sqlite3
from collections.abc import Iterable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs_clean (
    repo_id INTEGER PRIMARY KEY,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    repo_id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    n_tokens INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS dedup (
    repo_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    kept_id INTEGER
);
CREATE TABLE IF NOT EXISTS awesome_lists (
    list_repo TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    readme TEXT NOT NULL,
    fetched_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS topic_discovery (
    topic TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    round INTEGER NOT NULL,
    count INTEGER NOT NULL,
    assoc REAL NOT NULL,
    lift REAL,
    seed_assoc REAL
);
CREATE TABLE IF NOT EXISTS topic_expansion (
    topic TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    count INTEGER NOT NULL,
    purity REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS label_thresholds (
    domain TEXT PRIMARY KEY,
    threshold REAL,
    cv_precision REAL,
    cv_support INTEGER
);
CREATE TABLE IF NOT EXISTS labels (
    repo_id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,
    label_source TEXT NOT NULL,
    confidence REAL
);
"""

BATCH = 1000


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def reset(self, table: str) -> None:
        """Drop a derived table and recreate it from the current schema (picks up new columns)."""
        with self.db:
            self.db.execute(f"DROP TABLE IF EXISTS {table}")
        self.db.executescript(SCHEMA)

    def has_column(self, table: str, column: str) -> bool:
        return any(row[1] == column for row in self.db.execute(f"PRAGMA table_info({table})"))

    def iter_rows(self, sql: str, params: tuple = ()) -> Iterator[tuple]:
        cur = self.db.execute(sql, params)
        while rows := cur.fetchmany(BATCH):
            yield from rows

    def replace_all(self, table: str, columns: tuple[str, ...], rows: Iterable[tuple]) -> int:
        marks = ",".join("?" * len(columns))
        sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({marks})"
        count = 0
        with self.db:
            self.db.execute(f"DELETE FROM {table}")
            batch = []
            for row in rows:
                batch.append(row)
                if len(batch) >= BATCH:
                    self.db.executemany(sql, batch)
                    count += len(batch)
                    batch.clear()
            self.db.executemany(sql, batch)
            count += len(batch)
        return count

    def upsert_awesome(self, list_repo: str, domain: str, readme: str, fetched_at: int) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO awesome_lists (list_repo, domain, readme, fetched_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(list_repo) DO UPDATE SET domain = excluded.domain, readme = excluded.readme, "
                "fetched_at = excluded.fetched_at",
                (list_repo, domain, readme, fetched_at),
            )

    def kept_ids(self) -> list[int]:
        return [r[0] for r in self.db.execute("SELECT repo_id FROM dedup WHERE status = 'kept' ORDER BY repo_id")]

    def labelled(self, sources: tuple[str, ...] | None = None) -> dict[int, str]:
        sql = ("SELECT l.repo_id, l.domain FROM labels l JOIN dedup d ON d.repo_id = l.repo_id "
               "WHERE d.status = 'kept' AND l.domain NOT IN ('unknown', 'other')")
        if sources is None:
            return dict(self.db.execute(sql))
        marks = ",".join("?" * len(sources))
        return dict(self.db.execute(f"{sql} AND l.label_source IN ({marks})", sources))
