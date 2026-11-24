# GitHub repository crawler

Collects repository metadata and README text from the GitHub REST and GraphQL APIs into SQLite.
It runs as close to the rate limit as possible without exceeding it, fetches popular
and recent repositories first, and resumes across runs without fetching a repository twice.

## Usage

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env    # then put a personal access token in GITHUB_TOKEN
.\.venv\Scripts\python -m crawler run --hours 2
.\.venv\Scripts\python -m crawler stats
```

| Option | Default | Meaning |
|---|---|---|
| `--hours` / `--minutes` | unlimited | Time budget for this run |
| `--db` | `crawler.db` | SQLite file (global option, before the subcommand) |
| `--workers` | 8 | Concurrent REST README requests |
| `--graphql-workers` | 4 | Concurrent GraphQL README batches |
| `--batch` | 50 | Max repositories per GraphQL README query |
| `--reserve` | 0.01 | Fraction of each quota deliberately left unused |
| `--max-backlog` | 50000 | Pause discovery while this many READMEs are pending |

When the time budget ends (or on Ctrl+C) all tasks are cancelled, READMEs claimed but not
fetched are returned to `pending`, and a session summary is logged. The next `run` continues
from the saved cursors.

## Three quotas, one token

| Bucket | Limit | Used for |
|---|---|---|
| `search` | 30 req/min | `GET /search/repositories` — discovery; 100 repos per page with full metadata |
| `graphql` | 5000 points/hr | README text for up to `--batch` repositories per query (~1 point each) |
| `core` | 5000 req/hr | `GET /repos/{owner}/{repo}/readme` (raw) — GraphQL misses, then regular pending |

All pipelines run concurrently, so a single token uses every quota in full. The backlog cap
pauses discovery if README fetching falls behind.

## README fetching

GraphQL can only read a file by exact path, so each batch query asks, per repository, for
`HEAD:README.md` plus the names of the root tree entries:

- `README.md` found → `ok`.
- Another root file matching `readme(.ext)*` (case-insensitive; `.md` > `.markdown` > `.rst` >
  `.txt` > …) → fetched in a second batch query → `ok`.
- Empty repository (no `HEAD`) or no README-like file, `.github/` or `docs/` → `none`.
- Repository not resolvable (renamed, deleted, blocked), README only in `.github/`/`docs/`,
  binary README, or a failed follow-up → `rest`, retried by the REST workers, which use GitHub's
  own README detection and follow renames.

A failing batch (timeout / 5xx, or a truncated HTTP 200 body — seen with large batches of long
READMEs) is split in half recursively; the batch size then grows back by
one per successful query. REST workers take `rest` rows first and otherwise help with `pending`,
so the core quota is never idle.

Every invalid-JSON GraphQL response is logged (size, `Content-Length`, encodings, request id,
batch size, elapsed time) and dumped to `diagnostics/graphql-<ms>-<request-id>.txt` with the
response headers, the query and the last 2000 characters of the body.

Note: when a repository has READMEs both in the root and in `.github/`, GitHub's UI prefers
`.github/`; the GraphQL path stores the root one.

## Rate limiting (`client.py`)

Each bucket has a `BucketLimiter` that spaces requests evenly over the rest of the window:

```
interval = (reset - now) / (remaining - reserve)
```

- State is initialised from `GET /rate_limit` (free) and updated from every response's
  `X-RateLimit-Remaining` / `X-RateLimit-Reset` headers. `remaining` is decremented
  optimistically on send, so concurrent requests never overshoot.
- `429`, or `403` with `remaining=0` / `Retry-After` / a "rate limit" message, blocks the bucket
  until `Retry-After` or the reset time; without either it backs off `60 * 2^attempt` seconds
  (secondary limits). `5xx` and transport errors retry with exponential backoff.
- GraphQL `RATE_LIMITED` errors (HTTP 200 with `remaining=0`) are treated like a 403 rate limit.
- Expected steady state: search ≈ 29.7/min, core ≈ 82.5/min, graphql ≈ 82.5 queries/min —
  below the secondary limits (100 concurrent requests, 900 REST / 2000 GraphQL points/min).

## Priority: popular first, then newest first (`crawl.py`)

Repositories are split into star tiers that are processed in order:
`>=1000`, `100..999`, `10..99`, `1..9`, `0`.

Inside a tier the crawler walks `created:` time windows **backwards from now**. The search API
returns at most 1000 results per query, so:

- if a window's `total_count` > 1000 it is halved (down to 1 second) until it fits;
- if it returns < 250, the next window doubles (up to 2 years). The learned width is saved per tier.
- Windows are half-open `[start, end)` (queried as `start..end-1s`), so they never overlap.
- Pages 2..N of a window are requested concurrently, and the next window's first page is
  prefetched while the current one is paged. Search responses take 1–3 s, longer than the 2 s
  pacing interval, so a strictly sequential loop would only reach about half the quota. A
  prefetch is discarded if a higher-priority cursor (e.g. a new gap) takes over.
- Results inside a window are requested with `sort=stars&order=desc` (the search API has no
  `created` sort); every page of the window is fetched, so the order only affects which repos
  land in the database first.

`step()` always advances the highest-priority unfinished cursor by one window, so new repositories
in a higher tier are picked up before older ones in a lower tier. The README queue is ordered by
`stars DESC, created_at DESC`.

## Resume and de-duplication

Per tier, `crawl_state` stores:

| Key | Meaning |
|---|---|
| `upper` | Newest creation time fully covered |
| `lower` | Oldest creation time reached by the backward sweep |
| `gap_top`, `gap_cursor` | Present while filling the range `(upper, gap_top]` created since the last run |
| `span` | Adaptive window width |

On each run a tier whose `upper` is more than 1 hour old first fills that gap (newest first),
then continues the backward sweep from `lower`. Cursors move only after a window is fully paged,
so an interrupted window is simply re-fetched. The search index lags slightly, so `now` is taken
as 5 minutes ago.

`repos.id` is the primary key. A repository seen again (e.g. after its stars moved it into another
tier) only has its metadata refreshed; its README status is kept, and only `pending` READMEs are
requested. READMEs claimed by a worker are marked `queued` and reset to `pending` at start/stop
(a `rest` row reset this way just goes through GraphQL once more).

## Schema

- `repos(id, full_name, description, language, topics JSON, stars, forks, default_branch,
  html_url, created_at, pushed_at, fetched_at, readme_status, readme)`
  — `readme_status` is `pending | rest | queued | ok | none | error`.
- `crawl_state(key, value JSON)`.

## Known limits

- One-second windows with more than 1000 repositories are truncated (logged as a warning).
- Repositories created inside a window while it is being paged can shift results between pages;
  the 5-minute index lag makes this rare, and duplicates are ignored.
- Repositories hidden from search (e.g. flagged accounts) are not discovered.

## Tests

```powershell
.\.venv\Scripts\python -m pytest
```
