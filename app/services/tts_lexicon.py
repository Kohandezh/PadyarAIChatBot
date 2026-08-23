"""How this installation wants particular words read aloud.

Persian does not write its short vowels, so a written word can carry more than
one reading and only the sentence says which: «دور» is both `duur` (far) and
`dowr` (a turn), «کرم» is both `kerm` (a worm) and `kerem` (a cream). The model
has to guess, and on the exhibition dataset it guessed «دور» wrong every time.

No amount of training removes that. A person reading the same word cold guesses
too. What removes it is someone who knows the text saying, once, how it goes —
and that is all this file is: a list of "when you see this, read it as that",
kept in the customer's own database and applied to the text on its way to the
speech engine.

It is deliberately NOT phonetics. An operator writes «دوور», not `/duːr/`,
because the thing they can check is whether it sounds right when they press
play, and the thing they cannot do is learn IPA.

WHOLE WORDS ONLY. The INOTEX narration contains «دوربینِ شناخته‌شده», and a
plain string replace would turn a rule about «دور» into «دوووربین». The one
rule that matters here is that a rule fires on a word, never inside one.
"""
import json
import re
from typing import List, Tuple

from app.db.queries import get_setting, set_setting

SETTING_KEY = "tts_lexicon"

# Enough for a specialist vocabulary — the eye dataset needs perhaps a dozen —
# without letting a paste of the whole knowledge base become a regex.
MAX_ENTRIES = 200
MAX_WORD_CHARS = 80

# A word does not end in the middle of a word. Persian letters and digits are
# \w, so \w carries most of this; ZWNJ is added because «لایه‌های» is one word
# to a reader and \w does not know that.
_EDGE = r"[\w‌]"


def _rows(raw) -> List[Tuple[str, str]]:
    """Whatever is in the settings row → clean (written, spoken) pairs."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        # A hand-edited row must not take the speech engine down with it.
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict):
            written, spoken = item.get("written"), item.get("spoken")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            written, spoken = item
        else:
            continue
        written = str(written or "").strip()
        spoken = str(spoken or "").strip()
        if written and spoken:
            out.append((written, spoken))
    return out


def load() -> List[dict]:
    """The saved list, for the admin page."""
    return [{"written": w, "spoken": s}
            for w, s in _rows(get_setting(SETTING_KEY, ""))]


def save(entries) -> List[dict]:
    """Validate and store. Raises ValueError with a Persian message.

    The message is Persian because it is shown to the operator verbatim; this
    is the same contract every other admin endpoint in this app keeps.
    """
    cleaned, seen = [], set()
    for item in entries or []:
        written = str((item or {}).get("written", "")).strip()
        spoken = str((item or {}).get("spoken", "")).strip()
        if not written and not spoken:
            continue  # an empty row is how the page says "I added one, never mind"
        if not written or not spoken:
            raise ValueError("هر ردیف باید هر دو ستون را داشته باشد")
        if len(written) > MAX_WORD_CHARS or len(spoken) > MAX_WORD_CHARS:
            raise ValueError(f"هر خانه حداکثر {MAX_WORD_CHARS} نویسه می‌تواند باشد")
        if written in seen:
            raise ValueError(f"«{written}» دو بار نوشته شده است")
        seen.add(written)
        cleaned.append({"written": written, "spoken": spoken})

    if len(cleaned) > MAX_ENTRIES:
        raise ValueError(f"حداکثر {MAX_ENTRIES} کلمه می‌توانید ذخیره کنید")

    set_setting(SETTING_KEY, json.dumps(
        [[e["written"], e["spoken"]] for e in cleaned], ensure_ascii=False))
    return cleaned


def _pattern(pairs: List[Tuple[str, str]]):
    """One alternation for the whole list, longest written form first.

    One pass, not one pass per rule. A rule whose output contains another
    rule's input would otherwise fire twice — «دور» → «دوور» and then a rule
    on «دوور» rewriting that again — and the operator would have no way to
    predict what came out. Longest first so «عدسی چشم» wins over «عدسی» when
    both are listed.
    """
    ordered = sorted(pairs, key=lambda p: len(p[0]), reverse=True)
    body = "|".join(re.escape(written) for written, _ in ordered)
    return re.compile(f"(?<!{_EDGE})(?:{body})(?!{_EDGE})"), dict(ordered)


def apply(text: str) -> str:
    """Rewrite `text` the way this installation wants it read."""
    pairs = _rows(get_setting(SETTING_KEY, ""))
    if not pairs or not text:
        return text
    pattern, table = _pattern(pairs)
    return pattern.sub(lambda m: table[m.group(0)], text)
