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


def _dual_hits(retrieve, original: str, expanded: str, k: int):
    """One retriever's verdict on BOTH query forms, unioned by document.

    `retrieve(query, k) -> [(index, score)]`. Expansion widens recall — a
    colloquial word reaches formally-worded entries — but the expanded query
    can drift off the embedding model's region (dense=0.000 on
    «هزینه غرفه…» pre-fix) while the original keeps the exact typed
    vocabulary decisive. A document's score is its MAX over the two forms:
    two mediocre agreements must not outvote one strong match. The forms are
    usually near-identical after normalization, and `expanded == original`
    skips the second call entirely.
    """
    first = retrieve(original, k) if original else []
    if not expanded or expanded == original:
        return first
    second = retrieve(expanded, k)
    if not second:
        return first
    by_idx = dict(first)
    for idx, score in second:
        if score > by_idx.get(idx, -1.0):
            by_idx[idx] = score
    return sorted(by_idx.items(), key=lambda p: -p[1])[:k]

# Trained intent classifier (this installation's own model, retrained on
# every reindex from the question corpus). None when the semantic backend
# is off or training data is insufficient.
intent_classifier = None

# Every token the knowledge base KNOWNS, and the same set grouped by length
# for the edit-distance-1 typo probe. Built in load_dataset_internal from
# the normalized titles, descriptions, English fields and curated questions,
# plus the synonym table's own rows: a colloquial word that maps through
# synonyms is known language even when no document uses it verbatim.
_corpus_vocab: set = set()
_vocab_by_len: dict = {}

# Distinctive title tokens: token -> dataset index, for tokens that appear in
# exactly ONE entry's title across the whole dataset. This is how a query that
# NAMES a known entity gets anchored to that entity's own entry, no matter
# what the similarity scores say. The 2026-08-26 الکامپ guard above covers
# entities the corpus does NOT know; this covers confusion BETWEEN known
# entries (measured 2026-08-27: «شماره مدیرعامل دوندگان لبه علم» was served
# the دبیرخانه phone FAQ at 0.87, and «درباره دکیو بهم بگو» fell through to
# the paid AI tier at 0.691 — both entities exist in the dataset).
_distinctive_title_tokens: dict = {}
# id -> dataset index, so entry_mentions/entity_coverage can reuse the
# already-normalized descriptions instead of re-normalizing per call.
_dataset_index_by_id: dict = {}


