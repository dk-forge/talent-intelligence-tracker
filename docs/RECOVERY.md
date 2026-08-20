# Recovery

**For the person reading this at 2am with the site down.** Work top to bottom.
Nothing here needs a decision until step 3.

The short version: **the data is safe and the website is not.** Every row this
project has ever collected is committed to this repository as
`data/talent_intel.db`, and the live WordPress table is built out of it by
re-running one push. What is not in any repository is WordPress itself: the blog
posts, the uploads, the theme, the other plugins, and the email subscriber list.
Section 4 is the honest list, and it is the part worth reading before you need
it.

---

## 1. First: is it the host, or is it the data?

Two commands, in this order. Neither writes anything.

```bash
python3 ops_status.py     # the data, offline, no keys
python3 ci_status.py      # the runs behind it (needs gh + network)
```

Read `[2f] HOST` in the first one. It is the offline read of
`data/host_status.json`, which `host-watch.yml` writes every 15 minutes from
GitHub, not from the host, so it still answers when Bluehost does not.

| What you see | What it means | Go to |
|---|---|---|
| `[2f] HOST` says down or stale, `[1] DATA` looks normal | Somebody else's server is off. Your rows are fine. | Section 2, then wait |
| `[1] DATA` counts have dropped, or the database will not open | The repository's own copy is damaged | Section 3 |
| `ci_status.py` exits 3 (no gh, no credential, no network) | You could not check. That is not an all clear. | Get a network, then re-run |
| `[6] BACKUP` is FAIL | The committed database is not restorable as it stands | Section 3, and read the named check |

A sustained host outage (three consecutive failed probes) opens **one** GitHub
issue in this repository. That channel is deliberately not on the host. If there
is no issue and the site is down, the outage is younger than about 45 minutes.

**Do not start a restore because the site is down.** A host outage needs
patience, not a rebuild. Restore only when the data itself is wrong, or when you
are genuinely moving to a new host.

---

## 2. Where the data is

One file, committed on every collect run:

```bash
git log --oneline -- data/talent_intel.db | head        # every version ever pushed
git log --oneline -- data/talent_intel.db | wc -l       # 503 versions as of 2026-08-19
```

Any past version can be handed back whole, with every column:

```bash
git show <sha>:data/talent_intel.db > /tmp/restored.db
sqlite3 /tmp/restored.db "PRAGMA integrity_check;"
sqlite3 /tmp/restored.db "SELECT COUNT(*) FROM signals;"
```

Write it to a **new path**, never over `data/talent_intel.db`. Restoring by
copying a file over the tracked one is what destroyed 9,572 signal rows on
2026-07-28. If you are putting rows back into the current database rather than
replacing it, the tool is `merge_db.py`, which merges by row identity:

```bash
python3 merge_db.py /tmp/restored.db data/talent_intel.db
```

`restore_lost_rows.py` is the worked example of walking the file's own history
and merging each version forward. Read its docstring before doing this by hand.

The current state, for comparison when you are deciding whether something is
wrong. These are the numbers `data/backup_check.json` recorded on 2026-08-20,
and that file is the authority rather than this table:

| table | rows |
|---|---|
| signals | 33,369 |
| seen_urls | 56,103 |
| source_links | 6,448 |
| employer_identity | 5,457 |
| source_health | 329 |
| publish_guardrails | 39 |
| funding_corroborations | 0 |

78.6 MiB, growing about 650 KB and about 180 signal rows a day. Measured over
2026-08-05 to 2026-08-20, so roughly 240 MB and 65,000 signal rows a year.

---

## 3. Rebuilding the live site from the committed database

This is the sequence for a new host, and it is the same sequence for a
`wp_tit_signals` table that has been lost or corrupted on the current one.

**Step 0. Stop the collectors from publishing into a half-built site.** Comment
out the `schedule:` line in `.github/workflows/collect.yml` and
`collect-press.yml` and push. Turn them back on at the end. A collect run that
lands mid-restore is not dangerous to the data, but it will confuse every count
you are about to check.

**Step 1. Stand WordPress up.** New host, new WordPress, `/blog` install, site
URL `https://<domain>/blog`. Nothing in this repository can do this for you and
nothing in it is a backup of the old install. See section 4.

**Step 2. Install the plugin.** The source of truth is
`wordpress-plugin/talent-intelligence-tracker/`, and it is entirely in this
repository. Either upload that directory to `wp-content/plugins/` by hand and
activate it, or point the FTPS secrets at the new host and run the deploy
workflow. On activation the plugin creates `{prefix}tit_signals` itself
(`tit_create_or_update_table()` in `includes/db.php`), so the table does not
need to be restored, only refilled.

Secrets the rebuild needs, by name (they live in GitHub Actions settings, never
in the repository): `WP_SITE_URL`, `WP_API_KEY`, `FTP_HOST`, `FTP_PORT`,
`FTP_USERNAME`, `FTP_PASSWORD`, `WP_PLUGIN_REMOTE_DIR`. Collection also needs
`OPENROUTER_API_KEY`. If the old host is gone, all of the FTPS ones are new
values and the API key is whatever you set on the new install.

