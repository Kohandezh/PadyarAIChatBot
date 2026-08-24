import asyncio
import os
import threading
import time
from typing import List, Optional, Dict

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.config import logger, SIMILARITY_THRESHOLD, RERANK_ENABLED
from app.db.connection import get_db_connection
from app.services import bm25, embeddings, rerank
from app.utils.normalizer import normalize_persian, load_synonyms_from_db


# --- Global State ---
dataset: List[dict] = []
descriptions: List[str] = []
vectorizer: Optional[TfidfVectorizer] = None
tfidf_matrix = None

normalized_titles: List[str] = []
normalized_descriptions: List[str] = []

# Questions state
questions_data: List[dict] = []
normalized_questions: List[str] = []
dataset_lookup: Dict[str, dict] = {}

questions_vectorizer: Optional[TfidfVectorizer] = None
questions_tfidf_matrix = None

# Semantic backend (admin setting `search_backend`: "tfidf" | "embedding").
# Indexes rebuild on every reindex — dataset edits in the panel refresh them.
dataset_embedding_index = None
questions_embedding_index = None

# Lexical BM25 indexes. Always built (they are pure Python and cost
# milliseconds), so the reranker has a lexical signal even when the semantic
# backend is off or its model is unavailable.
dataset_bm25_index = None
questions_bm25_index = None

# How many candidates each first-stage retriever hands to the reranker.
RERANK_CANDIDATES = 5

# Trained intent classifier (this installation's own model, retrained on
# every reindex from the question corpus). None when the semantic backend
# is off or training data is insufficient.
intent_classifier = None


# --- Cross-worker index freshness ---
# Every retrieval index is process-local, but the app runs several uvicorn
# workers. Before this existed, a dataset edit reindexed only the worker that
# handled it; the others kept serving the old answers until a restart. At a
# live event, where staff correct content while visitors are asking, that is
# a correctness bug, not a performance one.
#
# Writers stamp a monotonically increasing version into `settings`; readers
# poll that version at most once per INDEX_REFRESH_SECONDS (one fresh settings
# read) and rebuild in the background when they are behind.
INDEX_VERSION_KEY = "search_index_version"
INDEX_REFRESH_SECONDS = max(1.0, float(os.getenv("SEARCH_INDEX_REFRESH_SECONDS", "5")))
_index_version = 0
_last_version_check = 0.0
_rebuild_lock = threading.Lock()


def _read_index_version() -> int:
    from app.db.queries import get_setting
    try:
        return int(get_setting(INDEX_VERSION_KEY, "0", fresh=True) or 0)
    except (TypeError, ValueError):
        return 0


def init_index_version() -> None:
    """Adopt the stored version at boot. First boot ever publishes 1, so the
    key exists for later comparisons."""
    global _index_version
    current = _read_index_version()
    if current <= 0:
        from app.db.queries import set_setting
        current = 1
        set_setting(INDEX_VERSION_KEY, str(current))
    _index_version = current


def bump_index_version() -> None:
    """Publish a new version without rebuilding — for writers that have
    already refreshed their own in-process state (e.g. synonym edits)."""
    global _index_version
    _index_version = max(time.time_ns(), _index_version + 1)
    from app.db.queries import set_setting
    set_setting(INDEX_VERSION_KEY, str(_index_version))


def _rebuild(publish: bool, version_floor: int = 0) -> None:
    """Single rebuild path. Never blocks: a rebuild already in progress wins
    and the caller simply returns."""
    global _index_version
    if not _rebuild_lock.acquire(blocking=False):
        return
    try:
        try:
            started = time.monotonic()
            load_dataset_internal()
            if publish:
                _index_version = max(time.time_ns(), _index_version + 1)
                from app.db.queries import set_setting
                set_setting(INDEX_VERSION_KEY, str(_index_version))
            else:
                _index_version = max(version_floor, _index_version)
            report_reindex(len(dataset), len(questions_data),
                           int((time.monotonic() - started) * 1000))
        except Exception:  # noqa: BLE001 — a failed rebuild must retry on the next poll
            logger.exception("[search] index rebuild failed")
            _index_version = 0
    finally:
        _rebuild_lock.release()


def reindex_and_publish() -> None:
    """Rebuild after THIS worker changed content, and stamp a new version so
    every other worker picks the change up within INDEX_REFRESH_SECONDS."""
    _rebuild(publish=True)


def _maybe_refresh() -> None:
    """Version poll on the query path. At most one fresh settings read per
    INDEX_REFRESH_SECONDS; a newer version triggers a background rebuild so
    the in-flight query still answers from the current (old) index."""
    global _last_version_check, _index_version
    now = time.monotonic()
    if now - _last_version_check < INDEX_REFRESH_SECONDS:
        return
    _last_version_check = now
    try:
        latest = _read_index_version()
    except Exception:  # noqa: BLE001 — freshness polling must never 500 a query
        return
    if latest > _index_version:
        _index_version = latest
        try:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, _rebuild, False, latest)
        except RuntimeError:
            threading.Thread(target=_rebuild, args=(False, latest), daemon=True).start()


