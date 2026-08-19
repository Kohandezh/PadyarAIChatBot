"""Semantic retriever unit tests.

The calibration contract and the fallback wiring are exercised without the
model; the live end-to-end check runs only when model2vec (and its cached
model) is present, so CI stays hermetic.
"""
import pytest

from app.services import embeddings


def test_calibration_maps_noise_to_zero_and_matches_high():
    # Band measured on the INOTEX corpus (see embeddings.py): noise ≤ 0.49
    # maps toward 0, a genuine paraphrase (~0.72) must clear the 0.70 trust
    # bar, and saturation reaches 1.0 well before a perfect cosine.
    assert embeddings._calibrate(0.30) == 0.0
    assert embeddings._calibrate(embeddings.COSINE_FLOOR) == 0.0
    assert embeddings._calibrate(0.72) > 0.70
    assert embeddings._calibrate(0.95) == 1.0


def test_calibration_is_monotonic():
    values = [embeddings._calibrate(c / 100) for c in range(0, 101, 5)]
    assert values == sorted(values)


def test_build_index_empty_returns_none():
    assert embeddings.build_index([]) is None


def test_search_falls_back_to_tfidf_when_backend_unavailable(monkeypatch):
    """With the embedding backend selected but the library missing, loading
    must still produce a working TF-IDF index instead of failing."""
    import app.services.search as search

    monkeypatch.setattr(embeddings, "available", lambda: False)
    monkeypatch.setattr(
        "app.db.queries.get_setting",
        lambda key, default=None: "embedding" if key == "search_backend" else default,
    )
    search.load_dataset_internal()
    assert search.dataset_embedding_index is None
    if search.dataset:
        entry, score = search.find_best_match(search.dataset[0]["title"])
        assert entry is not None


@pytest.mark.skipif(not embeddings.available(), reason="model2vec not installed")
def test_live_paraphrase_separation():
    """The retriever must score a colloquial paraphrase above threshold and an
    out-of-domain question at zero. Skipped unless the model is installed."""
    index = embeddings.build_index([
        "کجا ماشین پارک کنم",
        "ساعت بازدید نمایشگاه چند است",
    ])
    if index is None:
        pytest.skip("model unavailable (offline and no cache)")
    idx, score = index.search("کجا ماشینمو بذارم؟")
    assert idx == 0
    assert score > 0.2
    _, noise = index.search("آب و هوای فردا چطوره؟")
    assert noise < 0.2