def _within_edit1(a: str, b: str) -> bool:
    """True when a and b differ by at most one substitution or one
    insertion/deletion — the tolerance that lets ordinary Persian typos and
    colloquial spellings (برگذاری/برگزاری، استارتاپا/استارتاپ) keep serving
    while a genuinely foreign word stays unknown."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(x != y for x, y in zip(a, b)) == 1
    if la > lb:
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def unknown_salient_tokens(query: str) -> list:
    """Content tokens of the ORIGINAL query that the WHOLE corpus does not
    know — the strongest "asked about something we have nothing on" signal.

    Why this must exist (measured 2026-08-26, live): «تاریخ برگزاری نمایشگاه
    الکامپ» was served the INOTEX date with 0.844 confidence. الکامپ appears
    in no document, no curated question and no synonym, and the lexical
    retrievers simply DROP unknown terms — so the query degraded to its
    common words («تاریخ برگزاری نمایشگاه») and matched strongly. Coverage
    could not save it: it is one token among four and only one signal of
    three on one path, absent entirely from the questions blend.

    A token is KNOWN when it appears verbatim somewhere in the corpus, or is
    within edit distance 1 of a vocabulary token (typo/colloquial tolerance),
    or would be replaced by a synonym. Salience: at least 4 characters, and
    for ASCII (English) tokens at least 5 — measured on the live corpus,
    4-letter English words are overwhelmingly function words the tiny English
    fields never contain («book», «tell»), while a 4-letter Persian word is
    already content («دلار», «جدید», «دکیو»).
    """
    if not _corpus_vocab:
        return []
    from app.services.rerank import content_tokens
    out = []
    for tok in content_tokens(normalize_persian(query, expand_synonyms=False)):
        if len(tok) < 4 or (tok.isascii() and len(tok) < 5) or tok in _corpus_vocab:
            continue
        same_len = _vocab_by_len.get(len(tok), set())
        near_len = (_vocab_by_len.get(len(tok) - 1, set())
                    | _vocab_by_len.get(len(tok) + 1, set()))
        if not any(_within_edit1(tok, v) for v in same_len | near_len):
            out.append(tok)
    return out


def resolve_named_entity(query: str):
    """The single dataset entry the query NAMES, or (None, set()).

    Tokenizes the UNexpanded normalized query (same approach as
    unknown_salient_tokens: the anchor must see what the visitor actually
    typed, not what synonym expansion added) and looks each content token up
    in the distinctive-title-token map. Exactly ONE entry hit -> that entry
    plus the tokens that named it. Zero or more than one -> (None, set()):
    ambiguity must never guess.

    Why this exists (measured 2026-08-27): the الکامپ guard only protects
    against entities the corpus does NOT know. When a query names a KNOWN
    entity but retrieval anchors on the query's other tokens, nothing stopped
    a confident wrong answer — «شماره مدیرعامل دوندگان لبه علم» matched the
    دبیرخانه phone FAQ at 0.87 through «شماره/تلفن», wrong entity. The
    principle: no similarity score may override the entity the visitor
    actually named.
    """
    if not _distinctive_title_tokens:
        return None, set()
    from app.services.rerank import content_tokens
    hits = {}
    for tok in content_tokens(normalize_persian(query, expand_synonyms=False)):
        idx = _distinctive_title_tokens.get(tok)
        if idx is not None and 0 <= idx < len(dataset):
            hits.setdefault(idx, set()).add(tok)
    if len(hits) != 1:
        return None, set()
    idx, matched = next(iter(hits.items()))
    return dataset[idx], matched


def _entry_normalized_text(entry: dict) -> str:
    """Normalized title+text of an entry, via the prebuilt index when the
    entry is resolvable by id, else normalized on the fly."""
    idx = _dataset_index_by_id.get(entry.get("id", ""), -1)
    if 0 <= idx < len(normalized_descriptions):
        return normalized_descriptions[idx]
    return normalize_persian(f"{entry.get('title', '')} {entry.get('text', '')}")


def entry_mentions(entry: dict, tokens: set) -> bool:
    """True when the entry's normalized title+text contains ANY of the given
    tokens — i.e. the candidate at least talks about the named entity, so
    serving it is not an entity mix-up."""
    if not entry or not tokens:
        return False
    return bool(set(_entry_normalized_text(entry).split()) & tokens)


def entity_coverage(entry: dict, query: str) -> float:
    """Share of the query's content tokens present in the entry (title+text).

    The honest signal a "the visitor named this entity" answer actually has:
    deterministic, 0..1, no similarity model involved.
    """
    from app.services.rerank import content_tokens
    q = content_tokens(normalize_persian(query, expand_synonyms=False))
    if not q:
        return 0.0
    return len(q & set(_entry_normalized_text(entry).split())) / len(q)


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
    global _corpus_vocab, _vocab_by_len
    global _distinctive_title_tokens, _dataset_index_by_id

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

    # --- Unknown-entity vocabulary ------------------------------------
    # Every token any retriever could legitimately match against: normalized
    # titles+descriptions (the titles are inside them), the curated
    # questions, the English fields, and the synonym table's own words. A
    # salient token outside this set names something the knowledge base has
    # NO information on — the strongest "do not answer locally" signal there
    # is (see unknown_salient_tokens for the incident this answers).
    from app.utils.normalizer import active_synonyms as _active_synonyms
    _vocab = set()
    for _text in _normalized_descriptions:
        _vocab.update(_text.split())
    for _text in _normalized_questions:
        _vocab.update(_text.split())
    for _item in _dataset:
        _vocab.update(normalize_persian(_item.get("title_en") or "").split())
        _vocab.update(normalize_persian(_item.get("text_en") or "").split())
    for _src, _dst in _active_synonyms:
        _vocab.update(_src.split())
        _vocab.update((_dst or "").split())
    _vocab.discard("")
    _vbl = {}
    for _tok in _vocab:
        _vbl.setdefault(len(_tok), set()).add(_tok)

    # --- Distinctive title tokens (named-entity anchor) ----------------
    # Built from UNexpanded text only (measured 2026-08-27, live, after the
    # anchor shipped: امسال/حوزه/شماره pollution). A token unique among
    # titles is not a NAME unless it is unique across the whole knowledge
    # base, and synonym expansion must never leak into the name map:
    #   * «امسال» sat in exactly one (question-style) title but in three
    #     entries' texts — it anchored the stage entry and overrode the
    #     correct date answer at 0.965.
    #   * «حوزه» was unique to the faq-08 title yet common in entry texts —
    #     it resolved faq-08 and gated OFF the company-list tier.
    #   * a synonym (تماس→شماره) injected «شماره» into the contact entry's
    #     EXPANDED title, so the query that caused the original incident
    #     hit two entries and the anchor switched itself off.
    # A token is distinctive for entry i only when it appears in exactly
    # one entry's unexpanded title (title df == 1) AND in no other entry's
    # unexpanded title+text (whole-base df == 1), and is long enough to
    # name something: >= 3 chars, >= 4 for pure-ASCII (short English
    # fragments are too often function words). content_tokens already drops
    # stopwords. `normalized_titles` / `normalized_descriptions` themselves
    # stay synonym-EXPANDED — retrieval and entry_mentions depend on that;
    # only this name map must see what the admin actually typed. See
    # resolve_named_entity for the original incident (entity confusion
    # between KNOWN entries, 2026-08-27).
    _plain_title_sets = [
        rerank.content_tokens(
            normalize_persian(item.get("title", "").strip(),
                              expand_synonyms=False))
        for item in _dataset]
    _plain_doc_sets = [
        set(normalize_persian(
            f"{item.get('title', '').strip()} {item.get('text', '').strip()}",
            expand_synonyms=False).split())
        for item in _dataset]
    _title_df = {}
    for _toks in _plain_title_sets:
        for _tok in _toks:
            _title_df[_tok] = _title_df.get(_tok, 0) + 1
    _doc_df = {}
    for _doc in _plain_doc_sets:
        for _tok in _doc:
            _doc_df[_tok] = _doc_df.get(_tok, 0) + 1
    _distinctive = {}
    for _i, _toks in enumerate(_plain_title_sets):
        for _tok in _toks:
            if (_title_df[_tok] == 1
                    and _doc_df.get(_tok, 0) == 1
                    and len(_tok) >= (4 if _tok.isascii() else 3)):
                _distinctive[_tok] = _i
    _index_by_id = {item.get("id", ""): _i for _i, item in enumerate(_dataset)
                    if item.get("id", "")}

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
    _corpus_vocab = _vocab
    _vocab_by_len = _vbl
    _distinctive_title_tokens = _distinctive
    _dataset_index_by_id = _index_by_id
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
            # Each retriever sees BOTH query forms — original (coverage_query)
            # and expanded — and the union of their hits goes to the reranker.
            # Coverage inside rerank stays on the original (see rerank.rerank).
            dense_hits = []
            if dataset_embedding_index is not None:
                dense_hits = _dual_hits(
                    dataset_embedding_index.search_topk,
                    coverage_query, normalized_query, RERANK_CANDIDATES)
            lexical_hits = []
            if dataset_bm25_index is not None:
                lexical_hits = _dual_hits(
                    dataset_bm25_index.top_k,
                    coverage_query, normalized_query, RERANK_CANDIDATES)

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


def find_top_matches(query: str, k: int = 8):
    """The same ranking as find_best_match, k results deep.

    Returns [(entry, score, signals)] best-first — the triple
    scripts/run_eval.py already consumes from full_ranking(), so the evaluation
    harness and the runtime read one shape.

    WHY A NEW FUNCTION AND NOT A PARAMETER ON find_best_match: RERANK_CANDIDATES
    tunes what the first-stage retrievers propose on today's path, and the
    measured recall@1 = 0.786 in the evidence pack depends on it. This takes
    its OWN k, so asking for thirteen candidates cannot move a number that is
    already published.

    THE HEAD MUST AGREE WITH find_best_match. The title-overlap branch above is
    a branch EXIT with a synthetic 0.95+ score, so when it fires that entry is
    PREPENDED here and removed from the reranked tail. Without that, the
    candidate list and Tier 1 would rank the same corpus differently and the
    eagerness margin would compare scores from a ranking nobody serves.
    """
    k = max(1, int(k))
    if not dataset or not normalized_titles:
        _maybe_refresh()
        return []

    _maybe_refresh()
    normalized_query = normalize_persian(query)
    # Same query, synonyms NOT expanded — the coverage signal must see what the
    # visitor actually typed. Dropping this argument while copying
    # find_best_match is a one-word mistake that reopens the «قیمت دلار»
    # hallucination hole, and nothing else in the system would notice.
    coverage_query = normalize_persian(query, expand_synonyms=False)

    query_tokens = set(normalized_query.split())
    best_title_score = 0.0
    best_title_idx = -1
    for idx, norm_title in enumerate(normalized_titles):
        title_tokens = set(norm_title.split())
        shared = len(query_tokens & title_tokens)
        if query_tokens and title_tokens and (shared >= 3 or shared == len(query_tokens) == len(title_tokens)):
            overlap = shared / len(query_tokens | title_tokens)
            if overlap >= 0.6 and overlap > best_title_score:
                best_title_score = overlap
                best_title_idx = idx

    head = []
    if best_title_score >= 0.6 and best_title_idx != -1:
        head = [(dataset[best_title_idx],
                 min(0.95 + (best_title_score - 0.6) * 0.5, 1.0),
                 {"title_overlap": round(best_title_score, 3)})]
        if len(head) >= k:
            return head[:k]

    ranked = []
    if RERANK_ENABLED and (dataset_embedding_index is not None or dataset_bm25_index is not None):
        try:
            dense_hits = []
            if dataset_embedding_index is not None:
                dense_hits = _dual_hits(
                    dataset_embedding_index.search_topk,
                    coverage_query, normalized_query, k)
            lexical_hits = []
            if dataset_bm25_index is not None:
                lexical_hits = _dual_hits(
                    dataset_bm25_index.top_k,
                    coverage_query, normalized_query, k)
            ranked = rerank.rerank(
                normalized_query, normalized_descriptions, dense_hits,
                lexical_hits, coverage_query=coverage_query)
        except Exception as e:  # noqa: BLE001 — same soft-fail contract as find_best_match
            logger.error(f"Hybrid retrieval failed for top-k, falling back to TF-IDF: {e}")
            ranked = []

    if not ranked and vectorizer is not None and tfidf_matrix is not None:
        try:
            query_vec = vectorizer.transform([normalized_query])
            sims = cosine_similarity(query_vec, tfidf_matrix).flatten()
            ranked = [(int(i), float(sims[i]), {"tfidf": round(float(sims[i]), 3)})
                      for i in np.argsort(-sims)[:k]]
        except Exception as e:  # noqa: BLE001
            logger.error(f"TF-IDF top-k failed: {e}")
            ranked = []

    taken = {entry.get("id") for entry, _s, _sig in head}
    out = list(head)
    for idx, score, signals in ranked:
        if not (0 <= idx < len(dataset)):
            continue
        entry = dataset[idx]
        if entry.get("id") in taken:
            continue
        taken.add(entry.get("id"))
        out.append((entry, float(score), dict(signals)))
        if len(out) >= k:
            break
    return out[:k]


def get_entry(entry_id: str):
    """One dataset record by id, or None.

    The pick tier stores IDS, not text, so this is the lookup that turns a
    stored offer back into an answer with its video_url intact. None is the
    honest answer for an id an admin deleted between the turn that offered it
    and the turn that picked it — the caller then falls through to normal
    retrieval instead of serving a stale dict.
    """
    if not entry_id:
        return None
    _maybe_refresh()
    return dataset_lookup.get(entry_id)


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
                dense_hits = _dual_hits(
                    questions_embedding_index.search_topk,
                    coverage_query, normalized_query, RERANK_CANDIDATES)
            lexical_hits = []
            if questions_bm25_index is not None:
                lexical_hits = _dual_hits(
                    questions_bm25_index.top_k,
                    coverage_query, normalized_query, RERANK_CANDIDATES)
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
