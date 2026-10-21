"""A feed teaser is cut on a word boundary, never through a figure.

2026-10-20: youngster.id's RSS description for Reka read '... memperoleh
pendanaan US$110 juta [...]'. `_plain` capped it at TEASER_CHARS (400), and the
400th character fell inside the figure, so raw_text ended '... pendanaan US$11'.
The model faithfully extracted 'US$11', the verbatim guard passed it (the
digits WERE in the text we gave it), and the plausibility floor refused a
sub-thousand round that no scale word could rescue. The string was not a parse
failure; it was a cut. The cut is fixed here, where it was made.
"""
from collectors.national_press import TEASER_CHARS, _plain

REKA = (
    "<p>youngster.id - Dani Yogatama, Co-Founder dan CEO Reka, membawa "
    "perusahaan teknologi kecerdasan buatan (AI) tersebut bersaing di pasar "
    "global melalui pengembangan model AI multimodal. Reka mengembangkan "
    "teknologi yang mampu memproses teks, gambar, audio, dan video dalam satu "
    "sistem, dengan fokus pada kinerja dan efisiensi komputasi. Perusahaan "
    "yang didirikan pada 2022 itu memperoleh pendanaan US$110 juta "
    "[&#8230;]</p>"
)


def test_the_reka_teaser_does_not_end_inside_the_figure():
    text = _plain(REKA)
    assert len(text) <= TEASER_CHARS
    assert not text.endswith("US$11")
    assert "US$11" not in text.split() or "US$110" in text


def test_a_cut_never_leaves_a_partial_token():
    words = ["kata"] * 200
    text = _plain(" ".join(words) + " US$110 juta", limit=len("kata ") * 3 + 2)
    assert text == "kata kata kata"


def test_short_text_and_exact_boundaries_are_untouched():
    assert _plain("pendanaan US$110 juta") == "pendanaan US$110 juta"
    assert _plain("abc def", limit=7) == "abc def"
    assert _plain("abc def ghi", limit=7) == "abc def"
    # One unbroken token longer than the limit is still capped, not emptied.
    assert _plain("x" * 50, limit=10) == "x" * 10
