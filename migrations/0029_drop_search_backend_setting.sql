-- Drop the stray `search_backend` settings row. The second, selectable
-- lexical engine that key pointed at is gone; retrieval has been
-- single-engine (BM25 + local model2vec embeddings fused by the reranker)
-- since. Nothing under app/ reads the key any more — a row that survives
-- an upgrade only lies to an operator reading the settings table by hand.
--
-- WHAT THIS DESTROYS: nothing. The key has no reader, so deleting it
-- changes no behavior. A plain DELETE is idempotent: on a database that
-- never carried the row it is a no-op.

DELETE FROM app.settings WHERE key = 'search_backend';
