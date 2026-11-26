import logging
import time

from crawler.client import GitHubClient
from sbs.common.store import Store

log = logging.getLogger("sbs")

RAW = {"Accept": "application/vnd.github.raw+json"}


async def run(store: Store, awesome_lists: dict, client: GitHubClient) -> int:
    fetched = 0
    for domain, entry in awesome_lists.items():
        for list_repo in entry.get("lists", []):
            r = await client.get("core", f"/repos/{list_repo}/readme", headers=RAW)
            if r.status_code != 200:
                log.warning("awesome list %s: HTTP %s, skipped", list_repo, r.status_code)
                continue
            store.upsert_awesome(list_repo, domain, r.text, int(time.time()))
            fetched += 1
    log.info("fetch-awesome: %d lists stored", fetched)
    return fetched