**Step 3. Offer every row for publication again.** Rows carry a local
`published_at` marker, and `publish()` only sends rows where it is NULL. A fresh
site holds nothing, so the marker has to be cleared before anything will be
sent:

```bash
cp data/talent_intel.db /tmp/republish.db          # work on a copy first
sqlite3 /tmp/republish.db "UPDATE signals SET published_at = NULL;"
```

Check what that would send, without sending it:

```python
python - <<'PY'
import sqlite3
from pipeline import publish
conn = sqlite3.connect("/tmp/republish.db"); conn.row_factory = sqlite3.Row
print(publish.publish(conn, dry_run=True))
PY
```

Measured on 2026-08-19 this reports `would_send: 31157, quarantined: 7`, which
is every current row except the ones an unanswered publish guardrail is
holding. If your number is far from the current signal count, stop and find out
why before sending anything.

**Step 4. Send them.** When the dry run looks right, do it against the real
database:

```bash
sqlite3 data/talent_intel.db "UPDATE signals SET published_at = NULL;"
WP_SITE_URL=https://<domain>/blog WP_API_KEY=<key> python - <<'PY'
import sqlite3
from pipeline import publish
conn = sqlite3.connect("data/talent_intel.db"); conn.row_factory = sqlite3.Row
print(publish.publish(conn))
PY
```

It sends in batches of 25, marks only what the server accepted, and is safe to
re-run: a content hash the server already holds is a duplicate rather than an
error. Expect this to take a while and to be interrupted at least once on shared
hosting. Re-run it.

**Step 5. Push the derived columns.** Funding amounts in USD, tickers, CIKs,
headquarters and the Wayback snapshots travel on a second path, because they are
learned after a row is published:

```bash
gh workflow run enrich.yml -R dk-forge/talent-intelligence-tracker \
     --ref main -f dry_run=false
```

Skipping this leaves the money charts showing a fraction of the real total. That
is not hypothetical: on 2026-07-28 the local database held $20.79bn of parsed
funding while the site showed $3.2M.

**Step 6. Commit the database.** `published_at` has changed on every row, and
that state has to be pushed or the next collect run will offer all 31,000 rows
again.

```bash
git add data/talent_intel.db && git commit -m "restore: republished to the new host"
git push
```

This is safe as a plain local commit **only because step 0 disarmed the
collectors**, so nothing else is writing the database while you do it. If you
skipped step 0, stop and do it now: a scheduled writer that pushes while you are
holding a modified copy is the exact race that cost 9,572 rows, and no amount of
care at this end prevents it. Every automated database write goes through
`drain-writers.yml` for the same reason.

**The dashboard's own URLs survive this.** Company and place pages are routed
by a slug derived from the company or place name, never by the WordPress row id,
so a republished row lands back on the same permalink it had before even though
its `row_id` is new. That is worth knowing because it means a rebuild does not
cost the tracker's own search rankings. It does nothing for the blog posts,
which are a different problem (section 4, item 1).

**Step 7. Check the four surfaces**, then re-arm the crons you commented out in
step 0. `python3 ops_status.py` and `python3 published_figures.py` are the two
that will tell you whether a reader is seeing the right numbers.

---

## 4. What this backup does NOT cover

Read this section before you need it. Everything below is on Bluehost and
nowhere else. If the host disappears tonight, it is gone.

**1. `wp_posts`: the blog. Not backed up anywhere in this repository.**
Every article on asktherecruiter.com/blog, every page, every revision, every
category, every permalink that a search engine has indexed. This is the SEO
asset, it is years of writing, and no part of it is in this repo or the
sibling's. **A WordPress export (Tools, Export, All content) produces one XML
file that covers this**, and Bluehost's own account backups cover it too. Both
are outside this repository and neither happens automatically today. If you do
one thing after reading this document, do that one.

**2. Uploads and media.** `wp-content/uploads/`: every image in every post,
including the ones a post will 404 a broken image for after a restore. Not in
any repository. Covered by a Bluehost account backup, and partly by the
WordPress export (which references the files but does not contain them).

**3. The WordPress install itself.** The theme, its customisations, the other
plugins and their settings, `wp_options`, users, permalinks structure, and
`.htaccess`. Not in any repository. **One item in here is load-bearing for this
project specifically**: a CSS rule stored in the WordPress database, attached to
core's `wp-block-library` handle and in neither repo, sets
`color:#2a2a2a !important` on `.entry-content p`. The plugin's stylesheet is
written to beat it. On a clean install that rule will not exist, the dashboard
will still render correctly, and anybody reading `contrast_audit.py` afterwards
will be confused. Covered by a Bluehost backup only.

