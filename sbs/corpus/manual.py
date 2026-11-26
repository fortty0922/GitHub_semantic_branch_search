import csv
from pathlib import Path

LABEL_FIELDS = ("repo_id", "domain", "note")
EXCLUDE_FIELDS = ("repo_id", "reason")


def _read(path: str | Path) -> dict[int, dict]:
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {int(row["repo_id"]): row for row in csv.DictReader(f)}


def _write(path: str | Path, fields: tuple[str, ...], rows: dict[int, dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for repo_id in sorted(rows):
            writer.writerow({k: rows[repo_id].get(k, "") for k in fields} | {"repo_id": repo_id})


def read_labels(path: str | Path) -> dict[int, str]:
    return {i: row["domain"] for i, row in _read(path).items()}


def read_excluded(path: str | Path) -> dict[int, str]:
    return {i: row.get("reason", "") for i, row in _read(path).items()}


def set_label(path: str | Path, repo_id: int, domain: str, note: str = "") -> None:
    rows = _read(path)
    rows[int(repo_id)] = {"domain": domain, "note": note}
    _write(path, LABEL_FIELDS, rows)


def exclude(path: str | Path, repo_id: int, reason: str = "") -> None:
    rows = _read(path)
    rows[int(repo_id)] = {"reason": reason}
    _write(path, EXCLUDE_FIELDS, rows)


def remove(path: str | Path, repo_id: int) -> bool:
    rows = _read(path)
    if int(repo_id) not in rows:
        return False
    del rows[int(repo_id)]
    with open(path, newline="", encoding="utf-8") as f:
        fields = tuple(next(csv.reader(f)))
    _write(path, fields, rows)
    return True
