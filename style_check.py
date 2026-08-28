"""Reader-copy style scorer, shared byte-identical by both trackers.

The standard this enforces is written down in docs/STYLE.md. This file is the
machine half of it: it pulls the strings a reader actually sees off both
products, scores them, and names the sentence that failed.

Two things make this harder than "grep for quotes", and both are why the file
is this long.

First, both codebases write enormous prose comments in exactly the register of
the copy. A rationale block above a chart function reads like a paragraph on
the page, and frequently quotes the display string verbatim, including the
string it replaced. Anything that scores text nodes without stripping comments
first is scoring the commentary. So every extractor here runs a quote-aware
comment stripper that replaces comment bytes with spaces and keeps newlines,
which means byte offsets still map one-to-one onto the original file and a
failure can name a real line number.

Second, "a string in the source" is not "a string a reader sees". The plugins
carry SQL, CSS class names, REST routes, option keys and CSV headers in the
same quotes as the copy. The prose filter below is deliberately conservative:
when in doubt it drops the segment, because a checker that cries about a SQL
fragment gets muted, and a muted checker is not a checker.

Stdlib only, on purpose. Every install in these repos is hash-pinned and a
reading-level formula is eighty lines of arithmetic, not a dependency.
"""

import ast
import os
import re
import sys
import json

# --------------------------------------------------------------------------
# Thresholds. These are MEASURED, not theoretical. See docs/STYLE.md for the
# reading that set each one and the date it was taken.
# --------------------------------------------------------------------------

MAX_SENTENCE_WORDS = 30      # hard ceiling for any single body sentence
TARGET_GRADE_MEAN = 11.0     # per-page mean Flesch-Kincaid grade ceiling
MAX_PASSIVE_RATIO = 0.25     # share of body sentences that may be passive
MIN_SEGMENT_WORDS = 4        # below this a segment is a label, not body copy
BODY_SENTENCE_WORDS = 8      # a segment this long is body copy even unpunctuated

# Words and phrases the copy on these two products should not use. Every entry
# was found in the real copy of one of the two products when the standard was
# written, with the replacement that was actually applied. This is not a
# generic corporate-jargon list; a rule nobody ever trips teaches nothing.
BANNED_JARGON = {
    "workforce reduction": "job cuts",
    "workforce reductions": "job cuts",
    "headcount reduction": "job cuts",
    "headcount reductions": "job cuts",
    "reduction in force": "job cuts",
    "separation event": "layoff",
    "regulatory instrument": "filing",
    "regulatory instruments": "filings",
    "verification was performed": "we checked",
    "verification is performed": "we check",
    "utilise": "use",
    "utilize": "use",
    "utilised": "used",
    "utilized": "used",
    "utilisation": "use",
    "utilization": "use",
    "leverage": "use",
    "leveraging": "using",
    "methodology employed": "how we did it",
    "aforementioned": "this",
    "heretofore": "until now",
    "in order to": "to",
    "prior to": "before",
    "subsequent to": "after",
    "in the event that": "if",
    "at this point in time": "now",
    "commence": "start",
    "commences": "starts",
    "terminate": "end",
    "endeavour": "try",
    "endeavor": "try",
    "facilitate": "help",
    "facilitates": "helps",
    "ascertain": "find out",
    "ascertained": "found out",
    "disaggregate": "break down",
    "disaggregated": "broken down",
    "granular": "detailed",
    "granularity": "detail",
    "operationalise": "put into practice",
    "operationalize": "put into practice",
    "surface area": "range",
    "cadence": "how often",
    "signal fidelity": "accuracy",
    "data artifact": "data problem",
    # NOT "official". In this codebase a canonical event is the one we count
    # in its own right, the row that is not folded into a larger one
    # (superset_of = 0). Swapping in "official" reads as government-issued and
    # changes the claim. A jargon list that suggests a wrong synonym is worse
    # than no list, so this one tells the writer to say the meaning instead.
    "canonical": "say the meaning plainly, for example \"the one we count\"",
    "instantiate": "create",
    "instantiated": "created",
    "downstream consumer": "anyone using the data",
    "downstream consumers": "anyone using the data",
    "end user": "reader",
    "end users": "readers",
    "actionable insight": "something you can use",
    "actionable insights": "things you can use",
    "best in class": "the best we found",
    "industry leading": "",
    "world class": "",
    "state of the art": "",
    "unparalleled": "",
    "unrivalled": "",
    "unrivaled": "",
}

# Hedging stacks. Two hedges in a row say less than one hedge.
HEDGE_STACKS = [
    r"\bmay\s+potentially\b",
    r"\bmight\s+potentially\b",
    r"\bcould\s+potentially\b",
    r"\bappears?\s+to\s+possibly\b",
    r"\bseems?\s+to\s+possibly\b",
    r"\bmay\s+or\s+may\s+not\s+possibly\b",
    r"\bpossibly\s+may\b",
    r"\bcan\s+sometimes\s+occasionally\b",
    r"\bappears?\s+to\s+suggest\s+that\s+it\s+may\b",
    r"\bwe\s+believe\s+it\s+may\s+possibly\b",
]

