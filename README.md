# GitHub Semantic Branch Search

A crawler that collects public GitHub repository metadata and README text into SQLite through
the official GitHub REST and GraphQL APIs, staying within the published rate limits. The data
is intended as a corpus for research on semantic search over repositories.

Setup (Python 3.11+): run `init.bat` on Windows or `./init.sh` on Linux/macOS, then set
`GITHUB_TOKEN` in `.env`. The scripts also create the local `config/*.toml` files from the tracked
`config/*.toml.example` templates. See [docs/crawler.md](docs/crawler.md) for usage and design.

## Research goal

The corpus is used to study terms whose meaning depends on the technical domain (e.g.
*pipeline* in machine learning vs DevOps) and to build a repository search that groups results by
meaning, names each group, and suggests follow-up keywords. Design documents:

- [docs/architecture.md](docs/architecture.md) — layout, data flow, design decisions, roadmap
- [docs/pipeline.md](docs/pipeline.md) — data preparation, term processing, vectors, retrieval
- [docs/evaluation.md](docs/evaluation.md) — metrics, simulated user, controls, error analysis

## Implementation status

| Stage | Package | Status |
|---|---|---|
| Crawler (star tiers, created windows, README fetching) | `crawler/` | Done |
| Crawler domain mode | `crawler/` | Done |
| Data preparation | `sbs/corpus/` | Done |
| Term processing | `sbs/terms/` | Planned |
| Vectors and clustering | `sbs/vectors/` | Planned |
| Retrieval | `sbs/retrieval/` | Planned |
| Demo | `sbs/web/` | Planned |
| Evaluation | `sbs/evaluation/` | Planned |

Stage order and details: [docs/architecture.md#roadmap](docs/architecture.md#roadmap).

## Intended use and compliance

This project is developed and published for **academic and non-commercial research** purposes.
Anyone running it is responsible for using it, and any data it collects, in accordance with:

- the [GitHub Terms of Service](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service)
  and [Acceptable Use Policies](https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies),
  including the rules on API usage and on information usage;
- the [GitHub Privacy Statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement)
  and the data-protection laws that apply to you and your institution;
- the license of each crawled repository — README text remains the work of its authors and is
  not relicensed by this project.

In particular, the crawler:

- uses only official API endpoints with the user's own token, and paces requests below the
  primary rate limits, backing off on any rate-limit or abuse-detection response;
- accesses only public repositories and stores repository-level data, not user profiles or
  contact details.

Users should **not** use the collected data for spam, selling personal information, profiling
individuals, or any commercial redistribution, and should anonymize or aggregate results before
publication where appropriate. Results published from this data should respect the original
licenses of the repositories involved.

This statement describes the intended use; it is not legal advice. Check the current GitHub
policies and your institution's research-ethics requirements before collecting or sharing data.

## License

The source code is released under the [MIT License](LICENSE). The license covers this
project's code only, not the data collected with it.
