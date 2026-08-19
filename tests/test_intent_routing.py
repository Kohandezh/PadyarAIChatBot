"""Regression tests for the intent-routing fix.

The customer reported the chatbot confidently answering with unrelated videos:
a polite opener ("سلام، ...") was swallowed by a greeting entry, and a cost
question that named a procedure ("هزینه آب مروارید") was pulled to the procedure
video. The fix has three deterministic, offline-testable pieces:

  * greetings are stripped from the query before matching (and a *pure* greeting
    is preserved so it still matches the intro),
  * a 1-2 word query can no longer fake a near-perfect token-overlap score, and
  * cost phrasings route to the cost video via the questions index.

The GPT-classification leg of the pipeline is covered separately (it needs the
live API); these tests pin the parts that must hold without any network call.
"""
from app.utils.normalizer import strip_leading_greeting


class TestStripLeadingGreeting:
    def test_pure_greeting_is_preserved(self):
        # A message that is only a greeting must stay whole so it can match the
        # intro — we must NOT turn it into an empty query.
        for g in ["سلام", "سلام.", "سلام علیکم", "درود", "وقت بخیر"]:
            core, only = strip_leading_greeting(g)
            assert only is True
            assert core == g

    def test_greeting_prefix_is_removed_from_real_question(self):
        core, only = strip_leading_greeting("سلام هزینه جراحی فمتو رو میخواستم بدونم")
        assert only is False
        assert core == "هزینه جراحی فمتو رو میخواستم بدونم"

    def test_greeting_with_punctuation_separator(self):
        core, only = strip_leading_greeting("سلام، هزینه فمتو چقدره؟")
        assert only is False
        assert core.startswith("هزینه")
        assert "سلام" not in core

    def test_longer_greeting_matched_before_shorter(self):
        core, only = strip_leading_greeting("سلام علیکم آب مروارید دارم")
        assert only is False
        assert core == "آب مروارید دارم"

    def test_no_greeting_is_unchanged(self):
        core, only = strip_leading_greeting("هزینه عمل آب مروارید")
        assert only is False
        assert core == "هزینه عمل آب مروارید"

    def test_empty_input(self):
        core, only = strip_leading_greeting("")
        assert only is False
        assert core == ""


def test_short_overlap_no_longer_inflates_confidence(tmp_path, monkeypatch):
    """A short query that only *partially* overlaps a short title must score by
    real similarity, not the old Jaccard shortcut that returned 0.95+ whenever
    token overlap reached 0.6 (now gated behind >= 3 shared tokens).

    Query "لیزیک چشم درد" shares 2 tokens with title "لیزیک چشم" (Jaccard 0.67).
    The old code boosted any overlap >= 0.6 to >= 0.95; with the >= 3 shared-token
    gate it now falls back to the genuine (lower) TF-IDF score.
    """
    import app.config as cfg
    import app.db.connection as dbc

    # get_db_connection()/init_db() re-read app.config.DB_PATH at call time.
    db_file = tmp_path / "search.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(db_file))
    dbc.init_db()

    conn = dbc.get_db_connection()
    # Isolate the corpus. The second row puts "درد" in the TF-IDF vocabulary so
    # the query's extra token isn't silently dropped (which would force cosine=1).
    # Clear synonyms so expansion can't inflate the shared-token count and mask
    # the guard (e.g. "لیزیک" -> "لیزر لیزیک" would turn 2 shared tokens into 3).
    conn.execute("DELETE FROM dataset")
    conn.execute("DELETE FROM synonyms")
    conn.executemany(
        "INSERT INTO dataset (id, title, text, video_url) VALUES (?, ?, ?, ?)",
        [
            ("vid_x", "لیزیک چشم", "پاسخ", "/v.mp4"),
            ("vid_y", "درد شدید مزمن", "پاسخ", "/v2.mp4"),
        ],
    )
    conn.commit()
    conn.close()

    from app.services import search

    search.load_dataset_internal()

    entry, score = search.find_best_match("لیزیک چشم درد")

    assert entry["id"] == "vid_x"      # still finds the right (closest) entry
    assert score < 0.95                # but no longer inflated to the 0.95+ band


def test_branch_address_synonym_disambiguates(tmp_path, monkeypatch):
    """Branch addresses are disambiguated only by a location word. Users type it
    joined ("شهرری") while the title has it spaced ("شهر ری") — to TF-IDF those
    are unrelated tokens, so the query used to collide with another branch on the
    generic words (آدرس/کلینیک/نور) and return the wrong clinic.

    The synonym شهرری -> شهر ری makes the location word matchable, routing the
    query to the correct Rey branch instead of the Motahari clinic.
    """
    import app.config as cfg
    import app.db.connection as dbc

    db_file = tmp_path / "addr.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(db_file))
    dbc.init_db()

    conn = dbc.get_db_connection()
    conn.execute("DELETE FROM dataset")
    conn.execute("DELETE FROM synonyms")
    conn.executemany(
        "INSERT INTO dataset (id, title, text, video_url) VALUES (?, ?, ?, '')",
        [
            ("vid_reyAddress", "آدرس کلینیک نور شهر ری", "شهرری ..."),
            ("vid_location", "آدرس دقیق بیمارستان نور کجاست", "تهران ولیعصر ..."),
            ("vid_motahari", "آدرس کلینیک نور مطهری", "مطهری ..."),
        ],
    )
    conn.commit()
    conn.close()

    from app.services import search

    query = "آدرس کلینیک نور شهرري"  # joined spelling, Arabic yeh

    # Without the synonym, the joined location word is invisible -> wrong branch.
    search.load_dataset_internal()
    wrong, _ = search.find_best_match(query)
    assert wrong["id"] != "vid_reyAddress"

    # With the synonym, it routes to the correct Rey branch.
    conn = dbc.get_db_connection()
    conn.execute("INSERT INTO synonyms (source, target) VALUES (?, ?)", ("شهرری", "شهر ری"))
    conn.commit()
    conn.close()
    search.load_dataset_internal()
    fixed, score = search.find_best_match(query)
    assert fixed["id"] == "vid_reyAddress"
    assert score >= cfg.TRUSTED_MATCH_THRESHOLD
