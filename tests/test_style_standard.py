"""THE READING LEVEL OF THE COPY IS A TEST, NOT A TASTE.

WHY THIS TEST EXISTS.

Both dashboards are read by people who do not work here. The owner's brief was
that the language should read like a quality daily newspaper, so that somebody
with a college education understands a page on the first pass without going
anywhere else. That is a real requirement and it decays the way every prose
requirement decays: one careful rewrite, then a year of small edits by people
who already know what the words mean, and the page slides back into trade
vocabulary nobody outside the building parses.

So the standard is written down once, in docs/STYLE.md, byte-identical in this
repo and in the sibling tracker, and this test is the half of it a machine can
hold. It scores the strings a reader actually sees and fails while they drift.

WHAT IT SCORES, AND WHAT IT REFUSES TO SCORE. Only reader-facing copy: page
templates, JS UI strings, chart titles and subtitles, tile labels, the
methodology and recall prose, and the email a human opens. Not code comments,
not docblocks, not variable names, not test fixtures. That distinction is not
pedantry. Both codebases write enormous rationale comments in exactly the
register of the copy, and those comments frequently quote the display string
verbatim, INCLUDING THE VERSION THAT WAS REPLACED. A checker that reads comments
would grade the commentary, pass while the page was wrong, and fail after a
correct fix. So railway/style_check.py strips comments first, and the tests
below prove it does, on a file built to catch exactly that mistake.

THE THRESHOLDS ARE MEASURED, NOT CHOSEN. Every ceiling in style_check.py was
set from a reading taken across the real copy of both products, recorded in
docs/STYLE.md with its date. A bar that fails everything on day one gets
suppressed in a week; a bar nothing can ever trip teaches nothing. These were
set at, or slightly better than, where the better pages already were, and they
failed real pages on the day they landed.

A FAILURE NAMES THE SENTENCE. Every assertion below prints the offending
string with its file and its line number. A style failure that says only
"grade too high" tells a reader to go hunting, which is how a check becomes a
thing people disable.
"""
import hashlib
import os
import pathlib
import sys
import unittest


def _repo_root():
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    raise RuntimeError("test_style_standard: no repo root above %s" % here)


ROOT = _repo_root()

# style_check.py sits at the repo root in the talent tracker and under
# railway/ here. Finding it rather than hardcoding it is what lets this test
# file stay byte-identical in both repos.
for _cand in (ROOT / "style_check.py", ROOT / "railway" / "style_check.py"):
    if _cand.exists():
        sys.path.insert(0, str(_cand.parent))
        STYLE_CHECK_PATH = _cand
        break
else:
    raise RuntimeError("test_style_standard: cannot find style_check.py")

import style_check as sc                                    # noqa: E402

STYLE_MD_PATH = ROOT / "docs" / "STYLE.md"
TECHLOG_CANDIDATES = [ROOT / "docs" / "TECHLOG.md", ROOT / "docs" / "TECHLOG.md"]

# The standard and the scorer are both byte-identical across the two repos.
# Editing either deliberately means updating these digests, and updating a
# digest is the moment you are told this is a two-repo change. The workflow
# .github/workflows/style-standard.yml is the only check that can see the
# sibling; these two constants are what make an ACCIDENTAL edit fail offline.
STYLE_MD_SHA256 = "28975ec6e9e5d99e95c8fc775f8ab033d558454091e8b8c3a972d314ef238c85"
STYLE_CHECK_SHA256 = "a45b3347508d830d128042f524946755508b2e5fd56bf971905a9cf2930e68b9"


