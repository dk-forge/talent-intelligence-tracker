"""Read a bracketed block out of a PHP file without guessing where it ends.

Several tests assert on PHP the test suite cannot execute, so they read the
source as text. The tempting way to isolate a block is to slice between two
literals::

    block = php[php.index("$orders = array("):php.index("$per_page =")]

That silently couples the assertion to a SECOND, unrelated piece of code: the
end literal must keep existing, and must keep sitting after the start. Several
agents commit to this repo concurrently, so unrelated code moves and gets
renamed constantly, and when the end literal goes the test dies with a bare
``ValueError: substring not found`` that names nothing a reader can act on.

That is exactly how CI broke on 1.36.0: the region-tab test sliced from
``array('Europe'`` to ``array('India'``, and India stopped being a top-level
region when the strip was rebuilt as one exhaustive taxonomy. The property
under test still held. Only the delimiter was wrong.

So: match the brackets instead. The block ends where it actually ends, and
nothing outside it can move without the test still reading the right text.
"""

from __future__ import annotations

_PAIRS = {"(": ")", "[": "]", "{": "}"}


def balanced_block(source: str, opener: str, *, what: str | None = None) -> str:
    """Return the text inside the bracketed block that `opener` opens.

    `opener` is a literal ENDING IN the block's opening bracket, e.g.
    ``"$orders = array("``. That bracket is matched to its partner and the text
    between them is returned. Single- and double-quoted PHP strings are skipped,
    so a bracket inside a string literal cannot close the block early.

    Requiring the bracket to be part of `opener` keeps this unambiguous: there
    is no scanning ahead for "the next bracket", which would happily find the
    parameter list of an unrelated function.

    Raises AssertionError naming what was being looked for, rather than the bare
    ValueError that `str.index` would raise.
    """
    label = what or opener
    assert opener and opener[-1] in _PAIRS, (
        f"balanced_block needs an opener ending in one of {''.join(_PAIRS)}; "
        f"got {opener!r}"
    )
    assert opener in source, (
        f"could not find {label!r} in the source. It was renamed or moved, so "
        f"this test is no longer reading the code it means to assert about."
    )

    opening = opener[-1]
    closing = _PAIRS[opening]
    i = source.index(opener) + len(opener) - 1
    start = i + 1

    depth = 0
    quote = ""
    while i < len(source):
        ch = source[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return source[start:i]
        i += 1

    raise AssertionError(f"{label!r} is never closed; the brackets are unbalanced")
