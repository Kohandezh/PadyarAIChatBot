#!/usr/bin/env python3
"""Own-model-next spike: learned reranker vs domain-adapted embedding.

Research code for docs/features/own-model-next/RESEARCH.md. It changes no
app behaviour: every candidate is applied by patching module globals inside
this process only, on a throwaway SQLite database, and nothing is written to
the install's database or to data/.

What it measures, all from ONE harness loop that copies the golden-mode loop
of scripts/run_eval.py at commit 420eb1e (branch feat/eval-golden-set):

  baseline   the shipped hand-set fusion in app/services/rerank.py
  A1-lr      logistic regression over the existing rerank signals,
             replacing the fused score on the dataset ranking (tier 1)
  A1-lr-q    A1 on the dataset ranking PLUS a second logistic regression on
             the curated-questions ranking. Both rankings call the same
             rerank.best (search.py:728 and search.py:926), so (a) can
             replace either
  A2-gbm     sklearn HistGradientBoosting over the same signals (dataset)
  A2-gbm-q   A2 on both rankings
  R1-platt   NOT learned weights: a monotone rescale of the shipped score
             (one-feature logistic regression), dataset ranking; R1-platt-q
             on both rankings. Ranking cannot change, only the scale
  W-grid     NOT learned: the three hand weights re-tuned on the curated
             questions over a 0.05 grid, applied as the product applies them
  A1-lr-cv   A1 with golden queries ALSO in training, k-fold cross-validated
             (a stand-in for "the install has labelled chat logs");
             only held-out folds are ever scored
  B1-idf     the model2vec embedding re-weighted with this corpus's token
             IDF (a CPU-only domain adaptation; no torch in .venv)
  B1-idf-cal B1 with the cosine floor shifted to keep the shipped
             calibration on the curated questions
  B2-adapter a ridge-regularised linear map from question vectors to entry
             vectors, fitted on the curated questions, applied to queries of
             the dataset index only

Training data for every candidate is the corpus's own curated `questions`
(what a real install has), plus "held-out entry" negatives: each curated
question re-ranked with its own entry removed, so every candidate is wrong
and the model learns what "the knowledge base has no answer" looks like.
Golden queries are never trained on, except in A1-lr-cv, which reports
held-out folds only.

Noise: per-query flips, an exact McNemar test on hit@1, and a paired
bootstrap 95% interval for the change in recall@1 and MRR.

USAGE (from the project root):
    .venv/bin/python scripts/experiments/own_model_next.py \
        --golden /path/to/golden.json --out /tmp/own-model-next.json

The golden file names its corpus (resolved next to it), exactly as
scripts/run_eval.py requires.
"""
import argparse
import json
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# Same pins as scripts/run_eval.py: SQLite only, a throwaway database, set
# before the first `app` import because app/config.py reads them at import.
os.environ["DB_BACKEND"] = "sqlite"
# The embedding snapshot is pinned and cached under data/models; this run must
# not reach the network for it.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
_TMP = tempfile.mkdtemp(prefix="padyar-own-model-next-")
os.environ["DB_PATH"] = os.path.join(_TMP, "eval.db")
os.environ["LOGS_DB_PATH"] = os.path.join(_TMP, "application_logs.db")

import numpy as np  # noqa: E402

TRUST = 0.70     # TRUSTED_MATCH_THRESHOLD, app/config.py:41
FALLBACK = 0.45  # LOCAL_FALLBACK_THRESHOLD, app/config.py:46
FEATURES = ["dense", "lexical", "coverage", "agreement", "coverage_zero"]


# --------------------------------------------------------------------------
# Corpus loading and seeding: copied from scripts/run_eval.py @ 420eb1e
# (load_corpus, seed_corpus) so the database is the one the benchmark uses.
# --------------------------------------------------------------------------

def load_golden(path: Path):
    golden = json.loads(path.read_text(encoding="utf-8"))
    corpus_path = Path(golden["corpus"])
    if not corpus_path.is_absolute():
        corpus_path = path.parent / corpus_path
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    if corpus.get("corpus_version") != golden.get("knowledge_version"):
        sys.exit("golden knowledge_version does not match the corpus")
    return golden, corpus, corpus_path


def seed_corpus(corpus):
    from app.db.connection import get_db_connection
    entries = corpus["entries"]
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM questions")
        conn.execute("DELETE FROM dataset")
        conn.execute("DELETE FROM synonyms")
        conn.executemany(
            "INSERT INTO dataset (id, title, text, video_url, title_en,"
            " text_en, position) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(row["id"], row["title"], row["text"], "",
              row.get("title_en", ""), row.get("text_en", ""), (i + 1) * 10)
             for i, row in enumerate(entries)])
        conn.executemany(
            "INSERT INTO questions (question, dataset_id, video_url)"
            " VALUES (?, ?, '')",
            [(q, row["id"]) for row in entries for q in row.get("questions", [])])
        conn.executemany(
            "INSERT INTO synonyms (source, target) VALUES (?, ?)",
            [(s, t) for s, t in corpus.get("synonyms", [])])
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# The harness loop: the golden-mode loop of scripts/run_eval.py @ 420eb1e,
# kept line for line in its decisions (tier order, the 0.6 serve floor, the
# "ranked right but deferred counts as a hit" rule, full_ranking's tail).
# Returns one record per query so variants can be compared query by query.
# --------------------------------------------------------------------------

