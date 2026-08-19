"""Okapi BM25 lexical retriever — pure Python, no new dependency.

Why BM25 alongside the existing TF-IDF vectorizer: TF-IDF cosine normalizes
document length away entirely, which on this corpus lets a long answer body
outrank a short, exactly-on-topic entry. BM25's saturation (k1) and length
normalization (b) are what make a rare Persian term like «غرفه» decisive
without a long document drowning it.

The corpus is small (tens of documents, hundreds of questions), so a plain
dict-based index is both simpler and faster than a sparse matrix here, and it
adds no runtime infrastructure: it is built in-process on every reindex,
exactly like the TF-IDF and embedding indexes.
"""
import math
from collections import Counter
from typing import Dict, List, Tuple

K1 = 1.5   # term-frequency saturation
B = 0.75   # length normalization strength


class BM25Index:
    """Immutable BM25 index over pre-normalized texts.

    Built whole and published atomically by the caller, mirroring how
    ``search.load_dataset_internal`` swaps its other indexes — a concurrent
    request never observes a half-built index.
    """

    def __init__(self, texts: List[str]):
        self.docs: List[Counter] = [Counter(t.split()) for t in texts]
        self.lengths: List[int] = [sum(d.values()) for d in self.docs]
        self.avg_len: float = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0

        # Document frequency per term, then the standard BM25+ IDF form, which
        # stays positive for terms appearing in more than half the corpus
        # (the classic formula goes negative there — on a 15-document corpus
        # that would actively penalize common domain words like «اینوتکس»).
        df: Dict[str, int] = {}
        for doc in self.docs:
            for term in doc:
                df[term] = df.get(term, 0) + 1
        n = len(self.docs)
        self.idf: Dict[str, float] = {
            term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()
        }

    def scores(self, query: str) -> List[float]:
        """Raw BM25 score per document (unbounded, higher is better)."""
        terms = query.split()
        if not terms or not self.docs:
            return [0.0] * len(self.docs)

        out: List[float] = []
        for doc, length in zip(self.docs, self.lengths):
            score = 0.0
            for term in terms:
                freq = doc.get(term)
                if not freq:
                    continue
                idf = self.idf.get(term, 0.0)
                denom = freq + K1 * (1 - B + B * (length / self.avg_len if self.avg_len else 1.0))
                score += idf * (freq * (K1 + 1)) / denom
            out.append(score)
        return out

    def top_k(self, query: str, k: int = 5) -> List[Tuple[int, float]]:
        """Top-k (index, normalized 0..1 score), best first.

        Normalization is relative to the best hit for THIS query: BM25 scores
        are unbounded and query-dependent, so an absolute threshold on them is
        meaningless. The reranker consumes the relative shape; absolute
        confidence still comes from the calibrated dense score.
        """
        raw = self.scores(query)
        if not raw:
            return []
        best = max(raw)
        if best <= 0:
            return []
        ranked = sorted(enumerate(raw), key=lambda p: p[1], reverse=True)[:k]
        return [(idx, score / best) for idx, score in ranked if score > 0]


def build_index(texts: List[str]):
    """Build an index, returning None on failure so callers degrade to the
    other retrievers instead of taking the chatbot down."""
    from app.config import logger
    if not texts:
        return None
    try:
        return BM25Index(texts)
    except Exception as e:  # noqa: BLE001 — retrieval must never be fatal
        logger.error(f"[bm25] index build failed, continuing without it: {e}")
        return None
