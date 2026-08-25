"""The two admin tools the leads page owes an operator mid-exhibition:

  * delete a colleague (همکار غرفه) who captured nothing — one with history
    is refused, because their name hangs on every registration they made;
  * add a company that is missing from the list, so the visitor standing at
    that booth has something to select. The row is born with NO answer text —
    nothing reaches the chatbot without the review queue, whoever typed it.
"""
import pytest


@pytest.fixture
def visitor(client):
    res = client.post("/admin/api/leads/visitors", json={"name": "تست"})
    assert res.status_code == 200, res.text
    return res.json()


def test_visitor_with_zero_captures_can_be_deleted(client, visitor):
    res = client.delete(f"/admin/api/leads/visitors/{visitor['id']}")
    assert res.status_code == 200, res.text
    roster = client.get("/admin/api/leads/visitors").json()["visitors"]
    assert visitor["id"] not in [v["id"] for v in roster]


def test_unknown_visitor_delete_is_404(client):
    res = client.delete("/admin/api/leads/visitors/does-not-exist")
    assert res.status_code == 404, res.text


def test_visitor_with_leads_is_refused(client, visitor, conn):
    conn.execute(
        "INSERT INTO dataset (id, title, text) VALUES ('c1', 'شرکت حذف', 'متن')")
    conn.execute(
        "INSERT INTO company_leads (id, dataset_id, company_name, visitor_id,"
        " first_name, last_name, position, phone, phone_hash, status, created_at)"
        " VALUES ('lead1', 'c1', 'شرکت حذف', ?, 'نام', '', '', '09120000000', 'h',"
        " 'unverified', now())", (visitor["id"],))
    conn.commit()
    res = client.delete(f"/admin/api/leads/visitors/{visitor['id']}")
    assert res.status_code == 409, res.text
    roster = client.get("/admin/api/leads/visitors").json()["visitors"]
    assert visitor["id"] in [v["id"] for v in roster]


def test_added_company_is_found_by_the_visitor_search(client, visitor):
    res = client.post("/admin/api/leads/companies", json={"title": "شرکت غایب"})
    assert res.status_code == 200, res.text
    dataset_id = res.json()["dataset_id"]
    assert dataset_id.startswith("booth-")

    # The operator hands the booth the same personal link as any other.
    code = visitor["link"].rsplit("/", 1)[1]
    res = client.get(f"/v/{code}", follow_redirects=False)
    client.cookies.set("padyar_visitor", res.cookies.get("padyar_visitor"))
    res = client.get("/api/leads/companies?q=%D8%BA%D8%A7%DB%8C%D8%A8")
    ids = [c["id"] for c in res.json()["companies"]]
    assert dataset_id in ids


def test_added_company_is_born_with_no_answer_text(client, conn):
    res = client.post("/admin/api/leads/companies", json={"title": "شرکت بی‌متن"})
    assert res.status_code == 200, res.text
    row = conn.execute("SELECT text, position FROM dataset WHERE id = ?",
                       (res.json()["dataset_id"],)).fetchone()
    assert row["text"] == ""          # nothing unreviewed on the chatbot
    assert row["position"] is not None  # stable display order, like the editor


def test_duplicate_company_is_refused_by_its_real_name(client, conn):
    conn.execute(
        "INSERT INTO dataset (id, title, text) VALUES ('c2', 'شرکت تکراری', 'متن')")
    conn.commit()
    # A half-space apart: normalisation must catch it, not the exact string.
    res = client.post("/admin/api/leads/companies",
                      json={"title": "شرکت\u200cتکراری"})
    assert res.status_code == 409, res.text
    assert "شرکت تکراری" in res.json()["detail"]


def test_empty_title_is_refused(client):
    res = client.post("/admin/api/leads/companies", json={"title": "  "})
    assert res.status_code == 400, res.text
