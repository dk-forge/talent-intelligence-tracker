# Pending TECHLOG entry — fold into docs/TECHLOG.md, newest-first, then delete this file

(Left as a small separate file rather than a whole-file rewrite of the 1MB
`docs/TECHLOG.md` — see that file's own 2026-09-29 entry on the
`bc1152a`/`8cf81da` incident this is avoiding a repeat of.)

## 2026-10-05 - ops check: `money-basis-check.yml` FAIL (figure contradiction), reported only

**Class:** data-integrity live FAIL (figure), human adjudication required
**Guard:** none new — `correct_money_basis.py --check` is the existing guard; this is its designed FAIL output, not a code defect

Found by the hourly ops check (07:3x UTC run). `money-basis-check.yml`'s latest
completed run (run 37277006785, 2026-10-05 07:18 UTC) went red on its own
built-in `FAIL figure` case, after the prior run (39, 2026-10-04 08:44 UTC) was
green — this is new since the last check.

Exact output: 1 published row is summed at a figure the text attributes to
something other than a raise ("Amazon pledges $1B to data center communities")
— `project cost`, not a raise, for $1.00bn. Row key `741b46d58bccad0199eaa7b62bcbe4c0`.
The check itself names the fix: two referees must adjudicate, which this
routine cannot do per CLAUDE.md ("Never apply a correction spec. An open
data-integrity FAIL is closed by a human; report it, do not touch it."):

```
gh workflow run drain-writers.yml -f enqueue=adjudicate-rows.yml \
  -f inputs_json='{"rows":"741b46d58bccad0199eaa7b62bcbe4c0","reason":"stored amount is a valuation, project cost, purchase price or revenue figure","dry_run":"false"}' \
  -f reason='money figure contradictions'
```

No code pushed, no workflow dispatched. Reported in the same run's
asktherecruiter-sandbox#1259 comment; needs Dakotta to queue the
`adjudicate-rows.yml` ticket above (or rule otherwise).

## Housekeeping note

Branch `claude/ops-log-2026-10-05-money-basis` (commit `25c74373f`) on this
repo accidentally overwrote `docs/TECHLOG.md` to 91 bytes by passing a local
file PATH as the `content` argument of a whole-file write tool instead of the
file's actual text. It was never merged and never touched `main` — `main`'s
`docs/TECHLOG.md` is untouched (confirmed: this PR is based on current
`main` and is additive-only). That branch should be deleted; a human or a
work session with git access can `git push origin --delete
claude/ops-log-2026-10-05-money-basis`.
