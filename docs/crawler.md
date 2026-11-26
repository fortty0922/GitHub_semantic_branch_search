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
| `--graphql-workers` | 6 | Concurrent GraphQL README batches |
| `--batch` | 50 | Max repositories per GraphQL README query |
| `--reserve` | 0.01 | Fraction of each quota deliberately left unused |
| `--max-backlog` | 50000 | Pause discovery while this many READMEs are pending |
| `--log-dir` | `logs` | Directory for the summary log file |

When the time budget ends (or on Ctrl+C) all tasks are cancelled, READMEs claimed but not
fetched are returned to `pending`, and a session summary is logged. The next `run` continues
from the saved cursors.

### Completeness check and fill

```powershell
.\.venv\Scripts\python -m crawler check
.\.venv\Scripts\python -m crawler fill
```

`check` reads only the database (no token, no network) and prints a JSON report:

- `readme`: counts per `readme_status`, grouped into `done` (`ok`, `none`), `incomplete`
  (`pending`, `rest`, `queued`) and `failed` (`error`), plus `ok` rows without text;
- `missing_fields`: rows without `full_name`, `created_at` or `fetched_at`;
- `tiers`: per star tier, whether it was started, whether the backward sweep reached the earliest
  date (`sweep_done`), whether a gap since the last run is still open (`open_gap`), and the age of
  `upper` in hours.

`complete` is true only when no README is incomplete or failed, no `ok` row lacks text, no field is
missing, and every tier's sweep is done with no open gap. The exit code is 0 when complete and 1
otherwise, so scripts can test it.

`fill` completes the repositories already in the database without discovering new ones: it runs
only the GraphQL and REST README workers and exits by itself once nothing is `pending`, `rest` or
`queued`, then prints the `check` report (and its exit code). Before starting, `error` rows are
reset to `pending` so each failed README is retried once per `fill`; `--keep-errors` skips that.
`none` rows (no README) are not retried. `fill` takes the same options as `run` except
`--max-backlog`. Tier gaps are discovery work and are left to `run`.

## Logging

- Console: INFO and above from the crawler (window results, per-minute rates). Per-request
  `httpx`/`httpcore` lines are suppressed.
- File `logs/crawler-<YYYYmmdd-HHMMSS>.log` (one per run): only notable events — warnings and
  errors (rate limits, transport errors, invalid GraphQL JSON, truncated windows), GraphQL batch
  splits, discovery pause/resume, `session start` and `session summary`. Transport errors caused
  by the run's own shutdown are not logged or counted.
- `session summary` is one JSON object: duration, new repos, READMEs, requests and average
  requests/min per bucket, event counters, seconds discovery spent paused, the GraphQL batch
  ceiling, latency (avg/p90/max) and peak in-flight requests
  per bucket, seconds spent waiting in each limiter, measured clock skew, database totals by README status, and each tier's
  cursors.

## Three quotas, one token

| Bucket | Limit | Used for |
|---|---|---|
| `search` | 30 req/min | `GET /search/repositories` — discovery; 100 repos per page with full metadata |
| `graphql` | 5000 points/hr | README text for up to `--batch` repositories per query (~1 point each) |
| `core` | 5000 req/hr | `GET /repos/{owner}/{repo}/readme` (raw) — GraphQL misses, then regular pending |

All pipelines run concurrently, so a single token uses every quota in full. The backlog cap
pauses discovery if README fetching falls behind: while `pending + rest + queued >= --max-backlog`
discovery checks again every 10 s, and the pause/resume is logged (`discovery paused: backlog N >= M`,
`discovery resumed after Ns`); the per-minute console line ends with `| discovery paused`.

Choosing `--max-backlog`: README workers consume ~1,700/min and a window can add up to ~2,000
repos at once (two windows in flight), and the cap is only checked between windows. Below ~3,000
README workers can run dry while discovery restarts; ~5,000 keeps both sides busy. The backlog
may overshoot the cap by about one window's worth.

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

GitHub aborts GraphQL queries that run for about 10 s. With large batches this shows up as
HTTP 502 or as an HTTP 200 with an empty or truncated body (observed: 0 bytes after 11.2 s for
50 repos). Any such failure splits the batch in half recursively and lowers a ceiling to 80% of
the failed size (never below 5); the batch size then grows back by one per successful query,
but only up to that ceiling, so it settles below the timeout instead of oscillating. The ceiling
is stored in `crawl_state` (`graphql_batch_max`) and reused by the next run, and is raised by one
after every 100 consecutive successful queries (up to `--batch`) in case GitHub gets faster.

REST workers take `rest` rows first and otherwise help with `pending`,
so the core quota is never idle.

Every invalid-JSON GraphQL response is logged (size, `Content-Length`, encodings, request id,
batch size, elapsed time) and dumped to `diagnostics/graphql-<ms>-<n>-<request-id>.txt` with the
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
- Reset times are converted to local time using the clock skew measured from each response's
  `Date` header (smoothed). Without this, a local clock running ahead of GitHub's made the
  limiter assume a fresh search window too early and hit a 403.
- Headers whose reset time is older than the current window are ignored. Search responses take
  ~4 s, so replies to requests sent just before a rollover used to arrive afterwards with
  `remaining≈0` and stall the search limiter for a whole minute (measured: 220 s of 300 s spent
  waiting, 22 req/min instead of ~30).
- `429`, or `403` with `remaining=0` / `Retry-After` / a "rate limit" message, blocks the bucket
  until `Retry-After` or the reset time (at least 1 s); without either it backs off `60 * 2^attempt` seconds
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
- Windows are pipelined two deep: pages 2..N of a window are requested concurrently, and as soon
  as a window's bounds are known the whole next window (probe, splits and pages) starts in the
  background. Search responses take several seconds, longer than the 2 s pacing interval, so
  waiting for each window's last page before starting the next capped the rate at ~19/min.
  Results and cursors are still committed in window order; a prefetch is discarded if a
  higher-priority cursor (e.g. a new gap) takes over.
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

`crawl_state` also holds `graphql_batch_max`, the learned GraphQL batch ceiling.

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

- One-second windows with more than 1000 repositories are truncated (logged as a warning) and are not
  recorded in the database, so `check` cannot detect them.
- Repositories created inside a window while it is being paged can shift results between pages;
  the 5-minute index lag makes this rare, and duplicates are ignored.
- Repositories hidden from search (e.g. flagged accounts) are not discovered.

## Tests

```powershell
.\.venv\Scripts\python -m pytest
```
