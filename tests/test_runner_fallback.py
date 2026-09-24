"""The collectors fall back to a GitHub-hosted runner when the self-hosted one
is gone, and an overdue guardrail never reddens a collection it did not break
(coverage audit 2026-09-24, self-heal slice).

On 2026-09-23 the Contabo runner went offline; collect hung in checkout for 10
minutes and died, EDINET and DART missed their weekly slots, and nothing moved
the work anywhere else. The repo is public, so hosted minutes cost nothing.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

import runner_pick

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
COLLECTORS = {"collect.yml": "collect", "collect-press.yml": "collect-press",
              "collect-structured.yml": "collect"}


def _load(name):
    doc = yaml.safe_load((WF / name).read_text())
    return doc, doc.get("on") or doc.get(True)


# --- the decision ----------------------------------------------------------

def test_a_fresh_heartbeat_keeps_the_self_hosted_runner():
    labels = runner_pick.choose((NOW - timedelta(minutes=40)).isoformat(), now=NOW)
    assert labels == runner_pick.SELF_HOSTED


def test_a_stale_heartbeat_falls_back_to_hosted():
    labels = runner_pick.choose((NOW - timedelta(hours=3)).isoformat(), now=NOW)
    assert labels == runner_pick.HOSTED


def test_no_heartbeat_ever_is_unknown_and_keeps_the_status_quo():
    # Before the heartbeat has ever run (first deploy) there is no evidence
    # the box is down; moving every collector on no evidence is a change of
    # egress (EU -> US) nobody decided.
    assert runner_pick.choose(None, now=NOW) == runner_pick.SELF_HOSTED


def test_the_output_is_json_runs_on_can_read():
    for labels in (runner_pick.SELF_HOSTED, runner_pick.HOSTED):
        assert json.loads(runner_pick.as_output(labels)) == labels


def test_the_latest_success_is_read_from_the_runs_payload():
    payload = {"workflow_runs": [
        {"conclusion": "cancelled", "updated_at": "2026-09-24T11:50:00Z"},
        {"conclusion": "success", "updated_at": "2026-09-24T11:20:00Z"},
        {"conclusion": "success", "updated_at": "2026-09-24T10:50:00Z"}]}
    assert runner_pick.latest_success(payload) == "2026-09-24T11:20:00Z"
    assert runner_pick.latest_success({"workflow_runs": []}) is None


# --- the wiring ------------------------------------------------------------

def test_every_collector_picks_its_runner_first():
    for name, job in COLLECTORS.items():
        doc, _ = _load(name)
        pick = doc["jobs"]["pick-runner"]
        assert pick["runs-on"] == "ubuntu-latest", name
        assert (pick.get("permissions") or {}).get("actions") == "read", name
        main = doc["jobs"][job]
        needs = main["needs"] if isinstance(main["needs"], list) else [main["needs"]]
        assert "pick-runner" in needs, name
        assert "fromJSON(needs.pick-runner.outputs.labels)" in str(main["runs-on"]), name


def test_the_heartbeat_runs_on_the_box_and_needs_nothing_from_it():
    doc, on = _load("runner-heartbeat.yml")
    job = doc["jobs"]["heartbeat"]
    assert job["runs-on"] == ["self-hosted", "linux", "contabo"]
    assert on["schedule"]
    assert not any("checkout" in str(s.get("uses", "")) for s in job["steps"])
    assert doc["concurrency"]["cancel-in-progress"] is True


def test_press_and_structured_split_the_guardrail_from_the_collection():
    for name, job in (("collect-press.yml", "collect-press"),
                      ("collect-structured.yml", "collect")):
        doc, _ = _load(name)
        main = doc["jobs"][job]
        step = next(s for s in main["steps"] if s.get("name") == "Collect")
        run = step["run"]
        assert "TIT_GUARDRAIL_MARKER" in run, name
        assert '"$rc" -ne 4' in run, name
        assert step.get("id") == "collect", name
        assert "guardrail_overdue" in str(main.get("outputs")), name
        g = doc["jobs"]["guardrail-overdue"]
        assert g["runs-on"] == "ubuntu-latest"
        assert "guardrail_overdue == 'true'" in g["if"], name


def test_the_catch_up_writes_tickets_and_syncs_issues_off_the_box():
    doc, on = _load("catch-up.yml")
    assert on["schedule"]
    job = next(iter(doc["jobs"].values()))
    assert job["runs-on"] == "ubuntu-latest"
    text = (WF / "catch-up.yml").read_text()
    assert "collection_schedule.py --enqueue" in text
    assert "collection_schedule.py --issues" in text
    assert "data/writer_queue.json" in text
    assert doc["permissions"].get("issues") == "write"
