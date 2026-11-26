# Architecture

The project studies terms that mean different things in different technical domains (e.g.
*pipeline*, *kernel*, *container*) and builds a repository search that separates those senses.
Results are grouped by meaning, each group gets a readable name, and the user can refine the
search with suggested keywords.

This document describes the planned layout and data flow. Method details are in
[pipeline.md](pipeline.md), the evaluation protocol in [evaluation.md](evaluation.md), and the
existing crawler in [crawler.md](crawler.md).

> Status: `crawler/` and `sbs/corpus/` are implemented; the later stages below are the target layout.

## Data flow

```
GitHub API ──crawler──▶ repos (SQLite)
                          │
                    sbs.corpus   clean → tokenize → dedup → label → split (frozen test set)
                          │
                    sbs.terms    extract multi-word terms → filter polysemous candidates
                          │
                    sbs.vectors  word2vec / contextual embeddings → sense probe, clustering
                          │
                    sbs.retrieval BM25 + dense → fusion → grouping → naming → expansion
                     │                      │
                  sbs.web (demo)      sbs.evaluation (test set only)
```

## Layout

```
crawler/                  GitHub crawler (existing), extended with a domain mode

config/
  experiment.toml         every scale and threshold parameter
  domains.toml            search sub-queries per domain
  topic_map.toml          GitHub topic → domain mapping
  awesome_lists.toml      awesome-list sources per domain
  control_terms.toml      monosemous control terms

sbs/
  __main__.py             CLI entry point: argument parsing and dispatch only
  common/
    settings.py           loads config/*.toml
    store.py              SQLite tables for processed data
  corpus/
    clean.py              strip Markdown, code blocks, HTML, URLs, badges, e-mail addresses
    tokens.py             English tokenizer
    dedup.py              drop forks, template-generated and near-duplicate READMEs
    awesome_fetch.py      fetch the READMEs of the configured awesome-lists (network)
    labels/
      topics.py           topic → domain by majority vote
      awesome.py          repository links parsed from awesome-list READMEs
      classifier.py       TF-IDF + logistic regression for the remaining repositories
      assign.py           applies the three sources in order and writes `labels`
    split.py              frozen test set; train_ids() / test_ids()
  terms/
    extract.py            multi-word term extraction (branching entropy + PMI)
    filter.py             polysemy candidates (per-domain min count, Jaccard, TF-IDF)
  vectors/
    word2vec.py           static word vectors (baseline)
    contextual.py         Transformer vectors per occurrence; repository embeddings
    sense_probe.py        domain prediction from each vector type
    clustering.py         K-means with silhouette-based choice of k
  retrieval/
    bm25.py               field-weighted BM25 (also the baseline)
    dense.py              embedding search
    hybrid.py             rank fusion
    grouping.py           group results by cluster
    naming.py             group names from a local LLM, cached
    expansion.py          follow-up keyword suggestions
  web/
    app.py                FastAPI backend
    static/index.html     demo page
  evaluation/
    precision.py          precision@10
    simulation.py         simulated user: MRR and steps to top 10
    controls.py           shuffled labels, monosemous terms
    audit.py              label audit and error-analysis export / summary

data/                     generated artifacts (git-ignored) except the frozen test-set id file
tests/
  crawler/                crawler tests
  common/                 settings tests
  corpus/ terms/ vectors/ retrieval/ evaluation/   one test file per module
```

Rules for the layout:

- One responsibility per file; unrelated code or settings are not combined.
- Each stage is a subpackage and reads only the outputs of earlier stages.
- No experiment scale or threshold is written in code or docs; all of them live in
  `config/experiment.toml`, so the scale can change without code changes.

## Stage inputs and outputs

| Stage | Reads | Writes |
|---|---|---|
| crawler (domain mode) | `domains.toml`, `experiment.toml` | `repos`, `repo_domains` |
| `corpus` | `repos`, `experiment.toml`, `topic_map.toml`, `awesome_lists.toml` | `docs_clean`, `tokens`, `dedup`, `awesome_lists`, `labels`, test-set id file |
| `terms` | `tokens`, `labels` (train only) | `terms` (counts per domain, Jaccard, TF-IDF, rank) |
| `vectors` | `tokens`, `terms`, `labels` (train only) | word2vec model, occurrence vectors, repository embeddings, `repo_cluster`, probe scores |
| `retrieval` | `docs_clean`, embeddings, `repo_cluster` | BM25 index, group-name cache |
| `web` | `retrieval` | — |
| `evaluation` | everything above, test set only | metric tables, audit / error CSVs |

## Test-set isolation

The test set is drawn once by `corpus/split.py` (stratified by domain, fixed seed) right after
the crawl, and its id file is committed with the date and a SHA-256 of its contents. From then on:

- every fitted model, threshold, k and label mapping uses `train_ids()` only;
- only `sbs/evaluation/` reads `test_ids()`;
- a test asserts that the two sets are disjoint and that the id file's hash is unchanged.

## Design decisions

| Topic | Decision |
|---|---|
| Relevance for precision@10 | Domain labels as a proxy: a query is a polysemous term plus a target domain; test repositories with that label that contain the term are relevant. A manual spot check validates the proxy. |
| "GitHub search" baseline | Offline field-weighted BM25 on the same frozen corpus, so results are reproducible. |
| Group naming LLM | Local model through Ollama; names are cached and can be precomputed offline. |
| Demo | FastAPI with a single static HTML page. |

## Roadmap

1. Crawler domain mode and `config/`; start the crawl.
2. `corpus`; freeze and commit the test set as soon as the crawl finishes.
3. `terms`.
4. `vectors` (sense probe and clustering).
5. `retrieval` and `web`.
6. `evaluation`.

Planned dependencies: `numpy`, `scikit-learn`, `gensim`, `torch`, `transformers`,
`sentence-transformers`, `datasketch`, `fastapi`, `uvicorn` (Ollama is called through the
existing `httpx`). If `gensim` or `torch` has no wheel for the current Python version, the
virtual environment moves to the newest supported one.
