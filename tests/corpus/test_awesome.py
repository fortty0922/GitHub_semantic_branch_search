import httpx

from crawler.client import GitHubClient
from sbs.corpus import awesome_fetch
from sbs.corpus.labels import awesome

README = """
# Awesome ML
- [Torch](https://github.com/pytorch/pytorch) - tensors
- [Repo](https://github.com/Owner/Name.git#readme)
- [Topic](https://github.com/topics/ml)
- [Self](https://github.com/me/awesome-ml)
"""


def test_parse_links():
    assert awesome.parse_links(README, exclude="me/awesome-ml") == {"pytorch/pytorch", "owner/name"}


def test_index_drops_ambiguous(store):
    store.upsert_awesome("a/ml", "machine-learning", "github.com/x/one github.com/x/both", 0)
    store.upsert_awesome("a/ops", "devops", "github.com/x/two github.com/X/Both", 0)
    assert awesome.index(store) == {"x/one": "machine-learning", "x/two": "devops"}


async def test_fetch_stores_readmes_and_skips_missing(store, tmp_path):
    def handler(request):
        if request.url.path == "/repos/me/awesome-ml/readme":
            return httpx.Response(200, text=README)
        return httpx.Response(404, json={})

    client = GitHubClient("t", transport=httpx.MockTransport(handler), diag_dir=tmp_path / "diag")
    lists = {"machine-learning": {"lists": ["me/awesome-ml"]}, "devops": {"lists": ["gone/list"]}}
    try:
        assert await awesome_fetch.run(store, lists, client) == 1
    finally:
        await client.close()
    rows = store.db.execute("SELECT list_repo, domain, readme FROM awesome_lists").fetchall()
    assert rows == [("me/awesome-ml", "machine-learning", README)]
