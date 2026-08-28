"""Dual-query retrieval: each retriever sees the original AND the expanded form.

WHY
---
Synonym expansion is right for recall and wrong for the embedding model: the
2026-08-26 diagnostic showed the expanded form scoring dense=0.000 where the
original kept the exact vocabulary decisive. So every hybrid retriever (dense
and BM25, over both the dataset and the questions index) runs on BOTH forms
and unions its hits by document with the max score; coverage stays on the
original, exactly as before.

The second test is a wiring test in the AGENTS.md sense: it fails if a
retriever is ever handed only one form again, which an aggregate metric
would never prove.
"""
import pytest

from app.services import search as S


def test_union_keeps_best_per_document_and_orders():
    calls = []

    def retrieve(q, k):
        calls.append(q)
        return [(0, 0.9), (1, 0.5)] if q == "orig" else [(1, 0.8), (2, 0.4)]

    hits = S._dual_hits(retrieve, "orig", "expanded", 5)
    assert calls == ["orig", "expanded"]
    assert hits == [(0, 0.9), (1, 0.8), (2, 0.4)]


def test_identical_forms_query_once():
    calls = []

    def retrieve(q, k):
        calls.append(q)
        return [(0, 1.0)]

    assert S._dual_hits(retrieve, "same", "same", 5) == [(0, 1.0)]
    assert calls == ["same"]


def test_max_not_sum_two_mediocre_do_not_outvote_one_strong():
    def retrieve(q, k):
        # doc 0: strong on original only. doc 1: mediocre on both.
        return [(0, 0.9), (1, 0.4)] if q == "orig" else [(1, 0.4), (2, 0.3)]

    hits = S._dual_hits(retrieve, "orig", "exp", 5)
    assert hits[0] == (0, 0.9), "0.4+0.4 agreement must not beat a 0.9 match"


@pytest.fixture
def hybrid_index(tmp_path, monkeypatch):
    """A real loaded index with a stub retriever recording what it was fed."""
    import os
    os.environ.setdefault("OPENAI_API_KEY", "test")
    monkeypatch.setattr(S, "dataset", [{"id": "x", "title": "t", "text": "b"}])
    monkeypatch.setattr(S, "normalized_titles", ["t"])
    monkeypatch.setattr(S, "normalized_descriptions", ["t b"])
    monkeypatch.setattr(S, "questions_data", [])
    monkeypatch.setattr(S, "normalized_questions", [])
    seen = []

    class Stub:
        def search_topk(self, q, k):
            seen.append(("dense", q))
            return [(0, 0.5)]

    monkeypatch.setattr(S, "dataset_embedding_index", Stub())
    monkeypatch.setattr(S, "dataset_bm25_index", None)
    return seen


def test_find_best_match_feeds_both_forms_to_the_retriever(hybrid_index, monkeypatch):
    monkeypatch.setattr(S, "_maybe_refresh", lambda: None)

    class FakeSynonyms:
        def __init__(self):
            import app.utils.normalizer as n
            monkeypatch.setattr(n, "active_synonyms", [("غرفه", "غرفه استند")])
            monkeypatch.setattr(n, "_expansions_cache", ((), ()))
            monkeypatch.setattr(n, "_expansion_pattern_cache", ((), None))

    FakeSynonyms()
    entry, score = S.find_best_match("غرفه چقدر است")
    assert entry is not None
    forms = [q for kind, q in hybrid_index if kind == "dense"]
    # «غرفه چقدر است» (original) AND its expansion both reached the retriever.
    assert any("غرفه" in q and "استند" not in q for q in forms), forms
    assert any("استند" in q for q in forms), forms
    assert 0.0 <= score <= 1.0
