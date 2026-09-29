# Tech Log — Talent Intelligence Tracker

**INCIDENT 2026-09-29 21:06 UTC — this file was accidentally overwritten by the hourly ops-check session and needs a manual restore. The full history is NOT lost; it is sitting in git history untouched. See below before adding anything new.**

## RECOVERY (do this first, then delete this notice)

The ops-check session called `create_or_update_file` with a literal placeholder
string instead of this file's real content, wiping ~975KB of chronological
history down to a few bytes, in commit `bc1152a641f3500a04a421bfa554641200204407`
("docs(techlog): close stale claude-autofix #194, already fixed by #193
[skip ci]"). That commit changed ONLY this one file; nothing else on `main`
was touched, and nothing has landed on `main` since.

The prior, complete content is untouched at:
- parent commit: `00c0c40bc0764c18886588f7af8981fb0642b7c6`
- blob SHA: `787b41f7773c145445528deea8c6c1e48f87f2f9`

**One-line fix from a local clone with push access:**

```bash
git revert bc1152a641f3500a04a421bfa554641200204407 --no-edit
git push origin main
```

That cleanly restores the full log (verified: `bc1152a` is a direct child of
`00c0c40` with no intervening commits touching this path, so the revert has
zero conflicts). After restoring, re-append the one entry that was meant to
be added — "hourly ops check: closed stale claude-autofix issue #194 (MDD
vocab gap), already fixed by #193" — and delete this incident notice.

The ops-check session could not perform this revert itself: it has no `gh`
CLI or direct GitHub API access (MCP tools only), and this file's real
content (~996KB, ~373K tokens) exceeds what the available `create_or_update_file`
tool can safely transmit in one call without risking a silent truncation —
attempting it risked making the corruption worse and harder to diagnose, so
it stopped and is asking for this one manual command instead.

Reported live via a push notification and as the `ACTION:` line on the
hourly ops-check comment on asktherecruiter-sandbox#1259.
