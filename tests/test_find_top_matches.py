"""`find_top_matches` — the same retrieval, k results instead of one.

WHY IT IS NEEDED: today `find_best_match()` runs the whole hybrid pipeline
(dense + BM25 + reranker) and then throws away everything except the winner.
The selection tier needs the candidates, so the ranking has to be exposed
without changing the ranking.

WHY IT IS A NEW FUNCTION AND NOT A PARAMETER: `RERANK_CANDIDATES = 5` tunes
what the retrievers propose to the reranker on today's path, and today's
measured recall@1 = 0.786 depends on it. `find_top_matches` takes its OWN k so
asking for thirteen candidates cannot move a number that is already in the
evidence pack.

THE TWO PROPERTIES THAT MATTER MOST:
  * the HEAD must agree with `find_best_match` — if the candidate list and
    Tier 1 rank the corpus differently, the eagerness margin compares scores
    from a ranking nobody serves;
  * `coverage_query=` must be passed through — dropping it reopens the
    «قیمت دلار» hole, where an out-of-domain query scores well because the
    synonym-expanded form covers tokens the visitor never typed.

Also here: `get_entry`, the id → record lookup the pick tier and the selection
tier both read their chosen record back through.
"""
import pytest
from fastapi.testclient import TestClient


TITLE_SHORTCUT = "هزینه غرفه نمایشگاه"

DATASET = [
    ("faq-cost", TITLE_SHORTCUT,
     "هزینه اجاره غرفه بر اساس متراژ محاسبه می شود و در دفتر اعلام می گردد.", ""),
    ("faq-hours", "ساعت کاری",
     "ساعت کاری نمایشگاه از نه صبح تا شش بعد از ظهر است.", ""),
    ("faq-guide", "اطلاعات بازدید",
     "درباره غرفه ها و ورودی و مسیر دسترسی توضیح کامل موجود است.", ""),
]
DATASET += [
    (f"co-{n}", f"شرکت شماره {n}",
     f"شرکت شماره {n} در زمینه هوش مصنوعی و نرم افزار فعال است و غرفه دارد.",
     f"ghorfe-{n:02d}.mp4")
    for n in range(1, 18)
]


def _seed(rows=DATASET):
    import app.db.connection as dbc
    conn = dbc.get_db_connection()
    conn.execute("DELETE FROM dataset")
    conn.execute("DELETE FROM questions")
    conn.execute("DELETE FROM synonyms")
    for i, title, text, video in rows:
        conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                     " VALUES (?, ?, ?, ?)", (i, title, text, video))
    conn.commit()
    conn.close()
    from app.services import search
    search.load_dataset_internal()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "topk.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.db.queries import set_setting
        set_setting("openai_enabled", "true")
        set_setting("search_backend", "tfidf")
        yield c
    security._chat_rate_limits.clear()


# ── The shape of the result ──────────────────────────────────────────────

def test_it_returns_entries_with_their_score_and_signals_best_first(client):
    """(entry, score, signals) — the same triple `scripts/run_eval.py` already
    consumes from `full_ranking()`, so the evaluation harness and the runtime
    read one shape. `signals` is what makes a ranking decision explainable
    after the fact."""
    _seed()
    from app.services import search

    results = search.find_top_matches("هوش مصنوعی و نرم افزار", k=8)
    assert results, "a query made of corpus words must retrieve something"
    for entry, score, signals in results:
        assert isinstance(entry, dict) and entry.get("id"), entry
        assert isinstance(score, float), score
        assert isinstance(signals, dict), signals
    scores = [s for _e, s, _sig in results]
    assert scores == sorted(scores, reverse=True), scores


@pytest.mark.parametrize("k", [1, 3, 8, 13])
def test_it_never_returns_more_than_k(client, k):
    """The prompt budget is the reason k exists at all. A retriever that
    returns twenty records when asked for eight silently doubles what the
    model is shown."""
    _seed()
    from app.services import search
    results = search.find_top_matches("هوش مصنوعی و نرم افزار و غرفه", k=k)
    assert len(results) <= k, len(results)


def test_it_returns_unique_records(client):
    """Dense and lexical retrievers propose overlapping candidates. A record
    listed twice wastes a slot and invites the model to "choose" between the
    same company and itself."""
    _seed()
    from app.services import search
    results = search.find_top_matches("هوش مصنوعی و نرم افزار و غرفه", k=13)
    ids = [e["id"] for e, _s, _sig in results]
    assert len(ids) == len(set(ids)), ids


# ── It must not skip the freshness check ─────────────────────────────────

def test_it_refreshes_the_index_before_ranking(client):
    """Staff correct content while visitors ask. Skipping `_maybe_refresh()`
    serves stale content after an admin edit on a multi-worker install — the
    exact reason INDEX_VERSION_KEY exists."""
    _seed()
    from app.services import search
    calls = {"n": 0}
    real = search._maybe_refresh

    def spy():
        calls["n"] += 1
        return real()

    search._maybe_refresh = spy
    try:
        search.find_top_matches("هوش مصنوعی", k=5)
    finally:
        search._maybe_refresh = real
    assert calls["n"] >= 1


# ── The coverage query must survive the copy ─────────────────────────────

