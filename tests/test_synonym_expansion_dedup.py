"""Synonym expansion must not repeat the source inside its own replacement.

THE DEFECT
----------
`_expansions()` built `source -> "source-target-words + synonyms"`, so one
word became its own first token and appeared several times:

    هزینه  →  هزینه هزینه قیمت نرخ مبلغ مبلغ نرخ هزینه قیمت نرخ مبلغ مبلغ نرخ

Measured on the live corpus, this pushed the expanded query outside the
embedding model's comfortable region (dense dropped to 0.000 on
«هزینه غرفه چقدر است؟») and inflated term frequencies for BM25/TF-IDF
without adding meaning. The retrieval diagnostic run of 2026-08-26 pinned
the behaviour; these tests hold the fix.

The right shape: the replacement carries ONLY words the source itself does
not already contain.
"""
import pytest

from app.utils.normalizer import _expansions, normalize_persian


@pytest.fixture(autouse=True)
def _synonyms(monkeypatch):
    import app.utils.normalizer as normalizer
    monkeypatch.setattr(normalizer, "active_synonyms", [
        ("هزینه", "هزینه قیمت نرخ مبلغ"),
        ("هزینه", "مبلغ نرخ"),
        ("غرفه", "غرفه استند booth فضای نمایشگاهی"),
        ("شرکت", "شرکت بنگاه مجموعه"),
        # The live cascade pair: each row's TARGET contains the other's SOURCE.
        ("قیمت", "هزینه تعرفه"),
    ])
    monkeypatch.setattr(normalizer, "_expansions_cache", ((), ()))
    monkeypatch.setattr(normalizer, "_expansion_pattern_cache", ((), None))
    yield
    monkeypatch.setattr(normalizer, "_expansions_cache", ((), ()))
    monkeypatch.setattr(normalizer, "_expansion_pattern_cache", ((), None))


def test_replacement_does_not_repeat_the_source_token():
    built = dict(_expansions())
    assert built["هزینه"].split().count("هزینه") == 1, \
        "the source word may appear exactly once in its own replacement"
    assert built["غرفه"].split().count("غرفه") == 1


def test_replacement_merges_all_targets_without_duplicates():
    built = dict(_expansions())
    # Two rows for هزینه: «هزینه قیمت نرخ مبلغ» + «مبلغ نرخ». The source
    # once, then exactly the three synonym words — nothing doubled.
    assert built["هزینه"].split() == ["هزینه", "قیمت", "نرخ", "مبلغ"]


def test_normalize_persian_leaves_no_doubled_expansion():
    out = normalize_persian("هزینه غرفه چقدر است؟")
    tokens = out.split()
    # The visitor said هزینه once and غرفه once; expansion may ADD synonym
    # words but must not repeat either source word.
    assert tokens.count("هزینه") == 1
    assert tokens.count("غرفه") == 1
    # And the synonyms actually arrived.
    assert "قیمت" in tokens and "استند" in tokens


def test_replacement_does_not_cascade_across_sources():
    """The live-table defect: «هزینه» expands to «…قیمت…», then the «قیمت»
    row fired on the word the first replacement just INSERTED and re-inserted
    «هزینه». One pass over the original tokens, nothing re-fires."""
    out = normalize_persian("هزینه غرفه")
    tokens = out.split()
    assert tokens.count("هزینه") == 1, out
    assert tokens.count("قیمت") == 1, out


def test_unexpanded_form_is_untouched():
    assert normalize_persian("هزینه غرفه", expand_synonyms=False) == "هزینه غرفه"
