# GitHub repository crawler

Collects repository metadata and README text from the GitHub REST API into SQLite.
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
| `--workers` | 8 | Concurrent README requests |
| `--reserve` | 0.01 | Fraction of each quota deliberately left unused |
| `--max-backlog` | 50000 | Pause discovery while this many READMEs are pending |

When the time budget ends (or on Ctrl+C) all tasks are cancelled, READMEs claimed but not
fetched are returned to `pending`, and a session summary is logged. The next `run` continues
from the saved cursors.

## Two quotas, one token

| Bucket | Limit | Used for |
|---|---|---|
| `search` | 30 req/min | `GET /search/repositories` — discovery; 100 repos per page with full metadata |
| `core` | 5000 req/hr | `GET /repos/{owner}/{repo}/readme` (raw) |

Both pipelines run concurrently, so a single token uses both quotas in full. Discovery can
produce far more repositories than README fetching can consume (~4,900/hr), hence the backlog cap.

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
- Expected steady state: search ≈ 29.7/min, core ≈ 82.5/min — far below the secondary limits
  (100 concurrent requests, 900 points/min).

## Priority: popular first, then newest first (`crawl.py`)

Repositories are split into star tiers that are processed in order:
`>=1000`, `100..999`, `10..99`, `1..9`, `0`.

Inside a tier the crawler walks `created:` time windows **backwards from now**. The search API
returns at most 1000 results per query, so:

- if a window's `total_count` > 1000 it is halved (down to 1 second) until it fits;
- if it returns < 250, the next window doubles (up to 2 years). The learned width is saved per tier.
- Windows are half-open `[start, end)` (queried as `start..end-1s`), so they never overlap.
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
requested. READMEs claimed by a worker are marked `queued` and reset to `pending` at start/stop.

## Schema

- `repos(id, full_name, description, language, topics JSON, stars, forks, default_branch,
  html_url, created_at, pushed_at, fetched_at, readme_status, readme)`
  — `readme_status` is `pending | queued | ok | none | error`.
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
