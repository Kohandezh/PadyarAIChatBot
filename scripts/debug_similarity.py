"""Debug TF-IDF similarity matching against the live dataset.

Run from the project root:  python scripts/debug_similarity.py [query]

Loads the dataset from SQLite (the single source of truth) — the same data the
chatbot matches against — so results mirror production behavior.
"""

import os
import sys

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

# Make the `app` package importable no matter where this is launched from.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.db.connection import get_db_connection

sys.stdout.reconfigure(encoding="utf-8")

conn = get_db_connection()
rows = conn.execute("SELECT id, title, text FROM dataset ORDER BY id").fetchall()
conn.close()

if not rows:
    print("Dataset is empty. Add entries via the admin panel first.")
    sys.exit(0)

dataset = [dict(r) for r in rows]
descriptions = [f"{item.get('title', '')} {item.get('text', '')}" for item in dataset]

vectorizer = TfidfVectorizer(ngram_range=(1, 3), sublinear_tf=True)
tfidf_matrix = vectorizer.fit_transform(descriptions)

query = sys.argv[1] if len(sys.argv) > 1 else "قهوه"
query_vec = vectorizer.transform([query])
cosine_similarities = cosine_similarity(query_vec, tfidf_matrix).flatten()

best_idx = int(np.argmax(cosine_similarities))
print(f"Query: {query}")
print(f"Entries: {len(dataset)}")
print(f"Best match: {dataset[best_idx]['id']} — {dataset[best_idx]['title']}")
print(f"Best score: {cosine_similarities[best_idx]:.4f}")
