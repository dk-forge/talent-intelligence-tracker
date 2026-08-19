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

# Once-daily cadence (24h) plus queue slack. Shared by everything collect.yml
# sweeps on its 22:00 UTC cron and by national_press on collect-press.yml's
# 23:00 one (the same once-daily owner decision, e60ce7f then df0efdf; the
# pair moved from 16:00/17:00 to after the US close in 01e1737). The
# old name here was TWICE_DAILY_HOURS = 14, and the day the schedule went
# once-daily that leash became permanent noise: every collector read stale for
# ten hours before its next scheduled run, which is the 2-day-ceiling-on-a-
# weekly-job mistake the sibling wrote down. tests/test_ingest_schedule.py now
# binds this number to collect.yml's real cadence.
SCHEDULED_SWEEP_HOURS = 26

MAX_AGE_HOURS = {
    # collect.yml, 22:00 UTC daily. Since the schedule became a sweep, all
    # four run every scheduled slot, so all four share the cron's leash.
    # Before that, gdelt and the SEC pair were dispatch-only and carried 336h,
    # which stopped being honest the moment the schedule started running them.
    "google_news": SCHEDULED_SWEEP_HOURS,
    "gdelt": SCHEDULED_SWEEP_HOURS,
    "sec_edgar": SCHEDULED_SWEEP_HOURS,
    "sec_form_d": SCHEDULED_SWEEP_HOURS,
    # collect-press.yml, 23:00 UTC daily, an hour behind collect by the same
    # owner decision. It was 09:00, which collided with collect-structured's
    # daily cron inside the shared `talent-collect` lock and got the pending
    # press run cancelled most mornings — the leash was right and the schedule
    # was not. See the comment on that workflow's cron.
    "national_press": SCHEDULED_SWEEP_HOURS,
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
    # Israel and Singapore both ship DORMANT: neither has a cron anywhere, so
    # neither files a health row and neither of these leashes ticks yet. They
    # are entered now rather than later so that arming one is a schedule
    # decision alone and nobody has to remember a second file.
    #
    # TIGHTEN BOTH THE DAY A CRON IS CHOSEN, and note they want different
    # numbers, because the two sources are not the same shape.
    #
    # israel_registrar reads a rolling ONE-YEAR changes file, so a missed run
    # is recoverable for a year simply by widening TIT_IL_DAYS, and nothing is
    # ever lost permanently. That is the most forgiving source in the tracker.
    # On a weekly slot it wants 180, the same as the six above.
    "israel_registrar": 2400,   # ~100 days: DORMANT, no cron, no health row
    # singapore_acra reads a MONTHLY snapshot, so its natural cadence is
    # monthly and a weekly leash would be permanent noise: the file simply does
    # not change between most runs. On a monthly slot it wants roughly 840
    # (~35 days, one refresh plus room to notice). It is also the one source
    # here whose gap cannot be closed by widening a window, because an
    # incorporation date is stated on the snapshot rather than accumulated: a
    # company incorporated and struck off between two readings is never in any
    # file, and no window reaches it.
    "singapore_acra": 2400,     # ~100 days: DORMANT, no cron, no health row
    # SEC publishes the Form D DATA SETS once a quarter, so this source is
    # quiet by design between them.
    "sec_form_d_bulk": 2400,   # ~100 days: one quarter, plus room to notice
    # The discovery tripwire, ARMED 2026-07-30 and leashed as though it were
    # not until 2026-08-02. This entry used to read "ships DORMANT: nothing
    # schedules it", and told the next person to tighten it "the day the
    # schedule in .github/workflows/tripwire.yml is uncommented". That day never
    # came and never could: arming it meant DELETING that cron and moving the
    # slot to schedule-link-hygiene.yml, which is not a lock member — so the
    # instruction's own trigger was written against a line that arming removes.
    # The result was a live twice-weekly collector wearing a 100-day leash: it
    # could have broken on a Monday and reported `ok` until November.
    #
    # The Mon+Thu 07:00 UTC pair in schedule-link-hygiene.yml is a WORST GAP OF
    # 96 HOURS (Thu -> Mon), not 3.5 days. This entry read 336 until 2026-08-18
    # — four missed runs before it spoke — and 336 was wide because the leash
    # was carrying a second job it should never have had: until the same day,
    # the tripwire filed NO health row at all when it declined to spend, so its
    # newest row was the last run that bought something and the leash had to
    # tolerate a whole binding month. It did not tolerate it; it reported
    # "tripwire last ran 406h ago — stale" about a collector that ran green on
    # 08-10, 08-13 and 08-17. Widening it further would have bought silence
    # about a genuinely dead job instead.
    #
    # The fix was the missing row (run_tripwire.report_declined -> status
    # `skipped`), and with it every scheduled slot now leaves evidence whether
    # it spends or not. So the leash can be what the schedule says it is: 96h of
    # cadence plus the queue's worst-case wait, since the slot writes a TICKET
    # and drain-writers dispatches it into an empty lock group — a run may
    # legitimately wait behind a backfill slice entitled to its 350 minutes.
    # One missed pair, not four.
    "tripwire": 168,           # 7 days: the 96h Thu->Mon gap, plus the queue's wait
    # The benchmark-diff chase, weekly on Tuesdays from the same scheduler
    # and DORMANT until the owner arms a BENCHMARK_* secret. While dormant it
    # files no health row at all, so this leash never ticks; once armed, one
    # missed weekly slot plus the queue's worst-case wait is the widest quiet
    # a healthy loop can produce. If the owner disarms it later, delete its
    # slot from schedule-link-hygiene.yml in the same change or the last
    # armed run ages into a permanent STALE that means only "disarmed".
    "benchmark_chase": 384,    # ~16 days: weekly, plus the queue's wait
    # The historical press walker ships DISPATCH-ONLY: there is no cron in
    # .github/workflows/backfill-press-2026.yml and adding one is both a spend
    # decision and a writer-lock decision. So a run followed by weeks of silence
    # is the expected state and not an incident, the same shape as `tripwire`
    # above. It also reads HISTORY rather than today's news, so "when did it
    # last run" is a question about the owner's pace and not about coverage
    # going quiet. Tighten this the day a schedule is chosen.
    "press_archive": 2400,
    # primary_chase is DISPATCH-ONLY, and collect.yml's own comment says why:
    # "its population is a hand-made list of URLs, so a cron would re-fetch
    # documents that have already been read". It appears in that workflow's
    # `source` choice list only so a run can be QUEUED through drain-writers
    # like every other database writer — never in the scheduled sweep.
    #
    # It had NO entry here until 2026-08-18, so it fell through to
    # DEFAULT_MAX_AGE_HOURS and wore a 336h leash nobody chose. It then reported
    # "last ran 360h ago — stale (its schedule expects a run within 336h)"
    # about a job that has no schedule to expect anything of. That default is a
    # backstop for a source nobody has thought about yet; spending it as a
    # verdict on a source somebody deliberately left unscheduled is how a report
    # earns a false alarm. Same shape and same number as press_archive above.
    "primary_chase": 2400,
    # The two recall families, recall.yml, Mondays 08:00 UTC. Both file a health
    # row from measure_recall.py on every run, one per family
    # (analysis/recall/family.py owns which is which). Weekly cadence plus a day
    # of slack: one missed Monday is a measurement nobody took, which is worth
    # saying, and 336 would have let a dead measurement sit for a fortnight
    # while the published rate went on looking current.
    #
    # Neither had an entry until 2026-08-18. They were found by the test that
    # now refuses a collector with no chosen leash, rather than by anybody
    # noticing — which is the whole argument for that test.
    "recall": 192,             # 8 days: weekly, plus a day of slack
    "recall_us": 192,
    # Link rot and archiving, both scheduled since 2026-07-30 — but by
    # schedule-link-hygiene.yml writing a TICKET rather than by a cron in their
    # own workflows, because both are database writers and a cron in a
    # lock-group workflow gets evicted or evicts something else. So each leash
    # is the cadence plus the queue's worst-case wait, not just GitHub's start
    # delay: a ticket written while a long backfill holds the writer lock waits
    # for it, and a 350-minute slice is entitled to its 350 minutes.
    #
    # archive_sources: every EIGHT hours since 2026-07-31 (nightly -> 3h on
    # 2026-07-30 -> 8h). The cadence came down because each run holds the single
    # writer lock for up to 25 minutes and eight runs a day is 200 minutes of it;
    # the leash did NOT need to move with it, which is the point of deriving it
    # from queue slack rather than from the interval. A single skipped slot is
    # not worth a page — the candidate list IS the gap, so the next run simply
    # picks up what the last one did not — but three slots in a row producing
    # nothing is a job that has stopped. 26h is a full day of missed slots plus
    # queue slack, and it is deliberately not interval-plus-slack:
    # a ticket written while a 350-minute backfill holds the writer lock is
    # entitled to those 350 minutes, and a leash that pages for that would be an
    # alarm for the queue working as designed.
    #
    # Note what this leash does NOT catch, and what does: a run dispatched by
    # hand with the default dry_run=true records nothing to source_health, so it
    # cannot refresh this clock. A schedule that quietly went dry therefore
    # still ages into STALE. That is on purpose (it happened on 2026-07-30, run
    # 30507215991, and went unnoticed for a day).
    "archive_sources": 26,
    # link_check: daily at 05:30 UTC since 2026-07-30 (was weekly on Mondays).
    # One missed day plus queue slack. Weekly plus slack used to be 180h, which
    # meant a checker could be dead for a week and a half before anything said
    # so, while the Monday digest read its silence as "nothing has rotted".
    "link_check": 36,
}

# A collector nobody has written a schedule-derived leash for. Two weeks: long
# enough that a new dispatch-only source is not an instant alarm, short enough
# that a forgotten one surfaces inside a month.
DEFAULT_MAX_AGE_HOURS = 336


def max_age_hours(collector: str) -> int:
    """The leash for one collector, by its own schedule."""
    return MAX_AGE_HOURS.get(collector, DEFAULT_MAX_AGE_HOURS)
