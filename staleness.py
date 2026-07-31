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
    # collect-structured.yml, weekly on Mondays. India files leadership
    # disclosures every business day, so the window is 7 days and the leash is
    # one missed run plus slack rather than a month: unlike the two annual
    # returns above, a fortnight of silence here is a fortnight of a working
    # daily feed going uncollected. Not perishable though — the API answers
    # arbitrary date ranges, so a gap can be back-filled with TIT_BSE_DAYS.
    "bse_india": 180,      # ~7.5 days: weekly cron plus room to notice
    # collect-structured.yml, weekly on Mondays, same shape as bse_india: the
    # window is 7 days and the API answers any calendar date, so a missed week
    # is back-fillable with TIT_EDINET_DAYS rather than lost. Not perishable.
    # Deliberately the same leash and NOT a longer one just because Japan's
    # clause is thin: the leash measures whether the COLLECTOR ran, not whether
    # Japan filed anything, and those are different questions. A week where the
    # clause genuinely fires for nobody is reported through the read count
    # (edinet_japan.LAST_RUN["read"]), not by letting the run go quiet.
    "edinet_japan": 180,   # ~7.5 days: weekly cron plus room to notice
    # collect-structured.yml, weekly on WEDNESDAYS (Monday is bse_india and
    # Tuesday is edinet_japan; one writer lock, one slot each). Same shape as
    # both of those otherwise, and the leash is the cadence plus slack. Korea's
    # allowlisted leadership items ran 12 to 49 a week over the twelve weeks to
    # 2026-07-29, so a week that produces none has not gone quiet — it has
    # broken, and the collector already refuses below its floor. The API answers
    # any date range up to three months, so a missed week is back-fillable with
    # TIT_DART_DAYS rather than lost.
    "opendart_korea": 180,  # ~7.5 days: weekly cron plus room to notice
    # collect-structured.yml, weekly on THURSDAYS (Monday bse_india, Tuesday
    # edinet_japan, Wednesday opendart_korea; one writer lock, one slot each).
    # The leash is the CADENCE and not the rotation: a run reads one of four
    # roster slices, so the whole UK roster is swept every 4 weeks, but a run is
    # still due every week and 4 weeks of tolerance would hide three missed
    # ones. The rotation's own tolerance lives in the collector's window, which
    # is derived as 4x7+14 days precisely so a missed slice is picked up on its
    # next visit rather than becoming a hole.
    "companies_house": 180,  # ~7.5 days: weekly cron plus room to notice
    # collect-structured.yml, weekly on FRIDAYS (Monday bse_india, Tuesday
    # edinet_japan, Wednesday opendart_korea, Thursday companies_house; one
    # writer lock, one slot each). Cadence plus slack, the same shape as the
    # four above. Czechia's change feed publishes daily and the collector reads
    # a 14-day window, so a missed week is recoverable by widening
    # TIT_ARES_DAYS — but only up to 28 days, because that is all the
    # notification feed holds and it answers a wider request with the batches it
    # has rather than an error. Two consecutive misses are therefore the point
    # at which a hole becomes permanent, which is why the leash flags part-way
    # into the second week rather than after two full misses.
    "czechia_ares": 180,     # ~7.5 days: weekly cron plus room to notice
    # collect-structured.yml, weekly on SATURDAYS, same argument one day
    # further along. Estonia is the opposite of perishable in one way and worse
    # than perishable in another: the published file is a fresh full snapshot
    # every day, so a missed run is recovered simply by widening TIT_EE_DAYS
    # over the next one, but the file holds CURRENT office-holders only — so an
    # appointment made and ended between two runs vanishes from the source
    # entirely and no window can reach it. Cadence plus slack, not a month.
    "estonia_ariregister": 180,  # ~7.5 days: weekly cron plus room to notice
    # collect-structured.yml, weekly on SUNDAYS — the last day of the week that
    # no other database writer holds, which is why Spain is the seventh and
    # last weekly structured slot this schedule has room for. Cadence plus
    # slack, the same shape as the five above. BORME's archive is permanent and
    # the summary API answers any past date, so a missed week is recovered by
    # widening TIT_BORME_DAYS up to MAX_DAYS and nothing is lost permanently.
    "spain_borme": 180,      # ~7.5 days: weekly cron plus room to notice
    # SEC publishes the Form D DATA SETS once a quarter, so this source is
    # quiet by design between them.
    "sec_form_d_bulk": 2400,   # ~100 days: one quarter, plus room to notice
    # The discovery tripwire ships DORMANT: nothing schedules it, so a manual
    # run followed by weeks of silence is the expected state, not an incident.
    # Tighten this to 336 (twice-weekly cadence, two missed runs) the day the
    # schedule in .github/workflows/tripwire.yml is uncommented.
    "tripwire": 2400,
    # The historical press walker ships DISPATCH-ONLY: there is no cron in
    # .github/workflows/backfill-press-2026.yml and adding one is both a spend
    # decision and a writer-lock decision. So a run followed by weeks of silence
    # is the expected state and not an incident, the same shape as `tripwire`
    # above. It also reads HISTORY rather than today's news, so "when did it
    # last run" is a question about the owner's pace and not about coverage
    # going quiet. Tighten this the day a schedule is chosen.
    "press_archive": 2400,
    # Link rot and archiving, both scheduled since 2026-07-30 — but by
    # schedule-link-hygiene.yml writing a TICKET rather than by a cron in their
    # own workflows, because both are database writers and a cron in a
    # lock-group workflow gets evicted or evicts something else. So each leash
    # is the cadence plus the queue's worst-case wait, not just GitHub's start
    # delay: a ticket written while a long backfill holds the writer lock waits
    # for it, and a 350-minute slice is entitled to its 350 minutes.
    #
    # archive_sources: nightly (24h). Two missed nights (48h) plus ~6h of
    # worst-case queue wait. A single skipped night is not worth a page: the
    # candidate list is the GAP, so tomorrow's run simply picks up what
    # yesterday's did not.
    "archive_sources": 54,
    # link_check: weekly on Mondays (168h). One missed Monday is a fortnight of
    # not knowing whether anything we cite has rotted, which is too long, so
    # this is the weekly-cron shape bse_india already uses — cadence plus slack,
    # flagging part-way into the second week rather than after two full misses.
    "link_check": 180,
}

# A collector nobody has written a schedule-derived leash for. Two weeks: long
# enough that a new dispatch-only source is not an instant alarm, short enough
# that a forgotten one surfaces inside a month.
DEFAULT_MAX_AGE_HOURS = 336


def max_age_hours(collector: str) -> int:
    """The leash for one collector, by its own schedule."""
    return MAX_AGE_HOURS.get(collector, DEFAULT_MAX_AGE_HOURS)
