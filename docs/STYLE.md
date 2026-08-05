# The language standard

One standard, two products. This file is **byte-identical** in
`dk-forge/ai-layoff-tracker` and `dk-forge/talent-intelligence-tracker`, for the
same reason `docs/card-contract.json` is: a standard that exists in two copies
becomes two standards the moment somebody edits one of them, and the edit that
causes the drift is never the one that gets announced.

## What we are aiming at

Write like a quality daily newspaper. The Los Angeles Times and the Boston
Globe are the reference: serious, plain, unhurried, no house jargon. A reader
with a college education should understand a page on the first pass, without
opening a second page to decode the first.

That is the whole brief. Everything below is what it turns into when you have
to check it automatically.

## The numbers

These are enforced by `style_check.py` and they are **measured, not chosen**.
The reading below was taken across the real reader copy of both products on
**2026-08-05**, before any of it was rewritten. The bars were then set at, or
slightly better than, where the better pages already sat: a bar that fails
everything on day one gets suppressed within a week, and a bar nothing can ever
trip teaches nothing.

| Rule | Bar | Where the copy actually was |
|---|---|---|
| Any single body sentence | **30 words** maximum | 123 sentences were over it (71 layoff, 52 talent). The worst was 93 words. |
| Page mean reading level | **11.0** Flesch-Kincaid grade or lower | Most pages sat at 6 to 9. Two were over: methodology 12.7, health 11.0. |
| Passive voice, share of body sentences on a page | **25%** or lower | Median page 18%. Five pages were over: corrections 38%, health 30%, press 26%, sources 26%, methodology 26%. |
| Sentence length, average | 15 to 20 words | Aim, not a gate. The ceiling above is the gate. |

Reading level uses Flesch-Kincaid, implemented directly in `style_check.py`
with its own syllable counter. No dependency was added for this: both repos
hash-pin every install, and a reading-level formula is eighty lines of
arithmetic.

**Measured result of the rewrite that introduced this standard**, same scorer,
same corpus, before and after:

| Product | Mean grade before | Mean grade after |
|---|---|---|
| Layoff tracker | 8.46 | 7.01 |
| Talent tracker | 7.14 | 6.54 |

The worst page on either product went from grade 12.7 to 8.1, and the most
passive page from 38% to 3%. 123 over-length sentences went to zero.

## The rules

**Short sentences.** Aim for 15 to 20 words. Never exceed 30. A semicolon
joining two independent clauses is almost always two sentences that have not
been separated yet.

**Active voice, with a named actor.** "We removed the row", not "the row was
removed". The passive hides who did a thing, and on a corrections log that is
the one fact a reader came for. Some passive is honest and stays: "nothing here
is estimated into existence" is a better sentence than its active form. Hence a
25% ceiling rather than zero.

**Plain words over trade terms.** Say the thing:

| Not this | This |
|---|---|
| workforce reduction, headcount reduction, reduction in force | job cuts |
| regulatory instrument | filing |
| verification was performed | we checked |
| cadence | how often |
| granular, granularity | detailed, detail |
| leverage, utilise, utilize | use |
| in order to / prior to / subsequent to | to / before / after |
| downstream consumers, end users | readers, anyone using the data |
| canonical | say the meaning: "the one we count" |

`canonical` is the instructive one. It does **not** mean "official" here, it
means the row we count in its own right rather than folding into a larger one.
A jargon list that suggests a wrong synonym is worse than no list, so this
entry tells you to write the meaning instead of swapping a word.

**Explain a term the first time it appears on a page.** A reader must never
need another page to understand this one. Expand an abbreviation on first use:
"the US Bureau of Labor Statistics", then BLS.

**No hedging stacks.** "May potentially", "appears to possibly". One hedge at
most, or none.

**Numbers get context.** Pair a percentage with a plain equivalent where it
helps: "about one in eight". Never at the cost of precision, and never invent
the equivalent.

**Attribution is explicit and legally careful.** We report the reason the
employer gave. We never assert, in our own voice, that AI caused a layoff. That
sentence, and the announced-versus-verified distinction, and the not-comparable
units label on the recall page, and the partial-period note, are load-bearing.
Rewrite them for clarity as often as you like. Never soften what they claim.

**The standing bans.** No em dashes and no en dashes, anywhere in reader copy:
use a comma, a full stop, or a colon. No superlatives and no marketing
language. Never invent a number. Never name a competitor or a paid data
product; generic references such as "WARN-only aggregators" stay generic.

**Quoting a banned term is allowed.** Both products describe the phrases they
*search for* in employer and press language, and some of those phrases are on
the banned list. `"workforce reduction"` is a real discovery term in
`source_registry.py`. Rewriting it out of that list would not improve the copy,
it would make the page describe a collector that does not exist. So put a term
you are **reporting** rather than **using** in double or curly quotation marks,
and the jargon rule steps aside. Straight single quotes do not count, because
an apostrophe is not a quotation.

## Teaching by example

Every pair below is real, from these two products, on the day the standard
landed.

**A 60-word sentence becomes three.** (layoff methodology, the WARN legal caveat)

