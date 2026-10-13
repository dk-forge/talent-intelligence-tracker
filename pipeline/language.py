"""The language a row's headline is written in, decided locally and for free.

WHY THIS EXISTS (owner-approved 2026-10-12). The daily talent digest quotes
headlines in their source's own language, and a Portuguese line in an English
email read as unfinished work. The digest composer (ai-layoff-tracker,
includes/digest-talent.php) refused to label a row with a language because we
did not STORE one and naming a guessed language is a claim the reader cannot
check. So the guess is now made once, here, at ingest, with its confidence
stored beside it, and a guess that is not confident is not made.

THE RULES, each pinned by tests/test_language.py:

  - No model call, no network, no dependency. Unicode script for non-Latin
    text, and distinctive function words (plus a few unambiguous letters) for
    Latin text. Budget: the owner's "no extra AI spend" holds by construction.
  - The answer is an ISO 639-1 code AND a confidence in [0, 1].
  - BELOW THRESHOLD THE CODE IS NULL. Confidence is still returned, so a row
    that was examined and found undecidable is told apart from a row nobody
    has looked at (language_confidence IS NULL). Short headlines, brand-name
    salads and Danish-vs-Norwegian all land here, which is the point.
  - It reads the HEADLINE, because that is the text the digest quotes. The
    summary is model-written and can be English over a Spanish headline; it is
    the English gist candidate, never evidence of the source's language.
"""

from __future__ import annotations

import re
import unicodedata

# A code is only stored at or above this. Chosen so a two-word lead over the
# runner-up is not enough on its own (2 vs 1 -> 0.67) but 3 vs 1 is (0.75).
THRESHOLD = 0.75
# And never on fewer than this many distinctive hits: one stopword is a
# coincidence ("de" in a brand name), not a language.
MIN_HITS = 2

_WORDS = {
    "en": "the and of to for with on at by is are will from its new after has "
          "have hiring hires hire jobs plans says over more than into amid "
          "appoints names raises adds opens creates an as be this that roles "
          "staff workers expands announces opens secures launches acquires "
          "named joins in total",
    "de": "der die das und mit für von den dem ist ein eine einen auf bei nach "
          "sich neue neuen stellen wird zum zur des im auch über mehr als "
          "nicht aus wie arbeitsplätze mitarbeiter",
    "fr": "de le la les des du et pour dans sur avec une un au aux est qui par "
          "plus nouveau nouvelle nouveaux recrute emplois postes sera son ses "
          "d'emplois embauche nommé nommée",
    "es": "de el la los las del y en para con por una un que se más nuevo nueva "
          "empleos contratará puestos al su sus trabajadores nuevos empleo",
    "pt": "de é o os as do da dos das e em para com por uma um que se mais novo "
          "nova vagas empregos no na nos ao seu sua contrata novos emprego "
          "trabalhadores",
    "it": "de il lo gli le della del dei e di per con una un che si più nuovo "
          "nuova assunzioni posti lavoro nel nella alla al nuovi",
    "nl": "de het een en van voor met op in is naar bij nieuwe banen "
          "vacatures wordt zijn niet ook uit",
    "sv": "och för med på att som av en ett till nya jobb är om från har inte",
    "da": "og for med på at som af en et til nye job er om fra har det ikke",
    "no": "og for med på at som av en et til nye jobber er om fra har det ikke",
    "pl": "i w z na do się nie dla od po że jest pracy nowych miejsc oraz",
    "cs": "a v na se s z do pro je že od po nových míst práce",
    "tr": "ve bir için ile bu yeni istihdam kişi olarak",
    "id": "dan di yang untuk dengan ke dari ini akan baru karyawan kerja "
          "lowongan",
    "ro": "și în la cu pentru pe din noi locuri muncă",
}
_STOP = {lang: frozenset(words.split()) for lang, words in _WORDS.items()}

# Letters that belong to one Latin-script language among those above. Each
# distinct letter present adds one hit, so "ação" is not three votes.
_LETTERS = {
    "es": "ñ¿¡", "pt": "ãõ", "de": "ß", "pl": "łżśćńąę", "cs": "řůě",
    "tr": "ğşı", "ro": "șțţş", "fr": "œ",
}

