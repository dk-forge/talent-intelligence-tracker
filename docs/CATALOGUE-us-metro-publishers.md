# US metro and global hub publishers — a verified catalogue, and what it is worth

Research pass, 2026-08-13. **Nothing here is wired.** `data/us_metro_publishers.csv`
is a research artifact: no collector reads it, `build_feeds_export.py` does not
export it, and no row of it appears in `data/sources_catalogue.csv`, which is
the file `national_press.load_feeds()` actually sweeps. Promoting any row into
that file is the arming step, and it is a separate decision with its own cost.

**Cost of this pass: $0.** No model was called. Every verdict below is an HTTP
fetch.

---

## The headline, before the catalogue

**A catalogue of San Francisco local publishers will not move the San Francisco
number, and the measurement already said so before this pass started.**
`data/recall_us_rejection_audit.json` places all 30 US gold misses:
`publisher_unknown: 0` and `publisher_not_wired: 0`. Not one miss is a
publisher nobody had researched. 26 of the 30 are `walked_never_read` — a
walker swept that day at a fraction of its depth, because depth is money.

The San Francisco cell is the clearest case. Its 10 misses were published by
TechCrunch (5), Fortune (1), a company's own newsroom (1), BioSpace (1), The
Robot Report (1) and Pulse 2.0 (1). TechCrunch and Fortune are already swept
feeds, verified `200 ok`. **Zero of the 10 were broken by a San Francisco metro
business journal, a Bay Area local paper, or a university or hospital
communications office.** I checked that directly rather than assuming it: the
on-site search at Mission Local, SFist, Palo Alto Online, The San Francisco
Examiner and The San Francisco Standard returns no article naming any of the
nine companies I could query. The Bay Area local press did not cover these
rounds. It is not a reading gap there; it is an absence.

So the catalogue below is honest about being mostly redundant — with **one
exception that turned out to matter a great deal.**

---

## The one finding worth the pass: Pulse 2.0

`pulse2.com` is a US technology trade publication. It is not in
`data/sources_catalogue.csv`, it is not a swept feed, and it is not on the
aggregator blocklist. We already fetch it opportunistically — the audit records
121 URLs fetched from that domain, second only to the wire — but we never walk
it.

I searched its own site for every company in the 30 US gold misses and compared
the amount in the result slug against the amount in the gold row.

| cell | misses | covered by Pulse 2.0 at the matching amount |
|---|---:|---:|
| San Francisco | 10 | **10** |
| New York | 8 | 7 |
| Austin | 3 | 3 |
| Rest of US | 9 | 6 |
| **total** | **30** | **26** |

The four it did not cover: Wonder ($650M, New York), Mantle Energy ($5M,
Dallas), Auger ($50M, Seattle), logcat.ai ($2.55M, Seattle).

**A hit on the company is not a hit on the event, and the count above is on
events.** Pulse 2.0 has written about most of these companies more than once, so
the amount in the result slug was compared against the amount in the gold row
and an earlier round was scored as a miss: its Bland article at $16M and its
Patronus AI article at $17M do not count, and the $50M Bland Series C and $50M
Patronus Series B, which are separate articles, do. One row needed reading
rather than matching: Pearl Health's gold `amount_usd` is $50M because the set
records the equity portion of a $110M total, so the Pulse 2.0 article headlined
$110M is the same event and counts.

Examples, all fetched:

- `pulse2.com/peregrine-technologies-raises-250-million-series-d-at-6-8-billion...`
  against gold `sf_funding-03`, $250M, 2026-06-22.
- `pulse2.com/osanni-bio-raises-190-million-in-series-b-funding/` against gold
  `sf_funding-04`, $190M, 2026-06-23.
- `pulse2.com/queue-raises-12-6-million-to-launch-autonomous-robotic-pharmacy/`
  against gold `sf_funding-09`, $12.6M, 2026-06-30.

One publisher, free, robots-permitted, covering 87% of the US funding events a
gold set assembled without any sight of our collectors says we missed.

**And its RSS feed will not deliver them.** `pulse2.com/feed/` verified LIVE
(20 items, newest 0d), but the 20 items spanned **29 minutes** when sampled, and
its sitemap shows **67 to 103 posts a day**. A twice-daily poll of a
half-hour-deep feed sees a single-digit percentage of what that site publishes.
Wiring the feed and calling San Francisco handled would be worse than doing
nothing, because it would look like coverage.

What would work is the sitemap. `pulse2.com/wp-sitemap.xml` indexes 47
post-sitemaps, each `<loc>` paired with a `<lastmod>`, so the site is walkable
in announcement-date order with no feed window at all. Its slugs carry the
company and the amount (`osanni-bio-raises-190-million-in-series-b-funding`),
so a **deterministic, zero-cost prefilter runs on the URL before any model sees
anything**: on 2026-08-12, 70 of that day's 103 slugs carried a funding marker.
`robots.txt` allows everything and sets `Crawl-Delay: 20`, which the current
collector does not honour and would have to.

