"""How long each collector may stay quiet before that silence is an incident.

One map, imported by BOTH tools that judge staleness. It used to live twice:
health_digest.py carried a per-collector map while ops_status.py applied a
global 36 hours to everything, so the two disagreed about every collector that
was not on the 2x/day cron — ops_status called a five-day-old sec_form_d run
"stale" while the digest called the same row healthy. Two authorities with
different answers is one authority too many: whichever a session read first
set what it believed.

Stdlib-only on purpose. ops_status.py must run with no dependencies installed
(it is the first command of every session, before any venv exists), so nothing
here may import beyond the standard library — and nothing here imports at all.

The numbers are derived from each collector's actual schedule, not picked:

* A source on a 2x/day cron runs every 12 hours. The leash is that cadence
  plus two hours of GitHub queue slack (the observed worst start delay is
  about 1.1h). That is deliberately tighter than the old "two missed runs"
  36h: a missed run IS a coverage hole, and 36h of silence about it meant a
  collector could skip three runs before anything said so.
* A monthly source gets a month plus slack. The old 14-day default marked
  sec_execcomp stale every month, nine days before its next scheduled run.
* A quarterly or dormant source gets a leash long enough that deliberate
  silence is never an alarm. Tighten each the day its schedule is armed.
"""

from __future__ import annotations

# 2x/day cadence (12h) plus queue slack. Shared by everything collect.yml
# sweeps on its 06:00/18:00 cron and by national_press on its 09:00/21:00 one.
TWICE_DAILY_HOURS = 14

MAX_AGE_HOURS = {
    # collect.yml, 06:00 and 18:00 UTC. Since the schedule became a sweep,
    # all four run every scheduled slot, so all four share the cron's leash.
    # Before that, gdelt and the SEC pair were dispatch-only and carried 336h,
    # which stopped being honest the moment the schedule started running them.
    "google_news": TWICE_DAILY_HOURS,
    "gdelt": TWICE_DAILY_HOURS,
    "sec_edgar": TWICE_DAILY_HOURS,
    "sec_form_d": TWICE_DAILY_HOURS,
    # collect-press.yml, 09:00 and 21:00 UTC.
    "national_press": TWICE_DAILY_HOURS,
    # collect-structured.yml. ats_boards is daily and PERISHABLE: a missed day
    # is a hole in a series nothing can back-fill, so one missed run plus
    # slack is the leash. The other two are monthly (the 5th and the 6th), so
    # a month plus slack: the 14-day default used to flag them mid-cycle,
    # which is how a digest teaches people to ignore it.
    "ats_boards": 48,
    "sec_execcomp": 840,   # ~35 days: monthly cron plus room to notice
    "uk_paygap": 840,
    # SEC publishes the Form D DATA SETS once a quarter, so this source is
    # quiet by design between them.
    "sec_form_d_bulk": 2400,   # ~100 days: one quarter, plus room to notice
    # The discovery tripwire ships DORMANT: nothing schedules it, so a manual
    # run followed by weeks of silence is the expected state, not an incident.
    # Tighten this to 336 (twice-weekly cadence, two missed runs) the day the
    # schedule in .github/workflows/tripwire.yml is uncommented.
    "tripwire": 2400,
    # Link rot and archiving. Both ship DORMANT (no cron in their workflows),
    # so a manual run followed by weeks of silence is the expected state
    # rather than an incident. Tighten both to 200 the day their schedules
    # are uncommented: link-check is weekly and archive-sources is daily, so
    # two missed runs is what should start a conversation.
    "link_check": 2400,
    "archive_sources": 2400,
}

# A collector nobody has written a schedule-derived leash for. Two weeks: long
# enough that a new dispatch-only source is not an instant alarm, short enough
# that a forgotten one surfaces inside a month.
DEFAULT_MAX_AGE_HOURS = 336


def max_age_hours(collector: str) -> int:
    """The leash for one collector, by its own schedule."""
    return MAX_AGE_HOURS.get(collector, DEFAULT_MAX_AGE_HOURS)
