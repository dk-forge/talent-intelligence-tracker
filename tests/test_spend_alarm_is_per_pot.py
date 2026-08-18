"""The spend alarm must describe the pot that actually stops collection.

WHY THIS FILE EXISTS
--------------------
On 2026-08-17 the weekly health digest mailed the owner:

    Spend in 2026-08: $12.18 of the $8.00 monthly allowance.
    AT THE CEILING. spend.py --enforce now exits 1, so collection will not
    run until the month rolls over or the allowance in spend.py changes.

Every clause after the first line was false, and it was read as a live outage:

  * No workflow runs `spend.py --enforce`. `collect.yml` and `collect-press.yml`
    run `--degrade`, which exits 0 and switches paid reads off while every free
    collector, the free prefilter and both dedup layers keep running. The
    `--enforce` sentence describes the behaviour that was REMOVED on
    2026-07-30 and was never updated here.
  * Since the two pots landed (budget.py, 2026-08-13) the line that stops
    collection is the COMMITTED pot, not the whole allowance. On the day of
    that email the committed pot held $3.20 of $7.11 against a $6.40 stop
    line, the collectors were running, and four days of priced health rows
    prove it. The $8.98 over the line was catch-up spend that by design
    cannot degrade a scheduled collector.
  * So the digest raised "collection has stopped" on a month where the ONLY
    thing that had stopped was the backfill walkers, which is the two-pot
    design working exactly as written.

An alarm that reports a working budget as an outage is the same defect as an
alarm that mails eight times in an afternoon: the owner learns to discount it,
and the real one arrives into a discounted channel. So the digest now reports
per pot, and the sentence it prints has to match the flag the workflows
actually run.
"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import budget  # noqa: E402
import health_digest  # noqa: E402

NOW = datetime(2026, 8, 17, 13, 0, tzinfo=timezone.utc)

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

# The August 2026 reading, to the cent, so a change to the arithmetic has to
# argue with a real month rather than with a made-up one.
AUGUST_TOTAL = 12.18
AUGUST_COMMITTED = 3.202498
ALLOWANCE = 8.00


def august_spend(**over):
    """The spend dict `health_digest.spend_line()` produced on 2026-08-17."""
    charged = budget.charge({budget.COMMITTED: AUGUST_COMMITTED,
                             budget.DISCRETIONARY: 0.0},
                            month_total=AUGUST_TOTAL)
    pots = budget.pots(ALLOWANCE)
    payload = {
        "month": "2026-08",
        "spent": AUGUST_TOTAL,
        "allowance": ALLOWANCE,
        "committed_spent": charged[budget.COMMITTED],
        "committed_pot": pots[budget.COMMITTED],
        "committed_over": False,
        "discretionary_spent": charged[budget.DISCRETIONARY],
        "discretionary_pot": pots[budget.DISCRETIONARY],
        "discretionary_over": True,
        "at_ceiling": False,
        "total_over": True,
        "lifetime": 29.04,
        "limit": 20.0,
    }
    payload.update(over)
    return payload


class TestTheSentenceMatchesTheCode(unittest.TestCase):
    def test_no_workflow_runs_the_flag_the_digest_names(self):
        """`--enforce` is named in owner-facing copy; nothing runs it."""
        # `run:` steps only. Several files MENTION --enforce in a comment
        # explaining why they do not use it, and those comments are the
        # history this test is protecting rather than the defect it hunts.
        runners = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "run:" in stripped and "spend.py --enforce" in stripped:
                    runners.append(path.name)
        self.assertEqual(
            runners, [],
            "a workflow runs `spend.py --enforce`, so the digest's sentence "
            "about it would be true and this test should be deleted rather "
            "than the sentence")

    def test_the_digest_never_claims_enforce_stops_collection(self):
        _, body = health_digest.build_email(
            health_digest.classify({}, NOW), False, 2, august_spend(), "local")
        self.assertNotIn(
            "--enforce", body,
            "the digest still tells the owner `spend.py --enforce` stops "
            "collection. No workflow has run --enforce since 2026-07-30; the "
            "collect jobs run --degrade, which exits 0 and keeps every free "
            "collector running")


class TestTheAlarmIsPerPot(unittest.TestCase):
    def test_an_overspent_catchup_pot_is_not_a_collection_outage(self):
        """August 2026: total past the line, collectors funded and running."""
        subject, body = health_digest.build_email(
            health_digest.classify({}, NOW), False, 2, august_spend(), "local")
        self.assertNotIn(
            "collection will not run", body,
            "the digest says collection will not run on a month where the "
            "committed pot holds $%.2f of $%.2f and the collectors filed "
            "priced health rows on each of the four days after"
            % (AUGUST_COMMITTED, budget.pots(ALLOWANCE)[budget.COMMITTED]))
        self.assertNotEqual(
            subject, "LLM spend has reached the monthly ceiling",
            "an overspent CATCH-UP pot took the subject line of a collection "
            "outage. The two pots exist precisely so that a backfill cannot "
            "degrade a scheduled collector, and this subject says it did")

    def test_it_names_the_pot_that_is_actually_overspent(self):
        _, body = health_digest.build_email(
            health_digest.classify({}, NOW),
            False, 2, august_spend(), "local")
        self.assertIn(
            "catch-up", body.lower(),
            "the digest reports one total and never says WHICH pot is over, "
            "which is the question the owner has to answer and the only one "
            "that decides whether anything is wrong")

    def test_a_real_collection_stop_is_still_loud(self):
        """The other half. Muting the false alarm must not mute the true one."""
        spend = august_spend(committed_over=True, at_ceiling=True,
                             committed_spent=6.80)
        subject, body = health_digest.build_email(
            health_digest.classify({}, NOW),
            False, 2, spend, "local")
        self.assertIn(
            "paid reads", body.lower(),
            "the committed pot is past its stop line, so paid reads are OFF "
            "for the scheduled collectors and the digest has to say so")
        self.assertEqual(subject, "LLM spend has reached the monthly ceiling")


class TestTheWalkerRationReadsAMeasuredPot(unittest.TestCase):
    """The catch-up pot has no meter of its own, and nothing said so.

    No backfill walker files a priced health row -- `backfill_gdelt_2026.py`,
    `backfill_gnews_2026.py` and `backfill_press_2026.py` print
    `classify.STATS` to the run log and persist none of it. So
    `budget.ledger_spend()` reports `discretionary: 0.0` in every month there
    has ever been, and a walker asking `walker_ration` for its share reads a
    pot that is full by construction.

    `spend.py` knows the real figure (the key's own month delta) and
    reconciles it with `budget.charge`. It just never handed it to the walker.
    """

    LEDGER = {budget.COMMITTED: AUGUST_COMMITTED, budget.DISCRETIONARY: 0.0}

    def test_the_ledger_alone_shows_a_spent_pot_as_untouched(self):
        """The premise, pinned so the fix cannot be read as paranoia."""
        self.assertEqual(self.LEDGER[budget.DISCRETIONARY], 0.0)
        blind = budget.decide(kind=budget.DISCRETIONARY, allowance=ALLOWANCE,
                              charged=self.LEDGER)
        self.assertFalse(
            blind.skip,
            "sanity: an unreconciled ledger has to look funded, or this whole "
            "test class is about nothing")

    def test_a_walker_reconciles_the_month_total_when_it_is_published(self):
        import os

        os.environ[budget.MONTH_SPEND_ENV] = str(AUGUST_TOTAL)
        try:
            decision = budget.decide(kind=budget.DISCRETIONARY,
                                     allowance=ALLOWANCE,
                                     ledger=self.LEDGER)
        finally:
            os.environ.pop(budget.MONTH_SPEND_ENV, None)
        self.assertTrue(
            decision.skip,
            "the catch-up pot is $%.2f and August spent $%.2f of it, but the "
            "walker was handed a live ration of $%.4f because it read the "
            "committed cost ledger, which no walker has ever written a row to"
            % (budget.pots(ALLOWANCE)[budget.DISCRETIONARY],
               AUGUST_TOTAL - AUGUST_COMMITTED, decision.ceiling))
        self.assertEqual(decision.ceiling, 0.0)

    def test_no_published_total_is_unknown_and_not_a_full_pot(self):
        """Absence of a signal is never a pass -- but it is not a stop either.

        With no month total published the ledger stands alone and is a FLOOR,
        exactly as `budget.charge` documents. The run is allowed to proceed on
        it, because failing closed here would stop catch-up work over a
        missing environment variable -- but the reason string has to say the
        pot was UNMEASURED so nobody reads the ration as a measurement.
        """
        import os

        os.environ.pop(budget.MONTH_SPEND_ENV, None)
        decision = budget.decide(kind=budget.DISCRETIONARY,
                                 allowance=ALLOWANCE, ledger=self.LEDGER)
        self.assertIn(
            "unmeasured", decision.reason.lower(),
            "with no authoritative month total the catch-up pot is a FLOOR "
            "and reads as untouched. The ration may still be issued, but the "
            "disclosure has to say it was never measured")

    def test_spend_publishes_the_total_it_already_knows(self):
        """The producer half: --degrade has the number and must pass it on."""
        import os
        import sys
        import tempfile
        import types

        sys.modules.setdefault("requests", types.ModuleType("requests"))
        import spend  # noqa: E402

        self.assertTrue(
            hasattr(spend, "publish_month_total"),
            "spend.py computes the authoritative month total and reconciles "
            "it, then drops it. Every walker in the same job then re-reads "
            "the unreconciled ledger, which has no row for walker spend in "
            "it at all")
        with tempfile.NamedTemporaryFile("w+", delete=False) as fh:
            path = fh.name
        os.environ["GITHUB_ENV"] = path
        os.environ.pop(budget.MONTH_SPEND_ENV, None)
        try:
            spend.publish_month_total(AUGUST_TOTAL)
            with open(path) as fh:
                written = fh.read()
        finally:
            os.environ.pop("GITHUB_ENV", None)
            os.environ.pop(budget.MONTH_SPEND_ENV, None)
            os.unlink(path)
        self.assertIn(
            budget.MONTH_SPEND_ENV, written,
            "--degrade did not write the month total into $GITHUB_ENV, so no "
            "later step of the job can reconcile the ledger against it")
        self.assertEqual(
            budget.published_month_total({budget.MONTH_SPEND_ENV:
                                          written.split("=")[1].strip()}),
            AUGUST_TOTAL)


class TestTheTripwireReadsTheSameCeilingAsEverythingElse(unittest.TestCase):
    """Two ceilings, and only one of them was brought along.

    `run_tripwire.spend_guard`'s own docstring says: "Inventing a second
    ceiling here would mean two numbers to keep in step and one of them
    silently wrong." That is precisely what happened. It was written against
    the WHOLE allowance (`month_delta >= MONTHLY_ALLOWANCE_USD * 0.9`) on
    2026-07-30. `budget.py` split the allowance into two pots on 2026-08-13
    and `spend.py --gate` moved with it. This function did not.

    The consequence is not a red run, which is why nobody saw it. The
    workflow's gate (`spend.py --gate`, committed pot, $3.20 of $7.11) says
    OPEN and the paid step runs; this guard (whole allowance, $12.18 of $7.20)
    says CLOSED and returns 0 having bought nothing. The job is green, the
    `over=true` marker step never fires, and `writer_queue` files the ticket
    as **landed** -- claiming work that was never done, which is exactly what
    the marker exists to prevent. The tripwire has produced one result file
    since 2026-08-02 and read STALE at 384h with every check green.

    The fix is not a third number. It is that this guard asks `budget`, like
    everything else that decides whether to spend.
    """

    def _guard(self, **kwargs):
        import sys
        import types

        sys.modules.setdefault("requests", types.ModuleType("requests"))
        import run_tripwire  # noqa: E402

        return run_tripwire.spend_guard(**kwargs)

    def test_the_guard_can_be_asked_without_a_network(self):
        """It fetched the key unconditionally, so nothing could test it."""
        allowed, why = self._guard(month_total=0.10, month="2026-08",
                                   charged={budget.COMMITTED: 0.10,
                                            budget.DISCRETIONARY: 0.0})
        self.assertTrue(allowed, why)

    def test_an_overspent_catchup_pot_does_not_close_the_tripwire(self):
        """August 2026, the reading that silently stopped discovery."""
        charged = budget.charge({budget.COMMITTED: AUGUST_COMMITTED,
                                 budget.DISCRETIONARY: 0.0},
                                month_total=AUGUST_TOTAL)
        allowed, why = self._guard(month_total=AUGUST_TOTAL, month="2026-08",
                                   charged=charged)
        self.assertTrue(
            allowed,
            "the tripwire refused to spend because the month TOTAL ($%.2f) is "
            "past 90%% of the whole $%.2f allowance. That has not been the "
            "line since 2026-08-13: this run declares no TIT_RUN_KIND, so it "
            "is COMMITTED work and its pot holds $%.2f of $%.2f. "
            "`spend.py --gate` in the same workflow says OPEN, so the two "
            "guards disagree and the run goes green having done nothing. "
            "Guard said: %s"
            % (AUGUST_TOTAL, ALLOWANCE, AUGUST_COMMITTED,
               budget.pots(ALLOWANCE)[budget.COMMITTED], why))

    def test_a_genuinely_spent_committed_pot_still_closes_it(self):
        """The other half: this must not become a way to always spend."""
        allowed, why = self._guard(
            month_total=9.00, month="2026-08",
            charged={budget.COMMITTED: 6.90, budget.DISCRETIONARY: 2.10})
        self.assertFalse(allowed, why)
        self.assertIn("committed", why.lower())

    def test_an_unreadable_meter_still_refuses(self):
        """UNKNOWN is not a licence to spend. Unchanged, and pinned."""
        allowed, why = self._guard(month_total=None, month=None,
                                   charged=None, fetch_error="boom")
        self.assertFalse(allowed)
        self.assertIn("cannot read spend", why)


if __name__ == "__main__":
    unittest.main()


class TestTheProjectionKnowsTodaysCadence(unittest.TestCase):
    """`cost_projection.py` is the tool this repo tells the owner to run.

    It hard-coded `runs_per_day = 2` for all five collectors, seeded from two
    real runs on 2026-07-30. On 2026-08-14 the owner cut `collect.yml` to a
    single daily cron and `collect-press.yml` with it. The table did not move,
    so section [4] reported $23.08/month against a committed cost ledger that
    measured $0.38/day = $11.57/month, and the two could not be reconciled by
    anyone reading either.

    The cadence is now counted off the workflow's own live crons, so the only
    way to make this wrong again is to change what a cron means.
    """

    def _mod(self):
        import sys
        import types

        sys.modules.setdefault("requests", types.ModuleType("requests"))
        import cost_projection  # noqa: E402

        return cost_projection

    def test_the_cadence_matches_the_workflows_live_crons(self):
        cost_projection = self._mod()
        for collector, (_c, _g, _s, _r, per_day) in cost_projection.FUNNEL.items():
            workflow = cost_projection.COLLECTOR_WORKFLOW[collector]
            expected = cost_projection.runs_per_day(workflow)
            self.assertEqual(
                per_day, expected,
                "%s is projected at %d run(s)/day but %s schedules %d. Every "
                "figure in sections [2], [4] and [5] is scaled by this number"
                % (collector, per_day, workflow, expected))

    def test_collect_is_once_daily_and_the_table_agrees(self):
        cost_projection = self._mod()
        self.assertEqual(
            cost_projection.runs_per_day("collect.yml"), 1,
            "collect.yml went once-daily on 2026-08-14 (e60ce7f, df0efdf). If "
            "it has moved again, this number and the FUNNEL follow it "
            "automatically; this assertion is here to make the change visible")
        self.assertEqual(cost_projection.FUNNEL["google_news"][4], 1)

    def test_a_commented_out_cron_is_not_a_run(self):
        cost_projection = self._mod()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "disarmed.yml"
            path.write_text("on:\n  schedule:\n    # - cron: '0 6 * * *'\n"
                            "    # - cron: '0 18 * * *'\n", encoding="utf-8")
            self.assertEqual(
                cost_projection.runs_per_day("disarmed.yml", Path(tmp)), 0,
                "commenting the schedule out is how this repo disarms a job, "
                "so a commented cron must not project recurring spend")