def full_ranking(query, search):
    from app.services import rerank
    from app.utils.normalizer import normalize_persian
    nq = normalize_persian(query)
    dense_hits = (search.dataset_embedding_index.search_topk(nq, search.RERANK_CANDIDATES)
                  if search.dataset_embedding_index is not None else [])
    lexical_hits = (search.dataset_bm25_index.top_k(nq, search.RERANK_CANDIDATES)
                    if search.dataset_bm25_index is not None else [])
    ranked = rerank.rerank(nq, search.normalized_descriptions, dense_hits, lexical_hits)
    ids = [search.dataset[i]["id"] for i, _s, _g in ranked]
    head, _score = search.find_best_match(query)
    if head:
        ids = [head["id"]] + [i for i in ids if i != head["id"]]
    return ids


def t1_stage(query, search):
    """Tier 1 on its own: the ranking and top score find_best_match's hybrid
    branch produces (app/services/search.py:712-737), without the other
    tiers masking it. The title-overlap shortcut (search.py:684-706) is
    excluded on purpose: no learned model touches it."""
    from app.services import rerank
    from app.utils.normalizer import normalize_persian
    nq = normalize_persian(query)
    cq = normalize_persian(query, expand_synonyms=False)
    dense = (search._dual_hits(search.dataset_embedding_index.search_topk, cq, nq,
                               search.RERANK_CANDIDATES)
             if search.dataset_embedding_index is not None else [])
    lexical = (search._dual_hits(search.dataset_bm25_index.top_k, cq, nq,
                                 search.RERANK_CANDIDATES)
               if search.dataset_bm25_index is not None else [])
    ranked = rerank.rerank(nq, search.normalized_descriptions, dense, lexical,
                           coverage_query=cq)
    return [search.dataset[i]["id"] for i, _s, _g in ranked], (ranked[0][1] if ranked else 0.0)


def run_harness(queries, search):
    records = []
    for item in queries:
        q, expect, cat = item["q"], item["expect"], item["cat"]
        unknown = search.unknown_salient_tokens(q)
        if unknown:
            entry, score, tier = None, 0.0, "unknown-gate"
        else:
            xe, xs = search.find_similar_question(q, exact_only=True)
            tier = "T0"
            if xe and xs >= 0.9:
                entry, score = xe, xs
            else:
                entry, score = search.find_best_match(q)
                tier = "T1"
                if (not entry) or score < TRUST:
                    qe, qs = search.find_similar_question(q)
                    if qe and qs >= TRUST:
                        entry, score, tier = qe, qs, "T1-questions"
                if (not entry) or score < TRUST:
                    ie, ip = search.classify_intent_local(q)
                    if ie and ip >= 0.6:
                        entry, score, tier = ie, ip, "T1.5"
        served = entry if (entry and score >= 0.6) else None
        stage_ids, stage_score = t1_stage(q, search)
        # The questions-index blend on its own (search.py:871-948): the same
        # rerank.best over the curated questions, served as max(jaccard, rank).
        qb_entry, qb_score = search.find_similar_question(q)
        rec = {"q": q, "cat": cat, "expect": expect, "control": item.get("control"),
               "served": served["id"] if served else None,
               "tier": tier if served else "none", "score": round(float(score), 4),
               "t1_top": stage_ids[0] if stage_ids else None,
               "t1_score": round(float(stage_score), 4), "unknown_gate": bool(unknown),
               "qblend_top": qb_entry["id"] if qb_entry else None,
               "qblend_score": round(float(qb_score), 4)}
        if expect:
            accepted = expect if isinstance(expect, list) else [expect]
            ranking = full_ranking(q, search)
            if served:
                ranking = [served["id"]] + [i for i in ranking if i != served["id"]]
            found = [ranking.index(e) + 1 for e in accepted if e in ranking]
            rank = min(found) if found else None
            hit1 = bool((served and served["id"] in accepted) or (not served and rank == 1))
            s_found = [stage_ids.index(e) + 1 for e in accepted if e in stage_ids]
            s_rank = min(s_found) if s_found else None
            rec.update({"rank": rank, "hit1": hit1, "hit3": bool(rank and rank <= 3),
                        "hit5": bool(rank and rank <= 5), "hit8": bool(rank and rank <= 8),
                        "rr": (1.0 / rank) if rank else 0.0,
                        "t1_rank": s_rank, "t1_hit1": s_rank == 1,
                        "t1_hit3": bool(s_rank and s_rank <= 3),
                        "t1_rr": (1.0 / s_rank) if s_rank else 0.0,
                        # served above the trust bar, and wrong
                        "confident_wrong": bool(served and score >= TRUST
                                                and served["id"] not in accepted)})
        else:
            rec.update({"false_confident": bool(served and score >= TRUST
                                                and cat != "legacy_contamination"),
                        "fc_any": bool(served and score >= TRUST),
                        "t1_fc_trust": stage_score >= TRUST,
                        "t1_fc_fallback": stage_score >= FALLBACK})
        records.append(rec)
    return records


