import asyncio
import os
import threading
import time
from typing import List, Dict

from app.config import logger, RERANK_ENABLED
from app.db.connection import get_db_connection
from app.services import bm25, embeddings, rerank
from app.utils.normalizer import normalize_persian, load_synonyms_from_db


# --- Global State ---
dataset: List[dict] = []
descriptions: List[str] = []

normalized_titles: List[str] = []
normalized_descriptions: List[str] = []

# Questions state
questions_data: List[dict] = []
normalized_questions: List[str] = []
dataset_lookup: Dict[str, dict] = {}

# id -> company row, built the same way as dataset_lookup. Companies left
# `dataset` in migrations/0013_companies.sql, but a curated question
# (find_similar_question) or a numbered pick (get_entry) can still resolve to
# a company id, and the named-entity anchor (resolve_named_entity) can still
# name one — see docs/features/companies-own-table/RESEARCH.md section 2.
# Every id-based reader that used to find a company through dataset_lookup
# now tries this as a fallback: `dataset_lookup.get(id) or
# companies_lookup.get(id)`.
companies_lookup: Dict[str, dict] = {}

# Semantic indexes (local model2vec embeddings, no external API). Rebuilt on
# every reindex, so dataset edits in the panel refresh them. None when
# model2vec is not installed on this host — retrieval then runs on BM25 alone.
#
# There is no backend CHOICE any more. TF-IDF used to sit here as a second,
# selectable engine behind the `search_backend` setting; it was removed
# 2026-08-28 because two engines meant two rankings to reason about and the
# operator had no way to tell which one was better for their content.
dataset_embedding_index = None
questions_embedding_index = None

# Lexical BM25 indexes. Always built (they are pure Python and cost
# milliseconds), so the reranker has a lexical signal even when the embedding
# model is unavailable.
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


def _bm25_only(index, normalized_query: str, k: int):
    """Raw BM25 order as `(index, score, signals)` triples, or an empty list.

    The last resort for the CANDIDATE LIST when the fused ranking is
    unavailable — the reranker raised, or the embedding index did. TF-IDF used
    to hold this job; BM25 inherits it because it is pure Python, always built,
    and never needs a model file.

    It is deliberately NOT used by find_best_match. `BM25Index.top_k`
    normalizes each score against the best hit for that query, so the top
    result is always exactly 1.0. Handing that to the trust gate would serve a
    degraded guess as a certainty, which is the failure this codebase spends
    most of its guards preventing. The selection tier reads the records
    themselves, so a relative score there costs ranking quality, not truth.
    """
    if index is None:
        return []
    try:
        return [(idx, float(score), {"bm25": round(float(score), 3)})
                for idx, score in index.top_k(normalized_query, k)]
    except Exception as e:  # noqa: BLE001 — a broken last resort is still a last resort
        logger.error(f"BM25 fallback failed: {e}")
        return []


def _intent_training_set(matrix, questions):
    """(vectors, labels) for intent.train.

    Used to also drop company-labeled questions here (this install held 222
    dataset rows, 168 of them exhibitor companies, so "which record is this?"
    was a 222-way problem with a handful of examples per class — a
    near-singleton company class could win a softmax that should never have
    been asked the question). That filter is gone: migrations/0013_companies.sql
    moved companies out of `dataset` entirely, so a company id predicted here
    no longer resolves through classify_intent_local's plain
    `dataset_lookup.get(dataset_id)` — it simply falls through to the next
    tier, same net effect as the old filter, with no per-reindex query needed
    to know which ids are companies.

    The length assertion stays: it is not company-specific, it is what
    protects `matrix`/`labels` from drifting out of step no matter what future
    change touches this function.
    """
    labels = [q.get('dataset_id', '') for q in questions]
    assert len(matrix) == len(labels), "intent vectors and labels fell out of step"
    return matrix, labels


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

