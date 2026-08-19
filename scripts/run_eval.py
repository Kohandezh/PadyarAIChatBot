#!/usr/bin/env python3
"""Padyar retrieval evaluation harness — offline, reproducible, no external AI.

Runs the golden INOTEX dataset (data/eval/golden-inotex.json) against the
LOCAL retrieval pipeline exactly as /chat uses it (title-overlap tier,
semantic/TF-IDF ranking, questions index, trained intent classifier) and
measures:

  answerable queries : recall@1, recall@3, MRR (rank of the expected entry)
                       All three are read off ONE ranking — the one the live
                       pipeline produces: the entry it serves at rank 1, then
                       the reranked union of the dense and BM25 candidates.
                       recall@1 and recall@3 are therefore strictly nested.
                       With --backend tfidf (the baseline) the tail is the
                       plain TF-IDF ranking and no reranking is applied.
  unsupported        : false-confident-answer rate (local score >= trust bar)
  legacy queries     : contamination rate (legacy tokens in any served answer)
  prompt injection   : confident-answer rate + secret-leak check
  latency            : p50 / p95 per query (retrieval only)

The external AI fallback is deliberately NOT called: this measures the
proprietary local layer, and CI must not depend on external providers.

USAGE (from the project root):
    .venv/bin/python scripts/run_eval.py
    .venv/bin/python scripts/run_eval.py --backend tfidf     # baseline compare
    .venv/bin/python scripts/run_eval.py --out results.json

Exit code 1 when a hard gate fails (contamination > 0 or secret leak).
"""
import argparse
import json
import statistics
import sys
import time
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GOLDEN = ROOT / "data" / "eval" / "golden-inotex.json"
DEFAULT_OUT = ROOT / "docs" / "knowledge-based-evidence" / "appendices" / "benchmark-results" / "retrieval-eval.json"

LEGACY_TOKENS = ["الکامپ", "elecomp", "نورا", "noorvision"]
SECRET_MARKERS = ["OPENAI_API_KEY", "SECRET_KEY", "sk-", "api.gapgpt"]
TRUST = 0.70  # mirrors TRUSTED_MATCH_THRESHOLD in app/config.py


def full_ranking(query, search, hybrid):
    """Rank dataset entries for `query` the way the LIVE retriever ranks them.

    ``hybrid=True`` mirrors ``app/services/search.find_best_match`` branch for
    branch: each first-stage retriever proposes ``RERANK_CANDIDATES`` rows and
    the feature reranker orders their union. It calls the very same service
    objects and functions the product calls (``search.dataset_embedding_index``,
    ``search.dataset_bm25_index``, ``rerank.rerank``, ``find_best_match``)
    instead of re-implementing any scoring here — that re-implementation is
    exactly how this harness previously drifted away from the product and
    reported a recall@3 measured on the dense index alone.

    ``find_best_match`` supplies the head of the list. That is deliberate: the
    title-overlap shortcut and the TF-IDF safety net are inlined inside that
    function, so asking the service which entry it would return is the only
    way to represent those two branches without copying them.

    ``hybrid=False`` is the honest baseline — the plain ranking of a single
    retriever, used for ``--backend tfidf`` and when ``RETRIEVAL_RERANK`` is
    off. That run must not quietly turn into a hybrid run.
    """
    from app.utils.normalizer import normalize_persian
    nq = normalize_persian(query)

    def to_ids(order):
        return [search.dataset[i]["id"] for i in order if 0 <= i < len(search.dataset)]

    if hybrid and (search.dataset_embedding_index is not None
                   or search.dataset_bm25_index is not None):
        from app.services import rerank
        dense_hits = []
        if search.dataset_embedding_index is not None:
            dense_hits = search.dataset_embedding_index.search_topk(nq, search.RERANK_CANDIDATES)
        lexical_hits = []
        if search.dataset_bm25_index is not None:
            lexical_hits = search.dataset_bm25_index.top_k(nq, search.RERANK_CANDIDATES)
        ranked = rerank.rerank(nq, search.normalized_descriptions, dense_hits, lexical_hits)
        ids = to_ids([idx for idx, _score, _signals in ranked])
        head, _score = search.find_best_match(query)
        if head:
            ids = [head["id"]] + [i for i in ids if i != head["id"]]
        return ids

    if search.dataset_embedding_index is not None:
        # Reranking disabled: find_best_match degrades to plain dense argmax,
        # so the honest ranking here is the full dense order.
        import numpy as np
        idx = search.dataset_embedding_index
        from app.services.embeddings import _get_model
        m = _get_model(idx.model_name)
        v = np.asarray(m.encode([nq]), dtype=np.float32)[0]
        n = np.linalg.norm(v)
        if n == 0:
            return []
        sims = idx.matrix @ (v / n)
        return to_ids(list(np.argsort(-sims)))

    if search.vectorizer is not None and search.tfidf_matrix is not None:
        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as np
        qv = search.vectorizer.transform([nq])
        sims = cosine_similarity(qv, search.tfidf_matrix).flatten()
        return to_ids(list(np.argsort(-sims)))
    return []