**4. The email subscriber list, and it is not even this project's.** The digest
signup on the dashboard is rendered by the sibling AI Layoff Tracker's plugin
(`alt_digest_subscribe_form`, called from `includes/shortcodes.php`): one
WordPress install, one subscriber list, one consent record per person. The
addresses and the consent records live in the sibling's WordPress tables on
Bluehost. **They are not in this repository, they are not in the sibling's
repository, and they must never be put in either.** See section 6.

**5. WordPress-side state this repository does not hold.** Small, and mostly
regenerates itself, but name it so nobody spends an hour looking for it:
`tit_ci_alert_state` (which CI failures are currently open, so a restore may
re-send one alert), `tit_source_health` and `tit_recall*` and `tit_board_series`
(projections of committed files, refilled by the next run of the job that writes
them), the `tit_eph_*` rate-limit counters, and every `_transient_tit_%` cache.
Losing all of it costs one duplicate email and one cold cache.

One item in that list is a setting rather than a cache: **where operational mail
goes**. The recipient is decided server-side (a `TIT_ALERT_TO` constant in
`wp-config.php`, or a `tit_alert_to` option, falling back to
info@asktherecruiter.com). If it was ever overridden on the old install, the
override is gone with the install and alerts quietly go to the default address
instead. Check it after a rebuild.

**6. Anything since the last commit.** See section 5.

**7. The sibling tracker's data.** The AI Layoff Tracker is a separate
repository with its own recovery story. Its layoff rows are read by this site at
render time and are not held here. If both are down, both need restoring, and
this document covers one of them.

---

## 5. What is missing after a restore, and what to do about it

**Up to one day of collection.** `collect.yml` runs once daily at 22:00 UTC and
`collect-press.yml` at 23:00, and each commits the database at the end of the
run. So the worst case is a host failure just before a collect run: everything
that run would have found is not yet anywhere. There is no fix and it does not
need one. The collectors are date-windowed, the next scheduled run picks the
window up, and a `seen_urls` row that does not exist yet means the URL is
re-read rather than skipped.

**Rows a running job had in memory when it died.** Same answer: nothing marks
them, and the next run re-collects them.

**The `published_at` markers, if you restored an older revision of the
database.** Harmless. Rows that are already on the site will be re-sent and the
server will report them as duplicates.

**A backfill's position.** `data/backfill_state.json` is committed alongside, so
a walker resumes at the slice it recorded, not at the beginning.

---

## 6. Personal data: what may never be committed here

**This repository is public.** It is public on purpose (it keeps Actions minutes
free) and it must stay that way, which means the rule is absolute rather than a
preference.

What this project's own WordPress side stores: `wp_tit_signals` holds published
company signals and their source URLs, which are public documents and carry no
personal data beyond the names of executives already named in a press release.
The plugin runs two rate-limit counters keyed on a requester's IP address
(`tit_eph_export_rl_*` and `tit_eph_feed_rl_*`), which expire on their own and
are never exported. **Nothing else about a reader is stored by this plugin.**

**The subscriber list is the exception, and it is not ours.** It belongs to the
sibling plugin (see section 4, item 4). Email addresses and consent records are
personal data. They are not in this repository, they must never be committed to
it, and no workflow here may read or export them.

If the owner wants that list backed up, and it is the one thing here that
genuinely cannot be replaced, it needs a destination that is not a public git
repository. That means a private location with its own access control and its
own retention rule, a decision about who may read it, and a consent record that
travels with each address. **That decision is the owner's**, and no session
should make it, create a destination for it, or move a single address anywhere
in the meantime. Until then, the honest statement is: the subscriber list is
protected by Bluehost's backups and by nothing this project controls.

---

## 7. The backup's own health

An unexercised backup is a hypothesis. `backup_check.py` is what exercises it,
weekly, from `.github/workflows/backup-check.yml`:

```bash
python3 backup_check.py            # check and print, offline, under a second
python3 backup_check.py --write    # ...and record the reading
```

It extracts the **committed blob** (not the working copy, which is the file a
session has been editing) into a temp directory, runs `PRAGMA integrity_check`,
counts every table, refuses any table that shrank since the last recorded
reading, and confirms the restored schema still carries every column the
republish path sends. `ops_status.py [6]` reads the committed ledger offline. A
failing week reds the run, and a red run mails the owner through `ci-alert.yml`
like every other red run here.

**The one ceiling worth knowing about now.** GitHub refuses any single file over
100 MiB in a push. The database was 78.6 MiB on 2026-08-19 and grows about
650 KB a day, which is roughly **five weeks** before pushes start being
rejected. When that happens, collection stops being able to save its own work,
and the failure arrives as a push error inside an unrelated 22:00 collect run.
`backup_check.py` fails at 90 MiB so it arrives as a Monday morning email
instead. The options at that point are a `VACUUM`, moving `seen_urls` (56,012
rows of pure bookkeeping, and the fastest-growing table) out of the committed
file, or Git LFS. All three are decisions rather than fixes, and the last one
changes what a plain `git clone` gives you, which is the whole backup story.
