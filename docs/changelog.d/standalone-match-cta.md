---
title: Standalone tracker page keeps a tracked free-match CTA
date: 2026-11-23
type: fix
area: growth
files: includes/embed.php, docs/TRACKER_LAUNCH_FIXES.md
tests: Rendered PHP regression, existing tracker launch regression tests, phone and desktop checks
rollback: Revert this change.
env: none
---
The standalone market-trend chart now keeps a named, UTM-tagged link to the free
match check on phone and desktop. Existing source attribution and figures
are retained. The launch review records the already merged code fixes and
separates the remaining operator settings from code. No deployment occurs.
