"""README's cadence, cost and keyed-source claims are generated, not typed
(coverage audit 2026-09-24).

The README said "cron, 2x/day" (once daily since 2026-08-14), "roughly
$0.60/month" (8.86 billed by 2026-09-22 against an $8.00 allowance) and "every
data source is free and keyless" (EDINET, OpenDART and Companies House need
keys). Each was true once and nobody's job to update. Now the block is rendered
from the workflow crons, spend.py and the workflows' secret mappings, and this
test fails when the committed README drifts from them.

Regenerate:  python3 readme_facts.py --write
"""

from __future__ import annotations

from pathlib import Path

import readme_facts

README = Path(__file__).resolve().parent.parent / "README.md"


def test_the_committed_readme_matches_the_config():
    text = README.read_text()
    assert readme_facts.BEGIN in text and readme_facts.END in text
    assert readme_facts.splice(text) == text, (
        "README's generated facts block is stale: python3 readme_facts.py --write")


def test_the_block_states_real_cadence_cost_and_keys():
    block = readme_facts.render()
    assert "daily at 22:00 UTC" in block
    assert "edinet_japan" in block and "weekly" in block
    assert f"${readme_facts.monthly_allowance():.2f}" in block
    for key in ("OPENROUTER_API_KEY", "EDINET_API_KEY_JP", "OPENDART_API_KEY_KR",
                "COMPANIES_HOUSE_API_KEY_UK"):
        assert key in block


def test_the_stale_claims_are_gone():
    text = README.read_text()
    assert "2x/day" not in text
    assert "free and keyless" not in text
    assert "$0.60/month" not in text