# Em dash and en dash stay banned. Both products already hold this line and the
# check is here so it cannot quietly regress in new copy.
BANNED_CHARS = {
    "—": "em dash",
    "–": "en dash",
}

# Irregular past participles, for the passive-voice detector. Regular ones are
# caught by the -ed rule.
IRREGULAR_PARTICIPLES = set("""
been begun bought brought built caught chosen come cut done drawn driven
eaten fallen felt fought found given gone grown held kept known laid led
left lent let lost made meant met paid put read run said seen sent set
shown shut sold sought spent split spoken stood taken taught thrown told
understood withheld won written drawn borne overseen rebuilt withdrawn
""".split())


# --------------------------------------------------------------------------
# Comment stripping. Length preserving, so offsets still map to line numbers.
# --------------------------------------------------------------------------

def _blank(text, start, end):
    """Replace text[start:end] with spaces, keeping newlines and length."""
    chunk = text[start:end]
    return "".join("\n" if c == "\n" else " " for c in chunk)


def strip_comments(src, lang):
    """Remove comments, preserving byte offsets and line numbers exactly.

    Quote aware in both directions: a // inside 'https://x' is not a comment,
    and a quote character inside a comment does not open a string. Both
    mistakes are live hazards in these repos, which is why this is hand rolled
    rather than a regex.
    """
    out = []
    i = 0
    n = len(src)
    quote = None
    while i < n:
        c = src[i]
        if quote:
            if c == "\\" and lang in ("php", "js", "py") and quote != "'":
                out.append(src[i:i + 2])
                i += 2
                continue
            if c == "\\" and lang == "php" and quote == "'":
                # PHP single quotes only escape \\ and \'
                out.append(src[i:i + 2])
                i += 2
                continue
            if c == "\\" and lang == "js":
                out.append(src[i:i + 2])
                i += 2
                continue
            out.append(c)
            if c == quote:
                quote = None
            i += 1
            continue

        # not in a string
        if c in "'\"" or (lang == "js" and c == "`"):
            quote = c
            out.append(c)
            i += 1
            continue

        if src.startswith("/*", i) and lang in ("php", "js"):
            end = src.find("*/", i + 2)
            end = n if end == -1 else end + 2
            out.append(_blank(src, i, end))
            i = end
            continue

        if src.startswith("//", i) and lang in ("php", "js"):
            end = src.find("\n", i)
            end = n if end == -1 else end
            out.append(_blank(src, i, end))
            i = end
            continue

        if c == "#" and lang in ("php", "py"):
            # PHP: # is a comment outside strings. Inside an href="#x" it is
            # inside a string and we never get here.
            end = src.find("\n", i)
            end = n if end == -1 else end
            out.append(_blank(src, i, end))
            i = end
            continue

        if src.startswith("<!--", i):
            end = src.find("-->", i + 4)
            end = n if end == -1 else end + 3
            out.append(_blank(src, i, end))
            i = end
            continue

        out.append(c)
        i += 1

    return "".join(out)


def strip_py_docstrings(src):
    """Blank out module, class and function docstrings, length preserving."""
    pattern = re.compile(r'("""|\'\'\')')
    out = list(src)
    i = 0
    while True:
        m = pattern.search(src, i)
        if not m:
            break
        # Only treat as a docstring if it opens a statement (start of a line,
        # optionally indented). Triple quotes used as values are rare here.
        line_start = src.rfind("\n", 0, m.start()) + 1
        prefix = src[line_start:m.start()].strip()
        close = src.find(m.group(1), m.end())
        close = len(src) if close == -1 else close + 3
        if prefix in ("", "r", "f", "b", "u"):
            for k in range(m.start(), close):
                if out[k] != "\n":
                    out[k] = " "
        i = close
    return "".join(out)


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

class Segment(object):
    """One string a reader can see, with where it came from."""

    def __init__(self, text, path, line, page):
        self.text = text
        self.path = path
        self.line = line
        self.page = page

    def __repr__(self):
        return "Segment(%r, %s:%d)" % (self.text[:40], self.path, self.line)


def _line_of(src, offset):
    return src.count("\n", 0, offset) + 1


