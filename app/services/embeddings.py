"""Local semantic retriever — multilingual sentence embeddings, no external API.

Runs entirely on the host: a static multilingual embedding model (model2vec,
default ``minishlab/potion-multilingual-128M``) embeds the knowledge base once
per reindex and every visitor query at request time — pure NumPy inference,
no GPU, no heavyweight runtime, millisecond latency. The model is cached under
``data/models`` after the first download, so an exhibition machine needs
network only once, when the backend is first enabled.

Scores are calibrated before they reach the pipeline: raw cosine values from
embedding models occupy a narrower band than TF-IDF scores, so ``_calibrate``
maps the useful band onto 0..1 and the existing SIMILARITY_THRESHOLD
semantics keep working unchanged.
"""
import threading
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from app.config import BASE_DIR, logger

DEFAULT_MODEL = "minishlab/potion-multilingual-128M"
CACHE_DIR = str(Path(BASE_DIR) / "data" / "models")

# Calibration band, measured on the live INOTEX corpus (2026-08-14, golden
# set): a genuine colloquial paraphrase scores cos ≈ 0.72, ambiguous
# wrong-entry matches cluster at 0.65–0.67, and out-of-domain queries top out
# near 0.49. The floor/span are set so the genuine match calibrates above
# TRUSTED_MATCH_THRESHOLD (0.70) while the ambiguous cluster lands below it
# and defers to the later tiers — a confident wrong answer costs more than a
# deferral.
#
# Re-measured 2026-08-26: the band above was tuned on the expansion-bloated
# queries; with the dedup'd expansion several true matches calibrate to 0.000
# («کافه سرمایه چیست؟»), i.e. they sit below the 0.45 floor. The values stay
# as shipped defaults until the Q6 sweep locks a new band, so they are
# env-overridable (EMBEDDING_COSINE_FLOOR/SPAN) for experiments without
# touching the product's config file. Calibration is query-side: the stored
# matrix is raw cosines and _calibrate runs on every search, so changing these
# needs no reindex.
import os as _os
COSINE_FLOOR = float(_os.getenv("EMBEDDING_COSINE_FLOOR", "0.45"))
COSINE_SPAN = float(_os.getenv("EMBEDDING_COSINE_SPAN", "0.35"))

_model = None
_model_name = None
_model_lock = threading.Lock()


def available() -> bool:
    try:
        import model2vec  # noqa: F401
        return True
    except ImportError:
        return False


def _get_model(name: str):
    global _model, _model_name
    with _model_lock:
        if _model is None or _model_name != name:
            import os
            # Pin the download cache inside the project so an exhibition
            # machine carries its model with the installation.
            os.environ.setdefault("HF_HOME", CACHE_DIR)
            from model2vec import StaticModel
            logger.info(f"[embeddings] loading model {name} (cache: {CACHE_DIR})")
            _model = StaticModel.from_pretrained(name)
            _model_name = name
        return _model


def _calibrate(cosine: float) -> float:
    return float(min(1.0, max(0.0, (cosine - COSINE_FLOOR) / COSINE_SPAN)))


class EmbeddingIndex:
    """Immutable embedding index over a list of texts.

    Built whole and swapped atomically by the caller, mirroring how
    ``search.load_dataset_internal`` publishes its TF-IDF state — a concurrent
    request never sees a half-built index.
    """

    def __init__(self, texts: List[str], model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        model = _get_model(model_name)
        vectors = np.asarray(model.encode(texts), dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.matrix = vectors / norms
        logger.info(f"[embeddings] indexed {len(texts)} texts, dim={self.matrix.shape[1]}")

    def search(self, query: str) -> Tuple[int, float]:
        """Return (best_row_index, calibrated_score in 0..1)."""
        top = self.search_topk(query, k=1)
        return top[0] if top else (-1, 0.0)

    def search_topk(self, query: str, k: int = 5) -> List[Tuple[int, float]]:
        """Top-k (row_index, calibrated_score), best first.

        Candidates for the reranking stage — the first-stage retriever's job
        is recall, so it hands several plausible rows to the reranker rather
        than committing to argmax on its own.
        """
        model = _get_model(self.model_name)
        vec = np.asarray(model.encode([query]), dtype=np.float32)[0]
        norm = np.linalg.norm(vec)
        if norm == 0:
            return []
        sims = self.matrix @ (vec / norm)
        k = min(k, sims.shape[0])
        # argpartition then sort only the k survivors — O(n) instead of a full
        # sort per query, which matters once the corpus grows past a booth-sized
        # knowledge base.
        idxs = np.argpartition(-sims, k - 1)[:k]
        idxs = idxs[np.argsort(-sims[idxs])]
        return [(int(i), _calibrate(float(sims[i]))) for i in idxs]


def build_index(texts: List[str], model_name: str = DEFAULT_MODEL) -> Optional[EmbeddingIndex]:
    """Build an index, returning None (with a log line) on any failure so the
    caller falls back to TF-IDF instead of taking the chatbot down."""
    if not texts:
        return None
    try:
        return EmbeddingIndex(texts, model_name)
    except Exception as e:
        logger.error(f"[embeddings] index build failed, falling back to TF-IDF: {e}")
        return None
