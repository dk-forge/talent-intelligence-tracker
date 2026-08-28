"""A closed-vocabulary value must fit the DB column that stores it.

THE DEFECT THIS CLOSES. `deal_type` was `VARCHAR(16)`; the vocabulary gained
`outbound_investment` (19 chars). It passed the api.php allowed-list (which
checks membership, not length) and then failed at INSERT with "Processing the
value for the following field failed: deal_type. The supplied value may be too
long or contains invalid data." — on every collector (collect, collect-
structured, collect national press), silently, for as long as any row was
classified as an outbound investment. The allowed-list and the column width had
drifted apart with nothing holding them together.

This test is that thing. It reads the REAL column widths out of db.php and the
REAL vocabularies out of pipeline/vocab.py, so a new vocabulary value that
overflows its column, or a column narrowed below its vocabulary, fails here —
in CI, in milliseconds — instead of at 2 a.m. against a live WordPress insert.
"""
import pathlib
import re

import pytest

import pipeline.vocab as vocab

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_DB_PHP = (_ROOT / "wordpress-plugin/talent-intelligence-tracker/includes/db.php"
           ).read_text(encoding="utf-8")

# Each closed vocabulary and the DB column that stores one of its values. If a
# new vocabulary-backed column is added, add it here — the point is that NOTHING
# ELSE makes these two agree.
_VOCAB_COLUMNS = {
    "deal_type": vocab.DEAL_TYPES,
    "work_mode": vocab.WORK_MODES,
    "site_event": vocab.SITE_EVENTS,
    "headcount_scope": vocab.HEADCOUNT_SCOPES,
    "employer_type": vocab.EMPLOYER_TYPES,
}


def _column_width(column: str) -> int:
    """The VARCHAR(N) width db.php declares for `column`, from the CREATE TABLE."""
    m = re.search(rf"\b{re.escape(column)}\s+VARCHAR\((\d+)\)", _DB_PHP, re.I)
    assert m, f"db.php declares no VARCHAR width for {column!r}"
    return int(m.group(1))


@pytest.mark.parametrize("column,values", sorted(_VOCAB_COLUMNS.items()))
def test_every_vocab_value_fits_its_column(column, values):
    width = _column_width(column)
    over = sorted(v for v in values if isinstance(v, str) and len(v) > width)
    assert not over, (
        f"{column} is VARCHAR({width}) but the vocabulary holds value(s) that "
        f"overflow it: {over}. Widen the column in db.php (and bump TIT_VERSION "
        f"so tit_create_or_update_table's ALTER runs on deploy), or the INSERT "
        f"fails at runtime the way outbound_investment did.")


def test_deal_type_is_wide_enough_for_outbound_investment():
    """The specific regression, pinned by name so a narrowing is unmissable."""
    assert _column_width("deal_type") >= len("outbound_investment")
    assert "outbound_investment" in vocab.DEAL_TYPES