def _blank_php_blocks(src):
    """Replace <?php ... ?> regions with spaces, seeding one real word.

    A sentence like "Updated <?php echo esc_html($d); ?> today" is one sentence
    with a value in the middle. Blanking the whole block loses a word and drags
    the word count down; seeding the literal word "one" keeps the sentence
    countable and keeps every later offset identical.
    """
    out = []
    i = 0
    n = len(src)
    while i < n:
        j = src.find("<?php", i)
        if j == -1:
            j = src.find("<?=", i)
        if j == -1:
            out.append(src[i:])
            break
        out.append(src[i:j])
        end = src.find("?>", j)
        end = n if end == -1 else end + 2
        blanked = _blank(src, j, end)
        # Pad the seeded word with spaces. Written flush ("...filing.one Where")
        # it welds the value onto the previous full stop and the sentence
        # splitter never sees a boundary, so two sentences get scored as one
        # very long one.
        if len(blanked) >= 5 and "\n" not in blanked[:5]:
            blanked = " one " + blanked[5:]
        out.append(blanked)
        i = end
    return "".join(out)


TAG_RE = re.compile(r"<[^>]{0,400}>", re.S)
ENTITY = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&#039;": "'", "&apos;": "'", "&nbsp;": " ", "&mdash;": "—",
    "&ndash;": "–", "&hellip;": "...",
    # These matter more than they look. This copy uses &rsquo; for almost every
    # apostrophe, and left undecoded the literal string "&rsquo;" travels into
    # the scored text, inflating the word count and dragging the letters ratio
    # in looks_like_copy() far enough to drop real sentences on the floor.
    "&lsquo;": "‘", "&rsquo;": "’", "&ldquo;": "“", "&rdquo;": "”",
    "&middot;": "·", "&bull;": "•", "&amp;nbsp;": " ", "&#8217;": "’",
}


def _unescape(text):
    for k, v in ENTITY.items():
        text = text.replace(k, v)
    return text


# Tags that sit INSIDE a sentence. A link or a <strong> in the middle of a
# paragraph must not split it, or a 40-word sentence wrapped around an <a>
# gets scored as two short ones and sails under the ceiling. Everything not
# listed here is treated as a block boundary and does end the segment.
INLINE_TAGS = set("""
a b i u s em strong span small sub sup abbr code kbd var cite q mark
time data bdi bdo wbr del ins samp dfn ruby rt rp
""".split())

TAG_NAME_RE = re.compile(r"^</?\s*([a-zA-Z][\w\-]*)")


def _html_text_segments(src, path, page, base_offset=0):
    """Yield the text nodes of an HTML region, with offsets into src.

    Text is accumulated across inline tags so a sentence stays one segment.
    """
    segs = []
    # Drop script and style bodies wholesale, they are never reader copy.
    cleaned = src
    for tag in ("script", "style"):
        for m in re.finditer(r"<%s\b.*?</%s>" % (tag, tag), cleaned,
                             re.S | re.I):
            cleaned = cleaned[:m.start()] + _blank(cleaned, m.start(), m.end()) \
                + cleaned[m.end():]

    # Pull attribute copy first: these are real reader strings.
    for m in re.finditer(
            r'\b(aria-label|title|placeholder|alt|data-label)\s*=\s*"([^"]{4,400})"',
            cleaned):
        segs.append((m.group(2), base_offset + m.start(2)))

    pos = 0
    buf = []          # accumulated text of the current block
    buf_off = None    # offset of the first text in the current block

    def flush():
        if buf and "".join(buf).strip():
            segs.append(("".join(buf), buf_off))
        del buf[:]

    for m in TAG_RE.finditer(cleaned):
        node = cleaned[pos:m.start()]
        if node.strip():
            if buf_off is None or not buf:
                buf_off = base_offset + pos
            buf.append(node)
        name_m = TAG_NAME_RE.match(m.group(0))
        name = name_m.group(1).lower() if name_m else ""
        if name not in INLINE_TAGS:
            flush()
            buf_off = None
        else:
            # An inline tag is transparent, but it does separate two words.
            buf.append(" ")
        pos = m.end()

    tail = cleaned[pos:]
    if tail.strip():
        if buf_off is None or not buf:
            buf_off = base_offset + pos
        buf.append(tail)
    flush()

    return segs


STRING_RE = re.compile(r"""(?<![\w$])('((?:\\.|[^'\\])*)'|"((?:\\.|[^"\\])*)")""",
                       re.S)
JOINER_RE = re.compile(r"^\s*[.+]\s*$")


def _literal_segments(src, path, page):
    """Extract quoted literals, joining adjacent . or + concatenations."""
    segs = []
    matches = list(STRING_RE.finditer(src))
    i = 0
    while i < len(matches):
        m = matches[i]
        text = m.group(2) if m.group(2) is not None else m.group(3)
        start = m.start()
        j = i + 1
        while j < len(matches):
            between = src[matches[j - 1].end():matches[j].start()]
            if JOINER_RE.match(between):
                nxt = matches[j]
                more = nxt.group(2) if nxt.group(2) is not None else nxt.group(3)
                text = text + more
                j += 1
            else:
                break
        segs.append((text, start))
        i = j if j > i + 1 else i + 1
    return segs


