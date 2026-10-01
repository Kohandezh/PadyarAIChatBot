#!/usr/bin/env python3
"""Load or train this install's intent model, record it, print its sidecar.

Run from the install's root, with the install's environment loaded, so it
reads the same database the app reads:

    set -a && . ./.env && set +a && .venv/bin/python scripts/train/train_intent_model.py

What it does: one reindex, exactly as the running app does it
(app/services/search.load_dataset_internal), with recording turned on:
  - the stored model is loaded when it is provably the model for this data
    (same training fingerprint, weights match their sha256). Retraining would
    produce the same bytes, so nothing is trained and the version stays;
  - otherwise the model is trained through intent.train (the only training
    implementation) and recorded in INTENT_MODEL_DIR as the next version;
  - the sidecar JSON is printed on stdout.

To force a fresh training run, move ONLY intent-classifier.npz away. Keep
intent-classifier.history.jsonl: the next version number is counted from it,
and without it the count starts again at 1.

Measuring on a corpus file instead of the install (the model card does this):

    .venv/bin/python scripts/train/train_intent_model.py --corpus data/eval/corpus.json

--corpus loads the file's entries, questions and synonyms into a throwaway
SQLite database in a new temp directory, and records the model there too.
The install's database and INTENT_MODEL_DIR are never read or written. The
temp directory is printed on stderr so the files can be checked by hand.
The file shape is {"synonyms": [[a, b], ...], "entries": [{"id", "title",
"text", "title_en", "text_en", "questions": [...]}, ...]}.

Exit 0 when a recorded model describes this data. Exit 1 when there is no
model (too little data, no embeddings) or the files could not be written; the
reason goes to stderr and the app log.
"""
import argparse
import json
import os
import sys
import tempfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def load_corpus(path: str) -> None:
    """Write a corpus file into whatever database app.config points at."""
    from app.db.connection import get_db_connection, init_db

    with open(path, "r", encoding="utf-8") as fh:
        corpus = json.load(fh)
    init_db()
    conn = get_db_connection()
    for table in ("dataset", "questions", "synonyms"):
        conn.execute(f"DELETE FROM {table}")
    for entry in corpus["entries"]:
        conn.execute(
            "INSERT INTO dataset (id, title, text, video_url, title_en, text_en) "
            "VALUES (?, ?, ?, '', ?, ?)",
            (entry["id"], entry["title"], entry["text"],
             entry.get("title_en", ""), entry.get("text_en", "")))
        for question in entry["questions"]:
            conn.execute("INSERT INTO questions (question, dataset_id, video_url) "
                         "VALUES (?, ?, '')", (question, entry["id"]))
    for source, target in corpus.get("synonyms", []):
        conn.execute("INSERT INTO synonyms (source, target) VALUES (?, ?)", (source, target))
    conn.commit()
    conn.close()


def _use_a_throwaway_install() -> str:
    """Point every path this run touches at a new temp directory. Must run
    before the first `app` import: app.config reads the environment once."""
    if "app.config" in sys.modules:
        sys.exit("--corpus configures its own database before any app import; "
                 "run it as its own command")
    tmp = tempfile.mkdtemp(prefix="padyar-intent-corpus-")
    os.environ["DB_BACKEND"] = "sqlite"
    os.environ["DB_PATH"] = os.path.join(tmp, "corpus.db")
    os.environ["LOGS_DB_PATH"] = os.path.join(tmp, "application_logs.db")
    os.environ["INTENT_MODEL_DIR"] = os.path.join(tmp, "intent-model")
    os.environ["SEED_DEFAULT_CONTENT"] = "false"
    return tmp


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", help="train on this corpus JSON file in a "
                        "throwaway database instead of the install's")
    args = parser.parse_args(argv)
    if args.corpus:
        tmp = _use_a_throwaway_install()
        print(f"throwaway install: {tmp}", file=sys.stderr)
        load_corpus(args.corpus)

    from app.services import intent, search

    intent.enable_recording()
    try:
        search.load_dataset_internal()
    finally:
        intent.disable_recording()

    classifier = search.intent_classifier
    if classifier is None:
        print("No model: too little data (at least "
              f"{intent.HYPERPARAMETERS['min_classes']} answers and "
              f"{intent.HYPERPARAMETERS['min_samples']} questions), no question "
              "embeddings, or training failed. See the log above.", file=sys.stderr)
        return 1
    if classifier.model_version is None:
        print("The model trained but could not be recorded in "
              f"{intent.artifact_paths()[0]!r}. See the log above.", file=sys.stderr)
        return 1

    how = "loaded, data unchanged" if classifier.loaded_from_artifact else "trained"
    print(f"model_version {classifier.model_version} ({how})", file=sys.stderr)
    with open(intent.artifact_paths()[1], "r", encoding="utf-8") as fh:
        meta = json.load(fh)
    print(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
