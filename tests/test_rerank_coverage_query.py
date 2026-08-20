"""Coverage must be measured against what the visitor actually typed.

THE DEFECT
----------
`normalize_persian` expands synonyms from the DB before the query reaches the
reranker, and the reranker used that expanded string for its coverage signal —
the one the module documents as "the anti-hallucination signal: a candidate
that shares no content word with the question is demoted no matter how close it
looks in embedding space".

Expansion defeats it. On the live corpus «قیمت دلار امروز چند است؟» normalises
to «هزینه هزینه قیمت نرخ مبلغ مبلغ نرخ دلار امروز چند است»: the single word
قیمت becomes five price-synonyms, while دلار — the one token that makes the
question out-of-domain — stays one token among six. An entry about ticket
prices then "covers" two thirds of the query, and the signal built to catch
hallucination votes in favour of it.

Expansion is still right for the two retrievers: it is how a colloquial
question reaches a formally-worded entry. It is only wrong for coverage, so the
query is passed twice — expanded for recall, unexpanded for precision.
"""
import pytest

from app.services import rerank


TICKET = "بلیط اینوتکس چقدر است هزینه قیمت نرخ مبلغ ورودیه"
VENUE = "مکان نمایشگاه کجاست محل آدرس"

# What normalize_persian actually produces for the out-of-domain query, and the
# same query with expansion switched off. Held as literals so the test states
# the defect even if the synonym table changes.
EXPANDED = "هزینه هزینه قیمت نرخ مبلغ مبلغ نرخ دلار امروز چند است"
UNEXPANDED = "قیمت دلار امروز چند است"


def test_expansion_inflates_coverage_for_an_out_of_domain_query():
    """The measurement that motivated the fix, pinned as an assertion."""
    inflated = rerank._coverage(EXPANDED, TICKET)
    honest = rerank._coverage(UNEXPANDED, TICKET)
    assert inflated > honest, (inflated, honest)
    assert inflated >= 0.6, "expanded query should look well covered"
    assert honest <= 0.4, "unexpanded query should not"


def test_the_distinguishing_token_is_diluted_by_expansion():
    """دلار is 1 of 3 content tokens in what the visitor typed and 1 of 6 after
    expansion — the expansion halves the weight of the only word that makes the
    question out-of-domain."""
    assert "دلار" in rerank.content_tokens(UNEXPANDED)
    assert "دلار" in rerank.content_tokens(EXPANDED)
    assert len(rerank.content_tokens(EXPANDED)) > len(rerank.content_tokens(UNEXPANDED))
    assert "دلار" not in rerank.content_tokens(TICKET)   # nothing in the corpus


def test_rerank_uses_the_coverage_query_when_given_one():
    dense = [(0, 1.0)]
    lexical = [(0, 1.0)]
    texts = [TICKET]

    without = rerank.rerank(EXPANDED, texts, dense, lexical)[0]
    with_ = rerank.rerank(EXPANDED, texts, dense, lexical,
                          coverage_query=UNEXPANDED)[0]

    assert with_[2]["coverage"] < without[2]["coverage"]
    assert with_[1] < without[1], "final score must fall with honest coverage"


def test_omitting_the_coverage_query_preserves_the_old_behaviour():
    """Defaulting to `query` keeps every existing caller identical."""
    dense, lexical, texts = [(0, 0.8)], [(0, 0.7)], [VENUE]
    a = rerank.rerank(EXPANDED, texts, dense, lexical)
    b = rerank.rerank(EXPANDED, texts, dense, lexical, coverage_query=EXPANDED)
    assert a == b


def test_best_passes_the_coverage_query_through():
    dense, lexical, texts = [(0, 1.0)], [(0, 1.0)], [TICKET]
    _i, score_expanded, _s = rerank.best(EXPANDED, texts, dense, lexical)
    _i, score_honest, _s = rerank.best(EXPANDED, texts, dense, lexical,
                                       coverage_query=UNEXPANDED)
    assert score_honest < score_expanded


def test_the_cross_lingual_carve_out_still_applies():
    """An English question against a Persian entry has legitimately zero
    coverage, and the dense signal must still be able to win. Halving rather
    than zeroing is deliberate and unchanged."""
    dense, lexical, texts = [(0, 1.0)], [(0, 1.0)], [VENUE]
    _i, score, signals = rerank.best("where is inotex held", texts, dense, lexical,
                                     coverage_query="where is inotex held")
    assert signals["coverage"] == 0.0
    assert score > 0.4, "a zero-coverage candidate is halved, not discarded"


def test_the_search_pipeline_passes_an_unexpanded_coverage_query():
    """The wiring, not just the reranker: search.py must compute both forms."""
    import inspect
    from app.services import search
    src = inspect.getsource(search)
    assert src.count("expand_synonyms=False") >= 2, \
        "both retrieval paths must build an unexpanded coverage query"
    assert src.count("coverage_query=coverage_query") >= 2


def test_an_out_of_domain_question_scores_lower_than_it_used_to():
    """End to end through the real questions index.

    HONEST SCOPE. «قیمت دلار امروز چند است؟» scored 0.99 against the questions
    index before this fix and scores ~0.94 after it. That is a real improvement
    to a signal that was actively wrong, and it is NOT enough to clear the 0.70
    trust bar: dense and lexical are both relative-per-query, so they are both
    ~1.0 for whatever the corpus's nearest item happens to be, and together
    they hold 0.85 of the blend. Coverage is 0.15 and cannot move a 0.90 base
    below 0.70 no matter how honest it is.

    This test pins the improvement without pretending the question is now
    refused. Closing that gap needs a change to how confidence is computed, not
    a better coverage number — see the analysis in the commit message.
    """
    from app.services import search as s
    s.load_dataset_internal()
    if not s.normalized_questions:
        pytest.skip("no questions index in this environment")
    _entry, score = s.find_similar_question("قیمت دلار امروز چند است؟")
    assert score < 0.98, f"expected the inflated 0.99 to drop; got {score:.3f}"
