# Pipeline design

Methods for each stage. Layout and data flow are in [architecture.md](architecture.md); every
number referred to here as a parameter is set in `config/experiment.toml`.

## 1. Data preparation

### Domain crawl

The initial domains are machine learning, DevOps, data engineering, web backend, computer
graphics and operating systems; the list and the queries for each domain are in
`config/domains.toml`, and the target number of repositories per domain is
`crawler.domain.target_per_domain` in `config/experiment.toml`. Usage is in
[crawler.md](crawler.md#domain-mode).

The existing crawler ([crawler.md](crawler.md)) already handles rate limits, resuming, README
fetching and the search API's cap of 1,000 results per query (star tiers × `created:` windows
halved until each fits). The domain mode adds:

- `Tier.qualifier`: a domain sub-query (e.g. `topic:machine-learning`, a phrase
  `in:description`) prefixed to the star qualifier. Each domain has several sub-queries, each with
  its own cursors in `crawl_state` (keys `domain:<domain>:<query>:tier:<stars>`). Star tiers stay
  the outer priority; within a star tier every domain and query takes its turn.
- A per-domain stop: once a domain has reached its target (distinct repositories in
  `repo_domains`), its cursors are no longer advanced. The check runs before each window, so a
  domain can overshoot by at most one window.
- `repo_domains(repo_id, domain, query)`: which domain query found each repository. A repository
  found by several domains keeps all rows.
- New `repos` columns `fork`, `is_template`, `archived` (returned by the search API), stored in
  every mode. An older database gets the columns on first open; its existing rows stay NULL until
  they are seen by a search again, so the dedup fork/template step only covers rows that have
  been re-fetched. The fetch date is the existing `fetched_at`.

Stored per repository: README, description, topics, metadata and fetch date.

### Running

```
python -m sbs [--db crawler.db] [--config config] corpus <step>
```

Steps: `clean`, `tokenize`, `dedup`, `fetch-awesome`, `label`, `split`, and `all`
(clean → tokenize → dedup → label → split). `fetch-awesome` is the only step that uses the network
(it needs `GITHUB_TOKEN`, as the crawler does) and is run once before `label`. Every other step
rebuilds its table from the previous step's output, so a step can be re-run after a config change.
`split` never overwrites a frozen test set; under `all` it is skipped with a message.

The parameters of every step are under `[corpus.*]` in `config/experiment.toml`.

### Cleaning (`corpus/clean.py`)

Input: repositories with `readme_status = 'ok'`. Removed from the README text: fenced and indented
code blocks, inline code, HTML comments, `<pre>`/`<code>`/`<script>`/`<style>` blocks, images and
badges, reference link definitions, link targets (link text is kept), remaining HTML tags (inner
text kept), HTML entities (unescaped), bare URLs and autolinks, e-mail addresses, and Markdown
markup (headings, emphasis, blockquotes, tables, list markers, rules). Paragraph breaks are kept.
Documents that are empty after cleaning are dropped. The result is stored in `docs_clean`.

### Tokenization (`corpus/tokens.py`)

Regex-based English tokenizer, lower-cased, keeping technical tokens such as `c++`, `c#`,
`node.js`, `.net`, hyphenated and snake_case words. Text is split into sentences on line breaks and
`. ! ? ; :` followed by whitespace, so terms are never extracted across sentences. Stop words are
kept here and removed only where a stage needs it, because term extraction relies on them as
boundaries. `tokens` stores one sentence per line with space-separated tokens, plus the token count.

### De-duplication (`corpus/dedup.py`)

Each repository gets one `dedup.status`, decided in this order:

