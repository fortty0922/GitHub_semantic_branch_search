import re

from sbs.common.store import Store

GITHUB_LINK = re.compile(r"github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+)", re.I)
RESERVED_OWNERS = {"topics", "orgs", "sponsors", "features", "marketplace", "apps", "collections", "settings"}


def parse_links(markdown: str, exclude: str = "") -> set[str]:
    names = set()
    for owner, name in GITHUB_LINK.findall(markdown):
        name = name.removesuffix(".git").rstrip(".")
        if not name or owner.lower() in RESERVED_OWNERS:
            continue
        names.add(f"{owner}/{name}".lower())
    names.discard(exclude.lower())
    return names


def index(store: Store) -> dict[str, str]:
    domains: dict[str, set[str]] = {}
    for list_repo, domain, readme in store.db.execute("SELECT list_repo, domain, readme FROM awesome_lists"):
        for full_name in parse_links(readme, exclude=list_repo):
            domains.setdefault(full_name, set()).add(domain)
    return {name: next(iter(ds)) for name, ds in domains.items() if len(ds) == 1}
