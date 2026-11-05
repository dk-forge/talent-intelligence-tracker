"""The weekly cache prune must stay scheduled, scoped and age-limited.

The repo's 10 GB Actions cache hit 100% on 2026-11-05. The prune deletes only
caches unused for MAX_AGE_DAYS (default 7, never below 1), with the least
permission that can delete a cache.
"""
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "actions-cache-prune.yml"


def _wf():
    return yaml.safe_load(WF.read_text())


def test_runs_weekly():
    on = _wf()[True]  # pyyaml reads the bare key 'on' as True
    crons = [s["cron"] for s in on["schedule"]]
    assert len(crons) == 1 and crons[0].split()[2:] == ["*", "*", "0"]


def test_least_privilege():
    assert _wf()["permissions"] == {"actions": "write"}


def test_age_limited_and_floor():
    text = WF.read_text()
    assert "MAX_AGE_DAYS: ${{ inputs.max_age_days || '7' }}" in text
    assert "if days < 1:" in text
    assert "if seen < cutoff:" in text