0. `excluded`: listed in the manual exclusion file (see [Manual overrides](#manual-overrides-corpusmanualpy)).
1. `fork` / `template`: repositories flagged `fork` or `is_template`. The step runs only when
   those columns exist in `repos`; a database from the original crawler schema skips it and logs that.
2. `non_english`: the share of ASCII letters among all letters of the cleaned README is below
   `min_english_ratio` (the tokenizer only keeps English, so such READMEs lose most of their text).
3. `empty` (no tokens) and `short` (fewer than `min_tokens` tokens).
4. `exact`: same SHA-256 of the token sequence.
5. `near`: MinHash LSH over word shingles of `shingle_size` with `num_perm` permutations, Jaccard
   `threshold`; candidate pairs are merged transitively into groups.

Within each duplicate group the repository with the most stars is `kept` (ties: lowest id), and
the others point to it through `kept_id`.

6. `boilerplate`: a duplicate group (exact and near combined) with at least `max_group_size`
   repositories is a template README (e.g. a framework's generated README) or spam, so the whole
   group, kept repository included, is dropped. The exception is a group whose kept repository has
   more than `keep_star_ratio` times the stars of any other member: an original with its copies,
   where the original stays `kept`.

Later stages read only `kept` repositories.

### Domain labels (`corpus/labels/`)

Applied in order to `kept` repositories, first match wins (a manual label, if any, comes first):

1. **Topics** (`topics.py`): `config/topic_map.toml` maps GitHub topics to domains; the domain with
   the most matching topics wins, a tie gives no label.
2. **Awesome-lists** (`awesome.py`): `fetch-awesome` stores the READMEs of the lists in
   `config/awesome_lists.toml` in `awesome_lists`; GitHub repository links are parsed from them
   (case-insensitive, the list itself excluded). A repository linked from lists of more than one
   domain gets no label from this source.
3. **Text classifier** (`classifier.py`, `threshold.py`): TF-IDF + logistic regression (balanced
   class weights) over description, topics and README tokens. Training classes are the domains,
   from the repositories labelled by the manual file and 1–2, plus `other`: repositories with at
   least `other_min_topics` topics of which none is in `topic_map.toml`. A class with fewer
   examples than `cv_folds` is not trained (logged).

   Acceptance thresholds are chosen automatically: `cv_folds`-fold cross-validated probabilities
   on the training set give, per domain, the lowest confidence at which predictions of that domain
   reach `target_precision` with at least `min_support` predictions. They are stored in
   `label_thresholds` (`threshold` is NULL when the target is never reached, so the model never
   assigns that domain). The model, refitted on the whole training set, then labels the remaining
   repositories: a predicted domain at or above its threshold is kept, `other` is kept as is, and
   everything else is `unknown`.

`other` and `unknown` repositories are not in the labelled set. The cross-validated precision is
measured on repositories that have topics or are in awesome-lists, which are usually easier than
the rest, so it is an optimistic estimate; the label audit reports the real accuracy.

`label_source` (`topic`, `awesome`, `manual`, `model`) and, for `model`, the predicted
probability (`confidence`) are stored so the audit can report accuracy per source.

### Manual overrides (`corpus/manual.py`)

Two CSV files, paths `paths.manual_labels` and `paths.manual_exclude` in
`config/experiment.toml`, hold hand-made corrections; they are committed with the repository so the
corpus can be rebuilt with the same decisions:

- labels file (`repo_id,domain,note`): the domain wins over every other source
  (`label_source = 'manual'`) and is used as classifier training data. `unknown` keeps a
  repository out of the labelled set.
- exclusion file (`repo_id,reason`): the repository gets dedup status `excluded` before any other
  dedup step, so another member of its duplicate group can be kept.

`dedup` and `label` read the files on every run, so overrides survive re-runs. The frozen test set
is never redrawn: an override on a test repository changes its label (or removes it from the
labelled set) as evaluation will read it, and `train_ids()` follows the current labels.
`other` can be used like a domain in the labels file.

### Review notebook (`notebooks/corpus_review.ipynb`)

Opens the database given by `SBS_DB` (default `crawler.db`) with the config in `SBS_CONFIG`
(default `config`) and shows, per step: raw vs cleaned samples, length ratios and residual markup;
token counts and frequent tokens; dedup statuses and the largest exact / near groups; labels by
domain and source, classifier confidence, samples per domain, agreement with the crawl domain
(domain mode) and awesome-list coverage; the test-split check. The last section edits the override
files through `corpus/manual.py` and can re-run `dedup` and `label` in place. The helpers it uses
are in `sbs/review/`. Clear all outputs before committing it (a test checks this), since outputs
contain README text.

```powershell
.\.venv\Scripts\python -m jupyter lab notebooks/corpus_review.ipynb
```

### Frozen test set (`corpus/split.py`)

Stratified by domain over `kept` repositories whose label comes from one of the configured
`sources` (by default not `model`, so relevance judgements in evaluation do not rest on classifier
guesses): within each domain the sorted
ids are shuffled with `seed` and the first `test_fraction` (rounded) are taken. The ids, seed and
creation date are written to the `paths.test_set` file together with a `.sha256` file of its
contents; both are committed. `test_ids()` refuses a file whose hash does not match, and
`train_ids()` is every labelled `kept` repository not in the test set. Frozen immediately and never
used for tuning; see [Test-set isolation](architecture.md#test-set-isolation).

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
