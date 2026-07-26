# Talent Intelligence Tracker

A continuously updated view of the market signals that move talent, segmented by
city, region and country. Every record links to a primary source.

**Live:** https://asktherecruiter.com/blog/talent-intelligence-tracker/

This is not a news feed. Every item carries a **talent read-through**: what the
development means for hiring, displacement or compensation in a named place.

## The four pillars

| Pillar | What it captures | Talent read-through |
|---|---|---|
| Company developments & M&A | acquisitions, expansions, new sites | net-new roles vs redundancy risk |
| Leadership changes | executive hires and departures | strategy pivots, downstream team churn |
| Rewards & compensation | comp actions, retention awards, pay transparency | benchmark shifts, retention pressure |
| How we work | RTO policy, hub investment, distributed work | where net-new roles physically land |

## How we source things, and what we will not do

A talent dashboard that publishes a plausible-but-wrong company fact is worse
than useless. These rules are enforced in code, not by good intentions, and
each one has a test.

1. **Every record links to a primary source URL.** No source, no record.
2. **We never let a model invent a number.** A figure in a summary must appear
   verbatim in the source text, or the whole record is discarded rather than
   repaired.
3. **Confidence is a visible field** — `verified`, `reported` or `rumored`. It
   is earned by the source and capped by it. A news article can never be
   promoted to `verified`, however confident the model sounds.
4. **Our analysis is labelled as analysis.** The talent read-through is our
   interpretation and is shown separately from the sourced fact.
5. **We never store another aggregator as a source.** A competing tracker can
   point us at a primary source; what we store is the primary source.
6. **Records are never silently overwritten.** A correction appends a new
   revision and the original survives, so you can reconstruct exactly what we
   published on any past date.

### What we do not collect

Layoffs, WARN notices and redundancies are **not** collected here. They are
already covered by the sibling [AI Layoff
Tracker](https://asktherecruiter.com/blog/ai-layoff-tracker/) and are read from
its public API at render time. One source of truth per fact — two collectors
would eventually publish two different numbers for the same event.

### What we do not claim

We do not claim to be fully automated. Scraper repair and judgement about novel
sources are human work. A country appearing in the source registry is not a
country we cover — coverage means a working connector, a health check and a
passing test, and the registry says which tier each market is actually at.

## Architecture

```
GitHub Actions (cron, 2x/day)
        |  collectors -> classify -> validate -> dedupe -> store
        |  POST to a keyed REST endpoint
WordPress plugin on asktherecruiter.com  (custom table + REST API + render)
        |
/blog/talent-intelligence-tracker/   (server-rendered, Cloudflare-cached)
```

```
collectors/     one file per source; each returns raw dicts, never writes
pipeline/       classify -> validate -> dedupe -> store, shared by every source
data/           talent_intel.db (committed, CC BY 4.0)
site/           generated dashboard
tests/          offline, fixture-based, no network
```

Every source funnels through the same classify/validate/store path, so every
guard applies exactly once. A new collector is about 40 lines, not a new
pipeline.

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python run_collect.py --dry-run --offline
```

That runs the whole pipeline against a captured fixture with no network and no
LLM spend, and prints what it *would* store. Drop `--offline` to hit live
Google News RSS (needs `OPENROUTER_API_KEY`). Drop `--dry-run` to actually
write.

```bash
.venv/bin/python -m pytest tests/ -q
```

## Cost

Roughly **$0.60/month**, all of it LLM classification. Every data source is
free and keyless. The budget holds because candidates are keyword-gated before
they reach the model, URLs already seen are skipped before any spend, the
classification prompt is deliberately tiny, and a `402` stops the run instead
of burning a batch of failures. Set a hard spend cap on the API key itself —
that is what makes it a guarantee rather than a hope.

## Licence

Code is [MIT](LICENSE). Data is [CC BY 4.0](LICENSE-DATA) — use it, including
commercially, with attribution.

## Corrections

Found a wrong record? Corrections are published with their date alongside the
original: https://asktherecruiter.com/blog/contact/