def summarize(records):
    ans = [r for r in records if r["expect"]]
    deny = [r for r in records if not r["expect"]]
    n = len(ans)

    def band(lo, hi, correct):
        return sum(1 for r in ans if lo <= r["t1_score"] < hi and r["t1_hit1"] == correct)
    return {
        "answerable": n, "deny": len(deny),
        "recall_at_1": round(sum(r["hit1"] for r in ans) / n, 3),
        "recall_at_3": round(sum(r["hit3"] for r in ans) / n, 3),
        "recall_at_5": round(sum(r["hit5"] for r in ans) / n, 3),
        "recall_at_8": round(sum(r["hit8"] for r in ans) / n, 3),
        "mrr": round(sum(r["rr"] for r in ans) / n, 3),
        "confident_wrong_answerable": sum(r["confident_wrong"] for r in ans),
        # correct AND served at or above the trust bar: answered without the
        # paid tier. The harness's recall@1 also counts a correct deferral.
        "served_correct_at_0.70": sum(
            1 for r in ans if r["served"] and r["score"] >= TRUST and r["hit1"]),
        # the harness's false_confident: unsupported + prompt_injection
        "false_confident_at_0.70": sum(r["false_confident"] for r in deny),
        "deny_served_at_0.70_incl_legacy": sum(r["fc_any"] for r in deny),
        # deny queries the unknown-entity gate refused before any reranker ran
        "deny_refused_by_unknown_gate": sum(r["unknown_gate"] for r in deny),
        "t1_recall_at_1": round(sum(r["t1_hit1"] for r in ans) / n, 3),
        "t1_recall_at_3": round(sum(r["t1_hit3"] for r in ans) / n, 3),
        "t1_mrr": round(sum(r["t1_rr"] for r in ans) / n, 3),
        "t1_deny_above_0.70": sum(r["t1_fc_trust"] for r in deny),
        "t1_deny_above_0.45": sum(r["t1_fc_fallback"] for r in deny),
        # Tier-1 top score bands for answerable queries: correct / wrong
        "t1_band_ge_0.70_correct_wrong": [band(TRUST, 9, True), band(TRUST, 9, False)],
        "t1_band_0.45_0.70_correct_wrong": [band(FALLBACK, TRUST, True),
                                            band(FALLBACK, TRUST, False)],
        "controls_served_at_0.70": {
            r["control"]: bool(r["served"] and r["score"] >= TRUST and r["hit1"])
            for r in ans if r.get("control")},
    }


# --------------------------------------------------------------------------
# Noise: paired comparison against the baseline, query by query.
# --------------------------------------------------------------------------