def load_dataset_internal():
    """Load dataset and questions from DB, build TF-IDF index.

    Uses local variables during loading to avoid a race condition where
    the global state is cleared (e.g. ``questions_data = []``) while a
    concurrent API request reads the half-loaded state.
    Globals are only reassigned once everything is ready.
    """
    global dataset, descriptions, vectorizer, tfidf_matrix
    global normalized_titles, normalized_descriptions
    global questions_data, normalized_questions, dataset_lookup
    global questions_vectorizer, questions_tfidf_matrix
    global dataset_embedding_index, questions_embedding_index
    global dataset_bm25_index, questions_bm25_index
    global intent_classifier

    load_synonyms_from_db()

    # --- Build everything into local variables first ---
    _dataset = []
    _descriptions = []
    _normalized_titles = []
    _normalized_descriptions = []
    _dataset_lookup = {}
    _vectorizer = None
    _tfidf_matrix = None

    _questions_data = []
    _normalized_questions = []
    _questions_vectorizer = None
    _questions_tfidf_matrix = None

    try:
        conn = get_db_connection()
        rows = conn.execute('SELECT id, title, text, video_url, title_en, text_en FROM dataset ORDER BY id').fetchall()
        conn.close()
        _dataset = [dict(r) for r in rows]

        if _dataset:
            # ساخت lookup dictionary برای دسترسی سریع
            for item in _dataset:
                item_id = item.get("id", "")
                if item_id:
                    _dataset_lookup[item_id] = item

            # نرمالایز کردن عناوین و توصیفات هنگام بارگذاری
            for item in _dataset:
                title = item.get("title", "").strip()
                text = item.get("text", "").strip()
                _normalized_titles.append(normalize_persian(title))
                _normalized_descriptions.append(normalize_persian(f"{title} {text}"))

            # آموزش وکتورایزر روی متن نرمالایز شده
            _vectorizer = TfidfVectorizer(
                ngram_range=(1, 3),
                sublinear_tf=True,
                token_pattern=r'(?u)\b\w+\b'  # بهتر برای فارسی
            )
            if _normalized_descriptions:
                _tfidf_matrix = _vectorizer.fit_transform(_normalized_descriptions)
                logger.info(f"Vectorized {len(_normalized_descriptions)} documents (normalized).")
            else:
                logger.warning("Dataset is empty or has no text fields.")
        else:
            logger.warning("Dataset table is empty")
    except Exception as e:
        logger.error(f"Error loading dataset: {e}")

    # بارگذاری questions از دیتابیس
    try:
        conn = get_db_connection()
        rows = conn.execute('SELECT id, question, dataset_id, video_url FROM questions ORDER BY id').fetchall()
        conn.close()
        _questions_data = [dict(r) for r in rows]
        for q in _questions_data:
            _normalized_questions.append(normalize_persian(q.get("question", "")))
        logger.info(f"Loaded {len(_questions_data)} questions from database")

        _questions_vectorizer = TfidfVectorizer(
            ngram_range=(1, 3),
            sublinear_tf=True,
            token_pattern=r'(?u)\b\w+\b'
        )
        if _normalized_questions:
            _questions_tfidf_matrix = _questions_vectorizer.fit_transform(_normalized_questions)
        else:
            _questions_tfidf_matrix = None
    except Exception as e:
        logger.error(f"Error loading questions: {e}")

    # Semantic backend: build embedding indexes only when enabled, and never
    # let a failure here take the retriever down — TF-IDF stays the safety net.
    _dataset_emb = None
    _questions_emb = None
    _intent = None
    try:
        from app.db.queries import get_setting
        if get_setting('search_backend', 'embedding') == 'embedding':
            if embeddings.available():
                model = get_setting('ai_embedding_model', '') or embeddings.DEFAULT_MODEL
                _dataset_emb = embeddings.build_index(_normalized_descriptions, model)
                _questions_emb = embeddings.build_index(_normalized_questions, model)
                if _questions_emb is not None:
                    from app.services import intent
                    _intent = intent.train(
                        _questions_emb.matrix,
                        [q.get('dataset_id', '') for q in _questions_data],
                        model,
                    )
            else:
                logger.warning("search_backend=embedding but model2vec is not installed; using TF-IDF")
    except Exception as e:
        logger.error(f"Embedding backend init failed, using TF-IDF: {e}")

    # --- Atomically publish to module-level globals ---
    dataset = _dataset
    descriptions = _descriptions
    normalized_titles = _normalized_titles
    normalized_descriptions = _normalized_descriptions
    dataset_lookup = _dataset_lookup
    vectorizer = _vectorizer
    tfidf_matrix = _tfidf_matrix

    questions_data = _questions_data
    normalized_questions = _normalized_questions
    questions_vectorizer = _questions_vectorizer
    questions_tfidf_matrix = _questions_tfidf_matrix
    dataset_embedding_index = _dataset_emb
    questions_embedding_index = _questions_emb
    dataset_bm25_index = bm25.build_index(_normalized_descriptions)
    questions_bm25_index = bm25.build_index(_normalized_questions)
    intent_classifier = _intent