def _clean_literal(text):
    """Turn a source literal into the sentence a reader would see."""
    text = text.replace("\\n", " ").replace("\\t", " ")
    text = text.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")
    if "<" in text and ">" in text:
        text = _unescape(TAG_RE.sub(" ", text))
    # printf placeholders stand in for a value; make them a countable word.
    text = re.sub(r"%(?:\d+\$)?[-+ 0#']*[\d.]*[sdfux]", "one", text)
    text = re.sub(r"\{[a-zA-Z_][\w.\[\]']*\}", "one", text)
    # Merging text across an inline tag leaves "the health page ; the log",
    # because the tag itself stood between the word and the punctuation.
    #
    # Done in two linear passes, not one. The single pattern \s+([,.;:!?])
    # backtracks quadratically on long whitespace runs that are NOT followed
    # by punctuation: at every offset inside such a run \s+ re-matches the
    # rest of the run and then fails the class, so a k-space run costs O(k^2)
    # and the 7k-line templates spent minutes here. A \s+ replace that always
    # succeeds consumes each run in one pass (O(n)), and a single-space match
    # before punctuation cannot re-scan a run. Every caller of this function
    # collapses whitespace with " ".join(text.split()) immediately after, so
    # normalising runs to one space here changes no reader-visible segment.
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r" ([,.;:!?])", r"\1", text)
    return text


# Anything matching these is machinery, not copy.
NOISE_PATTERNS = [
    re.compile(r"^\s*$"),
    re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|GROUP BY|ORDER BY|"
               r"LEFT JOIN|INNER JOIN|CREATE TABLE|ALTER TABLE|COALESCE|"
               r"UNION ALL)\b"),
    re.compile(r"^[\w\-./%:#]+$"),                      # single token
    re.compile(r"^https?://"),
    re.compile(r"^[/\\][\w\-./]*$"),                     # path
    re.compile(r"^[A-Z0-9_ ]+$"),                        # constant-ish
    re.compile(r"[{};]\s*$"),                            # css / code tail
    re.compile(r"^\s*[.#][\w\-]+\s*[,{]"),               # css selector
    re.compile(r"^\s*(function|return|var |const |let |if |for |while )"),
    re.compile(r"^[\w\-]+(\s*,\s*[\w\-]+)+$"),           # class or column list
    re.compile(r"^\s*[\d\s.,%$+\-()]+$"),                # numbers only
    re.compile(r"(Content-Type|List-Unsubscribe|User-Agent|Mozilla/|charset=)"),
    re.compile(r"^\s*(Y-m-d|d/m/Y|H:i|M j, Y)"),          # date formats
    re.compile(r"@[\w.]+\.(com|org|net)"),               # bare addresses
    re.compile(r"^\s*[\w\-]+\s*=\s*"),                   # key=value
    re.compile(r"^\s*(px|em|rem|vh|vw)\b"),
    # Code that leaked out of a desynced scan. A JS regex literal such as
    # /[\s']/g carries a lone quote, and a character-by-character scanner
    # treats it as opening a string, so the "string" it then reports is
    # really the surrounding source. Detecting regex-versus-division properly
    # is a parsing problem; recognising the code that falls out is not, and
    # this is the safe side to err on because the only cost is dropping a
    # segment that was never reader copy.
    re.compile(r"\b(function|document|querySelector|innerHTML|textContent|"
               r"getElementById|addEventListener|prototype|typeof|null|"
               r"undefined|Array|Object|JSON)\b\s*[.(\[]"),
    re.compile(r"(===|!==|=>|\+\+|&&|\|\||\)\s*\{|;\s*\w+\s*=)"),
    re.compile(r"^\s*[\]\)\}]"),                          # starts mid-expression
    re.compile(r"\.(js|css|php|py|json|svg|png)\b"),
]


def looks_like_copy(text):
    """Conservative prose filter. When unsure, say no."""
    t = " ".join(text.split())
    if len(t) < 12:
        return False
    for pat in NOISE_PATTERNS:
        if pat.search(t):
            return False
    words = t.split()
    if len(words) < 3:
        return False
    letters = sum(1 for c in t if c.isalpha())
    if letters < len(t) * 0.55:
        return False
    if not re.search(r"[a-z]{3}", t):
        return False
    # A run of tokens with no vowel-bearing normal word is markup residue.
    real = [w for w in words if re.match(r"^[A-Za-z][A-Za-z'\-]{1,}$", w)]
    if len(real) < 3:
        return False
    return True


