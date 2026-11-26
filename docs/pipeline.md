# Pipeline design

Methods for each stage. Layout and data flow are in [architecture.md](architecture.md); every
number referred to here as a parameter is set in `config/experiment.toml`.

## 1. Data preparation

### Domain crawl

The initial domains are machine learning, DevOps, data engineering, web backend, computer
graphics and operating systems. `config/domains.toml` lists them with one or a few seed topics
each and optional extra (non-topic) queries; the topics themselves are not written by hand but
discovered from data (see [Topic discovery](#topic-discovery-corpuslabelsdiscoverypy)) into
`config/topic_map.toml`. The domain mode searches `topic:<t>` for the `max_topic_queries` most
frequent *query topics* of each domain (topics discovered by round `max_query_round`, see below),
then the extra queries. The target number of repositories per
domain is `crawler.domain.target_per_domain` in `config/experiment.toml`. Usage is in
[crawler.md](crawler.md#domain-mode).

### Topic discovery (`corpus/labels/discovery.py`)

`python -m sbs --db <general crawl db> corpus discover-topics` grows each domain's seed topics over
the repositories of a database built by a general (non-domain) crawl, which serves as an unbiased
background sample. When the corpus stage has run on that database, only repositories that dedup
kept are used: spam campaigns, template copies and non-English repositories otherwise inflate the
topics they share (one run pulled Roblox-script and Windows-tweak topics into computer graphics).
Identical multi-topic lists count once, and so do near-identical long lists
(`corpus/labels/topic_collapse.py`): among lists with at least `collapse_min_topics` topics, a list
whose Jaccard similarity to an already kept list reaches `collapse_threshold` (MinHash LSH with
`collapse_num_perm` permutations) is dropped. Spam campaigns publish hundreds of repositories with
slightly varied copies of one long topic list that includes a seed such as `vulkan`, with READMEs
different enough to pass dedup; their topics would otherwise look strongly associated with the
seed from the first round on, which the seed anchor below cannot catch, since in the first round
the current topics are the seeds. In each round, an unassigned topic that occurs in between `min_count`
repositories and a `max_df` share of all repositories (very common topics such as `python` are
never candidates) joins domain D when, among the repositories carrying it, the share that also
carries one of D's current topics (assoc) is

- at least `min_assoc`;
- at least `min_lift` times D's share of all repositories. D's share grows with its topic set, so
  a large domain needs ever stronger evidence: this keeps one domain from absorbing general
  topics as it grows;
- at least `min_margin` times the share for any other domain (topics shared by two domains, e.g.
  `cuda`, join neither);
- and at least `min_seed_assoc` of its repositories carry one of D's original seed topics. This
  anchors every round to the seeds: without it, chains drift (`vulkan` → `directx` →
  `windows-11` → `debloat`), each step related only to the topic added just before.

Each domain takes at most `max_new_per_round` topics per round, most frequent first (ranking by
association instead filled the quota with rare, highly specific topics and left out `pytorch`). Rounds repeat up
to `max_rounds` or until nothing is added (`[corpus.topics]`). A rarely used topic as the only seed
leaves a domain almost empty, so each domain has a few seeds.

The result is written to `topic_map.toml` in the config directory as two lists per domain, most
frequent first: `topics`, every discovered topic, used for labels; and `query_topics`, only the
topics found by round `max_query_round`, used for crawl queries. Later rounds are only indirectly
related to the seeds (game development and console ports reached computer graphics in round 3), which
is acceptable for labelling repositories that are crawled anyway, but would steer the crawl itself
away from the domain. The details (round, count, association, lift, seed association) go to the
`topic_discovery` table. Like every `config/*.toml`, the file is local and not tracked (only the `.example`
templates are); keep a copy with the results of each experiment so a run can be reproduced. The
crawler's domain mode and the labels both read it.

The background sample matters: growing topics from a domain crawl itself would drift (every
repository found from an ML query looks like ML, so any topic it carries would join ML). The
association is a precision criterion: topics that are often used outside the domain, even if
relevant, stay out, and those repositories are left to the classifier.

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
code blocks, HTML comments, `<pre>`/`<code>`/`<script>`/`<style>` blocks, license boilerplate
(paragraphs containing standard license wording such as the Apache, MIT, GPL or BSD texts, and
copyright lines), GitHub alert markers (`[!NOTE]` etc.), images and badges (inline and
reference-style), reference link definitions, link targets (link text is kept), remaining HTML
tags (inner text kept), HTML entities (unescaped), bare URLs and autolinks, e-mail addresses, and
Markdown markup (tables, blockquotes, list markers, headings, emphasis, rules; container markup is
removed before headings so a heading inside a quote or table is caught). Inline code on a single
line is kept as text when it has at most `inline_code_max_words` words (tool and command names
such as `docker compose` carry domain information) and removed otherwise; stray backticks are
dropped. Paragraph breaks are kept. License text is removed because it is identical across
unrelated projects and would otherwise make them near duplicates.
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
   group, kept repository included, is dropped. Two kinds of group are an original with its copies
   instead, and keep the original `kept`:
   - mirrors: at least `mirror_share` of the members have the kept repository's name inside their
     own (compared without case and punctuation, e.g. `*/linux`, `*/openwrt`);
   - star dominance: the kept repository has more than `keep_star_ratio` times the stars of any
     other member.

Later stages read only `kept` repositories.

### Domain labels (`corpus/labels/`)

Applied in order to `kept` repositories, first match wins (a manual label, if any, comes first):

1. **Topics** (`topics.py`): `config/topic_map.toml` (generated by topic discovery) maps GitHub
   topics to domains; the domain with the most matching topics wins, a tie gives no label.
   **Expanded topics** (`expansion.py`, source `topic_ext`): among repositories labelled by mapped
   topics, an unmapped topic seen at least `min_count` times with at least `min_purity` of them in
   one domain is added to that domain (`[corpus.labels.expansion]`; `exclude` vetoes topics). The
   added topics are stored in `topic_expansion` and applied only when the mapped topics give no
   label. They also count as mapped when choosing `other` examples, so domain repositories tagged
   with newer topics (e.g. an LLM tag) are not used as `other`.
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

`label_source` (`topic`, `topic_ext`, `awesome`, `manual`, `model`) and, for `model`, the predicted
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
