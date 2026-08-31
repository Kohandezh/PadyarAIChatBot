"""Unit level: the rules the signup flow enforces, against the real
taxonomy file. The API contract around them is tests/test_signup_flow.py."""
import pytest

from app.services import signup


@pytest.fixture()
def row():
    return {"first_name": "زهرا", "last_name": "کریمی", "job": "خبرنگار / رسانه",
            "position": "کارشناس", "interests": "هوش مصنوعی"}


def test_a_complete_valid_row_is_complete(row):
    assert signup.is_complete(row)


def test_missing_field_is_incomplete(row):
    assert not signup.is_complete({**row, "job": ""})


def test_stale_label_is_incomplete(row):
    """Admin renamed the label: the visitor re-answers one question."""
    assert not signup.is_complete({**row, "job": "ژورنالیست"})


def test_student_with_a_title_is_incomplete(row):
    assert not signup.is_complete({**row, "job": "دانش‌آموز"})


def test_student_with_no_title_is_complete(row):
    assert signup.is_complete({**row, "job": "دانش‌آموز",
                               "position": "سمت سازمانی ندارم"})


def test_pending_step_order_and_skip(row):
    """name/job/position filled ⇒ interests asked; the no-position job
    offers exactly one chip."""
    p = signup.pending_step({**row, "interests": ""}, "fa")
    assert p["step"]["key"] == "interests" and p["step"]["multi"] is True
    q = signup.pending_step({"first_name": "", "last_name": "", "job": "دانش‌آموز",
                             "position": "", "interests": ""}, "fa")
    assert q["step"]["key"] == "name"
    p2 = signup.pending_step({"first_name": "آ", "last_name": "", "job": "دانش‌آموز",
                              "position": "", "interests": ""}, "fa")
    assert p2["step"]["key"] == "position"
    assert [o["label"] for o in p2["step"]["options"]] == ["سمت سازمانی ندارم"]


def test_pending_step_reasks_stale_job(row):
    """The 403 loop: every field present, one stale (admin renamed the
    label). pending_step must re-offer that field, not claim complete."""
    stale = {**row, "job": "ژورنالیست"}
    assert not signup.is_complete(stale)
    p = signup.pending_step(stale, "fa")
    assert p["step"]["key"] == "job"


def test_pending_step_reasks_stale_position(row):
    stale = {**row, "position": "مدیرکل"}
    p = signup.pending_step(stale, "fa")
    assert p["step"]["key"] == "position"


def test_pending_step_reasks_position_inconsistent_with_job(row):
    """Both labels are valid, but a student cannot hold a title: the
    stored pair is inconsistent, so position is asked again."""
    inconsistent = {**row, "job": "دانش‌آموز", "position": "کارشناس"}
    p = signup.pending_step(inconsistent, "fa")
    assert p["step"]["key"] == "position"
    assert [o["label"] for o in p["step"]["options"]] == ["سمت سازمانی ندارم"]


def test_pending_step_fail_open_without_taxonomy(monkeypatch):
    """Unconfigured install: a filled row is complete — no re-ask loop."""
    from app.services import taxonomy
    monkeypatch.setattr(taxonomy, "document",
                        lambda: dict(taxonomy._MINIMUM))
    filled = {"first_name": "آ", "job": "هر چیزی",
              "position": "هر چیزی", "interests": "هر چیزی"}
    assert signup.pending_step(filled, "fa") == {"complete": True}


def test_validate_answer_accepts_list_labels_only():
    ok, msg, fields = signup.validate_answer({}, "job", "دانش‌آموز")
    assert ok and fields["job"] == "دانش‌آموز"
    assert fields["position"] == "سمت سازمانی ندارم"   # auto-written
    ok, msg, _ = signup.validate_answer({}, "job", "فضانورد")
    assert not ok and msg
    ok, msg, fields = signup.validate_answer(
        {"job": "دانش‌آموز"}, "position", "کارشناس")
    assert not ok and msg
    ok, msg, fields = signup.validate_answer({}, "interests", "هوش مصنوعی، فضانورد")
    assert not ok and "فضانورد" in msg
    ok, msg, fields = signup.validate_answer({}, "interests",
                                             "هوش مصنوعی، به آموزش و یادگیری هوش مصنوعی علاقه دارم")
    assert ok   # flags are valid interest items


def test_validate_answer_name_splits_and_caps():
    ok, msg, fields = signup.validate_answer({}, "name", "  زهرا   کریمی نژاد  ")
    assert ok and fields == {"first_name": "زهرا", "last_name": "کریمی نژاد"}


def test_validate_profile_edit():
    assert signup.validate_profile_edit("دانش‌آموز", "کارشناس", "هوش مصنوعی")
    assert not signup.validate_profile_edit("دانش‌آموز", "سمت سازمانی ندارم", "هوش مصنوعی")
    assert signup.validate_profile_edit("خبرنگار / رسانه", "کارشناس", "فضانورد")


def test_sanitize_registration_drops_invalid_keeps_valid():
    out = signup.sanitize_registration({
        "job": "دانش‌آموز", "position": "کارشناس", "interests": "هوش مصنوعی، فضانورد"})
    assert out["job"] == "دانش‌آموز"
    assert out["position"] == ""      # inconsistent with the job ⇒ dropped
    assert out["interests"] == "هوش مصنوعی"


def test_fail_open_without_taxonomy(monkeypatch):
    """An install with no taxonomy file must not lock anybody out."""
    from app.services import taxonomy
    monkeypatch.setattr(taxonomy, "document",
                        lambda: dict(taxonomy._MINIMUM))
    assert signup.is_complete({"first_name": "آ", "job": "هر چیزی",
                               "position": "هر چیزی", "interests": "هر چیزی"})