def main() -> int:
    p = argparse.ArgumentParser(description="Run the INOTEX retrieval benchmark.")
    p.add_argument("--backend", choices=["embedding", "tfidf"], default="embedding")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    args = p.parse_args()

    # Pin the requested backend for this run (restored afterwards).
    conn = sqlite3.connect(ROOT / "chat_history.db")
    prev = conn.execute("SELECT value FROM settings WHERE key='search_backend'").fetchone()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('search_backend', ?)", (args.backend,))
    conn.commit()
    conn.close()

    from app.config import RERANK_ENABLED
    from app.services import search
    search.load_dataset_internal()

    # The hybrid path is what the product runs by default. The tfidf run is
    # the baseline and stays a single-retriever measurement on purpose.
    hybrid = RERANK_ENABLED and args.backend == "embedding"
    ranking_method = (
        "pipeline: served answer first, then the reranked union of the dense "
        "and BM25 candidates (mirrors app/services/search.find_best_match)"
        if hybrid else
        "baseline: served answer first, then the plain single-retriever ranking "
        "(TF-IDF cosine with --backend tfidf, dense argmax when the embedding "
        "backend is on but RETRIEVAL_RERANK is off); no reranking"
    )

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    queries = golden["queries"]

    ranks, latencies = [], []
    hits1 = hits3 = 0
    answerable = 0
    false_confident = {"unsupported": 0, "legacy_contamination": 0, "prompt_injection": 0}
    contaminated = 0
    secret_leaks = 0
    per_category = {}
    failures = []

    for item in queries:
        q, expect, cat = item["q"], item["expect"], item["cat"]
        t0 = time.perf_counter()
        # Mirrors the /chat tier order (app/routers/chat.py).
        xe, xs = search.find_similar_question(q, exact_only=True)
        if xe and xs >= 0.9:
            entry, score = xe, xs
        else:
            entry, score = search.find_best_match(q)
            if (not entry) or score < TRUST:
                qe, qs = search.find_similar_question(q)
                if qe and qs >= TRUST:
                    entry, score = qe, qs
            if (not entry) or score < TRUST:
                ie, ip = search.classify_intent_local(q)
                if ie and ip >= 0.6:
                    entry, score = ie, ip
        latencies.append((time.perf_counter() - t0) * 1000)

        served = entry if (entry and score >= 0.6) else None
        answer_text = (served or {}).get("text", "") + " " + (served or {}).get("text_en", "")

        cstat = per_category.setdefault(cat, {"n": 0, "correct": 0})
        cstat["n"] += 1

        if expect:
            accepted = expect if isinstance(expect, list) else [expect]
            answerable += 1
            ranking = full_ranking(q, search, hybrid)
            if served:
                # The pipeline's own rank-1 result is the entry it actually
                # serves — whichever tier produced it (curated questions,
                # hybrid retrieval, or the trained intent head). The retriever's
                # runners-up follow. Without this, recall@1 (whole pipeline) and
                # recall@3 (retrieval stage only) score different subsystems and
                # recall@3 can land below recall@1, which is impossible for a
                # correctly measured pair.
                ranking = [served["id"]] + [i for i in ranking if i != served["id"]]
            found = [ranking.index(e) + 1 for e in accepted if e in ranking]
            rank = min(found) if found else None
            ranks.append(rank)
            top = served["id"] if served else (ranking[0] if ranking else None)
            if served and served["id"] in accepted:
                hits1 += 1
                cstat["correct"] += 1
            elif not served and rank == 1:
                # ranked right but below the confidence bar — a deferral, not an error
                cstat["correct"] += 1
                hits1 += 1
            else:
                failures.append({"q": q, "expected": accepted, "got": top, "score": round(score, 3)})
            if rank and rank <= 3:
                hits3 += 1
        else:
            has_contamination = False
            for tok in LEGACY_TOKENS:
                if tok.lower() in answer_text.lower():
                    contaminated += 1
                    has_contamination = True
                    failures.append({"q": q, "issue": f"legacy token {tok!r} in answer"})
            for tok in SECRET_MARKERS:
                if tok in answer_text:
                    secret_leaks += 1
                    failures.append({"q": q, "issue": f"secret marker {tok!r} in answer"})

            if cat == "legacy_contamination":
                # Policy (§13.6): a legacy-event query may be answered with
                # CURRENT INOTEX information (a redirect) — the hard gate is
                # that no legacy identity appears in the served answer.
                if not has_contamination:
                    cstat["correct"] += 1
                if served:
                    cstat["redirected"] = cstat.get("redirected", 0) + 1
            elif served and score >= TRUST:
                false_confident[cat] = false_confident.get(cat, 0) + 1
                failures.append({"q": q, "expected": None, "got": served["id"], "score": round(score, 3)})
            else:
                cstat["correct"] += 1

    valid_ranks = [r for r in ranks if r]
    mrr = sum(1.0 / r for r in valid_ranks) / len(ranks) if ranks else 0.0
    lat_sorted = sorted(latencies)

    report = {
        "dataset_version": golden["dataset_version"],
        "knowledge_version": golden["knowledge_version"],
        "backend": args.backend,
        "rerank_enabled": RERANK_ENABLED,
        "ranking_method": ranking_method,
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "totals": {
            "queries": len(queries),
            "answerable": answerable,
            "recall_at_1": round(hits1 / answerable, 3) if answerable else None,
            "recall_at_3": round(hits3 / answerable, 3) if answerable else None,
            "mrr": round(mrr, 3),
            "false_confident_unsupported": false_confident.get("unsupported", 0),
            "false_confident_legacy": false_confident.get("legacy_contamination", 0),
            "false_confident_injection": false_confident.get("prompt_injection", 0),
            "legacy_contamination_answers": contaminated,
            "secret_leaks": secret_leaks,
            "latency_ms_p50": round(statistics.median(lat_sorted), 1),
            "latency_ms_p95": round(lat_sorted[int(len(lat_sorted) * 0.95) - 1], 1),
        },
        "per_category": per_category,
        "failures": failures,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # Restore the previous backend setting.
    conn = sqlite3.connect(ROOT / "chat_history.db")
    if prev:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('search_backend', ?)", (prev[0],))
        conn.commit()
    conn.close()

    t = report["totals"]
    print(f"backend={args.backend}  queries={t['queries']}  "
          f"recall@1={t['recall_at_1']}  recall@3={t['recall_at_3']}  mrr={t['mrr']}")
    print(f"false-confident: unsupported={t['false_confident_unsupported']} "
          f"legacy={t['false_confident_legacy']} injection={t['false_confident_injection']}")
    print(f"contaminated answers={t['legacy_contamination_answers']}  secret leaks={t['secret_leaks']}")
    print(f"latency p50={t['latency_ms_p50']}ms  p95={t['latency_ms_p95']}ms")
    print(f"report → {out}")
    if failures:
        print(f"\n{len(failures)} failure(s); first 5:")
        for f in failures[:5]:
            print("  ", json.dumps(f, ensure_ascii=False))

    hard_fail = contaminated > 0 or secret_leaks > 0
    return 1 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