def mcnemar_exact(b, c):
    """Two-sided exact McNemar p-value from the discordant counts."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def paired(base, cand, key_hit, key_rr, rng, n_boot):
    ans_b = [r for r in base if r["expect"]]
    ans_c = [r for r in cand if r["expect"]]
    assert [r["q"] for r in ans_b] == [r["q"] for r in ans_c]
    hb = np.array([r[key_hit] for r in ans_b], dtype=float)
    hc = np.array([r[key_hit] for r in ans_c], dtype=float)
    rb = np.array([r[key_rr] for r in ans_b])
    rc = np.array([r[key_rr] for r in ans_c])
    gained = [r["q"] for r, x, y in zip(ans_c, hb, hc) if y > x]
    lost = [r["q"] for r, x, y in zip(ans_c, hb, hc) if x > y]
    idx = rng.integers(0, len(hb), size=(n_boot, len(hb)))
    d_hit = (hc[idx] - hb[idx]).mean(axis=1)
    d_rr = (rc[idx] - rb[idx]).mean(axis=1)
    return {
        "delta_recall_at_1": round(float(hc.mean() - hb.mean()), 3),
        "delta_recall_at_1_ci95": [round(float(np.percentile(d_hit, 2.5)), 3),
                                   round(float(np.percentile(d_hit, 97.5)), 3)],
        "delta_mrr": round(float(rc.mean() - rb.mean()), 3),
        "delta_mrr_ci95": [round(float(np.percentile(d_rr, 2.5)), 3),
                           round(float(np.percentile(d_rr, 97.5)), 3)],
        "gained": gained, "lost": lost,
        "mcnemar_p": round(mcnemar_exact(len(gained), len(lost)), 4),
    }


def deny_flips(base, cand, key):
    db = [r for r in base if not r["expect"]]
    dc = [r for r in cand if not r["expect"]]
    return {"newly_confident": [c["q"] for b, c in zip(db, dc) if c[key] and not b[key]],
            "no_longer_confident": [c["q"] for b, c in zip(db, dc) if b[key] and not c[key]]}


# --------------------------------------------------------------------------
# Candidate (a): a learned scorer over the rerank signals.
# --------------------------------------------------------------------------

def signal_rows(query, texts, dense, lexical, coverage_query):
    """The rerank signals per candidate, computed exactly as
    app/services/rerank.py:118-134 computes them. Returns (idx, features)."""
    from app.services import rerank
    dense_by = dict(dense)
    lex_by = dict(lexical)
    cq = coverage_query if coverage_query is not None else query
    d_top = dense[0][0] if dense else -1
    l_top = lexical[0][0] if lexical else -1
    rows = []
    for idx in sorted(set(dense_by) | set(lex_by)):
        if idx < 0 or idx >= len(texts):
            continue
        cov = rerank._coverage(cq, texts[idx])
        rows.append((idx, [dense_by.get(idx, 0.0), lex_by.get(idx, 0.0), cov,
                           1.0 if (idx == d_top and idx == l_top) else 0.0,
                           1.0 if cov == 0.0 else 0.0]))
    return rows, dense, lexical


def shipped_score(f):
    """The shipped fusion (app/services/rerank.py:125-134) from a feature row."""
    from app.services import rerank
    d, lex, cov, agree, cov_zero = f
    s = rerank.W_DENSE * d + rerank.W_LEXICAL * lex + rerank.W_COVERAGE * cov
    if agree:
        s = min(1.0, s + rerank.AGREEMENT_BONUS)
    if cov_zero:
        s *= 0.5
    return min(1.0, s)


class PlattOnShipped:
    """A monotone rescale of the shipped score, P(correct | shipped score),
    fitted with a one-feature logistic regression (two numbers: slope and
    intercept). It cannot change any ranking, only the scale the thresholds
    read. The non-learned-weights control for A1."""

    def fit(self, X, y):
        from sklearn.linear_model import LogisticRegression
        self.lr = LogisticRegression(max_iter=2000).fit(
            np.array([[shipped_score(f)] for f in X]), y)
        return self

    def predict_proba(self, X):
        return self.lr.predict_proba(np.array([[shipped_score(f)] for f in X]))


def make_learned_rerank(search, original, ds_model=None, q_model=None):
    """A drop-in for rerank.rerank (same signature, same return shape) whose
    score is a model's P(correct). `ds_model` scores the dataset ranking
    (find_best_match, search.py:728) and `q_model` the curated-questions
    ranking (find_similar_question, search.py:926). Both call sites run the
    SAME shipped fusion, so a learned reranker can replace either or both;
    an index with no model keeps the shipped formula."""
    def learned(query, texts, dense=None, lexical=None, coverage_query=None):
        if texts is search.normalized_descriptions and ds_model is not None:
            model = ds_model
        elif texts is search.normalized_questions and q_model is not None:
            model = q_model
        else:
            return original(query, texts, dense, lexical, coverage_query)
        dense = dense or []
        lexical = lexical or []
        rows, _, _ = signal_rows(query, texts, dense, lexical, coverage_query)
        if not rows:
            return []
        X = np.array([f for _i, f in rows])
        p = model.predict_proba(X)[:, 1]
        d_rank = {i: pos for pos, (i, _s) in enumerate(dense)}
        l_rank = {i: pos for pos, (i, _s) in enumerate(lexical)}
        unranked = len(texts) + 1
        out = [(i, round(float(s), 4), dict(zip(FEATURES, f)))
               for (i, f), s in zip(rows, p)]
        out.sort(key=lambda r: (-r[1], d_rank.get(r[0], unranked),
                                l_rank.get(r[0], unranked), r[0]))
        return out
    return learned


def candidates_for(query, search, drop=(), index="dataset"):
    """The candidate lists one index hands the reranker for one query, the
    way search.py builds them (_dual_hits over both query forms), optionally
    with some rows removed. With `drop` the BM25 index is rebuilt without
    those rows, so its IDF and per-query normalisation are those of a
    knowledge base that never had them."""
    from app.services import bm25, embeddings
    from app.utils.normalizer import normalize_persian
    nq = normalize_persian(query)
    cq = normalize_persian(query, expand_synonyms=False)
    k = search.RERANK_CANDIDATES
    if index == "dataset":
        texts, emb_idx, bm_idx = (search.normalized_descriptions,
                                  search.dataset_embedding_index, search.dataset_bm25_index)
    else:
        texts, emb_idx, bm_idx = (search.normalized_questions,
                                  search.questions_embedding_index, search.questions_bm25_index)
    drop = set(drop)
    if not drop:
        dense_fn, lex_fn = emb_idx.search_topk, bm_idx.top_k
    else:
        keep = [i for i in range(len(texts)) if i not in drop]
        mat = emb_idx.matrix[keep]
        sub_bm25 = bm25.BM25Index([texts[i] for i in keep])
        model = embeddings._get_model(emb_idx.model_name)

        def dense_fn(text, kk):
            v = np.asarray(model.encode([text]), dtype=np.float32)[0]
            n = np.linalg.norm(v)
            if n == 0:
                return []
            sims = mat @ (v / n)
            order = np.argsort(-sims)[:kk]
            return [(keep[int(j)], embeddings._calibrate(float(sims[j]))) for j in order]

        def lex_fn(text, kk):
            return [(keep[j], s) for j, s in sub_bm25.top_k(text, kk)]
    dense = search._dual_hits(dense_fn, cq, nq, k)
    lexical = search._dual_hits(lex_fn, cq, nq, k)
    return nq, cq, texts, dense, lexical


def training_rows(pairs, search, id_to_idx, heldout_negatives=True):
    """(X, y, group) for the DATASET ranking from (query, entry_id or None)
    pairs. `group` numbers each ranked list, so a per-query objective (the
    weight grid) can be computed from the same rows."""
    X, y, g = [], [], []
    gid = -1
    for q, eid in pairs:
        target = id_to_idx.get(eid) if eid else None
        runs = [((), target)]
        if heldout_negatives and target is not None:
            runs.append(((target,), None))
        for drop, tgt in runs:
            nq, cq, texts, dense, lexical = candidates_for(q, search, drop)
            rows, _, _ = signal_rows(nq, texts, dense, lexical, cq)
            gid += 1
            for idx, f in rows:
                X.append(f)
                y.append(1 if (tgt is not None and idx == tgt) else 0)
                g.append(gid)
    return np.array(X), np.array(y), np.array(g)


def training_rows_questions(search):
    """(X, y) for the QUESTIONS ranking, leave-one-question-out.

    Each curated question is ranked against the questions index WITHOUT
    itself (otherwise it finds its own text at Jaccard 1.0). A candidate is
    positive when it is a sibling question of the same entry. A second pass
    removes every question of that entry: all candidates are negative, the
    "knowledge base has no answer" shape, as for the dataset ranking."""
    X, y = [], []
    labels = [q.get("dataset_id", "") for q in search.questions_data]
    for i, qrow in enumerate(search.questions_data):
        entry = labels[i]
        siblings = {j for j, e in enumerate(labels) if e == entry}
        for drop, pos in (({i}, True), (siblings, False)):
            nq, cq, texts, dense, lexical = candidates_for(
                qrow["question"], search, drop, index="questions")
            rows, _, _ = signal_rows(nq, texts, dense, lexical, cq)
            for idx, f in rows:
                X.append(f)
                y.append(1 if (pos and labels[idx] == entry) else 0)
    return np.array(X), np.array(y)


def fit_lr(X, y):
    from sklearn.linear_model import LogisticRegression
    # Default C=1.0; no class weighting, so predict_proba stays a probability
    # under the training mix instead of a rebalanced score.
    return LogisticRegression(max_iter=2000).fit(X, y)


def fit_gbm(X, y):
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_depth=3, max_iter=100, learning_rate=0.1,
                                          min_samples_leaf=10, random_state=0).fit(X, y)


def select_weights(X, y, g):
    """Re-tune the three hand weights on the curated questions ONLY (no
    golden), the non-learned control for A1. Grid over the simplex in steps
    of 0.05. Objective, in order: most curated questions ranked right; fewest
    held-out-entry lists whose top clears 0.70 (a confident answer when the
    knowledge base has none); most right answers at or above 0.70; closest
    to the shipped weights."""
    from app.services import rerank
    shipped = (rerank.W_DENSE, rerank.W_LEXICAL, rerank.W_COVERAGE)
    groups = sorted(set(g.tolist()))

    def score(d, lex, c):
        s = X[:, 0] * d + X[:, 1] * lex + X[:, 2] * c
        s = np.where(X[:, 3] == 1, np.minimum(1.0, s + rerank.AGREEMENT_BONUS), s)
        s = np.minimum(1.0, np.where(X[:, 4] == 1, s * 0.5, s))
        hits = neg_conf = pos_conf = 0
        for gid in groups:
            m = g == gid
            top = int(np.argmax(s[m]))
            has_pos = y[m].any()
            if has_pos and y[m][top] == 1:
                hits += 1
                pos_conf += int(s[m][top] >= TRUST)
            if not has_pos and s[m][top] >= TRUST:
                neg_conf += 1
        return hits, neg_conf, pos_conf

    def stats(t):
        return {"curated_hits": t[0], "heldout_top_above_0.70": t[1],
                "curated_right_above_0.70": t[2]}

    best = None
    for d in np.arange(0.05, 0.951, 0.05):
        for lex in np.arange(0.05, 0.951 - d, 0.05):
            c = 1.0 - d - lex
            if c < 0.049:
                continue
            t = score(d, lex, c)
            dist = abs(d - shipped[0]) + abs(lex - shipped[1]) + abs(c - shipped[2])
            key = (t[0], -t[1], t[2], -dist)
            if best is None or key > best[0]:
                best = (key, (round(float(d), 2), round(float(lex), 2), round(float(c), 2)), t)
    return best[1], {"chosen": stats(best[2]), "shipped": stats(score(*shipped))}


# --------------------------------------------------------------------------
# Candidate (b): domain-adapted embeddings, CPU only.
# --------------------------------------------------------------------------

class IdfWeightedModel:
    """model2vec's own encode (StaticModel._encode_batch: mean of token
    vectors, then L2-normalise) with each token additionally weighted by its
    IDF over this corpus's documents and questions. A token the corpus never
    uses gets the maximum weight, so an off-domain word in a query pulls the
    query vector away from every entry instead of being averaged away."""

    def __init__(self, base, corpus_texts):
        self.base = base
        docs = base.tokenize(corpus_texts)
        n = len(docs)
        df = {}
        for ids in docs:
            for t in set(ids):
                df[t] = df.get(t, 0) + 1
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}
        self.max_idf = math.log(n + 1) + 1.0
        self.dim = base.dim

    def encode(self, sentences, **_kw):
        single = isinstance(sentences, str)
        if single:
            sentences = [sentences]
        out = []
        for ids in self.base.tokenize(sentences, max_length=512):
            if not ids:
                out.append(np.zeros(self.dim))
                continue
            emb = self.base._encode_helper(ids)
            w = np.array([self.idf.get(t, self.max_idf) for t in ids])[:, None]
            v = (emb * w).sum(axis=0) / w.sum()
            out.append(v / (np.linalg.norm(v) + 1e-32))
        arr = np.stack(out)
        return arr[0] if single else arr


def fit_adapter(Q, D, lam):
    """argmin_W ||QW - D||^2 + lam ||W - I||^2, closed form."""
    d = Q.shape[1]
    return np.linalg.solve(Q.T @ Q + lam * np.eye(d), Q.T @ D + lam * np.eye(d))


def encode_norm(model, texts):
    v = np.asarray(model.encode(texts), dtype=np.float32)
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--golden", required=True)
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "own-model-next.json"))
    ap.add_argument("--bootstrap", type=int, default=10000)
    ap.add_argument("--kfold", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20260930)
    args = ap.parse_args()

    golden, corpus, corpus_path = load_golden(Path(args.golden))
    queries = golden["queries"]

    from app.db.connection import init_db
    init_db()
    from app.services import embeddings, rerank, search
    from app.utils.normalizer import normalize_persian
    seed_corpus(corpus)
    search.load_dataset_internal()
    if search.dataset_embedding_index is None:
        sys.exit("the embedding index did not build (is data/models populated?)")

    id_to_idx = {e["id"]: i for i, e in enumerate(search.dataset)}
    curated = [(q, e["id"]) for e in corpus["entries"] for q in e.get("questions", [])]
    norm = lambda s: " ".join(normalize_persian(s, expand_synonyms=False).split())  # noqa: E731
    overlap = sorted({g["q"] for g in queries} & {q for q, _ in curated}
                     | {g["q"] for g in queries if norm(g["q"]) in {norm(q) for q, _ in curated}})
    print(f"corpus={corpus_path} entries={len(search.dataset)} curated_questions={len(curated)} "
          f"golden={len(queries)} golden_in_curated={len(overlap)}")

    results = {"corpus": str(corpus_path), "golden_in_curated": overlap, "variants": {}}
    runs = {}

    def record(name, recs, extra=None):
        runs[name] = recs
        s = summarize(recs)
        if extra:
            s.update(extra)
        results["variants"][name] = {"summary": s}
        print(f"\n== {name}")
        print("   " + json.dumps(s, ensure_ascii=False))

    # ---- baseline ---------------------------------------------------------
    record("baseline", run_harness(queries, search))
    base_idx_score = sorted(
        (r["t1_score"] for r in runs["baseline"] if r["expect"] and r["t1_hit1"]))

    # ---- (a) learned reranker ---------------------------------------------
    original = rerank.rerank
    X, y, g = training_rows(curated, search, id_to_idx)
    print(f"\ntraining rows, dataset ranking (curated + held-out-entry negatives): {len(y)} "
          f"positives={int(y.sum())}")
    Xq, yq = training_rows_questions(search)
    print(f"training rows, questions ranking (leave-one-question-out + held-out-entry "
          f"negatives): {len(yq)} positives={int(yq.sum())}")
    lr = fit_lr(X, y)
    lr_q = fit_lr(Xq, yq)

    def coef_of(m):
        c = dict(zip(FEATURES, [round(float(v), 3) for v in m.coef_[0]]))
        c["intercept"] = round(float(m.intercept_[0]), 3)
        return c
    probe_q = "نمایشگاه کاوند کجا برگزار می شود؟"

    def probe(label):
        """The questions-index ranking for the one confident-wrong serve."""
        nq, cq, texts, dense, lexical = candidates_for(probe_q, search, index="questions")
        top = rerank.rerank(nq, texts, dense, lexical, coverage_query=cq)[:3]
        print(f"   probe[{label}] «{probe_q}» content_tokens={sorted(rerank.content_tokens(cq))}")
        for i, sc, sig in top:
            print(f"      {search.questions_data[i]['dataset_id']:<16} {sc:.4f} "
                  f"«{search.questions_data[i]['question']}» {sig}")
    try:
        print("\n== probe: questions index, shipped formula")
        probe("baseline")
        rerank.rerank = make_learned_rerank(search, original, ds_model=lr)
        record("A1-lr", run_harness(queries, search),
               {"train_rows": int(len(y)), "train_pos": int(y.sum()), "coef": coef_of(lr),
                "applied_to": "dataset ranking only"})
        rerank.rerank = make_learned_rerank(search, original, ds_model=lr, q_model=lr_q)
        record("A1-lr-q", run_harness(queries, search),
               {"q_train_rows": int(len(yq)), "q_train_pos": int(yq.sum()),
                "q_coef": coef_of(lr_q), "applied_to": "dataset AND questions ranking"})
        probe("A1-lr-q")
        gbm = fit_gbm(X, y)
        rerank.rerank = make_learned_rerank(search, original, ds_model=gbm)
        record("A2-gbm", run_harness(queries, search), {"applied_to": "dataset ranking only"})
        gbm_q = fit_gbm(Xq, yq)
        rerank.rerank = make_learned_rerank(search, original, ds_model=gbm, q_model=gbm_q)
        record("A2-gbm-q", run_harness(queries, search),
               {"applied_to": "dataset AND questions ranking"})
        probe("A2-gbm-q")
        # Non-learned controls for A1 (critic finding 5): a monotone rescale
        # of the shipped score, and the three hand weights re-tuned on the
        # curated questions. Neither learns feature weights.
        platt = PlattOnShipped().fit(X, y)
        platt_q = PlattOnShipped().fit(Xq, yq)
        rerank.rerank = make_learned_rerank(search, original, ds_model=platt)
        record("R1-platt", run_harness(queries, search),
               {"slope": round(float(platt.lr.coef_[0][0]), 3),
                "intercept": round(float(platt.lr.intercept_[0]), 3),
                "applied_to": "dataset ranking only"})
        rerank.rerank = make_learned_rerank(search, original, ds_model=platt, q_model=platt_q)
        record("R1-platt-q", run_harness(queries, search),
               {"applied_to": "dataset AND questions ranking"})
    finally:
        rerank.rerank = original

    weights, wstats = select_weights(X, y, g)
    w0 = (rerank.W_DENSE, rerank.W_LEXICAL, rerank.W_COVERAGE)
    try:
        rerank.W_DENSE, rerank.W_LEXICAL, rerank.W_COVERAGE = weights
        record("W-grid", run_harness(queries, search),
               {"weights_dense_lexical_coverage": list(weights), "selection_on_curated": wstats,
                "applied_to": "both rankings (module weights, as the product reads them)"})
    finally:
        rerank.W_DENSE, rerank.W_LEXICAL, rerank.W_COVERAGE = w0

    # A1 with golden in training, k-fold: only held-out folds are scored.
    k = args.kfold
    # Folds are a plain random split of all 67 golden queries: paraphrases of
    # one entry can land in train and test, and golden deny queries train as
    # negatives. Stated in RESEARCH.md; the result is null either way.
    order = np.random.default_rng(args.seed).permutation(len(queries))
    folds = [sorted(order[i::k]) for i in range(k)]
    cv_recs = [None] * len(queries)
    try:
        for f in range(k):
            test = set(folds[f])
            train_pairs = curated + [(queries[i]["q"], queries[i]["expect"])
                                     for i in range(len(queries)) if i not in test]
            Xf, yf, _gf = training_rows(train_pairs, search, id_to_idx)
            rerank.rerank = make_learned_rerank(search, original, ds_model=fit_lr(Xf, yf))
            fold_q = [queries[i] for i in folds[f]]
            for i, r in zip(folds[f], run_harness(fold_q, search)):
                cv_recs[i] = r
            rerank.rerank = original
    finally:
        rerank.rerank = original
    record("A1-lr-cv", cv_recs, {"kfold": k, "note": "held-out folds only"})

    # ---- (b1) IDF-weighted embedding --------------------------------------
    base_model = embeddings._get_model(search.dataset_embedding_index.model_name)
    corpus_texts = list(search.normalized_descriptions) + list(search.normalized_questions)
    idf_model = IdfWeightedModel(base_model, corpus_texts)
    q_texts = [normalize_persian(q) for q, _ in curated]
    d_texts = [search.normalized_descriptions[id_to_idx[e]] for _, e in curated]
    cos_pos_base = float(np.median(np.sum(encode_norm(base_model, q_texts)
                                          * encode_norm(base_model, d_texts), axis=1)))
    cos_pos_idf = float(np.median(np.sum(encode_norm(idf_model, q_texts)
                                         * encode_norm(idf_model, d_texts), axis=1)))
    floor0 = embeddings.COSINE_FLOOR
    try:
        embeddings._model = idf_model
        search.load_dataset_internal()
        record("B1-idf", run_harness(queries, search))
        # Keep the curated questions' median positive cosine at the same
        # calibrated value it had under the shipped model.
        embeddings.COSINE_FLOOR = floor0 + (cos_pos_idf - cos_pos_base)
        record("B1-idf-cal", run_harness(queries, search),
               {"cosine_floor": round(embeddings.COSINE_FLOOR, 4),
                "median_pos_cos_base": round(cos_pos_base, 4),
                "median_pos_cos_idf": round(cos_pos_idf, 4)})
    finally:
        embeddings.COSINE_FLOOR = floor0
        embeddings._model = base_model
        search.load_dataset_internal()

    # ---- (b2) linear query adapter ----------------------------------------
    Q = encode_norm(base_model, q_texts)
    D = search.dataset_embedding_index.matrix[[id_to_idx[e] for _, e in curated]]
    M = search.dataset_embedding_index.matrix
    labels = np.array([id_to_idx[e] for _, e in curated])
    # lambda by leave-one-question-out on the curated questions only.
    loqo = {}
    for lam in (0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0):
        hits = 0
        for i in range(len(Q)):
            m = np.ones(len(Q), bool)
            m[i] = False
            W = fit_adapter(Q[m], D[m], lam)
            v = Q[i] @ W
            hits += int(np.argmax(M @ (v / np.linalg.norm(v))) == labels[i])
        loqo[lam] = hits / len(Q)
    base_loqo = float(np.mean(np.argmax(Q @ M.T, axis=1) == labels))
    lam = max(loqo, key=lambda l: (loqo[l], l))
    W = fit_adapter(Q, D, lam)
    idx_obj = search.dataset_embedding_index
    orig_topk = idx_obj.search_topk

    def adapted_topk(query, k=5):
        v = np.asarray(base_model.encode([query]), dtype=np.float32)[0] @ W
        n = np.linalg.norm(v)
        if n == 0:
            return []
        sims = idx_obj.matrix @ (v / n)
        k = min(k, sims.shape[0])
        order_ = np.argsort(-sims)[:k]
        return [(int(i), embeddings._calibrate(float(sims[i]))) for i in order_]
    try:
        idx_obj.search_topk = adapted_topk
        record("B2-adapter", run_harness(queries, search),
               {"lambda": lam, "loqo_hit1_by_lambda": {str(a): round(b, 3) for a, b in loqo.items()},
                "loqo_hit1_shipped_embedding": round(base_loqo, 3)})
    finally:
        idx_obj.search_topk = orig_topk

    # ---- paired statistics vs baseline ------------------------------------
    print("\n== paired vs baseline (answerable n=%d, bootstrap B=%d, seed=%d)"
          % (sum(1 for q in queries if q["expect"]), args.bootstrap, args.seed))
    for name, recs in runs.items():
        if name == "baseline":
            continue
        # A fresh generator per comparison, so a variant's interval does not
        # depend on how many variants ran before it.
        pipe = paired(runs["baseline"], recs, "hit1", "rr",
                      np.random.default_rng(args.seed), args.bootstrap)
        stage = paired(runs["baseline"], recs, "t1_hit1", "t1_rr",
                       np.random.default_rng(args.seed), args.bootstrap)
        deny = deny_flips(runs["baseline"], recs, "fc_any")
        deny_t1 = deny_flips(runs["baseline"], recs, "t1_fc_trust")
        results["variants"][name].update(
            {"pipeline_vs_baseline": pipe, "t1_vs_baseline": stage,
             "deny_flips_pipeline": deny, "deny_flips_t1_at_0.70": deny_t1})
        print(f"-- {name}")
        for label, p in (("pipeline", pipe), ("tier1", stage)):
            print(f"   {label:8} d_r@1={p['delta_recall_at_1']:+.3f} "
                  f"ci95={p['delta_recall_at_1_ci95']} d_mrr={p['delta_mrr']:+.3f} "
                  f"ci95={p['delta_mrr_ci95']} gained={len(p['gained'])} "
                  f"lost={len(p['lost'])} mcnemar_p={p['mcnemar_p']}")
            for g in p["gained"]:
                print(f"      + {g}")
            for g in p["lost"]:
                print(f"      - {g}")
        print(f"   deny newly confident (pipeline): {deny['newly_confident']}")
        print(f"   deny newly above 0.70 (tier1):   {deny_t1['newly_confident']}")
        print(f"   deny no longer above 0.70 (tier1): {deny_t1['no_longer_confident']}")
        # Where the numbers in RESEARCH.md findings 2, 4 and 5 come from.
        base_by_q = {r["q"]: r for r in runs["baseline"]}
        for r in recs:
            b = base_by_q[r["q"]]
            if r["expect"] and (r["served"], r["tier"]) != (b["served"], b["tier"]):
                print(f"   served changed: «{r['q']}» {b['tier']} {b['served']} {b['score']}"
                      f" -> {r['tier']} {r['served']} {r['score']}")
            if r["expect"] and b["t1_hit1"] and b["t1_score"] >= FALLBACK > r["t1_score"]:
                print(f"   right answer fell below 0.45 at tier1: «{r['q']}» "
                      f"{b['t1_score']} -> {r['t1_score']}")
            if not r["expect"] and (r["t1_score"] >= TRUST) != (b["t1_score"] >= TRUST):
                print(f"   deny tier1 crossed 0.70: «{r['q']}» {b['t1_score']} -> {r['t1_score']}")

    # Smallest win the paired test can detect here (critic finding 3).
    n_ans = sum(1 for q in queries if q["expect"])
    need = next(w for w in range(1, n_ans + 1) if mcnemar_exact(w, 0) < 0.05)
    print(f"\n== detectable gain: exact McNemar needs >= {need} wins and 0 losses "
          f"(p={mcnemar_exact(need, 0):.4f}) = {need / n_ans:.3f} recall@1 on n={n_ans}; "
          f"{need - 1} wins and 0 losses gives p={mcnemar_exact(need - 1, 0):.4f}")

    results["per_query"] = runs
    results["baseline_t1_correct_scores"] = base_idx_score
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport: {args.out}", flush=True)


if __name__ == "__main__":
    main()
    # Skip interpreter teardown. Freeing the tokenizer of the 128M-token
    # vocabulary hung for minutes on this Mac (2026-09-30, observed on a
    # run_eval.py process that had already written its report), and this
    # script holds the shared heavy lock until it exits. Everything is
    # already written and flushed above.
    sys.stdout.flush()
    os._exit(0)
