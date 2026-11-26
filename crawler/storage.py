import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL,
    description TEXT,
    language TEXT,
    topics TEXT,
    stars INTEGER,
    forks INTEGER,
    default_branch TEXT,
    html_url TEXT,
    created_at TEXT,
    pushed_at TEXT,
    fetched_at INTEGER,
    fork INTEGER,
    is_template INTEGER,
    archived INTEGER,
    readme_status TEXT NOT NULL DEFAULT 'pending',
    readme TEXT
);
CREATE INDEX IF NOT EXISTS idx_readme_queue ON repos (readme_status, stars DESC, created_at DESC);
CREATE TABLE IF NOT EXISTS repo_domains (
    repo_id INTEGER NOT NULL,
    domain TEXT NOT NULL,
    query TEXT NOT NULL,
    PRIMARY KEY (repo_id, domain, query)
);
CREATE INDEX IF NOT EXISTS idx_repo_domains_domain ON repo_domains (domain, repo_id);
CREATE TABLE IF NOT EXISTS crawl_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

UPSERT = """
INSERT INTO repos (id, full_name, description, language, topics, stars, forks,
                   default_branch, html_url, created_at, pushed_at, fetched_at,
                   fork, is_template, archived)
VALUES (:id, :full_name, :description, :language, :topics, :stars, :forks,
        :default_branch, :html_url, :created_at, :pushed_at, :fetched_at,
        :fork, :is_template, :archived)
ON CONFLICT(id) DO UPDATE SET
    full_name = excluded.full_name,
    description = excluded.description,
    language = excluded.language,
    topics = excluded.topics,
    stars = excluded.stars,
    forks = excluded.forks,
    default_branch = excluded.default_branch,
    html_url = excluded.html_url,
    pushed_at = excluded.pushed_at,
    fetched_at = excluded.fetched_at,
    fork = COALESCE(excluded.fork, repos.fork),
    is_template = COALESCE(excluded.is_template, repos.is_template),
    archived = COALESCE(excluded.archived, repos.archived)
"""

FLAG_COLUMNS = ("fork", "is_template", "archived")


def flag(item: dict, name: str) -> int | None:
    return int(bool(item[name])) if item.get(name) is not None else None


def repo_row(item: dict, now: int) -> dict:
    return {
        "id": item["id"],
        "full_name": item["full_name"],
        "description": item.get("description"),
        "language": item.get("language"),
        "topics": json.dumps(item.get("topics") or []),
        "stars": item.get("stargazers_count", 0),
        "forks": item.get("forks_count", 0),
        "default_branch": item.get("default_branch"),
        "html_url": item.get("html_url"),
        "created_at": item.get("created_at"),
        "pushed_at": item.get("pushed_at"),
        "fetched_at": now,
        **{name: flag(item, name) for name in FLAG_COLUMNS},
    }


class Storage:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(repos)")}
        with self.db:
            for name in FLAG_COLUMNS:
                if name not in columns:
                    self.db.execute(f"ALTER TABLE repos ADD COLUMN {name} INTEGER")

    def close(self) -> None:
        self.db.close()

    def upsert_repos(self, items: list[dict], domain: str | None = None, query: str | None = None) -> int:
        if not items:
            return 0
        ids = [i["id"] for i in items]
        marks = ",".join("?" * len(ids))
        known = self.db.execute(f"SELECT COUNT(*) FROM repos WHERE id IN ({marks})", ids).fetchone()[0]
        now = int(time.time())
        with self.db:
            self.db.executemany(UPSERT, [repo_row(i, now) for i in items])
            if domain is not None:
                self.db.executemany(
                    "INSERT OR IGNORE INTO repo_domains (repo_id, domain, query) VALUES (?, ?, ?)",
                    [(i["id"], domain, query or "") for i in items],
                )
        return len(set(ids)) - known

    def domain_counts(self) -> dict[str, int]:
        return dict(self.db.execute("SELECT domain, COUNT(DISTINCT repo_id) FROM repo_domains GROUP BY domain"))

    def get_state(self, key: str):
        row = self.db.execute("SELECT value FROM crawl_state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_state(self, key: str, value) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO crawl_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )

    def claim_readmes(self, limit: int, status: str = "pending") -> list[tuple[int, str]]:
        if limit <= 0:
            return []
        with self.db:
            rows = self.db.execute(
                "UPDATE repos SET readme_status = 'queued' WHERE id IN ("
                " SELECT id FROM repos WHERE readme_status = ?"
                " ORDER BY stars DESC, created_at DESC LIMIT ?) "
                "RETURNING id, full_name, stars, created_at",
                (status, limit),
            ).fetchall()
        rows.sort(key=lambda r: r[3] or "", reverse=True)
        rows.sort(key=lambda r: r[2] or 0, reverse=True)
        return [(r[0], r[1]) for r in rows]

    def set_readme(self, repo_id: int, status: str, text: str | None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE repos SET readme_status = ?, readme = ? WHERE id = ?", (status, text, repo_id)
            )

    def requeue(self, ids: list[int], status: str) -> None:
        """Reset rows that are still `queued`; rows already finished keep their result."""
        with self.db:
            self.db.executemany(
                "UPDATE repos SET readme_status = ? WHERE id = ? AND readme_status = 'queued'",
                [(status, i) for i in ids],
            )

    def reset_errors(self) -> int:
        with self.db:
            return self.db.execute("UPDATE repos SET readme_status = 'pending' WHERE readme_status = 'error'").rowcount

    def release_queued(self) -> None:
        with self.db:
            self.db.execute("UPDATE repos SET readme_status = 'pending' WHERE readme_status = 'queued'")

    def pending_count(self) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM repos WHERE readme_status IN ('pending', 'rest', 'queued')"
        ).fetchone()[0]

    def stats(self) -> dict:
        total, distinct = self.db.execute("SELECT COUNT(*), COUNT(DISTINCT id) FROM repos").fetchone()
        readme = dict(self.db.execute("SELECT readme_status, COUNT(*) FROM repos GROUP BY readme_status"))
        state = {k: json.loads(v) for k, v in self.db.execute("SELECT key, value FROM crawl_state")}
        return {"repos": total, "distinct_ids": distinct, "readme": readme, "state": state}