> **Before:** The statute itself allows reduced notice under the
> faltering-company, unforeseeable-business-circumstances and natural-disaster
> exceptions (29 U.S.C. 2102(b); 20 C.F.R. 639.9), an employer may pay wages in
> place of part of the period, and only a court may decide whether an exception
> applies (29 U.S.C. 2104).
>
> **After:** The statute itself allows shorter notice under three exceptions: a
> faltering company, unforeseeable business circumstances, and a natural
> disaster (29 U.S.C. 2102(b); 20 C.F.R. 639.9). An employer may also pay wages
> in place of part of the period. Only a court may decide whether an exception
> applies (29 U.S.C. 2104).

Same citations, same claim, same legal care. Three sentences instead of one.

**Passive becomes active.** (layoff health page)

> **Before:** The query-backed HTML report is published from an immutable
> server-generated snapshot, with its data revision and coverage limits
> disclosed.
>
> **After:** ...query-backed HTML report from a fixed server-generated
> snapshot. We disclose its data revision and its coverage limits.

"Immutable" also went, because "fixed" is the word people use.

**Jargon becomes meaning, not another word.** (layoff health page)

> **Before:** an indexer admits every employer with at least one source-linked
> canonical event and a clean identity
>
> **After:** An indexer admits every employer with a clean identity and at
> least one source-linked event that we count in its own right, rather than
> folding into a larger one.

**An em dash becomes a full stop.** (layoff weekly health email)

> **Before:** Most breakages are a government/state site changing its page
> layout — the fix is a ...
>
> **After:** Most breakages are a government/state site changing its page
> layout. The fix is a ...

**A dense line becomes a readable one.** (layoff RSS feed description, grade
13.7 to 5.9)

> **Before:** Verified AI-related and general layoffs from SEC filings and
> credible news sources.
>
> **After:** Layoffs we verified from SEC filings and trusted news outlets.
> Covers cuts the employer tied to AI, and all the others too.

**Quoting the vocabulary instead of using it.** (layoff methodology)

> **Before:** Discovery searches a dialect-aware vocabulary (layoffs,
> redundancies, retrenchment, dismissals, sackings, workforce reduction and
> more than thirty other phrasings)
>
> **After:** We search for layoffs in many dialects. The word list covers
> "layoffs", "job cuts", "redundancies", "retrenchment", "dismissals",
> "sackings" and more than thirty other phrasings.

**Name the score in words the reader already owns.** (talent recall page, done
just before this standard was written, and the reason it reads the way it does)

> **Before:** In the tracker / And every field right
>
> **After:** Event captured / Captured with every detail correct

**Benefit first, not defence first.** (layoff tracker, first screen)

> **After:** Every entry links to the filing, notice or report it came from.

That is the voice. One clause, a concrete noun, a promise the reader can check.

## The check

`style_check.py` extracts the strings a reader actually sees and scores them.
Run it by hand at any time:

```
python3 railway/style_check.py      # layoff tracker
python3 style_check.py             # talent tracker
```

It prints a per-page table and every finding. The test
`test_style_standard.py` runs the same code and fails the build.

**It scores only reader copy**: page templates, JS UI strings, chart titles and
subtitles, tile labels, methodology and recall prose, and the emails a human
opens. It does **not** score code comments, docblocks, variable names or test
fixtures, and this is the single most important thing about it. Both codebases
write long rationale comments in exactly the register of the copy, and those
comments frequently quote the display string verbatim, *including the version
that was replaced*. A checker that read comments would grade the commentary,
pass while the page was wrong, and fail after a correct fix. So comments are
stripped first, by a quote-aware stripper that preserves byte offsets, which is
also how a failure can name a real line number.

**A failure names the sentence**, with its file and line:

```
wordpress-plugin/ai-layoff-tracker/templates/page-methodology.php:110 [methodology] sentence too long
    93 words, the ceiling is 30, split it
    string: Measured like-for-like against the public trackers by category, the result is not always that we are smaller: we run higher than ...
```

A page-level failure names the page, its reading, and the bar it missed. A
style failure that says only "grade too high" sends a reader hunting, and that
is how a check ends up disabled.

**Absence of a signal is not a pass.** A page with fewer than 8 scored strings
is not gated on its mean, because a mean over two strings is noise, and
`flesch_kincaid_grade` returns `None` rather than a number when there is too
little text. The test also asserts the extractor found copy at all, so a stale
target list fails loudly instead of passing vacuously.

## Two things that will bite you

**Some copy is pinned by other tests.** Guard tests assert exact phrases that
carry a legal or SEO meaning: `kept out of search results` in the facet
template, `counted as unrecorded, not assigned one` in the tracker corrections
block, `Metro widgets are deliberately unavailable` in the publisher page. Run
the full suite, not just this check. If a guard fails, keep its phrase and
rewrite around it.

**Changing this file is a two-repo job.** `test_style_standard.py` pins the
sha256 of this file and of `style_check.py`, and asserts both digests appear in
`docs/TECHLOG.md`. `.github/workflows/style-standard.yml` fetches the sibling's
copies daily and goes red while they differ. So: edit here, copy both files to
the sibling, update `STYLE_MD_SHA256` and `STYLE_CHECK_SHA256` in both repos,
and record both digests in both TECHLOGs. Both repos stay red until they agree,
which is the point.
