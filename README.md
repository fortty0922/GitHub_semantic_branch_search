# GitHub Semantic Branch Search

A crawler that collects public GitHub repository metadata and README text into SQLite through
the official GitHub REST and GraphQL APIs, staying within the published rate limits. The data
is intended as a corpus for research on semantic search over repositories.

See [docs/crawler.md](docs/crawler.md) for setup, usage and design.

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
