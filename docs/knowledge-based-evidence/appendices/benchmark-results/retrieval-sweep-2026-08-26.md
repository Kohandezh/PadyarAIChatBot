# Reranker weight × cosine-floor sweep — 2026-08-26 (Q6)

Golden set: 60 queries (42 answerable), post Q1 (expansion dedup) + Q2 (dual query).
TRUST=0.70 as shipped. 15 runs: 5 weight configs × 3 floors, span 0.35.

| weights (dense/bm25/cov) | floor | R@1 | R@3 | MRR | FP-unsupported | FP-injection |
|---|---|---|---|---|---|---|
| 0.45/0.35/0.20 | 0.45 | 0.929 | 0.929 | 0.937 | 2 | 2 |
| 0.50/0.30/0.20 | 0.45 | 0.929 | 0.929 | 0.937 | 2 | 2 |
| 0.55/0.25/0.20 | 0.45 | 0.929 | 0.929 | 0.937 | 2 | 2 |
| 0.62/0.23/0.15 | 0.45 | 0.929 | 0.929 | 0.937 | 2 | 2 |
| 0.40/0.40/0.20 | 0.45 | 0.905 | 0.929 | 0.925 | 2 | 2 |
| 0.55/0.25/0.20 | 0.40 | 0.881 | 0.929 | 0.913 | 2 | 2 |
| 0.45/0.35/0.20 | 0.40 | 0.881 | 0.929 | 0.913 | 3 | 2 |
| 0.50/0.30/0.20 | 0.40 | 0.881 | 0.929 | 0.913 | 3 | 2 |
| 0.62/0.23/0.15 | 0.40 | 0.857 | 0.929 | 0.902 | 3 | 2 |
| 0.40/0.40/0.20 | 0.40 | 0.857 | 0.929 | 0.898 | 3 | 2 |
| 0.55/0.25/0.20 | 0.35 | 0.857 | 0.905 | 0.894 | 3 | 2 |
| 0.62/0.23/0.15 | 0.35 | 0.833 | 0.905 | 0.882 | 3 | 2 |
| 0.45/0.35/0.20 | 0.35 | 0.833 | 0.905 | 0.879 | 3 | 2 |
| 0.50/0.30/0.20 | 0.35 | 0.833 | 0.905 | 0.877 | 3 | 2 |
| 0.40/0.40/0.20 | 0.35 | 0.810 | 0.905 | 0.864 | 3 | 2 |

## Findings

1. **The shipped floor (0.45) wins on every weight config** (R@1 0.929).
   Lowering it HURTS: 0.40 → 0.881, 0.35 → 0.833, and FP-unsupported rises
   2→3. The earlier hypothesis — the floor drowning true matches — was
   disproven for this corpus: the Q1 expansion fix had already rescued the
   dense=0.000 clusters; a lower floor now only lifts wrong answers past
   the trust bar.
2. **Weight configs A–D are indistinguishable on this corpus** (42 answerable
   queries; differences within one question). Only E (dense 0.40) measurably
   hurts (R@1 0.905). The corpus cannot justify changing the shipped
   0.62/0.23/0.15.
3. **FP-injection=2 is weight- and floor-invariant** — those two queries
   score 0.75/0.82 through the QUESTIONS-BLEND path, which the sweep does
   not touch. They are a threshold-policy matter for the import phase, not
   a reranker one.
4. The two remaining wrong answers («خبرای جدید رویداد کجاست؟» → venue 0.96,
   «When is INOTEX 2026 held?» → venue 0.82) also arrive via questions-blend
   — both are missing-curated-question cases (colloquial خبرای; English
   date), to be fixed by import-time canonical questions, not weights.

## Verdict (lock)

- floor/span: **keep 0.45/0.35** (already the default — no change).
- weights: **keep 0.62/0.23/0.15** (no data justifies a move; E is the only
  distinguishable loser).
- thresholds: **keep TRUST 0.70 / fallback 0.45 / questions 0.60**.
- Re-run this sweep after the 169-company import, where short company
  names make BM25 far more decisive — the corpus that would actually
  separate A from C.