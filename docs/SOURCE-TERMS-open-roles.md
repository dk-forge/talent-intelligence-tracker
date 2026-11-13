# Source terms: "Open roles now" (role titles and outbound apply links)

Owner rulings: 2026-11-12 (no public job listings) and 2026-11-13 ("yes,
employer links"). This file is the terms record `open_roles.ALLOWED_ATS` is
tested against (`tests/test_open_roles.py`). A provider is shown to readers
only if it has a row here marked **ON**.

What we show, and only this: company, role title, city, one link that goes
straight to the employer's own posting on its ATS (or the careers page the
employer chose as the posting's URL), and counts. No job page on our site, no
description text, no salary copy, no schema.org `JobPosting` markup, no public
API that serves roles.

Checked 2026-11-13 from each provider's own developer documentation.

| Source | Status | What its own docs say | Why that allows titles and links |
|---|---|---|---|
| Greenhouse Job Board API (`boards-api.greenhouse.io`) | **ON** | "Job Board data is publicly available, so authentication is not required for any GET endpoints." Each job carries `absolute_url`. No display or linking terms on the page. robots.txt disallows only `/embed/`. | Public, unauthenticated GET published for display; we link to the employer's own `absolute_url` and send every applicant to it. |
| Lever Postings API (`api.lever.co`) | **ON** | "all job postings in the `published` state are publicly viewable", and published jobs "may be scraped by third parties". `hostedUrl` is "Lever's hosted job posting page"; "directing candidates to Lever's hosted application form is considered a best practice." robots.txt `Allow: /`, `Crawl-delay: 1` (honoured by `ats_boards.ATS_DELAY`). | Lever says in writing that third parties may read published postings, and recommends sending candidates to its hosted page, which is the link we use. |
| Ashby public job posting API (`api.ashbyhq.com/posting-api`) | **ON** | No authentication; returns "all currently published Job Postings". `jobUrl` is the "URL of the Ashby hosted page for this job". Roles with `isListed: false` "should only be available via direct link". robots.txt answers 401 (no robots.txt under RFC 9309). | Public, unauthenticated, published for display. We skip `isListed: false` roles (already done in `ats_boards.fetch_postings`) and link to `jobUrl`. Weakest of the three: the docs describe the employer's own careers page and say nothing either way about third parties. |
| Workable widget API | OFF (also refused in ATR #3991) | robots allows, but rate-limits hard (Cloudflare 1015); no board on the watchlist. | Not on the watchlist, and not reviewed for display. |
| SmartRecruiters | OFF (also refused in ATR #3991) | robots.txt `Disallow: /` for every agent but LinkedInBot. | Refused by the publisher. |
| Recruitee, Pinpoint, Softgarden, Zoho Recruit | OFF (Recruitee and Pinpoint also refused in ATR #3991) | robots refuses the career hosts or the API path (see `collectors/ats_boards.py`). | Refused by the publisher. |
| Comeet, Teamtailor | OFF (Teamtailor also refused in ATR #3991) | Token required. | Not public. |
| Polymer, HireHive, Gem, Manatal | OFF | Reviewed in ATR #3991. | No written allowance. |
| **Jooble** | **OFF until its written OK** | Owner ruling. | Stays off until Jooble confirms in writing. |

## Agreement with the ATR terms review

`dk-forge/asktherecruiter-sandbox` reached the same verdict independently, read
2026-11-13:

* `docs/JOB_FEED.md` (source table) lists Greenhouse, Lever and Ashby as "Public
  job-board APIs built for embedding a company's own jobs; we link out to the
  original posting", and states "The seed-employer list stays Greenhouse, Lever
  and Ashby only."
* Issue #3991 (closed, 2026-11-12) reviewed nine more ATS vendors (Teamtailor,
  Recruitee, SmartRecruiters, Workable, Pinpoint, Polymer, HireHive, Gem,
  Manatal) against the Jooble standard of a written allowance to store and show
  postings. None clearly allows it, so none of the nine is ON here either.

If either source later reaches a different verdict on any provider, that
verdict wins: remove the provider from `open_roles.ALLOWED_ATS` and
`tit_open_roles_sources()` in the same commit as the change to this table.

## How a closed role leaves within 24 hours

1. Every publish replaces the site's copy whole, built only from boards read
   successfully that day. A role the employer closed is absent from its own
   board API, so it is absent from ours.
2. Every link a reader could click (five per company, the email pool) is
   re-requested before publishing; a 404 or 410 drops the role.
3. The plugin hides every block once its copy is older than 30 hours
   (`TIT_OPEN_ROLES_MAX_AGE_H`), so a failed run shows nothing, never
   yesterday's roles.