def extract_file(path, page, root):
    """Return the reader-facing Segments in one file."""
    with open(path, "r") as fh:
        raw = fh.read()
    rel = os.path.relpath(path, root)
    ext = os.path.splitext(path)[1].lower()
    segs = []

    if ext == ".php":
        src = strip_comments(raw, "php")
        html = _blank_php_blocks(src)
        for text, off in _html_text_segments(html, rel, page):
            segs.append((text, off))
        # PHP code regions: literals only. Walked span by span with real
        # offsets rather than by concatenating, so line numbers stay right.
        segs.extend(_literal_segments_in_spans(src, _php_code_spans(src)))
    elif ext == ".js":
        src = strip_comments(raw, "js")
        segs.extend(_literal_segments(src, rel, page))
    elif ext == ".py":
        return _python_segments(raw, rel, page)
    elif ext == ".json":
        return _json_segments(raw, rel, page)
    else:
        return []

    out = []
    seen = set()
    for text, off in segs:
        cleaned = _clean_literal(_unescape(text)) if ext != ".php" \
            else _clean_literal(_unescape(text))
        cleaned = " ".join(cleaned.split())
        if not looks_like_copy(cleaned):
            continue
        key = cleaned
        if key in seen:
            continue
        seen.add(key)
        out.append(Segment(cleaned, rel, _line_of(raw, off), page))
    return out


def _php_code_spans(src):
    """Offsets of the <?php ... ?> regions."""
    spans = []
    i = 0
    n = len(src)
    while i < n:
        j = src.find("<?php", i)
        if j == -1:
            j = src.find("<?=", i)
        if j == -1:
            break
        end = src.find("?>", j)
        end = n if end == -1 else end + 2
        spans.append((j, end))
        i = end
    if not spans:
        spans = [(0, n)]
    return spans


def _literal_segments_in_spans(src, spans):
    segs = []
    for a, b in spans:
        for text, off in _literal_segments(src[a:b], "", ""):
            segs.append((text, a + off))
    return segs