def _sha(path):
    with open(str(path), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _read(path):
    with open(str(path), "r") as fh:
        return fh.read()


# --------------------------------------------------------------------------
# Part 1. The scorer must actually work. These run against text built here, so
# they keep meaning something even when the product copy is perfect.
# --------------------------------------------------------------------------

class ScorerMechanics(unittest.TestCase):

    def test_syllable_counter(self):
        cases = {
            "the": 1, "cat": 1, "layoff": 2, "filing": 2, "company": 3,
            "employer": 3, "attribution": 4, "methodology": 5, "data": 2,
            "notice": 2, "verified": 3, "we": 1, "counted": 2, "checked": 1,
        }
        for word, want in cases.items():
            got = sc.syllables(word)
            self.assertEqual(
                got, want,
                "syllables(%r) = %d, expected %d" % (word, got, want))

    def test_grade_orders_simple_below_complex(self):
        simple = ("We count job cuts. Each one links to a filing. "
                  "We check the source. Nothing here is a guess.")
        complex_ = ("The methodological apparatus underpinning the "
                    "aforementioned classification infrastructure necessitates "
                    "substantial epistemological qualification before any "
                    "generalisable inferential conclusion may be "
                    "operationalised.")
        self.assertLess(sc.flesch_kincaid_grade(simple), 8.0)
        self.assertGreater(sc.flesch_kincaid_grade(complex_), 14.0)

    def test_grade_is_none_when_there_is_too_little_text(self):
        # Absence of a score must not read as a passing score.
        self.assertIsNone(sc.flesch_kincaid_grade("Two words"))

    def test_sentence_splitting_survives_abbreviations_and_decimals(self):
        text = ("We read SEC filings, e.g. Item 2.05 exit-cost filings. "
                "The U.S. rule is different. That is 2.5 times the figure.")
        self.assertEqual(len(sc.sentences(text)), 3)

    def test_passive_detection(self):
        for s in ("The row was removed by us.",
                  "Every figure is checked against the filing.",
                  "The notices were filed late."):
            self.assertTrue(sc.is_passive(s), "should be passive: %r" % s)
        for s in ("We removed the row.",
                  "We check every figure against the filing.",
                  "The employer filed the notice late."):
            self.assertFalse(sc.is_passive(s), "should be active: %r" % s)

    def test_nav_strip_is_not_scored_as_a_sentence(self):
        # A row of links joined by middots is a list of labels. Scored as a
        # sentence it reads as one enormous one and trips the ceiling for a
        # reason that has nothing to do with readability.
        nav = sc.Segment(
            "Sources · How complete, measured · Corrections · Hiring",
            "x.php", 1, "p")
        self.assertFalse(sc.is_body(nav))

    def test_long_sentence_is_caught_and_located(self):
        long_sentence = "We " + " ".join(["count"] * 40) + " filings."
        seg = sc.Segment(long_sentence, "page-x.php", 42, "x")
        findings, _ = sc.check_segments([seg])
        kinds = [f.kind for f in findings]
        self.assertIn("sentence too long", kinds)
        report = [f.format() for f in findings
                  if f.kind == "sentence too long"][0]
        self.assertIn("page-x.php:42", report)

    def test_banned_jargon_and_hedges_and_dashes_are_caught(self):
        checks = [
            ("We announced a workforce reduction this quarter.",
             "banned jargon"),
            ("This may potentially affect the count in some months.",
             "hedging stack"),
            ("The filing is late, we think, and the count may move.".replace(
                ",", "—", 1), "banned punctuation"),
        ]
        for text, kind in checks:
            seg = sc.Segment(text, "x.php", 1, "p")
            findings, _ = sc.check_segments([seg])
            self.assertIn(kind, [f.kind for f in findings],
                          "%r should raise %s" % (text, kind))

    def test_a_quoted_term_is_reported_not_used(self):
        # Both products describe the phrases they SEARCH FOR in employer and
        # press language, and some of those phrases are on the banned list.
        # "workforce reduction" is a real search term in source_registry.py.
        # Rewriting it out of that list would not improve the copy, it would
        # make the page describe a collector that does not exist.
        used = sc.Segment(
            "We announced a workforce reduction across the estate this year.",
            "x.php", 1, "p")
        quoted = sc.Segment(
            'Discovery searches for "layoffs", "redundancies" and '
            '"workforce reduction" across the index.', "x.php", 2, "p")
        self.assertIn("banned jargon",
                      [f.kind for f in sc.check_segments([used])[0]])
        self.assertNotIn("banned jargon",
                         [f.kind for f in sc.check_segments([quoted])[0]])

    def test_an_apostrophe_does_not_open_a_quotation(self):
        # If apostrophes paired as quotes, a span would open across half a
        # paragraph and quietly excuse the jargon inside it.
        seg = sc.Segment(
            "The employer's filing and the company's notice both describe a "
            "workforce reduction in plain terms.", "x.php", 1, "p")
        self.assertIn("banned jargon",
                      [f.kind for f in sc.check_segments([seg])[0]])

    def test_clean_copy_raises_nothing(self):
        # The bar must be passable, or it teaches people to switch it off.
        good = sc.Segment(
            "We count job cuts that an employer put in writing. Every entry "
            "links to the filing it came from. We report the reason the "
            "employer gave. We do not decide the cause ourselves.",
            "x.php", 1, "p")
        findings, _ = sc.check_segments([good])
        self.assertEqual([f.format() for f in findings], [])


class CommentStripping(unittest.TestCase):
    """The defect this whole design exists to prevent.

    Seven tests in this project have at some point passed against defective
    code because they matched a COMMENT instead of the string the page
    renders. These prove the extractor cannot make that mistake.
    """

    PHP_FIXTURE = (
        "<?php\n"
        "/* This rationale comment is written in the register of the copy and\n"
        "   it deliberately quotes the old display string: We utilise a\n"
        "   workforce reduction methodology employed across the estate. */\n"
        "// Another comment mentioning a workforce reduction in passing.\n"
        "?>\n"
        "<p>We count job cuts that an employer put in writing.</p>\n"
        "<?php $url = 'https://example.com/a//b'; // trailing comment\n"
        "echo '<p>Every entry links to its filing.</p>'; ?>\n"
    )

    JS_FIXTURE = (
        "/* A long essay comment that says we utilise a workforce reduction. */\n"
        "var a = 'https://example.com/x//y';\n"
        "el.textContent = 'We publish the number we can source.';\n"
        "// a workforce reduction mentioned only in a line comment\n"
    )

    PY_FIXTURE = (
        '"""Module docstring mentioning a workforce reduction."""\n'
        "# a workforce reduction in a hash comment\n"
        "def f(names):\n"
        '    """Doc mentioning a workforce reduction."""\n'
        "    return f\"Sources needing attention: {', '.join(names)} today.\"\n"
    )

    def _extract(self, tmpdir, name, body, ext):
        path = os.path.join(tmpdir, name)
        with open(path, "w") as fh:
            fh.write(body)
        return sc.extract_file(path, "p", tmpdir)

    def test_comment_text_never_reaches_the_corpus(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            segs = []
            segs += self._extract(tmp, "a.php", self.PHP_FIXTURE, ".php")
            segs += self._extract(tmp, "a.js", self.JS_FIXTURE, ".js")
            segs += self._extract(tmp, "a.py", self.PY_FIXTURE, ".py")
            blob = " ".join(s.text for s in segs).lower()
            for phrase in ("workforce reduction", "methodology employed",
                           "rationale", "essay", "docstring"):
                self.assertNotIn(
                    phrase, blob,
                    "comment text leaked into the scored corpus: %r\ngot: %s"
                    % (phrase, blob))

    def test_the_real_strings_do_reach_the_corpus(self):
        # The mirror of the test above. A stripper that threw everything away
        # would pass the first test and be useless.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            segs = []
            segs += self._extract(tmp, "a.php", self.PHP_FIXTURE, ".php")
            segs += self._extract(tmp, "a.js", self.JS_FIXTURE, ".js")
            segs += self._extract(tmp, "a.py", self.PY_FIXTURE, ".py")
            blob = " ".join(s.text for s in segs)
            for phrase in ("We count job cuts that an employer put in writing.",
                           "Every entry links to its filing.",
                           "We publish the number we can source.",
                           "Sources needing attention"):
                self.assertIn(phrase, blob,
                              "real display string was dropped: %r" % phrase)

    def test_comment_stripping_preserves_line_numbers(self):
        src = "a\n/* x\ny\nz */\nb\n"
        out = sc.strip_comments(src, "php")
        self.assertEqual(len(out), len(src))
        self.assertEqual(out.count("\n"), src.count("\n"))

    def test_a_url_is_not_mistaken_for_a_line_comment(self):
        src = "var a = 'https://example.com/x//y'; var b = 1;\n"
        self.assertIn("//y", sc.strip_comments(src, "js"))


# --------------------------------------------------------------------------
# Part 2. The product's own copy must meet the standard.
# --------------------------------------------------------------------------

class ProductCopy(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.segments = sc.collect(str(ROOT))
        cls.findings, cls.pages = sc.check_segments(cls.segments)
        cls.rows = sc.page_report(cls.pages)

    def test_the_extractor_found_the_copy_at_all(self):
        # An extractor that silently matched nothing would make every
        # assertion below vacuously true. Absence of a signal is not a pass.
        self.assertGreater(
            len(self.segments), 200,
            "only %d reader strings extracted, the target list in "
            "style_check.py is probably stale" % len(self.segments))

    def _assert_no(self, kind):
        bad = [f for f in self.findings if f.kind == kind]
        if bad:
            self.fail(
                "%d %s finding(s). Rewrite these to meet docs/STYLE.md:\n\n%s"
                % (len(bad), kind, "\n\n".join(f.format() for f in bad)))

    def test_no_sentence_exceeds_the_ceiling(self):
        self._assert_no("sentence too long")

    def test_no_banned_jargon(self):
        self._assert_no("banned jargon")

    def test_no_hedging_stacks(self):
        self._assert_no("hedging stack")

    def test_no_em_or_en_dashes(self):
        self._assert_no("banned punctuation")

    def test_every_page_meets_its_reading_level_and_voice(self):
        problems = sc.check_pages(self.rows)
        if problems:
            self.fail(
                "%d page(s) drifted from docs/STYLE.md:\n  %s\n\n"
                "Per-page reading:\n%s"
                % (len(problems), "\n  ".join(problems), _table(self.rows)))


def _table(rows):
    out = ["  %-20s %7s %8s %9s" % ("page", "grade", "passive", "segments")]
    for r in rows:
        g = "%.1f" % r["mean_grade"] if r["mean_grade"] is not None else "-"
        out.append("  %-20s %7s %7.0f%% %9d"
                   % (r["page"], g, r["passive_ratio"] * 100, r["segments"]))
    return "\n".join(out)


# --------------------------------------------------------------------------
# Part 3. The standard is one document, held in two repos.
# --------------------------------------------------------------------------

class SharedStandardDoesNotDrift(unittest.TestCase):

    def test_style_md_exists_and_matches_its_digest(self):
        self.assertTrue(STYLE_MD_PATH.exists(),
                        "docs/STYLE.md is missing from this repo")
        got = _sha(STYLE_MD_PATH)
        self.assertEqual(
            got, STYLE_MD_SHA256,
            "docs/STYLE.md changed.\n"
            "  expected %s\n  got      %s\n\n"
            "The standard is byte-identical in dk-forge/ai-layoff-tracker and "
            "dk-forge/talent-intelligence-tracker. If you meant to change it: "
            "copy the file into the sibling repo, update STYLE_MD_SHA256 in "
            "BOTH repos' test_style_standard.py, and record the new digest in "
            "each repo's docs/TECHLOG.md. Both repos stay red until they "
            "agree, which is the point." % (STYLE_MD_SHA256, got))

    def test_style_checker_matches_its_digest(self):
        got = _sha(STYLE_CHECK_PATH)
        self.assertEqual(
            got, STYLE_CHECK_SHA256,
            "style_check.py changed.\n"
            "  expected %s\n  got      %s\n\n"
            "The scorer is byte-identical in both repos so that one sentence "
            "cannot pass on one product and fail on the other. Copy it across, "
            "update STYLE_CHECK_SHA256 in both, and record the digest in each "
            "repo's docs/TECHLOG.md." % (STYLE_CHECK_SHA256, got))

    def test_the_digests_are_recorded_in_the_techlog(self):
        techlog = None
        for cand in TECHLOG_CANDIDATES:
            if cand.exists():
                techlog = _read(cand)
                break
        self.assertIsNotNone(techlog, "docs/TECHLOG.md is missing")
        for name, digest in (("STYLE.md", STYLE_MD_SHA256),
                             ("style_check.py", STYLE_CHECK_SHA256)):
            self.assertIn(
                digest, techlog,
                "the %s digest %s is not recorded in docs/TECHLOG.md. "
                "Recording it there is what makes a deliberate edit visible "
                "to the next reader." % (name, digest))

    def test_the_thresholds_are_written_down_where_humans_read_them(self):
        # A number that lives only in code is a number nobody agreed to.
        doc = _read(STYLE_MD_PATH)
        for value in (str(sc.MAX_SENTENCE_WORDS),
                      "%.1f" % sc.TARGET_GRADE_MEAN,
                      "%d%%" % int(sc.MAX_PASSIVE_RATIO * 100)):
            self.assertIn(
                value, doc,
                "threshold %r is enforced by style_check.py but not stated in "
                "docs/STYLE.md" % value)


if __name__ == "__main__":
    unittest.main()
