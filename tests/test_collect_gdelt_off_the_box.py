"""gdelt runs from a hosted runner when collect.yml ran on the shared box (#246).

From 2026-10-05 the sibling tracker's 22:00 UTC ingest runs on the same
self-hosted box and walks the same GDELT DOC API, which throttles by IP, so
every gdelt query from the box came back 429 and gdelt stored 0 rows per run.
"""
from pathlib import Path

import yaml

WF = Path(__file__).parent.parent / ".github/workflows/collect.yml"


def _wf():
    return yaml.safe_load(WF.read_text())


def test_the_box_hands_gdelt_to_the_hosted_job():
    run = next(s for s in _wf()["jobs"]["collect"]["steps"]
               if s.get("name") == "Collect")["run"]
    assert '"$source" = "gdelt"' in run and "RUNNER_ENVIRONMENT" in run
    assert "self-hosted" in run and "continue" in run


def test_the_hosted_gdelt_job_runs_after_collect_and_commits():
    job = _wf()["jobs"]["collect-gdelt"]
    assert job["runs-on"] == "ubuntu-latest"
    assert "collect" in job["needs"] and "pick-runner" in job["needs"]
    assert "self-hosted" in job["if"] and "schedule" in job["if"]
    runs = "\n".join(s.get("run") or "" for s in job["steps"])
    assert "--source gdelt" in runs and "--publish" in runs
    assert "python spend.py --degrade" in runs
    assert "merge_db.py" in runs and "git push" in runs
    assert "TIT_READTHROUGH_CAP" in str(job["steps"])
