"""Dataset CRUD through the real HTTP surface, on real PostgreSQL.

The duplicate case is the reason this file exists: `app/routers/dataset.py`
caught `sqlite3.IntegrityError`, which PostgreSQL never raises, so on the
production backend a duplicate id was a 500 with a traceback instead of the
Persian "این شناسه قبلاً وجود دارد". The SQLite suite could not see it.
"""
import pytest

PERSIAN_ID = "اینوتکس-تست-یونیکد"


def _create(client, item_id, **extra):
    body = {"id": item_id, "title": "عنوان", "text": "متن"}
    body.update(extra)
    return client.post("/admin/api/dataset", json=body)


# ── Create ──────────────────────────────────────────────────────────────

def test_create_returns_201_shape_and_persists(client, conn):
    res = _create(client, "pg-create", title="اینوتکس چیست", text="یک رویداد")
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "created"}

    row = conn.execute("SELECT title, text, position FROM dataset WHERE id = ?",
                       ("pg-create",)).fetchone()
    assert row["title"] == "اینوتکس چیست"
    assert row["position"] is not None


def test_a_persian_id_round_trips_intact(client, conn):
    assert _create(client, PERSIAN_ID).status_code == 200
    ids = [r["id"] for r in client.get("/admin/api/dataset").json()]
    assert PERSIAN_ID in ids
    row = conn.execute("SELECT id FROM dataset WHERE id = ?",
                       (PERSIAN_ID,)).fetchone()
    assert row["id"] == PERSIAN_ID


def test_text_containing_a_question_mark_and_a_percent_survives(client, conn):
    """The adapter rewrites `?` -> `%s` and doubles `%`. A knowledge base full
    of Persian questions is exactly where that goes wrong."""
    text = "۱۰۰% از بازدیدکنندگان؟ بله — اینوتکس چیست؟"
    assert _create(client, "pg-punct", text=text).status_code == 200
    row = conn.execute("SELECT text FROM dataset WHERE id = ?",
                       ("pg-punct",)).fetchone()
    assert row["text"] == text


# ── Duplicate ───────────────────────────────────────────────────────────

def test_a_duplicate_id_is_a_controlled_409_not_a_500(client):
    assert _create(client, "pg-dup").status_code == 200
    res = _create(client, "pg-dup", title="دیگر")
    assert res.status_code == 409, f"expected a controlled refusal, got {res.status_code}"
    assert res.json()["detail"] == "ID already exists"


def test_a_duplicate_persian_id_is_also_a_409(client):
    assert _create(client, PERSIAN_ID).status_code == 200
    assert _create(client, PERSIAN_ID).status_code == 409


def test_the_original_row_is_not_overwritten_by_the_refused_duplicate(client, conn):
    _create(client, "pg-dup2", title="اصلی")
    _create(client, "pg-dup2", title="مهاجم")
    row = conn.execute("SELECT title FROM dataset WHERE id = ?",
                       ("pg-dup2",)).fetchone()
    assert row["title"] == "اصلی"


def test_the_api_still_works_after_a_refused_duplicate(client):
    """The cascade check at HTTP level: a 409 must not poison later requests."""
    _create(client, "pg-dup3")
    assert _create(client, "pg-dup3").status_code == 409
    assert client.get("/admin/api/dataset").status_code == 200
    assert _create(client, "pg-after-dup").status_code == 200
    assert client.get("/api/dataset").status_code == 200


# ── Update / delete ─────────────────────────────────────────────────────

def test_update_changes_the_row(client, conn):
    _create(client, "pg-upd")
    res = client.put("/admin/api/dataset/pg-upd",
                     json={"title": "به‌روزشده", "text": "متن تازه",
                           "video_url": "", "title_en": "Updated", "text_en": "New"})
    assert res.status_code == 200
    row = conn.execute("SELECT title, title_en FROM dataset WHERE id = ?",
                       ("pg-upd",)).fetchone()
    assert row["title"] == "به‌روزشده"
    assert row["title_en"] == "Updated"


def test_updating_a_missing_row_is_404(client):
    res = client.put("/admin/api/dataset/nope", json={"title": "x", "text": "y"})
    assert res.status_code == 404


def test_delete_removes_the_row(client, conn):
    _create(client, "pg-del")
    assert client.delete("/admin/api/dataset/pg-del").status_code == 200
    assert conn.execute("SELECT count(*) AS n FROM dataset WHERE id = ?",
                        ("pg-del",)).fetchone()["n"] == 0


def test_deleting_a_missing_row_is_404(client):
    assert client.delete("/admin/api/dataset/nope").status_code == 404


def test_delete_then_recreate_the_same_id_succeeds(client):
    _create(client, "pg-recycle")
    client.delete("/admin/api/dataset/pg-recycle")
    assert _create(client, "pg-recycle").status_code == 200


# ── Ordering ────────────────────────────────────────────────────────────

def test_new_entries_keep_creation_order_on_the_public_endpoint(client):
    """`/api/dataset` used to `ORDER BY rowid` — a SQLite pseudo-column, so a
    hard 500 here. It now orders by `position`, which must reflect creation
    order rather than the alphabetical order of the ids."""
    for item_id in ("zz-first", "mm-second", "aa-third"):
        assert _create(client, item_id).status_code == 200
    served = [r["id"] for r in client.get("/api/dataset").json()]
    assert served == ["zz-first", "mm-second", "aa-third"]


def test_positions_are_spaced_so_an_entry_can_be_slotted_between(client, conn):
    _create(client, "pos-a")
    _create(client, "pos-b")
    rows = conn.execute("SELECT id, position FROM dataset ORDER BY position").fetchall()
    positions = [r["position"] for r in rows]
    assert positions == sorted(positions)
    assert positions[1] - positions[0] >= 2


def test_an_explicitly_positioned_row_sorts_where_it_is_put(client, conn):
    _create(client, "ord-1")
    _create(client, "ord-2")
    conn.execute("UPDATE dataset SET position = ? WHERE id = ?", (1, "ord-2"))
    conn.commit()
    served = [r["id"] for r in client.get("/api/dataset").json()]
    assert served == ["ord-2", "ord-1"]


def test_a_row_with_no_position_sorts_last(client, conn):
    _create(client, "ord-a")
    conn.execute("INSERT INTO dataset (id, title, text) VALUES (?,?,?)",
                 ("ord-null", "بدون جایگاه", "x"))
    conn.commit()
    served = [r["id"] for r in client.get("/api/dataset").json()]
    assert served[-1] == "ord-null"


@pytest.mark.parametrize("bad_body", [{}, {"id": "   "}])
def test_a_missing_id_is_rejected_before_touching_the_database(client, bad_body):
    assert client.post("/admin/api/dataset", json=bad_body).status_code == 400
