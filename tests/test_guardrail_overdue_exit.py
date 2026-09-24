"""An overdue guardrail finding must not paint the COLLECTION red.

From 2026-09-17 one stale finding "past their grace window" made every daily
collect.yml run fail, so a real collection outage (google_news storing zero
rows for five days, TECHLOG 2026-09-24) was indistinguishable from the
guardrail doing its job. The escalation is kept - it now exits with its own
code, and collect.yml turns that code into a separate red job.
"""

import run_collect
from pipeline import publish


import pytest


@pytest.fixture(autouse=True)
def _no_real_db(monkeypatch, tmp_path):
    """_publish() opens the database; never let a test touch data/."""
    from pipeline import schema
    real = schema.connect
    monkeypatch.setattr(schema, "connect", lambda *a, **k: real(tmp_path / "t.db"))


def _raise(exc):
    def f(conn):
        raise exc
    return f


def test_overdue_only_escalation_has_its_own_exit(monkeypatch):
    err = publish.PublishError("1 finding(s) past their grace window")
    err.overdue_only = True
    monkeypatch.setattr(publish, "publish_health", lambda conn: 0)
    monkeypatch.setattr(publish, "publish", _raise(err))
    assert run_collect._publish() == run_collect.GUARDRAIL_OVERDUE_EXIT
    assert run_collect.GUARDRAIL_OVERDUE_EXIT not in (0, 1)


def test_other_publish_failures_stay_exit_1(monkeypatch):
    monkeypatch.setattr(publish, "publish_health", lambda conn: 0)
    monkeypatch.setattr(publish, "publish", _raise(publish.PublishError("WP down")))
    assert run_collect._publish() == 1


def test_escalate_marks_the_error_overdue_only():
    report = {"overdue": [{"check_name": "amount", "subject": "x",
                           "age_hours": 100, "grace_hours": 48}]}
    try:
        publish._escalate(report, 3)
    except publish.PublishError as exc:
        assert getattr(exc, "overdue_only", False) is True
    else:
        raise AssertionError("_escalate must raise on an overdue finding")


def test_collect_yml_splits_the_guardrail_into_its_own_job():
    import pathlib, yaml
    wf = yaml.safe_load(pathlib.Path(".github/workflows/collect.yml").read_text())
    jobs = wf["jobs"]
    assert "guardrail-overdue" in jobs
    assert jobs["guardrail-overdue"]["needs"] == "collect"
    text = pathlib.Path(".github/workflows/collect.yml").read_text()
    assert "GUARDRAIL_OVERDUE_EXIT" in text or "exit code 4" in text or "-eq 4" in text