def test_it_passes_the_unexpanded_coverage_query_to_the_reranker(client, monkeypatch):
    """Coverage must be measured against what the VISITOR typed, not against
    the synonym-expanded form. Dropping this argument while copying
    find_best_match is a one-word mistake that reopens the «قیمت دلار»
    hallucination hole, and nothing else in the system would notice."""
    _seed()
    from app.services import search
    seen = {}
    real = search.rerank.rerank

    def spy(query, texts, dense=None, lexical=None, coverage_query=None):
        seen["query"] = query
        seen["coverage_query"] = coverage_query
        return real(query, texts, dense, lexical, coverage_query)

    monkeypatch.setattr(search.rerank, "rerank", spy)
    search.find_top_matches("هوش مصنوعی و نرم افزار", k=8)

    assert "coverage_query" in seen, "rerank.rerank was never called"
    assert seen["coverage_query"], "coverage_query must be passed, not None"
    from app.utils.normalizer import normalize_persian
    assert seen["coverage_query"] == normalize_persian(
        "هوش مصنوعی و نرم افزار", expand_synonyms=False)


# ── The head must agree with today's Tier 1 ──────────────────────────────

def test_the_title_overlap_shortcut_is_the_first_candidate_with_its_own_score(client):
    """`find_best_match` EXITS at the title-overlap branch with a synthetic
    0.95+ score. If find_top_matches ignored that branch, the two would rank
    the corpus differently and the eagerness margin would compare numbers
    nobody serves. scripts/run_eval.py already does exactly this prepend."""
    _seed()
    from app.services import search

    best, best_score = search.find_best_match(TITLE_SHORTCUT)
    assert best["id"] == "faq-cost" and best_score >= 0.95, (best, best_score)

    results = search.find_top_matches(TITLE_SHORTCUT, k=8)
    assert results, results
    top_entry, top_score, _sig = results[0]
    assert top_entry["id"] == "faq-cost", results[0]
    assert top_score == pytest.approx(best_score), (top_score, best_score)


def test_the_shortcut_record_is_not_repeated_in_the_reranked_tail(client):
    """Prepending without removing leaves the same record at rank 1 and again
    at rank 4 — a "choose one of these" list with a duplicate in it."""
    _seed()
    from app.services import search
    results = search.find_top_matches(TITLE_SHORTCUT, k=8)
    ids = [e["id"] for e, _s, _sig in results]
    assert ids.count("faq-cost") == 1, ids


def test_the_head_agrees_with_find_best_match_on_an_ordinary_query(client):
    """Not only on the shortcut path. Whatever Tier 1 would have served is
    candidate number one, or the two tiers disagree about the same corpus."""
    _seed()
    from app.services import search
    query = "ساعت کاری نمایشگاه"
    best, _score = search.find_best_match(query)
    results = search.find_top_matches(query, k=8)
    assert results, results
    assert results[0][0]["id"] == best["id"], (results[0][0], best)


# ── It degrades exactly like find_best_match ─────────────────────────────

def test_it_falls_back_to_tfidf_when_the_hybrid_stack_raises(client, monkeypatch):
    """Same soft-fail contract as `find_best_match`: embeddings or the
    reranker failing must cost ranking quality, never the answer. The
    selection tier still gets candidates."""
    _seed()
    from app.services import search

    class _Exploding:
        def search_topk(self, *a, **kw):
            raise RuntimeError("embedding index is down")

        def search(self, *a, **kw):
            raise RuntimeError("embedding index is down")

    def boom(*a, **kw):
        raise RuntimeError("reranker is down")

    monkeypatch.setattr(search, "dataset_embedding_index", _Exploding())
    monkeypatch.setattr(search.rerank, "rerank", boom)
    monkeypatch.setattr(search.rerank, "best", boom)

    results = search.find_top_matches("هوش مصنوعی و نرم افزار", k=5)
    assert results, "a degraded retriever must still propose candidates"
    assert len(results) <= 5
    assert all(isinstance(e, dict) and e.get("id") for e, _s, _sig in results)


def test_an_empty_dataset_returns_no_candidates_rather_than_raising(client):
    """A brand-new install with no content answers nothing, and it must do so
    without a traceback."""
    _seed(rows=[])
    from app.services import search
    assert search.find_top_matches("هر چیزی", k=8) == []


# ── get_entry ────────────────────────────────────────────────────────────

def test_get_entry_returns_the_record_for_a_known_id(client):
    """The pick tier stores IDS, not text, so this is the one lookup that
    turns a stored offer back into an answer — with its video_url intact."""
    _seed()
    from app.services import search
    entry = search.get_entry("co-3")
    assert entry is not None and entry["id"] == "co-3", entry
    assert entry["video_url"] == "ghorfe-03.mp4", entry


def test_get_entry_returns_none_for_a_record_that_no_longer_exists(client):
    """An admin can delete a company between the turn that offered it and the
    turn that picks it. None is the honest answer; the caller then falls
    through to normal retrieval instead of serving a stale dict."""
    _seed()
    from app.services import search
    assert search.get_entry("co-does-not-exist") is None
    assert search.get_entry("") is None


def test_get_entry_sees_a_record_added_after_the_process_started(client):
    """It refreshes first, for the same multi-worker reason find_top_matches
    does: a visitor picking from a list must not be told the record vanished
    because THIS worker's index is a minute old."""
    _seed()
    from app.services import search
    assert search.get_entry("co-late") is None

    import app.db.connection as dbc
    conn = dbc.get_db_connection()
    conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                 " VALUES ('co-late', 'شرکت دیرآمده', 'توضیح شرکت.', 'x.mp4')")
    conn.commit()
    conn.close()
    search.load_dataset_internal()

    entry = search.get_entry("co-late")
    assert entry is not None and entry["title"] == "شرکت دیرآمده", entry