# Letters of Latin-script languages this module has no word list for
# (Lithuanian, Latvian, Hungarian, Icelandic, Vietnamese). Any of them present
# means a confident answer would be a confident WRONG answer -- Lithuanian
# shares ą/ę with Polish -- so the text is declared undecidable instead.
_UNSUPPORTED = set("ųėįāēīūļņķģőűðþơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹđ")

# Non-Latin scripts, by the first word of the Unicode character name.
_SCRIPT = {
    "HANGUL": "ko", "HIRAGANA": "ja", "KATAKANA": "ja", "CJK": "zh",
    "ARABIC": "ar", "HEBREW": "he", "GREEK": "el", "THAI": "th",
    "DEVANAGARI": "hi", "BENGALI": "bn", "TAMIL": "ta", "GEORGIAN": "ka",
    "ARMENIAN": "hy", "CYRILLIC": "ru", "LATIN": None,
}

_TOKEN = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?", re.UNICODE)


def _script_of(ch: str) -> str | None:
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return None
    head = name.split(" ", 1)[0]
    return head if head in _SCRIPT else None


def _non_latin(text: str) -> tuple[str | None, float] | None:
    """Decide on script alone, or return None to fall through to Latin."""
    counts: dict[str, int] = {}
    letters = 0
    for ch in text:
        if not ch.isalpha():
            continue
        letters += 1
        script = _script_of(ch)
        if script:
            counts[script] = counts.get(script, 0) + 1
    if not letters:
        return None
    latin = counts.pop("LATIN", 0)
    if not counts:
        return None
    # Japanese is written with CJK ideographs too; any kana decides it.
    if "HIRAGANA" in counts or "KATAKANA" in counts:
        script_n = sum(counts.get(k, 0) for k in ("HIRAGANA", "KATAKANA", "CJK"))
        code = "ja"
    else:
        script, script_n = max(counts.items(), key=lambda kv: kv[1])
        code = _SCRIPT[script]
        if script == "CYRILLIC":
            code = _cyrillic(text)
        elif script == "ARABIC" and any(c in text for c in "پچژگ"):
            code = "fa"
    share = round(script_n / letters, 2)
    if share < 0.5 and latin:
        return None  # mostly Latin with a foreign name in it: let Latin decide
    if code is None or share < THRESHOLD:
        return (None, share)
    return (code, share)


def _cyrillic(text: str) -> str | None:
    """Russian, Ukrainian, Bulgarian or Serbian, only when a letter says so."""
    low = text.lower()
    if any(c in low for c in "іїєґ"):
        return "uk"
    if any(c in low for c in "ђћџљњј"):
        return "sr"
    if any(c in low for c in "ыэё"):
        return "ru"
    return None  # Cyrillic, but nothing tells these languages apart


def detect(text: str | None) -> tuple[str | None, float | None]:
    """Return (iso_639_1_code_or_None, confidence_or_None).

    (None, None) means there was nothing to examine. (None, c) means the text
    was examined and no language cleared THRESHOLD with c as the best margin.
    """
    text = unicodedata.normalize("NFC", (text or "").strip())
    if not text:
        return (None, None)
    decided = _non_latin(text)
    if decided is not None:
        return decided
    low = text.lower().replace("’", "'")
    if _UNSUPPORTED & set(low):
        return (None, 0.0)
    hits = {lang: 0 for lang in _STOP}
    for token in _TOKEN.findall(low):
        for lang, words in _STOP.items():
            if token in words:
                hits[lang] += 1
    for lang, chars in _LETTERS.items():
        hits[lang] += sum(1 for c in set(chars) if c in low)
    ranked = sorted(hits.items(), key=lambda kv: kv[1], reverse=True)
    (best_lang, best), (_, second) = ranked[0], ranked[1]
    if best == 0:
        return (None, 0.0)
    confidence = round(best / (best + second), 2)
    if best < MIN_HITS or confidence < THRESHOLD:
        return (None, confidence)
    return (best_lang, confidence)


def columns(headline: str | None) -> dict:
    """The two stored columns for a headline, ready to merge into a row."""
    code, confidence = detect(headline)
    return {"language": code, "language_confidence": confidence}