def classify_intent_local(query: str):
    """The trained classifier's verdict: (dataset entry, probability).

    Returns (None, 0.0) when no classifier is live or the winning intent no
    longer exists in the dataset — callers fall through to the next tier.
    """
    if intent_classifier is None:
        return None, 0.0
    _maybe_refresh()
    try:
        dataset_id, prob = intent_classifier.classify(normalize_persian(query))
        # Trust scales with measured quality: when the holdout accuracy of the
        # deployed classifier is weak (small corpus), only near-certain
        # predictions may answer — a confident wrong answer at a booth costs
        # more than a deferral to the next tier.
        acc = intent_classifier.holdout_accuracy
        if acc is not None and acc < 0.7 and prob < 0.85:
            return None, 0.0
        entry = dataset_lookup.get(dataset_id) if dataset_id else None
        if entry:
            return entry, prob
    except Exception as e:
        logger.error(f"Local intent classification failed: {e}")
    return None, 0.0


def find_best_match(query: str):
    if not dataset or not normalized_titles:
        _maybe_refresh()
        return None, 0.0

    _maybe_refresh()
    normalized_query = normalize_persian(query)
    # Same query, synonyms NOT expanded — the coverage signal must see
    # what the visitor actually typed. See rerank.rerank(coverage_query).
    coverage_query = normalize_persian(query, expand_synonyms=False)
    logger.debug(f"Normalized query: '{normalized_query}'")

    query_tokens = set(normalized_query.split())
    best_title_score = 0.0
    best_title_idx = -1

    for idx, norm_title in enumerate(normalized_titles):
        title_tokens = set(norm_title.split())

        # Require ≥3 shared tokens so a short query can't fake a near-perfect
        # (0.95+) match against a short title via PARTIAL overlap — that inflation
        # made trivial greetings/keywords look like high-confidence answers.
        # Exception: a full token-set overlap (query set == title set) is a genuine
        # exact match, allowed regardless of length (e.g. "هزینه غرفه" == title).
        shared = len(query_tokens & title_tokens)
        if query_tokens and title_tokens and (shared >= 3 or shared == len(query_tokens) == len(title_tokens)):
            overlap = shared / len(query_tokens | title_tokens)
            if overlap >= 0.6 and overlap > best_title_score:
                best_title_score = overlap
                best_title_idx = idx

    # اگر همپوشانی بالای ۶۰% وجود داشت → بازگشت مستقیم
    if best_title_score >= 0.6 and best_title_idx != -1:
        logger.info(f"High token overlap match ({best_title_score:.2f}): {normalized_titles[best_title_idx]}")
        return dataset[best_title_idx], min(0.95 + (best_title_score - 0.6) * 0.5, 1.0)

    # Hybrid retrieval + reranking: the dense and lexical retrievers each
    # propose candidates, the reranker rescores their union on the same 0..1
    # scale the thresholds already use. Any failure degrades to TF-IDF below
    # rather than taking the chatbot down.
    if RERANK_ENABLED and (dataset_embedding_index is not None or dataset_bm25_index is not None):
        try:
            dense_hits = []
            if dataset_embedding_index is not None:
                dense_hits = dataset_embedding_index.search_topk(normalized_query, RERANK_CANDIDATES)
            lexical_hits = []
            if dataset_bm25_index is not None:
                lexical_hits = dataset_bm25_index.top_k(normalized_query, RERANK_CANDIDATES)

            best_idx, score, signals = rerank.best(
                normalized_query, normalized_descriptions, dense_hits, lexical_hits, coverage_query=coverage_query)
            if best_idx >= 0:
                logger.debug(f"Hybrid rerank → item {best_idx} score={score:.3f} {signals}")
                return dataset[best_idx], score
        except Exception as e:
            logger.error(f"Hybrid retrieval failed, falling back to TF-IDF: {e}")
    elif dataset_embedding_index is not None:
        # Reranking disabled — plain dense argmax (the pre-hybrid behavior).
        try:
            best_idx, score = dataset_embedding_index.search(normalized_query)
            if best_idx >= 0:
                return dataset[best_idx], score
        except Exception as e:
            logger.error(f"Embedding search failed, falling back to TF-IDF: {e}")

    if not vectorizer or tfidf_matrix is None:
        return None, 0.0

    try:
        # تبدیل سوال نرمالایز شده به وکتور
        query_vec = vectorizer.transform([normalized_query])
        cosine_similarities = cosine_similarity(query_vec, tfidf_matrix).flatten()
        best_idx = int(np.argmax(cosine_similarities))
        best_score = float(cosine_similarities[best_idx])

        logger.debug(f"TF-IDF best match score: {best_score:.3f} for item {best_idx}")
        return dataset[best_idx], best_score
    except Exception as e:
        logger.error(f"Error in similarity calculation: {e}")
        return None, 0.0