def _python_segments(raw, rel, page):
    """Extract Python display strings with the real parser.

    Hand-scanning Python quotes is a losing game: an f-string like
    f"{', '.join(names)}" carries quotes inside the braces, and a naive
    scanner desyncs there and then reports a slice of surrounding CODE as
    reader copy. It did exactly that here before this function existed. ast
    also gives docstring identification and line numbers for free.
    """
    try:
        tree = ast.parse(raw)
    except SyntaxError:
        return []

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))

    pieces = []

    def render(node):
        """Flatten a string expression into the text a reader would see."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            out = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out.append(v.value)
                else:
                    out.append("one")     # a value slot, countable as a word
            return "".join(out)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = render(node.left), render(node.right)
            if left is not None and right is not None:
                return left + right
        return None

    for node in ast.walk(tree):
        if isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            if id(node) in docstrings:
                continue
            text = render(node)
            if text:
                pieces.append((text, getattr(node, "lineno", 1)))

    out = []
    seen = set()
    for text, line in pieces:
        t = " ".join(_clean_literal(_unescape(text)).split())
        if not looks_like_copy(t) or t in seen:
            continue
        seen.add(t)
        out.append(Segment(t, rel, line, page))
    return out


def _json_segments(raw, rel, page):
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    out = []
    seen = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            t = " ".join(_clean_literal(node).split())
            if looks_like_copy(t) and t not in seen:
                seen.add(t)
                line = 1
                idx = raw.find(node[:60])
                if idx != -1:
                    line = raw.count("\n", 0, idx) + 1
                out.append(Segment(t, rel, line, page))

    walk(data)
    return out


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

ABBREV = ("u.s", "u.k", "e.g", "i.e", "inc", "ltd", "co", "corp", "no",
          "vs", "approx", "dr", "mr", "ms", "jr", "sr", "st", "fig")

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def sentences(text):
    """Split into sentences, keeping abbreviations and decimals intact."""
    text = " ".join(text.split())
    if not text:
        return []
    parts = SENT_SPLIT.split(text)
    merged = []
    for part in parts:
        if merged:
            prev = merged[-1]
            tokens = prev.rstrip(".").split()
            last = tokens[-1].lower() if tokens else ""
            if last in ABBREV or re.search(r"\d\.$", prev) or len(prev) < 3:
                merged[-1] = prev + " " + part
                continue
        merged.append(part)
    return [s for s in merged if s.strip()]


def words_of(text):
    return re.findall(r"[A-Za-z][A-Za-z'\-]*", text)


VOWELS = "aeiouy"


def syllables(word):
    """Heuristic syllable count. Vowel groups, minus common silent endings."""
    w = word.lower().strip("'-")
    w = re.sub(r"[^a-z]", "", w)
    if not w:
        return 0
    if len(w) <= 3:
        return 1

    count = 0
    prev_vowel = False
    for idx, ch in enumerate(w):
        is_vowel = ch in VOWELS
        # "y" is a vowel in "company" and a glide in "layoff". Following
        # another vowel it joins the consonant side, otherwise "lay-off"
        # collapses into the single vowel run "ayo" and scores one syllable.
        if ch == "y" and idx > 0 and w[idx - 1] in "aeiou":
            is_vowel = False
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel

    # Silent e: "make" is one syllable, "the" is one, "little" is two.
    if w.endswith("e") and not w.endswith(("le", "ee", "ye")):
        if count > 1:
            count -= 1
    if w.endswith("le") and len(w) > 2 and w[-3] not in VOWELS:
        count += 1
    # -ed is usually silent, but only after a consonant other than t or d.
    # "checked" is one syllable, "counted" is two, and "verified" is three:
    # the vowel before the d in "verified" is doing its own work.
    if w.endswith("ed") and len(w) > 3 and w[-3] not in "td" \
            and w[-3] not in "aeiou":
        if count > 1:
            count -= 1
    # -es after a non-sibilant is silent.
    if w.endswith("es") and len(w) > 3 and w[-3] not in "sxzcgh":
        if count > 1:
            count -= 1
    if w.endswith(("ia", "io", "ual", "ual", "eous", "ious")):
        count += 1

    return max(1, count)


def flesch_kincaid_grade(text):
    """Flesch-Kincaid grade level. Returns None when there is too little text."""
    sents = sentences(text)
    words = words_of(text)
    if not sents or len(words) < 6:
        return None
    syl = sum(syllables(w) for w in words)
    return (0.39 * (len(words) / float(len(sents)))
            + 11.8 * (syl / float(len(words)))
            - 15.59)


BE_FORMS = r"(?:is|are|was|were|be|been|being|am|get|gets|got)"
PASSIVE_RE = re.compile(
    r"\b" + BE_FORMS + r"\s+(?:\w+ly\s+)?(?:not\s+)?(\w+)\b", re.I)


def is_passive(sentence):
    for m in PASSIVE_RE.finditer(sentence):
        w = m.group(1).lower()
        if w in IRREGULAR_PARTICIPLES:
            return True
        if w.endswith("ed") and len(w) > 4:
            # "is used", "was reported". Exclude adjectives we know about.
            if w not in ("need", "indeed", "embed", "exceed", "proceed",
                         "succeed", "agreed", "tired", "limited"):
                return True
    return False


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------

class Finding(object):
    def __init__(self, kind, seg, detail, value=None):
        self.kind = kind
        self.seg = seg
        self.detail = detail
        self.value = value

    def format(self):
        head = "%s:%d [%s] %s" % (self.seg.path, self.seg.line, self.seg.page,
                                  self.kind)
        return "%s\n    %s\n    string: %s" % (head, self.detail,
                                               _shorten(self.seg.text, 220))


def _shorten(text, n):
    return text if len(text) <= n else text[:n - 3] + "..."


# A navigation strip: "Sources - How complete - Corrections - Hiring", built
# by joining links with a middot or a pipe. Merging across the inline <a> tags
# is right for a sentence and wrong for a nav bar, and the result reads to a
# word counter as one enormous sentence. Nav is a list of labels; it gets the
# word rules and not the sentence rules.
NAV_SEPARATORS = re.compile(r"[·•|]|&middot;|&bull;")


def is_nav_strip(text):
    return len(NAV_SEPARATORS.findall(text)) >= 2


# Straight single quotes are deliberately NOT here. In prose an apostrophe is
# far commoner than a quotation ("the employer's own words"), and pairing
# apostrophes would open a span across half a paragraph and quietly excuse the
# jargon inside it. A quoted term must use double or curly quotes to be exempt.
QUOTE_PAIRS = [('"', '"'), ("“", "”"), ("‘", "’")]


def _quoted_spans(text):
    """Character spans of the text that sit inside quotation marks."""
    spans = []
    for open_q, close_q in QUOTE_PAIRS:
        if open_q == close_q:
            positions = [m.start() for m in
                         re.finditer(re.escape(open_q), text)]
            for a, b in zip(positions[0::2], positions[1::2]):
                spans.append((a, b + 1))
        else:
            for m in re.finditer(re.escape(open_q) + r"[^" +
                                 re.escape(close_q) + r"]{0,200}" +
                                 re.escape(close_q), text):
                spans.append((m.start(), m.end()))
    return spans


def is_body(seg):
    """Body copy gets the full score. Short labels only get the word rules."""
    w = words_of(seg.text)
    if len(w) < MIN_SEGMENT_WORDS:
        return False
    if is_nav_strip(seg.text):
        return False
    if re.search(r"[.!?]", seg.text):
        return True
    return len(w) >= BODY_SENTENCE_WORDS


def check_segments(segs):
    """Return (findings, page_stats)."""
    findings = []
    pages = {}

    for seg in segs:
        low = seg.text.lower()

        quoted = _quoted_spans(seg.text)
        for bad, better in sorted(BANNED_JARGON.items()):
            m = re.search(r"(?<![\w-])" + re.escape(bad) + r"(?![\w-])", low)
            if not m:
                continue
            # The ban is on OUR voice, not on words we QUOTE. Both products
            # describe the phrases they search for in employer and press
            # language, and "workforce reduction" is one of the real search
            # terms in source_registry.py. Rewriting it out of that list would
            # not improve the copy, it would make the page describe a
            # collector that does not exist. So a banned phrase inside
            # quotation marks passes, and the copy signals to the reader that
            # the term is being reported rather than used.
            if any(a <= m.start() and m.end() <= b for a, b in quoted):
                continue
            fix = ('use "%s" instead' % better) if better \
                else "cut it, it is a superlative"
            findings.append(Finding(
                "banned jargon", seg,
                'the phrase "%s" is on the banned list, %s. '
                "If you are QUOTING the term rather than using it, put it in "
                "quotation marks and this rule steps aside." % (bad, fix)))

        for pat in HEDGE_STACKS:
            m = re.search(pat, low)
            if m:
                findings.append(Finding(
                    "hedging stack", seg,
                    'stacked hedge "%s", keep at most one hedge'
                    % m.group(0)))

        for ch, name in BANNED_CHARS.items():
            if ch in seg.text:
                findings.append(Finding(
                    "banned punctuation", seg,
                    "contains an %s, use a comma, a full stop or a colon"
                    % name))

        if not is_body(seg):
            continue

        sents = sentences(seg.text)
        for s in sents:
            n = len(words_of(s))
            if n > MAX_SENTENCE_WORDS:
                findings.append(Finding(
                    "sentence too long", seg,
                    "%d words, the ceiling is %d, split it"
                    % (n, MAX_SENTENCE_WORDS), n))

        grade = flesch_kincaid_grade(seg.text)
        st = pages.setdefault(seg.page, {
            "grades": [], "sent": 0, "passive": 0, "segs": 0, "words": 0})
        st["segs"] += 1
        st["words"] += len(words_of(seg.text))
        if grade is not None:
            st["grades"].append(grade)
        for s in sents:
            if len(words_of(s)) < 4:
                continue
            st["sent"] += 1
            if is_passive(s):
                st["passive"] += 1

    return findings, pages


def page_report(pages):
    rows = []
    for page in sorted(pages):
        st = pages[page]
        grades = st["grades"]
        mean = sum(grades) / float(len(grades)) if grades else None
        ratio = (st["passive"] / float(st["sent"])) if st["sent"] else 0.0
        rows.append({
            "page": page,
            "mean_grade": mean,
            "passive_ratio": ratio,
            "passive": st["passive"],
            "segments": st["segs"],
            "sentences": st["sent"],
            "words": st["words"],
        })
    return rows


def check_pages(rows):
    findings = []
    for r in rows:
        if r["segments"] < 8:
            continue          # too little copy for a mean to mean anything
        if r["mean_grade"] is not None and r["mean_grade"] > TARGET_GRADE_MEAN:
            findings.append(
                "page '%s' reads at grade %.1f, the ceiling is %.1f "
                "(%d segments, %d words)"
                % (r["page"], r["mean_grade"], TARGET_GRADE_MEAN,
                   r["segments"], r["words"]))
        if r["sentences"] >= 20 and r["passive_ratio"] > MAX_PASSIVE_RATIO:
            findings.append(
                "page '%s' is %.0f%% passive, the ceiling is %.0f%% "
                "(%d of %d sentences)"
                % (r["page"], r["passive_ratio"] * 100,
                   MAX_PASSIVE_RATIO * 100, r["passive"], r["sentences"]))
    return findings


# --------------------------------------------------------------------------
# Per-product target lists. Which files hold copy a reader sees, and which
# page each one belongs to. Generated partials are excluded: they are written
# by a script, and the copy in them is three repeated boilerplate phrases
# around a table of country names.
# --------------------------------------------------------------------------

LAYOFF_TARGETS = [
    ("wordpress-plugin/ai-layoff-tracker/templates/page-tracker.php", "tracker"),
    ("wordpress-plugin/ai-layoff-tracker/templates/page-press.php", "press"),
    ("wordpress-plugin/ai-layoff-tracker/templates/page-methodology.php",
     "methodology"),
    ("wordpress-plugin/ai-layoff-tracker/templates/page-sources.php", "sources"),
    ("wordpress-plugin/ai-layoff-tracker/templates/page-report.php", "report"),
    ("wordpress-plugin/ai-layoff-tracker/templates/page-health.php", "health"),
    ("wordpress-plugin/ai-layoff-tracker/templates/page-facet.php", "facet"),
    ("wordpress-plugin/ai-layoff-tracker/templates/page-ai-quotes.php",
     "ai-quotes"),
    ("wordpress-plugin/ai-layoff-tracker/templates/page-quarterly-report.php",
     "report"),
    ("wordpress-plugin/ai-layoff-tracker/templates/page-company-directory.php",
     "company-directory"),
    ("wordpress-plugin/ai-layoff-tracker/templates/single-layoff.php", "entry"),
    ("wordpress-plugin/ai-layoff-tracker/templates/page-publisher.php",
     "publisher"),
    ("wordpress-plugin/ai-layoff-tracker/templates/page-dashboard.php",
     "tracker"),
    ("wordpress-plugin/ai-layoff-tracker/templates/page-ai-tracker.php",
     "tracker"),
    ("wordpress-plugin/ai-layoff-tracker/assets/layoffs.js", "tracker"),
    ("wordpress-plugin/ai-layoff-tracker/assets/health.js", "health"),
    ("wordpress-plugin/ai-layoff-tracker/assets/widget.js", "widget"),
    ("wordpress-plugin/ai-layoff-tracker/includes/subscribe.php", "email"),
    ("wordpress-plugin/ai-layoff-tracker/includes/contact.php", "contact"),
    ("wordpress-plugin/ai-layoff-tracker/includes/shortcodes.php", "tracker"),
    ("wordpress-plugin/ai-layoff-tracker/includes/export.php", "export"),
    ("wordpress-plugin/ai-layoff-tracker/includes/rss.php", "feed"),
    ("railway/health_digest.py", "email"),
    ("railway/ci_alert.py", "email"),
]

TALENT_TARGETS = [
    ("wordpress-plugin/talent-intelligence-tracker/includes/shortcodes.php",
     "dashboard"),
    ("wordpress-plugin/talent-intelligence-tracker/includes/recall.php",
     "recall"),
    ("wordpress-plugin/talent-intelligence-tracker/includes/places.php",
     "places"),
    ("wordpress-plugin/talent-intelligence-tracker/includes/press.php", "press"),
    ("wordpress-plugin/talent-intelligence-tracker/includes/sources.php",
     "sources"),
    ("wordpress-plugin/talent-intelligence-tracker/includes/corrections.php",
     "corrections"),
    ("wordpress-plugin/talent-intelligence-tracker/includes/company.php",
     "company"),
    ("wordpress-plugin/talent-intelligence-tracker/includes/cross_tracker.php",
     "dashboard"),
    ("wordpress-plugin/talent-intelligence-tracker/includes/board_series.php",
     "company"),
    ("wordpress-plugin/talent-intelligence-tracker/includes/page.php", "meta"),
    ("wordpress-plugin/talent-intelligence-tracker/includes/feed.php", "feed"),
    ("wordpress-plugin/talent-intelligence-tracker/assets/dashboard.js",
     "dashboard"),
    ("health_digest.py", "email"),
    ("ci_alert.py", "email"),
]


def detect_product(root):
    if os.path.isdir(os.path.join(root, "wordpress-plugin",
                                  "ai-layoff-tracker")):
        return "layoff", LAYOFF_TARGETS
    if os.path.isdir(os.path.join(root, "wordpress-plugin",
                                  "talent-intelligence-tracker")):
        return "talent", TALENT_TARGETS
    raise RuntimeError("style_check: cannot tell which product %r is" % root)


def repo_root(start=None):
    here = os.path.abspath(start or os.path.dirname(__file__))
    while True:
        if os.path.isdir(os.path.join(here, ".git")) or \
                os.path.isfile(os.path.join(here, ".git")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            raise RuntimeError("style_check: no repo root above %s" % start)
        here = parent


def collect(root=None):
    """Extract every reader-facing Segment for this product."""
    root = root or repo_root()
    _, targets = detect_product(root)
    segs = []
    for rel, page in targets:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue
        segs.extend(extract_file(path, page, root))
    return segs


def run(root=None):
    """Score the product. Returns (findings, page_rows)."""
    root = root or repo_root()
    segs = collect(root)
    findings, pages = check_segments(segs)
    rows = page_report(pages)
    return findings, rows


def main(argv):
    root = repo_root(argv[1]) if len(argv) > 1 else repo_root()
    product, _ = detect_product(root)
    segs = collect(root)
    findings, pages = check_segments(segs)
    rows = page_report(pages)

    print("=" * 70)
    print("READER-COPY STYLE  (%s)   see docs/STYLE.md" % product)
    print("=" * 70)
    print("%-20s %7s %8s %9s %8s" %
          ("page", "grade", "passive", "segments", "words"))
    for r in rows:
        g = "%.1f" % r["mean_grade"] if r["mean_grade"] is not None else "-"
        print("%-20s %7s %7.0f%% %9d %8d" %
              (r["page"], g, r["passive_ratio"] * 100,
               r["segments"], r["words"]))

    allg = [r["mean_grade"] for r in rows if r["mean_grade"] is not None]
    if allg:
        print("\noverall mean grade: %.2f across %d pages" %
              (sum(allg) / len(allg), len(allg)))

    page_findings = check_pages(rows)
    print("\n%d string finding(s), %d page finding(s)" %
          (len(findings), len(page_findings)))

    by_kind = {}
    for f in findings:
        by_kind.setdefault(f.kind, []).append(f)
    for kind in sorted(by_kind):
        print("\n--- %s (%d) ---" % (kind, len(by_kind[kind])))
        for f in by_kind[kind][:400]:
            print(f.format())
    for p in page_findings:
        print("PAGE: " + p)

    return 1 if (findings or page_findings) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