That is a real proposal, and it is still not free downstream: reading a
candidate costs money, and the budget is the measured bottleneck. What changes
is **precision per dollar**. A Pulse 2.0 funding slug is a funding event; a
Google News sweep result mostly is not.

---

## Second finding: the PR Newswire tombstone is wrong

`data/sources_catalogue.csv` records PR Newswire as
`2026-07-30 no feed published (15 paths, no rel=alternate)`. That verdict is
incorrect — the probe used the wrong path shape.

Seventeen feeds of the shape `/rss/<slug>/<slug>-list.rss` answer
`200 application/xml` with 20 items each, and `robots.txt` disallows only
`/templates/`, `/widget-landing-page.html` and `/multivu/`. This matters because
the wire published **8 of the 30 US misses**, more than any other domain.

Three qualifications, all measured, and together they say do not wire it as a
feed:

1. Every category returns the **same 20 items** as `all-news-releases` —
   identical headlines, identical span. The category slugs are not filters.
2. Those 20 items covered **4 hours**, against roughly 1,300 releases a day.
3. The real index is `sitemap-news.xml`: 27 pages of ~98 dated URLs, about two
   days deep, robots-allowed.

So the wire is fully reachable and it is a firehose. Reaching it is not the
problem; affording to read it is.

---

## The catalogue

`data/us_metro_publishers.csv`, 70 rows, every one fetched on 2026-08-13 with
the collector's own `national_press.fetch` and `robots_allows`, so a verdict
here is what the collector would see rather than a second opinion from a second
HTTP client. Columns: metro, city, publisher, sector, feed URL, robots verdict,
HTTP status, item count, age of the newest item, verdict, and whether we already
read it.

| verdict | rows | meaning |
|---|---:|---|
| LIVE | 30 | 200, parses, newest item under 120 days |
| DEAD | 20 | 404 or 403 on every path tried |
| REFUSED_BY_ROBOTS | 5 | the publisher's robots.txt disallows it — a finding, and excluded |
| EMPTY | 5 | 200 but parsed to zero items |
| STALE | 3 | parses, newest item over 120 days |
| UNDATED | 2 | parses, no item carries a readable date |
| LIVE_BUT_TOO_SHALLOW | 2 | live, and its window is too short for our cadence |
| UNREACHABLE | 2 | redirect loop or read timeout |
| DOMAIN_DRIFT | 1 | redirects to a different registrable domain |

Where a guessed path 404'd I asked the publisher's own homepage for its
`rel=alternate` and then probed 13 conventional paths, so a `DEAD` row means 14
or more paths tried, not one. That second pass recovered 12 feeds, including
LAist, UCLA Newsroom, KUT, BioSpace, InnovationMap Houston and The City.

Two entries worth naming because they are traps:

- **The San Francisco Examiner** only answers with the HTML entities intact in
  its search-feed URL. Unescaping the ampersands, which is the obviously correct
  thing to do, returns an empty feed. Its row carries the working string.
- **The City** moved to `thecityreporter.nyc`; the old domain trips the
  collector's own domain-drift guard, which is the guard working.

Five publishers refuse their feed in their own robots.txt and are therefore
excluded, not worked around: **Cold Spring Harbor Laboratory, UKTN, Tech.eu,
Tech in Asia, DealStreetAsia.** Three more — **San Jose Spotlight, SFGATE and
BioSpace** — allow the feed and disallow `/search`, so only their feeds are
listed and the overlap check below simply could not be run against them. That
gap is recorded rather than guessed around.

### What is genuinely new reach, and what is not

Of the 30 LIVE rows, 28 are outlets we do not currently read. But "new" is not
"useful". Set against the 30 known misses, the LIVE rows divide as:

- **Reaches a known miss:** Pulse 2.0 (26), PR Newswire (8, and unaffordable),
  BioSpace (1), The Robot Report (1), InnovationMap Houston (1), Dallas
  Innovates (1). The last two are already in `sources_catalogue.csv` without a
  feed; this pass supplies verified feed URLs for both.
- **Reaches no known miss:** the entire Bay Area local press set (Mission Local,
  SFist, Palo Alto Online, The San Francisco Examiner), the university and
  hospital newsrooms (Berkeley, Stanford, UCSF, UCLA, USC, Caltech, City of
  Hope, Chan Zuckerberg Biohub), the economic development bodies (LAEDC, Bay
  Area Council), and the metro general-news outlets (LAist, Gothamist, The City,
  LA Times Business, KUT). **That is 24 of the 30** — every LIVE row that is
  neither already read nor named above. They are real publishers with real
  feeds, and on the evidence of this gold set they would add candidates to a
  queue we already cannot afford to read.

That last sentence is the finding, and it should survive this document: a source
that adds candidates without adding precision makes the measured bottleneck
worse.

