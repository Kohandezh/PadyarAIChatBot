"""Debug retrieval scoring against the live dataset.

Run from the project root:  python scripts/debug_similarity.py [query]

Loads the dataset through the production retriever (app/services/search) —
Persian normalization, BM25 + embedding candidates, feature reranker — so the
numbers mirror what /chat actually scores.
"""

import os
import sys

# Make the `app` package importable no matter where this is launched from.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

sys.stdout.reconfigure(encoding="utf-8")

from app.services import search

search.load_dataset_internal()

if not search.dataset:
    print("Dataset is empty. Add entries via the admin panel first.")
    sys.exit(0)

query = sys.argv[1] if len(sys.argv) > 1 else "قهوه"

print(f"Query: {query}")
print(f"Entries: {len(search.dataset)}")

entry, score = search.find_best_match(query)
if entry:
    print(f"Best match: {entry['id']} — {entry['title']}")
    print(f"Best score: {score:.4f}")
else:
    print("Best match: none (no retriever reached its floor)")

print("Top matches:")
for e, s, signals in search.find_top_matches(query, k=5):
    print(f"  {s:.4f}  {e['id']} — {e['title']}  {signals}")
