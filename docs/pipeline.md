# Pipeline design

Methods for each stage. Layout and data flow are in [architecture.md](architecture.md); every
number referred to here as a parameter is set in `config/experiment.toml`.

## 1. Data preparation

### Domain crawl

The initial domains are machine learning, DevOps, data engineering, web backend, computer
graphics and operating systems; the list and the queries for each domain are in
`config/domains.toml`, and the target number of repositories per domain is a parameter.

The existing crawler ([crawler.md](crawler.md)) already handles rate limits, resuming, README
fetching and the search API's cap of 1,000 results per query (star tiers × `created:` windows
halved until each fits). The domain mode adds:

- `Tier.qualifier`: a domain sub-query (e.g. `topic:machine-learning`, a phrase
  `in:description`) prefixed to the star qualifier. Each domain has several sub-queries, each with
  its own cursors in `crawl_state` (keys prefixed by domain).
- A per-domain stop: once a domain reaches its target, its cursors are no longer advanced.
- `repo_domains(repo_id, domain, query)`: which domain query found each repository. A repository
  found by several domains keeps all rows.
- New `repos` columns `fork`, `is_template`, `archived` (returned by the search API). The fetch
  date is the existing `fetched_at`.

Stored per repository: README, description, topics, metadata and fetch date.

### Cleaning (`corpus/clean.py`)

Removed from the README text: fenced and indented code blocks, inline code, HTML tags, images and
badges, link targets (link text is kept), bare URLs, e-mail addresses, and Markdown markup
(headings, emphasis, tables, list markers). The result is stored in `docs_clean`.

### Tokenization (`corpus/tokens.py`)

Regex-based English tokenizer, lower-cased, keeping technical tokens such as `c++`, `c#`,
`node.js`. Stop words are kept here and removed only where a stage needs it, because term
extraction relies on them as boundaries.

### De-duplication (`corpus/dedup.py`)

1. Drop repositories flagged `fork` or `is_template`.
2. Exact duplicates: hash of the normalised cleaned text.
3. Near duplicates (template-generated READMEs): MinHash LSH over word shingles above a
   similarity parameter.

Within each duplicate group the repository with the most stars is kept.

### Domain labels (`corpus/labels.py`)

Applied in order, first match wins:

1. **Topics**: `config/topic_map.toml` maps GitHub topics to domains.
2. **Awesome-lists**: the READMEs listed in `config/awesome_lists.toml` are parsed for repository
   links; a repository listed under a domain's list gets that domain.
3. **Text classifier**: TF-IDF + logistic regression trained on repositories labelled by 1–2;
   predictions below a confidence parameter are labelled `unknown` and excluded.

`label_source` (`topic`, `awesome`, `model`) is stored so the audit can report accuracy per
source.

### Frozen test set (`corpus/split.py`)

Stratified by domain, size and seed are parameters. Frozen immediately and never used for tuning;
see [Test-set isolation](architecture.md#test-set-isolation).

## 2. Term processing

### Extraction (`terms/extract.py`)

Candidates are word n-grams up to a maximum length. A candidate is a term when both hold:

- **Cohesion**: pointwise mutual information between its parts is above a threshold
  (for n > 2, the minimum over all binary splits).
- **Freedom**: the left and right branching entropies (entropy of the neighbouring word
  distribution) are both above a threshold.

Example: *machine learning pipeline*.

### Polysemy candidates (`terms/filter.py`)

1. **Minimum count per domain**: a term is compared only across domains where it occurs at least
   the configured number of times, so sparse domains are not mistaken for divergent ones.
2. **Jaccard**: for each domain, the set of context words within a window around the term;
   the mean pairwise Jaccard between domains below a threshold means the contexts diverge, and
   the term is kept.
3. **TF-IDF**: each domain is treated as one document; a low score means the term is spread over
   many domains, and such terms rank first.

All thresholds are tuned on training data only.

## 3. Vectors and clustering

### Representations

- **word2vec** (`vectors/word2vec.py`): skip-gram trained on the training corpus, with extracted
  terms merged into single tokens. One vector per term — the static baseline.
- **Transformer** (`vectors/contextual.py`): a pretrained encoder; for each occurrence, the mean
  of the last hidden states over the term's subword tokens. One vector per occurrence. The same
  module produces repository embeddings with a sentence-embedding model for retrieval.

### Sense probe (`vectors/sense_probe.py`)

For each polysemy candidate, a classifier predicts the domain of each occurrence from:

- word2vec: the term vector concatenated with the mean of its context word vectors
  (the term vector alone is constant per term and is reported for reference);
- Transformer: the occurrence vector.

Cross-validated macro-F1 per term and averaged. Higher scores for contextual vectors indicate
they separate senses better.

### Clustering (`vectors/clustering.py`)

K-means on repository embeddings; k is chosen within a configured range by the best silhouette
score. Run offline; the assignment `repo_cluster` is used for grouping search results. The same
procedure is applied to the occurrence vectors of each polysemy candidate for analysis.

## 4. Retrieval

- **BM25** (`retrieval/bm25.py`): fields name, description, topics and README with configured
  weights. Also serves as the "GitHub search" baseline.
- **Dense** (`retrieval/dense.py`): cosine similarity between the query embedding and repository
  embeddings (brute force; the corpus is small enough).
- **Fusion** (`retrieval/hybrid.py`): Reciprocal Rank Fusion of the two rankings.
- **Grouping** (`retrieval/grouping.py`): results are grouped by `repo_cluster`, groups ordered by
  their best-ranked member.
- **Naming** (`retrieval/naming.py`): each group's top keywords (TF-IDF within the group) and
  README snippets from its top members go to a local LLM through Ollama, which returns a short
  name. Cached by cluster and snippet hash; can be precomputed for every cluster.
- **Expansion** (`retrieval/expansion.py`): term extraction plus TF-IDF of the selected group's
  READMEs against the whole corpus gives suggested keywords. Choosing one appends it to the query
  and searches again.
- **Demo** (`web/`): `GET /search?q=` returns named groups; `GET /expand?q=&group=` returns
  suggestions. The page shows groups, results and suggestion buttons.
