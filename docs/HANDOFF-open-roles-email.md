# Handoff: "Open roles now" in the daily and weekly emails (sibling repo)

Owner ruling 2026-11-13 ("yes, employer links"). This repo now produces the
section; the emails are SENT by the sibling plugin (`dk-forge/ai-layoff-tracker`,
which owns the subscriber list, consent, preferences and both senders). That
repo was readable but not writable from the session that built this, so the
three sibling changes below are specified here rather than made.

## What this repo already provides

`tit_open_roles_email_section($freq, $args)` in
`wordpress-plugin/talent-intelligence-tracker/includes/open_roles.php`, called
in-process exactly like `tit_follow_rows()` is today. It returns
`array('html', 'text', 'count')` or `null` (no current data, or the copy is
over 30h old: then say nothing).

    $args = array(
        'country' => 'US',          // ISO2 the reader chose, '' = everywhere
        'family'  => 'engineering', // role family slug, '' = all
        'link'    => function ($u) use ($send_id) { return alt_digest_track_link($send_id, $u); },
    );

Daily: one "Open roles now" heading, the reader's count, counts by country
(or by state inside the US), by city and by role family, up to 10 roles (two
per employer at most), then the "See all matches for your CV" button to
`https://asktherecruiter.com/?utm_source=talent-tracker&utm_medium=email&utm_campaign=open-roles-daily&utm_content=cv-matches#start-intake`.
Owner ruling 2026-11-13: this target (the home page intake form, `#start-intake`)
is correct; there is no separate sign-up page. Do not change it without asking.
Weekly: the same plus a trend line on the week and up to 20 roles grouped by
role family; `utm_campaign=open-roles-weekly`.

Role family slugs are `pipeline/vocab.py` `FUNCTIONS`; labels ride in the
payload as `family_labels`.

## The three sibling changes

1. **Preferences.** Two nullable columns on the subscriber row,
   `talent_country` (ISO2) and `talent_family` (a `FUNCTIONS` slug), each a
   `<select>` fieldset ("Country", "Topic") in `alt_prefs_form()`
   (`includes/subscriber-prefs.php`), shown when the talent list is ticked.
   Empty means "everywhere" / "all topics". Narrowing mail needs no
   reconfirmation (it does not add mail).
2. **Per-recipient section, both senders.** Compose it per recipient beside
   `talent_follow_section` (`includes/digest-api.php` around line 248 for the
   relay, and the in-WordPress loop in `includes/subscribe.php` around line
   8105), for talent-list readers only, appended directly after the talent
   section. Call it as `open_roles_section` in the relay payload and append it
   in `railway/digest_send.py` next to `talent_follow_section`.
3. **Heading.** The talent section already opens with "Who's hiring"
   (`digest-talent-briefing.php`), which is the heading the ruling asks for.
   Keep it; "Open roles now" follows it as its own H2.

Send test editions to events@dakotta.com only.

## Samples

Rendered from a live read of 40 real boards on 2026-11-13 (4,432 roles); the
weekly trend line in the sample uses a synthetic week-ago reading, because live
trends start once seven days of counts are recorded. See the PR for the
screenshots.
