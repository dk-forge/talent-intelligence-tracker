"""A vocabulary value that does not fit its VARCHAR column is a silent
publish failure, not a validation error: WordPress's insert rejects the row
with "the supplied value may be too long or contains invalid data" and
run_collect exits 1 on the batch. `outbound_investment` (19 chars) shipped
into `vocab.DEAL_TYPES` while `deal_type` stayed `VARCHAR(16)`, and every row
classified with it failed to publish — five in one run on 2026-08-24 before
anyone noticed, because nothing checked the two against each other.

This asserts every column here fits the vocabulary that actually fills it, so
a future vocab addition that overflows its column is a red test instead of a
publish-time surprise.
"""

import re
from pathlib import Path

from pipeline import money_raised, vocab

DB_PHP = (
    Path(__file__).parent.parent
    / "wordpress-plugin"
    / "talent-intelligence-tracker"
    / "includes"
    / "db.php"
)

# column -> the values the pipeline actually writes into it.
COLUMN_VOCABULARIES = {
    "employer_type": vocab.EMPLOYER_TYPES,
    "pillar": vocab.PILLARS,
    "signal_direction": vocab.SIGNAL_DIRECTIONS,
    "headcount_scope": vocab.HEADCOUNT_SCOPES,
    "funding_stage": vocab.FUNDING_STAGES,
    "work_mode": vocab.WORK_MODES,
    "deal_type": vocab.DEAL_TYPES,
    # money_basis holds either the literal "company_raise" or one of
    # money_raised's excluding deal types (pipeline/money_raised.py).
    "money_basis": {"company_raise"} | money_raised.EXCLUDING_DEAL_TYPES,
    "site_event": vocab.SITE_EVENTS,
    "materiality": vocab.MATERIALITY_LEVELS,
    "confidence": vocab.CONFIDENCE_TIERS,
}


def _column_widths():
    sql = DB_PHP.read_text()
    widths = {}
    for name in COLUMN_VOCABULARIES:
        m = re.search(rf"\b{name} VARCHAR\((\d+)\)", sql)
        assert m, f"{name} is not declared VARCHAR(N) in db.php — update the test"
        widths[name] = int(m.group(1))
    return widths


def test_every_vocabulary_value_fits_its_column():
    widths = _column_widths()
    for column, values in COLUMN_VOCABULARIES.items():
        width = widths[column]
        too_long = [v for v in values if len(v) > width]
        assert not too_long, (
            f"{column} is VARCHAR({width}) but these vocabulary values do not "
            f"fit and will fail to publish: {too_long}"
        )
