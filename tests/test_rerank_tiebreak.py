"""Reranker ordering — especially when the scores tie.

Regression: `rerank()` iterated its candidate union with `for idx in set(...)`
and sorted only by final score. Python's sort is stable, so whenever every
final score was equal the winner fell out of set-iteration order — effectively
the dataset row number — not relevance.

That happens for real queries: when a query's raw cosines all sit below
`COSINE_FLOOR` they calibrate to 0.0, and if BM25 also returns nothing then
every candidate scores exactly 0.0. The pipeline still defers (0.0 is below
every threshold), so a visitor never saw a wrong answer — but the reported best
candidate was meaningless and it cost real points in the offline benchmark.

The fix keeps each retriever's own ordering and uses it to break ties.
"""
from app.services import rerank


# Four candidates that share no content token with the query, so coverage is 0
# for all of them and — with zero dense/lexical scores — every final score ties.
TEXTS = [
    "aaa bbb",
    "ccc ddd",
    "eee fff",
    "ggg hhh",
]
QUERY = "zzz yyy"


def ids(ranked):
    return [idx for idx, _score, _signals in ranked]


def test_all_scores_tie_so_dense_order_decides():
    """The exact production case: every calibrated dense score is 0.0."""
    dense = [(3, 0.0), (1, 0.0), (0, 0.0), (2, 0.0)]
    ranked = rerank.rerank(QUERY, TEXTS, dense=dense, lexical=[])

    assert all(score == 0.0 for _idx, score, _s in ranked), "fixture must actually tie"
    assert ids(ranked) == [3, 1, 0, 2], "dense ordering was not preserved through the tie"


def test_the_winner_is_the_dense_top_not_the_lowest_row():
    """Before the fix this returned index 0 — the smallest row number."""
    dense = [(3, 0.0), (2, 0.0), (1, 0.0), (0, 0.0)]
    idx, score, _ = rerank.best(QUERY, TEXTS, dense=dense, lexical=[])
    assert idx == 3
    assert score == 0.0, "the tie-break must not invent a score"


def test_lexical_order_breaks_ties_the_dense_list_cannot():
    """A candidate only BM25 found still has a defined position."""
    dense = []
    lexical = [(2, 0.0), (0, 0.0), (3, 0.0)]
    assert ids(rerank.rerank(QUERY, TEXTS, dense=dense, lexical=lexical)) == [2, 0, 3]


def test_dense_rank_outranks_lexical_rank_on_a_tie():
    """Dense is the heavier-weighted and only calibrated signal, so it decides."""
    dense = [(1, 0.0), (0, 0.0)]
    lexical = [(0, 0.0), (1, 0.0)]
    assert ids(rerank.rerank(QUERY, TEXTS, dense=dense, lexical=lexical))[0] == 1


def test_a_real_score_still_beats_a_better_rank():
    """The tie-break must only apply to actual ties — never override a score."""
    # Index 0 is last in the dense list but is the only one with a real score.
    dense = [(1, 0.0), (2, 0.0), (0, 0.9)]
    idx, score, _ = rerank.best(QUERY, TEXTS, dense=dense, lexical=[])
    assert idx == 0
    assert score > 0.0


def test_ordering_is_stable_across_repeated_calls():
    """Set iteration made this technically implementation-defined; pin it."""
    dense = [(2, 0.0), (0, 0.0), (3, 0.0), (1, 0.0)]
    first = ids(rerank.rerank(QUERY, TEXTS, dense=dense, lexical=[]))
    for _ in range(20):
        assert ids(rerank.rerank(QUERY, TEXTS, dense=dense, lexical=[])) == first


def test_candidates_outside_the_text_range_are_still_dropped():
    """The existing bounds guard must survive the sort change."""
    dense = [(99, 0.0), (-1, 0.0), (1, 0.0)]
    assert ids(rerank.rerank(QUERY, TEXTS, dense=dense, lexical=[])) == [1]


def test_scored_ordering_is_unchanged_by_the_tie_break():
    """Normal, non-tied ranking must behave exactly as before."""
    dense = [(0, 0.9), (1, 0.5), (2, 0.2)]
    ranked = rerank.rerank(QUERY, TEXTS, dense=dense, lexical=[])
    scores = [s for _i, s, _sig in ranked]
    assert scores == sorted(scores, reverse=True)
    assert ids(ranked)[0] == 0