def find_similar_question(query: str, exact_only: bool = False):
    """جستجوی سوال مشابه در questions و بازگشت جواب dataset

    ``exact_only=True`` restricts scoring to the token-Jaccard overlap with
    the hand-curated questions — the near-exact signal Tier 0 of the chat
    pipeline trusts. The default blends in the semantic/TF-IDF ranking for
    the later fallback tiers.
    """
    if not questions_data or not normalized_questions:
        _maybe_refresh()
        return None, 0.0

    _maybe_refresh()
    normalized_query = normalize_persian(query)
    # Same query, synonyms NOT expanded — the coverage signal must see
    # what the visitor actually typed. See rerank.rerank(coverage_query).
    coverage_query = normalize_persian(query, expand_synonyms=False)
    query_tokens = set(normalized_query.split())

    if not query_tokens:
        return None, 0.0

    best_score = 0.0
    best_idx = -1

    for idx, norm_q in enumerate(normalized_questions):
        q_tokens = set(norm_q.split())
        if not q_tokens:
            continue
        union = len(query_tokens | q_tokens)
        overlap = len(query_tokens & q_tokens) / union if union > 0 else 0.0
        if overlap > best_score:
            best_score = overlap
            best_idx = idx

    # Check the ranking backend (hybrid rerank when available, else TF-IDF)
    tfidf_score = 0.0
    tfidf_idx = -1
    if exact_only:
        pass
    elif RERANK_ENABLED and (questions_embedding_index is not None or questions_bm25_index is not None):
        try:
            dense_hits = []
            if questions_embedding_index is not None:
                dense_hits = questions_embedding_index.search_topk(normalized_query, RERANK_CANDIDATES)
            lexical_hits = []
            if questions_bm25_index is not None:
                lexical_hits = questions_bm25_index.top_k(normalized_query, RERANK_CANDIDATES)
            tfidf_idx, tfidf_score, _ = rerank.best(
                normalized_query, normalized_questions, dense_hits, lexical_hits, coverage_query=coverage_query)
        except Exception as e:
            logger.error(f"Questions hybrid retrieval failed: {e}")
            tfidf_idx, tfidf_score = -1, 0.0
    elif questions_vectorizer and questions_tfidf_matrix is not None:
        try:
            query_vec = questions_vectorizer.transform([normalized_query])
            cosine_sims = cosine_similarity(query_vec, questions_tfidf_matrix).flatten()
            tfidf_idx = int(np.argmax(cosine_sims))
            tfidf_score = float(cosine_sims[tfidf_idx])
        except Exception as e:
            logger.error(f"Error in questions TF-IDF: {e}")

    final_score = max(best_score, tfidf_score)
    final_idx = best_idx if best_score >= tfidf_score else tfidf_idx

    # threshold پایین‌تر چون سوالات کوتاه‌تر و دقیق‌تر هستند
    if final_score >= 0.5 and final_idx != -1:
        question_entry = questions_data[final_idx]
        dataset_id = question_entry.get("dataset_id", "")
        dataset_entry = dataset_lookup.get(dataset_id)
        if dataset_entry:
            logger.info(f"Question match (Jaccard: {best_score:.2f}, TF-IDF: {tfidf_score:.2f}): {question_entry.get('question')} → {dataset_id}")
            return dataset_entry, final_score

    return None, 0.0


def report_reindex(document_count: int, question_count: int, duration_ms: int = 0) -> None:
    """Record a reindex outcome.

    Called by the reindex paths rather than logging inside the hot query path:
    a row per search would be a log storm at exhibition traffic, and the useful
    operational signal is "the index was rebuilt and how big it is".
    """
    from app.services import applog
    applog.info("retrieval", "retrieval.reindexed", "نمایهٔ جستجو بازسازی شد",
                duration_ms=duration_ms or None,
                metadata={"documents": document_count, "questions": question_count})


def report_empty_retrieval(query: str, score: float) -> None:
    """An empty/low-confidence retrieval is the signal that the knowledge base
    has a gap. Logged at info because it is expected, not an error."""
    from app.services import applog
    applog.info("retrieval", "retrieval.empty", "بازیابی نتیجهٔ مطمئنی نداشت",
                outcome="empty",
                metadata={"score": round(float(score or 0), 3),
                          "query": applog.apply_content_policy(query)})