# Distinctive title tokens: token -> entry id, for tokens that appear in
# exactly ONE entry's title across the whole knowledge base — dataset AND
# companies together (migrations/0013_companies.sql moved companies out of
# `dataset`, but a company is still a nameable entity, and a token distinctive
# only among dataset titles could still collide with a company's, so both are
# scanned here even though the retrieval indices above stay dataset-only).
# This is how a query that NAMES a known entity gets anchored to that entity's
# own entry, no matter what the similarity scores say. The 2026-08-26 الکامپ
# guard above covers entities the corpus does NOT know; this covers confusion
# BETWEEN known entries (measured 2026-08-27: «شماره مدیرعامل دوندگان لبه
# علم» was served the دبیرخانه phone FAQ at 0.87, and «درباره دکیو بهم بگو»
# fell through to the paid AI tier at 0.691 — both entities exist).
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
    """The single dataset-or-company entry the query NAMES, or (None, set()).

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
    hits = named_entity_hits(query)
    if len(hits) != 1:
        return None, set()
    entity_id, matched = next(iter(hits.items()))
    # Same fallback as get_entry()/find_similar_question(): companies left
    # `dataset` in migrations/0013_companies.sql but the anchor still has to
    # be able to name one — a named company is exactly what the company-field
    # tier in app/services/company_search.py resolves one recorded fact about.
    return dataset_lookup.get(entity_id) or companies_lookup.get(entity_id), matched


def named_entity_hits(query: str) -> dict:
    """{entry id: the query tokens that named it} for every NAMED entry.

    Split out of resolve_named_entity so the caller can tell "named nothing"
    from "named two things". Both used to come back as (None, set()), so the
    pipeline treated them identically and the local tiers went on to answer.

    That is a guess. «دوندگان لبه علم یا دکیو» names two companies; serving
    one of them at 0.98 is the same wrong-entity failure the anchor exists to
    prevent, just arriving through the questions index instead of the anchor.
    Ambiguity must never guess — see the caller in app/routers/chat.py.
    """
    if not _distinctive_title_tokens:
        return {}
    from app.services.rerank import content_tokens
    hits = {}
    for tok in content_tokens(normalize_persian(query, expand_synonyms=False)):
        entity_id = _distinctive_title_tokens.get(tok)
        if entity_id:
            hits.setdefault(entity_id, set()).add(tok)
    return hits


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
    """Load dataset and questions from the DB and build every retrieval index.

    Uses local variables during loading to avoid a race condition where
    the global state is cleared (e.g. ``questions_data = []``) while a
    concurrent API request reads the half-loaded state.
    Globals are only reassigned once everything is ready.
    """
    global dataset, descriptions
    global normalized_titles, normalized_descriptions
    global questions_data, normalized_questions, dataset_lookup, companies_lookup
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
    _companies = []
    _companies_lookup = {}

    _questions_data = []
    _normalized_questions = []

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

            if _normalized_descriptions:
                logger.info(f"Normalized {len(_normalized_descriptions)} documents.")
            else:
                logger.warning("Dataset is empty or has no text fields.")
        else:
            logger.warning("Dataset table is empty")
    except Exception as e:
        logger.error(f"Error loading dataset: {e}")

    # Companies (migrations/0013_companies.sql): NOT part of the retrieval
    # corpus above — that is the whole point of the move (an install's
    # FAQ-vs-company ratio no longer skews the embedding/BM25/intent index —
    # see docs/features/companies-own-table/RESEARCH.md). Loaded only for id
    # lookup (companies_lookup, used by get_entry/find_similar_question/
    # resolve_named_entity as a fallback) and for the named-entity anchor
    # below, which still has to be able to name a company.
    try:
        conn = get_db_connection()
        # `WHERE text <> ''` excludes a company a visitor just proposed at the
        # booth (app/services/leads.py's `propose_company`): that row has a
        # real title but an empty `text` until an admin approves its first
        # pending edit. Without this filter the shell would resolve by name in
        # `resolve_named_entity`/`get_entry` and answer nothing sensible —
        # visible before any review, which is the whole leak this line closes.
        rows = conn.execute(
            "SELECT id, title, text, video_url, title_en, text_en FROM companies"
            " WHERE text <> ''"
        ).fetchall()
        conn.close()
        _companies = [dict(r) for r in rows]
        for item in _companies:
            item_id = item.get("id", "")
            if item_id:
                _companies_lookup[item_id] = item
    except Exception as e:
        logger.error(f"Error loading companies: {e}")

    # بارگذاری questions از دیتابیس
    try:
        conn = get_db_connection()
        rows = conn.execute('SELECT id, question, dataset_id, video_url FROM questions ORDER BY id').fetchall()
        conn.close()
        _questions_data = [dict(r) for r in rows]
        for q in _questions_data:
            _normalized_questions.append(normalize_persian(q.get("question", "")))
        logger.info(f"Loaded {len(_questions_data)} questions from database")
    except Exception as e:
        logger.error(f"Error loading questions: {e}")

    # Semantic indexes. A failure here must never take the retriever down:
    # BM25 is always built and the reranker runs on it alone.
    _dataset_emb = None
    _questions_emb = None
    _intent = None
    try:
        from app.db.queries import get_setting
        if embeddings.available():
            model = get_setting('ai_embedding_model', '') or embeddings.DEFAULT_MODEL
            _dataset_emb = embeddings.build_index(_normalized_descriptions, model)
            _questions_emb = embeddings.build_index(_normalized_questions, model)
            if _questions_emb is not None:
                from app.services import intent
                # Companies are NOT intent classes. See _intent_training_set.
                vecs, labels = _intent_training_set(
                    _questions_emb.matrix, _questions_data)
                _intent = intent.train(vecs, labels, model)
        else:
            logger.warning("model2vec is not installed; retrieval runs on BM25 alone")
    except Exception as e:
        logger.error(f"Embedding index build failed, retrieval runs on BM25: {e}")

    # --- Unknown-entity vocabulary ------------------------------------
    # Every token any retriever could legitimately match against: normalized
    # titles+descriptions (the titles are inside them), the curated
    # questions, the English fields, and the synonym table's own words. A
    # salient token outside this set names something the knowledge base has
    # NO information on — the strongest "do not answer locally" signal there
    # is (see unknown_salient_tokens for the incident this answers).
    #
    # Companies' own title+text go in too, even though they are no longer
    # part of `_normalized_descriptions` (that stays FAQ-only — see the
    # "Companies" load above). Measured while writing
    # migrations/0013_companies.sql: without this, «شرکت» itself (and every
    # other word that lives only in company text — a company's own name, its
    # activity field) reads as an UNKNOWN token the moment no FAQ entry
    # happens to use it, and unknown_tokens gates OFF every local tier in
    # app/routers/chat.py, including the company-list and company-field tiers
    # this migration exists to keep working. The knowledge base still knows
    # about companies; only the FAQ retrieval corpus is companies-free.
    from app.utils.normalizer import active_synonyms as _active_synonyms
    _vocab = set()
    for _text in _normalized_descriptions:
        _vocab.update(_text.split())
    for _text in _normalized_questions:
        _vocab.update(_text.split())
    for _item in _dataset:
        _vocab.update(normalize_persian(_item.get("title_en") or "").split())
        _vocab.update(normalize_persian(_item.get("text_en") or "").split())
    for _item in _companies:
        _vocab.update(normalize_persian(
            f"{_item.get('title', '')} {_item.get('text', '')}").split())
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
    #
    # Scanned over dataset AND companies together, keyed by id rather than a
    # dataset index: before migrations/0013_companies.sql a company WAS a
    # dataset row, so this df count already included every company title —
    # dropping companies here (rather than only from the retrieval indices
    # above) would silently make company names collide with each other and
    # with FAQ titles that happen to share a word, undoing the anchor for
    # every «شماره تماس شرکت X» question the company-field tier answers.
    _entities = _dataset + _companies
    _plain_title_sets = [
        rerank.content_tokens(
            normalize_persian(item.get("title", "").strip(),
                              expand_synonyms=False))
        for item in _entities]
    _plain_doc_sets = [
        set(normalize_persian(
            f"{item.get('title', '').strip()} {item.get('text', '').strip()}",
            expand_synonyms=False).split())
        for item in _entities]
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
        _entity_id = _entities[_i].get("id", "")
        if not _entity_id:
            continue
        for _tok in _toks:
            if (_title_df[_tok] == 1
                    and _doc_df.get(_tok, 0) == 1
                    and len(_tok) >= (4 if _tok.isascii() else 3)):
                _distinctive[_tok] = _entity_id
    _index_by_id = {item.get("id", ""): _i for _i, item in enumerate(_dataset)
                    if item.get("id", "")}

    # --- Atomically publish to module-level globals ---
    dataset = _dataset
    descriptions = _descriptions
    normalized_titles = _normalized_titles
    normalized_descriptions = _normalized_descriptions
    dataset_lookup = _dataset_lookup
    companies_lookup = _companies_lookup

    questions_data = _questions_data
    normalized_questions = _normalized_questions
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

    Deliberately no companies_lookup fallback here, unlike get_entry() and
    find_similar_question(): a predicted id that names a company simply misses
    `dataset_lookup` and falls through, same as an id an admin deleted. That
    keeps this tier doing only its "useful job" (see _intent_training_set) —
    routing an FAQ question to its FAQ answer — even though its training set
    no longer excludes company-labeled questions by construction.
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
    # scale the thresholds already use. Any failure returns NO match rather
    # than taking the chatbot down — see the comment on the return below.
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
            logger.error(f"Hybrid retrieval failed, no match from this tier: {e}")
    elif dataset_embedding_index is not None:
        # Reranking disabled — plain dense argmax (the pre-hybrid behavior).
        try:
            best_idx, score = dataset_embedding_index.search(normalized_query)
            if best_idx >= 0:
                return dataset[best_idx], score
        except Exception as e:
            logger.error(f"Embedding search failed, no match from this tier: {e}")

    # NO last-resort ranking here, on purpose. TF-IDF used to sit at this
    # point and hand back a real 0..1 cosine score. BM25 cannot take that job:
    # BM25Index.top_k normalizes every query against its own best hit, so the
    # top result always scores exactly 1.0. Returning that would clear
    # TRUSTED_MATCH_THRESHOLD on EVERY query, including the ones we have no
    # answer for — a degraded guess served as a certainty.
    #
    # So when both retrievers are unavailable this tier says nothing and the
    # pipeline moves on to the tiers that read records rather than scores.
    # find_top_matches DOES fall back to BM25 (see _bm25_only): a candidate
    # LIST only needs an order, and the selection tier reads the records.
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
            logger.error(f"Hybrid retrieval failed for top-k, falling back to BM25: {e}")
            ranked = []

    if not ranked:
        ranked = _bm25_only(dataset_bm25_index, normalized_query, k)

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
    """One dataset-or-company record by id, or None.

    The pick tier stores IDS, not text, so this is the lookup that turns a
    stored offer back into an answer with its video_url intact. None is the
    honest answer for an id an admin deleted between the turn that offered it
    and the turn that picked it — the caller then falls through to normal
    retrieval instead of serving a stale dict.

    Falls back to companies_lookup: the company-list tier
    (app/services/company_search.py) offers companies as numbered options and
    stores their ids in the same offer_state a pick resolves against, and a
    curated question (Tier 0) can name a company as its dataset_id too — a
    visitor picking "2" or asking a company's own curated question has to
    resolve here, not only in `dataset`. See
    docs/features/companies-own-table/RESEARCH.md section 2.
    """
    if not entry_id:
        return None
    _maybe_refresh()
    return dataset_lookup.get(entry_id) or companies_lookup.get(entry_id)


def find_similar_question(query: str, exact_only: bool = False):
    """جستجوی سوال مشابه در questions و بازگشت جواب dataset

    ``exact_only=True`` restricts scoring to the token-Jaccard overlap with
    the hand-curated questions — the near-exact signal Tier 0 of the chat
    pipeline trusts. The default blends in the reranked semantic/lexical
    ranking for
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

    # The ranked signal, when a retriever is available. Jaccard overlap above
    # is the floor: it is a real 0..1 number computed from the query itself, so
    # an install with no embedding model still answers its questions index.
    rank_score = 0.0
    rank_idx = -1
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
            rank_idx, rank_score, _ = rerank.best(
                normalized_query, normalized_questions, dense_hits, lexical_hits, coverage_query=coverage_query)
        except Exception as e:
            logger.error(f"Questions hybrid retrieval failed: {e}")
            rank_idx, rank_score = -1, 0.0

    final_score = max(best_score, rank_score)
    final_idx = best_idx if best_score >= rank_score else rank_idx

    # threshold پایین‌تر چون سوالات کوتاه‌تر و دقیق‌تر هستند
    if final_score >= 0.5 and final_idx != -1:
        question_entry = questions_data[final_idx]
        dataset_id = question_entry.get("dataset_id", "")
        # A curated question can name a company (840 such rows on production —
        # see docs/features/companies-own-table/RESEARCH.md section 2), and
        # companies left `dataset` in migrations/0013_companies.sql, so the
        # fallback here is what keeps Tier 0 answering them at all.
        dataset_entry = dataset_lookup.get(dataset_id) or companies_lookup.get(dataset_id)
        if dataset_entry:
            logger.info(f"Question match (Jaccard: {best_score:.2f}, ranked: {rank_score:.2f}): {question_entry.get('question')} → {dataset_id}")
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