### Metro business journals: a structural finding

The metro business journal chain — Austin, San Francisco, Silicon Valley,
Boston, Crain's New York, Crain's Chicago, Puget Sound, Triangle, Pittsburgh —
is already catalogued and every one of them was recorded `dead: HTTP 403 bot
block` on 2026-07-29. This pass did not re-probe them; the earlier verdict
stands and nothing suggests it changed. **The single densest layer of US metro
business reporting is closed to automated reading**, and that is a finding to
record rather than a problem to route around. It is also part of why the local
press did not break the SF rounds in the first place: the outlets that would
have are behind a bot block or a paywall, and the rounds went to national trade
press instead.

---

## Global hubs

I probed the obvious equivalents and found the international side is **already
well covered**, which is the opposite of the US picture. `data/feeds.csv`
carries 658 feeds, of which only **38 are United States** — the least-fed
country in the catalogue is the one this gold set measures.

Verified new and worth having: **Sifted** (London, 24 items, newest 0d) and
**deutsche-startups.de** (Berlin, 15 items, newest 1d). Both LIVE, neither read.

Verified and already read: NeoFeed and Startups.com.br, both Brazil — the
existing Brazil set is 10 feeds deep.

Refused by their own robots.txt, and therefore excluded rather than
worked around: **UKTN**, **Tech.eu**, **Tech in Asia**, **DealStreetAsia**.

Unreachable or absent: **Startupticker.ch** (no feed on 14 paths),
**CTech by Calcalist** (none found; the existing Israel set already carries
Globes, Geektime, Techtime and the Innovation Authority), **The Logic** (403),
**Moneycontrol** (403), **Les Echos** (403), **BusinessCloud** (redirect loop),
**Wamda** (read timeout), **Contxto** (empty feed).

**Countries with no readable local business press are a finding, and this pass
did not find one.** Every hub I looked at either already has a feed in
`data/feeds.csv` or has one that a publisher deliberately closed. I did not pad
the list for symmetry; the international rows here number four because four is
what verified.

---

## Ranked shortlist, if any of this is ever armed

Arming is a separate decision with its own cost. In order:

1. **Pulse 2.0, walked by sitemap, not by feed.** 26 of 30 known misses. Free to
   enumerate, deterministically prefilterable on the slug before any spend,
   robots-permitted at a 20-second crawl delay. This is the only candidate whose
   payoff is measured rather than argued. It needs a sitemap-paging collector
   this repo does not have, and it needs the crawl delay honoured.
2. **BioSpace** (`https://www.biospace.com/index.rss`) and **The Robot Report**
   (`https://www.therobotreport.com/feed/`). One known miss each, both LIVE,
   both cheap, and both are sector trade press for exactly the bio/medical and
   robotics rounds the US set is thin on. Note BioSpace's robots.txt disallows
   `/search` while allowing the feed.
3. **InnovationMap Houston** and **Dallas Innovates.** Both already in the
   catalogue without a feed; this pass supplies verified URLs. One known miss
   each. Dallas Innovates' miss is `walked_never_read` and InnovationMap's is
   `fetched_then_dropped`, which is a filter defect and will not be fixed by
   wiring the feed — fix the filter first or it will be wired and still miss.
4. **Sifted** and **deutsche-startups.de.** No known miss behind either; they
   are reach in two hubs where our coverage is national-press-only.
5. **Everything else in the file: do not arm.** Recorded so the next session
   does not re-probe them, which is the whole point of writing a refusal down.

**PR Newswire is deliberately not on this list** despite accounting for 8
misses. Its route exists and its volume does not fit the budget, and wiring a
1,300-a-day firehose into a queue that can afford 14 reads a day would displace
better candidates. Correcting its catalogue tombstone is worth doing on its own.

---

## Does this move the San Francisco number?

**Not as a local-publisher catalogue: no.** The Bay Area local press, the
university newsrooms and the economic development bodies did not report these
rounds at all, and I verified that by searching their own archives rather than
inferring it. Twenty-four of the 30 live feeds found here would add candidates
and reach no known miss.

**As one publisher, possibly.** Pulse 2.0 covered 10 of 10 San Francisco misses
and 26 of 30 nationally. If it were walked by sitemap at full depth, every one
of those events would have been in front of the reader.

**Whether they would then have been read is a budget question, and this pass
cannot answer it.** The 26 `walked_never_read` misses were already reachable
through routes we run; being reachable was never what was missing. What Pulse
2.0 changes is the odds per dollar, because a slug that says
`raises-190-million-in-series-b` can be gated for free and a Google News result
cannot. That is an argument for a better-ranked queue, not for more sources, and
it should be measured before it is believed.

The honest bound: this catalogue moves San Francisco from 3/13 only if Pulse 2.0
is armed as a sitemap walk **and** the read ration reaches what it surfaces.
Arming it as an RSS feed alone would move nothing and would look like it had.
