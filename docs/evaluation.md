# Evaluation

All evaluation runs on the frozen test set only (see
[Test-set isolation](architecture.md#test-set-isolation)). Methods are in
[pipeline.md](pipeline.md); sample sizes, repetitions and targets are parameters in
`config/experiment.toml`.

## Core comparisons

| Comparison | Claim |
|---|---|
| word2vec vs Transformer | Context-dependent vectors separate the senses of a polysemous term better. |
| Baseline search vs this system | Grouping plus expansion finds a suitable repository faster. |
| Real vs shuffled labels; polysemous vs monosemous terms | The effect is neither chance nor an artefact of writing style. |

## Precision@10 (`evaluation/precision.py`)

- **Queries**: top-ranked polysemy candidates, each paired with every domain in which enough test
  repositories contain the term.
- **Relevance (proxy)**: a test repository is relevant to (term, domain) if it carries that domain
  label and its cleaned README contains the term.
- **Baseline**: field-weighted BM25, top 10.
- **System**: hybrid retrieval; the simulated user picks the group whose centroid is closest to
  the target domain's centroid (computed on training data); precision of that group's top 10.
- Reported: mean precision@10 per system and the difference, against the configured target gain.

## Simulated user (`evaluation/simulation.py`)

1. Pick a target repository from the test set.
2. Start with a vague query: one of its polysemous terms.
3. Baseline: the target's rank in the BM25 list.
4. System: at each step choose a group, then the suggested keyword most similar to the target's
   README; search again. Stop when the target is in the top 10 or after a step limit.

Reported: MRR for both, and the number of steps until the target enters the top 10.

## Controls (`evaluation/controls.py`)

- **Shuffled labels**: domain labels of the training data are permuted and the term filter and
  sense probe are rerun, repeated to obtain a distribution. Real-label scores should be clearly
  above it.
- **Monosemous terms**: terms with the same meaning in every domain (`config/control_terms.toml`,
  e.g. *install*) go through the same sense probe. Polysemous terms should score clearly higher,
  ruling out per-domain writing style as the cause.

## Label audit (`evaluation/audit.py`)

A random sample of labelled repositories is exported to CSV for manual checking; the filled-in
file is read back to estimate label accuracy with a confidence interval, overall and per
`label_source`.

## Error analysis (`evaluation/audit.py`)

Failed queries (precision or simulation) are exported with their top results. Each failure is
assigned manually to one category, then counted:

| Category | Meaning |
|---|---|
| Semantic confusion | Results use the term in another domain's sense. |
| Mixed types | Groups mix different kinds of repositories (libraries, tutorials, apps). |
| Non-project results | Lists, notes, course material or other non-project repositories. |
| Insufficient information | README too short or uninformative to judge. |

## Results

To be filled in after the experiments.
