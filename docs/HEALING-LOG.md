# Healing log — auto-merged fixes

The terse revert index for everything the self-healer merged on its own
(owner authorization 2026-08-14). **Every heal is ONE squash commit: the
revert is `git revert <merge sha>`.** Draft-only mode is one line: set the
repository variable `SELF_HEAL_AUTOMERGE_DISABLED=true`. Newest first; if two
merges race, keep BOTH entries. The narrative for each heal lives in
docs/TECHLOG.md under the same date.

<!-- Entries are appended above this line by `self_heal.py record`, which runs
     from self-heal.yml AFTER a successful auto-merge. Recording is
     best-effort by design: it can never fail a heal, so an empty stretch here
     is not proof no heal happened — cross-check
     `git log --grep 'self-heal: auto-merged'` on main. -->

## (no auto-merged heals yet)

The healer has never merged anything on its own. It is DORMANT until the
`CLAUDE_CODE_OAUTH_TOKEN` secret exists; until then every run gates, prints
what it would have done, and exits green.
