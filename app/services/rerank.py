"""Reranking stage over hybrid retrieval candidates.

WHAT THIS IS — and is not
-------------------------
This is a **feature-based reranker**, not a neural cross-encoder. It takes the
union of the candidates produced by the dense (embedding) and lexical (BM25)
retrievers and rescores each one against the query using signals that the
first-stage retrievers each miss on their own:

  dense        calibrated cosine — meaning, paraphrases, colloquial Persian
  lexical      BM25 (relative)   — rare decisive terms, exact vocabulary
  coverage     share of the UNEXPANDED query's *content* tokens actually
               present in the candidate — the anti-hallucination signal: a
               candidate that shares no content word with the question is
               demoted no matter how close it looks in embedding space.
               Unexpanded is load-bearing; see `rerank(coverage_query=...)`.
  agreement    small bonus when both retrievers independently rank it top-1

A cross-encoder (e.g. a multilingual mono-BERT) would score higher on paper,
but it requires torch + transformers and a several-hundred-MB model download —
new runtime infrastructure this installation deliberately does not take on
(exhibition machines are provisioned once, offline). That option is documented
in docs/engineering/DECISIONS.md rather than silently implied.

The output stays on the SAME 0..1 scale as before, so the pipeline's existing
thresholds (TRUSTED_MATCH_THRESHOLD, LOCAL_FALLBACK_THRESHOLD) keep their
meaning and no caller changes.
"""
from typing import Dict, List, Optional, Sequence, Tuple

# Weights sum to 1.0 across the scoring signals. Dense stays dominant because
# it is the only calibrated, absolute signal; BM25 is relative-per-query and
# coverage is a gate rather than a ranker.
W_DENSE = 0.62
W_LEXICAL = 0.23
W_COVERAGE = 0.15
AGREEMENT_BONUS = 0.04

# Tokens that carry no topical content: they must not count toward coverage,
# otherwise a query like «اینوتکس چیست» looks "covered" by every INOTEX entry.
STOPWORDS = {
    "و", "در", "به", "از", "که", "را", "با", "این", "آن", "است", "هست", "برای",
    "می", "شود", "شد", "کن", "کنم", "کنید", "چه", "چی", "چیست", "چیه", "آیا",
    "چطور", "چگونه", "کی", "کجا", "کجاست", "چند", "چقدر", "من", "ما", "شما",
    "the", "a", "an", "is", "are", "of", "to", "in", "for", "what", "when",
    "where", "how", "do", "does", "i", "we", "you", "it",
}


def content_tokens(text: str) -> set:
    return {t for t in text.split() if t not in STOPWORDS and len(t) > 1}


def _coverage(query: str, candidate_text: str) -> float:
    """Share of the query's content tokens present in the candidate."""
    q = content_tokens(query)
    if not q:
        return 0.0
    c = content_tokens(candidate_text)
    return len(q & c) / len(q)


def rerank(
    query: str,
    texts: Sequence[str],
    dense: Optional[List[Tuple[int, float]]] = None,
    lexical: Optional[List[Tuple[int, float]]] = None,
    coverage_query: Optional[str] = None,
) -> List[Tuple[int, float, Dict[str, float]]]:
    """Rescore the candidate union.

    ``dense``/``lexical`` are (index, score) lists from the two retrievers —
    dense scores calibrated 0..1, lexical normalized 0..1 per query. Returns
    (index, final_score, signals) sorted best-first; ``signals`` is kept for
    the logs and the evaluation harness, so a ranking decision can always be
    explained after the fact.

    ``coverage_query`` is the query WITHOUT synonym expansion, and passing it
    is what makes the coverage signal mean what this module says it means.

    Synonym expansion is right for the two retrievers — it is how a colloquial
    question reaches a formally-worded entry — but it destroys coverage, which
    exists to ask "does the candidate actually talk about what was asked?".
    Expansion answers that question with words the user never said. Measured on
    the live corpus: «قیمت دلار امروز چند است؟» normalises to
    «هزینه هزینه قیمت نرخ مبلغ مبلغ نرخ دلار امروز چند است» — one word, قیمت,
    became five price-synonyms, while دلار, the single token that makes the
    question out-of-domain, stayed one token among six. An entry about ticket
    prices then "covers" 0.667 of the query, and the anti-hallucination signal
    votes FOR the hallucination. On the unexpanded query the same entry covers
    0.333 and دلار is visibly unmatched.

    Defaults to ``query`` so an existing caller keeps its current behaviour.
    """
    dense = dense or []
    lexical = lexical or []
    cov_query = coverage_query if coverage_query is not None else query
    dense_by_idx = dict(dense)
    lexical_by_idx = dict(lexical)

    # Each retriever hands us its candidates already ordered best-first. Keep
    # that ORDER, not just the score: when a query's raw cosines all fall below
    # COSINE_FLOOR they calibrate to 0.0 and every final score ties, but the
    # ordering behind them was still real. Without this the tie was broken by
    # set-iteration order — effectively the dataset row number — so the reported
    # best candidate was arbitrary.
    dense_rank = {idx: pos for pos, (idx, _score) in enumerate(dense)}
    lexical_rank = {idx: pos for pos, (idx, _score) in enumerate(lexical)}
    # Sorts after anything either retriever actually ranked.
    UNRANKED = len(texts) + 1

    dense_top = dense[0][0] if dense else -1
    lexical_top = lexical[0][0] if lexical else -1

    results: List[Tuple[int, float, Dict[str, float]]] = []
    # sorted() rather than bare set iteration: the candidate set is built from a
    # set union, and nothing downstream should depend on its traversal order.
    for idx in sorted(set(dense_by_idx) | set(lexical_by_idx)):
        if idx < 0 or idx >= len(texts):
            continue
        d = dense_by_idx.get(idx, 0.0)
        lex = lexical_by_idx.get(idx, 0.0)
        cov = _coverage(cov_query, texts[idx])

        score = W_DENSE * d + W_LEXICAL * lex + W_COVERAGE * cov
        if idx == dense_top and idx == lexical_top:
            score = min(1.0, score + AGREEMENT_BONUS)

        # Coverage gate: zero shared content tokens means the candidate does
        # not talk about what was asked. Halve it rather than drop it — on a
        # cross-lingual query (English question, Persian entry) coverage is
        # legitimately zero and the dense signal must still be able to win.
        if cov == 0.0:
            score *= 0.5

        results.append((idx, round(min(1.0, score), 4),
                        {"dense": round(d, 4), "lexical": round(lex, 4), "coverage": round(cov, 4)}))

    # Score first; then the dense retriever's own rank, then the lexical one's,
    # then the index so the result is fully deterministic. Dense breaks the tie
    # before lexical because it is the heavier-weighted and only calibrated
    # signal. Nothing here changes a ranking where the scores actually differ.
    results.sort(key=lambda r: (-r[1],
                                dense_rank.get(r[0], UNRANKED),
                                lexical_rank.get(r[0], UNRANKED),
                                r[0]))
    return results


def best(
    query: str,
    texts: Sequence[str],
    dense: Optional[List[Tuple[int, float]]] = None,
    lexical: Optional[List[Tuple[int, float]]] = None,
    coverage_query: Optional[str] = None,
) -> Tuple[int, float, Dict[str, float]]:
    """Convenience wrapper: the single best candidate, or (-1, 0.0, {})."""
    ranked = rerank(query, texts, dense, lexical, coverage_query)
    return ranked[0] if ranked else (-1, 0.0, {})
