"""Pre-publish guardrails: arithmetic that has to hold before a figure goes out.

WHY THIS EXISTS. For weeks the live page said "$200.3bn raised". Roughly $86bn
of it was 998 Form D rows - single-asset property vehicles, insurance separate
accounts and synthetic GICs - filed on the same form real startups use and
published with a hardcoded "hiring" direction. Nothing in the pipeline asked
whether a single funding row was implausible, whether the period totals
reconciled, or whether the printed date span matched the data. The figure stood
in public until a human looked at it.

Four checks, all of them arithmetic and pattern matching:

  amount        no single funding row may exceed a threshold DERIVED from the
                stored distribution (never a round number typed in)
  period_totals a shorter period can never hold more than a longer one that
                contains it, and year-to-date can never exceed all-time
  date_span     the range the page prints and the span it computes must
                describe the same rows
  vehicle_name  funding rows whose issuer name is a vehicle that employs
                nobody: a street address, a numbered series, an insurance
                separate account, a synthetic GIC

THE FOUR RULES THAT SHAPE ALL OF IT.

1. **Flag, never silently drop.** Every finding lands in the `publish_guardrails`
   ledger and is surfaced by `ops_status.py [2d]`, `guardrails.py` and the weekly
   health digest. A genuine mega-round (Anthropic's $30bn, GIC and Coatue at a
   $380bn valuation, accepted 2026-08-04) must survive REVIEW rather than be
   auto-binned, so a human accepts or rejects it and the decision is remembered.
   Silent auto-correction is how you get a different invisible defect.

   THIS PARAGRAPH USED TO NAME A DIFFERENT EXAMPLE, and correcting it is worth
   more than the two lines it costs. It cited ChangXin Memory's $8.6bn, stored
   and accepted on 2026-07-29, as the genuine raise the review step exists to
   protect. It is not one. The source says "$8.6 billion IPO" and "Shanghai
   STAR Market IPO", and an IPO is not private funding by this product's own
   rule. So the canonical example of review working correctly was a review that
   got it wrong, and the row is on the live site now. It is UNRESOLVED here on
   purpose: a local retraction alone would take it off our copy while leaving
   it on the page and remove it from every ops surface that would otherwise
   nag, which is worse than the defect. It needs a session with credentials to
   run `python3 retract.py <signal_id> "an IPO is not private funding"`, which
   withdraws it on WordPress first and locally second.

   The lesson underneath is the one worth keeping: an accepted finding is a
   human's answer and it is remembered forever, so a wrong answer is remembered
   forever too. Accepting is the expensive direction.

2. **Quarantine, not halt.** A flagged row does not publish. Everything else in
   the same batch does. The first build halted the run instead, and the first
   two production runs showed exactly what that costs: eight findings stopped a
   collect and a backfill that were carrying dozens of good records, and one of
   the eight (X.AI, $16.6bn) is a real raise. A guard that stops the product
   every time a genuine mega-round lands is a guard that gets removed. What must
   NEVER weaken is the other half: an unreviewed row stays out of the batch and
   out of every figure computed from it.

3. **THREE STATES, NOT TWO: open, accepted, rejected.** Open is awaiting a
   human and escalates on a grace clock. Accepted releases the row. REJECTED
   WITHHOLDS IT, permanently and silently: the human has decided, so it never
   escalates, never nags, and never publishes.

   Until 2026-08-13 the ledger had two states wearing three names. Every reader
   asked `state IN ('accepted', 'rejected')` and called it "answered, therefore
   released", which is right for accepted and exactly backwards for rejected:
   on a row that has never published, rejection is the CHEAPEST correction
   there is - the figure has never been in public, so all that is needed is to
   never send it - and instead it was the one verdict that guaranteed
   publication. Measured on the committed database that morning, the next
   publish run would have sent Nvidia's $709bn (an infrastructure financing
   arrangement) and Oracle's $25bn (a corporate bond issue), both rejected by
   hand hours earlier, into a live corpus totalling $521.65bn.

   The code read that way because this docstring taught it to: it used to say
   rejection "does not delete anything: retract the row", which assumes the row
   is ALREADY LIVE. That case is real and unchanged - a published figure cannot
   be withheld, only retracted - but it is one of two, and the other one is the
   common one. Which correction a rejection needs is decided by where the row
   is, and nowhere else:

     never published   rejection alone is the whole correction. The row is
                       withheld for good. There is nothing to retract and
                       `retract.py` would find nothing to withdraw.
     already live      rejection records the judgement and the figure is still
                       on the page. It stays in the `live` bucket on the short
                       window and stays red until somebody runs
                       `python3 retract.py <signal_id> 'why'`.

4. **Red means a human neglected it, not that the machine noticed.** Publishing
   the clean rows and exiting 0 is the success case; the guardrail worked. The
   run escalates to non-zero only once a finding has been open past its grace
   window. See `LIVE_FINDING_GRACE_HOURS` for the full reasoning, including why
   there are two windows and where the numbers come from.

No model is called here, ever. This is arithmetic and regex; it costs nothing.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

# The four check names. Used as ledger keys, so they are stable strings and not
# an enum somebody renames.
AMOUNT = "amount"
PERIOD_TOTALS = "period_totals"
DATE_SPAN = "date_span"
VEHICLE_NAME = "vehicle_name"
CHECKS = (AMOUNT, PERIOD_TOTALS, DATE_SPAN, VEHICLE_NAME)

# The division that decides the failure mode, and it is not cosmetic.
#
# A ROW check names one row, so the answer is to hold that row back and publish
# everything else.
#
# An AGGREGATE check names no row. It says the arithmetic of the whole published
# set does not add up, and there is no clean subset of a total that is wrong.
# Those still halt, immediately and with no grace period, because publishing more
# rows into a broken total cannot improve it. They stay reviewable through the
# same ledger, so a human can still unblock one deliberately.
ROW_CHECKS = (AMOUNT, VEHICLE_NAME)
AGGREGATE_CHECKS = (PERIOD_TOTALS, DATE_SPAN)

# WHEN A QUARANTINE BECOMES A FAILURE. Two windows, and both are derived from
# the cadence rather than picked.
#
# The question a red run should answer is "has a person neglected this?", which
# means the person has to have been TOLD first. `health-digest.yml` runs Mondays
# 13:00 UTC, so the weekly email is the moment of telling.
#
#   held back, never reached the site: 192h = one full digest cycle plus a day.
#     Until the first digest fires, red would be blaming somebody who has not
#     been asked yet. And nothing is wrong in public: the guardrail did its job,
#     the suspect row is out of every figure. After a whole cycle of silence,
#     that is a choice.
#
#   already on the site: 72h. This one is different in kind. The row is in the
#     live aggregate NOW and quarantine cannot pull it back - only a human
#     retraction can. It is the $86bn failure in miniature, and the whole build
#     exists because that stood in public until somebody looked. The owner's own
#     requirement is that this run for "days" unattended, so three days is the
#     longest ordinary absence; past that an unchecked live figure has outlived
#     it and the run should be red when he gets back.
#
# The countdown is printed on every run, so the day it turns red is never a
# surprise. The alternative shapes were both rejected on this project's own
# evidence: exiting 0 forever makes the finding decorative, and exiting non-zero
# from the moment of creation makes red the normal state, which is how the
# permanently-red drain-writers taught everyone to skim past it.
LIVE_FINDING_GRACE_HOURS = 72
HELD_FINDING_GRACE_HOURS = 192

# THE AMOUNT QUEUE GETS ITS OWN, SHORTER DEADLINE, and it is not a tightening
# of the two above - it answers a different question and it is read by
# different tools.
#
# The windows above govern whether a DATA JOB exits non-zero. They are long on
# purpose: a run that goes red every time a mega-round lands is a run people
# learn to skim, and this project has the permanently-red drain-writers to
# prove it. Nothing below changes them.
#
# This one governs the two surfaces a human actually reads: `ops_status.py`,
# which the session ritual runs first, every session, and the weekly digest.
# What it exists for is the state found on 2026-08-04: fifteen `amount`
# findings, $874.2bn between them, EVERY ONE `state='open'` with `reviewed_at`
# NULL, one of them re-seen 229 times across five days. Nothing was broken.
# The rows were held exactly as designed, the countdown printed exactly as
# designed, and the design's own escalation was still 192 hours away for
# findings that had been ignored for four times that in aggregate. A check
# whose queue is never read has been silently converted into a delete, and the
# figures it deletes here are roughly four times everything the site publishes.
#
# 48 hours because that is the shortest window that cannot be triggered by an
# ordinary absence: the collectors run twice a day, so a finding older than two
# days has been offered to a person at least four times and refused four times.
# It is deliberately SHORTER than either grace window, because being told twice
# a session is cheap and a $30bn round missing from the product is not.
#
# Only `amount` findings, not every check. The vehicle-name queue is a handful
# of insurance separate accounts nobody is waiting on; this is the one that
# holds real money out of the product.
AMOUNT_REVIEW_DEADLINE_HOURS = 48

# The date a row is reported under, everywhere. Byte-identical to the plugin's
# own expression (includes/api.php, includes/shortcodes.php) so a guardrail
# cannot pass on a set the page never counted.
DATE_EXPR = "COALESCE(published_date, DATE(captured_at))"

# The rows the dashboard shows by default: tit_notable_where() in
# includes/api.php. Routine rows are held back by the detail control, so the
# span the page prints is computed over THIS set and not over the whole table.
NOTABLE_SQL = "(materiality IS NULL OR materiality <> 'routine')"

PUBLISHED_SQL = "is_current = 1"


class GuardrailTripped(RuntimeError):
    """Base. Carries the findings so a caller can print them."""

    def __init__(self, message: str, findings: list["Finding"]):
        self.findings = findings
        super().__init__(message)


class AggregateBroken(GuardrailTripped):
    """The published set does not add up. Nothing can be published.

    Raised BEFORE any send. Unlike a row finding there is nothing to quarantine:
    a total that is wrong is not made less wrong by adding rows to it.
    """

    def __init__(self, findings: list["Finding"]):
        names = sorted({f.check for f in findings})
        super().__init__(
            f"{len(findings)} aggregate guardrail finding(s) across "
            f"{', '.join(names)}. The published set does not add up, so there is "
            f"no clean subset to send and NOTHING was published. Review with:  "
            f"python3 guardrails.py", findings)


class QuarantineOverdue(GuardrailTripped):
    """A quarantined row has been waiting too long for a human.

    Raised AFTER the send, deliberately. The escalation must not cost a single
    clean row: the point of the whole change is that a suspect row does not take
    the batch down with it, and that has to stay true on the day the run goes
    red as well as on the days it does not.
    """

    def __init__(self, findings: list["Finding"], *, published: int, oldest_hours: float):
        super().__init__(
            f"{len(findings)} guardrail finding(s) have been open longer than "
            f"their grace window (oldest {oldest_hours:.0f}h). {published} clean "
            f"row(s) WERE published; the flagged ones were held back and are "
            f"still held back. This run is red because nobody has answered them, "
            f"not because anything failed. Answer them:  python3 guardrails.py",
            findings)


@dataclass(frozen=True)
class Finding:
    """One thing a guardrail caught.

    `subject` is the stable identity a human accepts or rejects: a row's
    content_hash for the row checks, a period or scope pair for the aggregate
    ones. It must not change between runs or an accepted finding would reopen
    itself every night.
    """

    check: str
    subject: str
    label: str
    detail: str
    value: float | None = None

    @property
    def key(self) -> str:
        return f"{self.check}/{self.subject}"


# --------------------------------------------------------------------------
# 1. Implausible single-row amounts
# --------------------------------------------------------------------------

# How many rows the fitted distribution is allowed to predict above the
# threshold. This is the whole derivation, and it is a tolerance rather than a
# number of dollars: at 0.1 we accept an expectation of one false flag per ten
# corpora of this size, which is a review burden of roughly one row a year and
# small enough that the queue keeps being read.
EXPECTED_ROWS_ABOVE = 0.1

# Below this many amounts the fit means nothing and the threshold would be
# whatever the first dozen rows happened to be.
MIN_SAMPLE = 200

# What to use when there is no usable distribution yet. Stated as a fallback and
# never as the rule, because a typed-in number is exactly what this check exists
# to avoid: it is only ever reached on a nearly empty database.
FALLBACK_THRESHOLD = 1_000_000_000


def derive_amount_threshold(values: list[int]) -> dict:
    """The point above which a row is not a member of the population we hold.

    HOW, and why this shape rather than a round number.

    Stored funding amounts are close to log-normal: measured 2026-07-29 over
    3,057 current rows, log10 has median 6.737 and a robust sigma of 0.631, and
    the empirical quantiles track a log-normal across four orders of magnitude
    ($5.5M at p50, $45M at p90, $376M at p99). So the threshold is the value
    whose EXPECTED COUNT under that fit is EXPECTED_ROWS_ABOVE. A row above it is
    one the corpus's own shape says should not occur even once - which is a
    statement about the population rather than an opinion about big numbers.

    Centre and scale come from the median and the MAD, never the mean and the
    standard deviation, and that choice was MEASURED against the real defect
    rather than assumed. Replaying the 998 retracted Form D vehicles back into
    the corpus:

        median / MAD   $1.80bn clean -> $1.35bn contaminated, catches 14 of the
                       vehicles, worth $68.4bn of the $85.6bn overstatement
        mean / sd      $2.32bn clean -> $2.42bn contaminated, catches 11,
                       worth $62.5bn

    The robust pair moves DOWN as the bad rows arrive; the mean-based one moves
    UP, which is a threshold that relaxes at exactly the moment it is needed.

    THE HONEST LIMIT, because it decides what the other guardrails are for. The
    retracted vehicles were NOT a distinguishable population by amount: their
    log10 median was 6.641 against the clean corpus's 6.737, near enough the
    same distribution. Only the individual extremes stood out. So this check
    catches the largest members of a bad class and never the class itself, and
    a contaminant that ever formed a large, tightly-clustered mode two decades
    above the corpus WOULD lift any fitted threshold, robust or not. That is
    what `check_vehicle_names` and `check_period_totals` are for: neither is
    distribution-based, so neither can be argued out of position by volume.

    Today (3,057 rows) it computes to $1,799,597,726 and flags 5 rows.

    Returns the threshold and the statistics behind it, so the ledger and the
    ops output can state the reasoning rather than assert a number.
    """
    usable = sorted(v for v in values if v and v > 0)
    n = len(usable)
    if n < MIN_SAMPLE:
        return {
            "threshold": FALLBACK_THRESHOLD, "n": n, "derived": False,
            "reason": (f"only {n} stored amounts (need {MIN_SAMPLE}); using the "
                       f"stated fallback until the distribution is real"),
        }

    logs = [math.log10(v) for v in usable]
    centre = statistics.median(logs)
    mad = statistics.median([abs(x - centre) for x in logs])
    # 1.4826 makes the MAD a consistent estimator of sigma for a normal.
    sigma = 1.4826 * mad
    if sigma <= 0:
        return {
            "threshold": FALLBACK_THRESHOLD, "n": n, "derived": False,
            "reason": "every stored amount is the same value; no spread to fit",
        }

    z = statistics.NormalDist().inv_cdf(1 - EXPECTED_ROWS_ABOVE / n)
    threshold = 10 ** (centre + z * sigma)
    return {
        "threshold": int(threshold), "n": n, "derived": True,
        "centre": centre, "sigma": sigma, "z": z,
        "reason": (f"log10 median {centre:.3f}, robust sigma {sigma:.3f} over "
                   f"{n} stored amounts; z={z:.2f} is where the fit expects "
                   f"{EXPECTED_ROWS_ABOVE} rows in a corpus this size"),
    }


def stored_amounts(conn) -> list[int]:
    return [row[0] for row in conn.execute(
        f"SELECT funding_amount_usd FROM signals "
        f"WHERE {PUBLISHED_SQL} AND funding_amount_usd > 0")]


# --------------------------------------------------------------------------
# 1b. The way out of the check, and why the check needed one
# --------------------------------------------------------------------------
#
# THE INVERSION. `derive_amount_threshold` asks whether the corpus's own shape
# can explain a figure, and in 2026 the answer for every real AI mega-round is
# no: measured 2026-08-04 over 3,928 stored amounts the ceiling is $6.55bn,
# which sits BELOW xAI's Series E, below Waymo's round, below both of
# Anthropic's and below OpenAI's. The check was therefore quarantining correct
# answers at the same rate as wrong ones, and it showed:  15 rows worth $874bn
# sat in the queue, some re-seen 229 times over five days, every one of them
# `state='open'` with `reviewed_at` NULL. A review step with nobody on it is
# not a review step; it is a delete with a nicer name.
#
# RAISING THE THRESHOLD IS NOT THE FIX and this is the evidence, not an
# opinion. Sorted by size, the queue interleaves:
#
#   $539bn  Arch          "Surpasses $539 Billion In Private Market ASSETS"
#   $100bn  Turkish Airlines  a 100bn LIRA capex programme; the story is $2.3bn
#    $30bn  Anthropic     a real round, led by named investors
#    $20bn  xAI           a real Series E
#    $15bn  A16z          an investor's own fund close, not a company round
#
# Any threshold that lets Anthropic through lets Arch and Turkish Airlines
# through too. Size does not separate them because size is not what is wrong
# with them.
#
# WHAT DOES SEPARATE THEM, and it is two different questions that need two
# different answers:
#
#   IS THE FIGURE RIGHT?  Independent outlets. A misparse is one outlet's
#     mistake; a $30bn round is reported by everyone. Anthropic's $30bn had
#     reuters.com, w.media and Anthropic's own newsroom all arrive within
#     three days stating the same number - and every one of them was thrown
#     away by dedup as "duplicate" while the row itself sat quarantined for
#     wanting exactly that corroboration. See schema.funding_corroborations.
#
#   IS IT A COMPANY RAISE AT ALL?  Nothing about outlet count can answer this,
#     and the corpus proves it: Kingswood's $4bn fund close was reported by TWO
#     independent outlets (businesswire.com and citybiz.co) stating the same
#     $4bn. Corroboration would have auto-published a private equity fund
#     close as a company round. So the class test is a separate, INDEPENDENT
#     condition, and both must hold.
#
# The auto-accept therefore requires corroboration AND a clean class. Anything
# else stays exactly where it was: held, out of every figure, waiting for a
# person - which is now a person the ops check and the digest will not let
# alone (see AMOUNT_REVIEW_DEADLINE_HOURS).

#: Independent outlets that must state the same figure before it publishes
#: itself. Two, because two is the smallest number that can disagree: one
#: outlet's number is a claim, and the second one is the first thing in the
#: system capable of contradicting it. Higher would be safer and emptier - the
#: whole corpus contains no funding figure above the threshold with three
#: independent outlets on it today, so a 3 would be a rule that never fires and
#: a queue that never empties.
CORROBORATION_MIN_OUTLETS = 2

#: The four things that are NOT a company raising money, in the words the
#: sources actually use. A row whose own headline or summary says one of these
#: can never auto-accept however many outlets repeat it, because what the
#: outlets are agreeing on is a figure that was never a round.
#:
#: Every pattern was measured against the seventeen rows in the amount ledger
#: on 2026-08-04 and against the rounds that must NOT be vetoed:
#:
#:   assets       Arch "Surpasses $539 Billion In Private Market Assets". The
#:                bare word is enough here: a funding row that mentions assets
#:                at all is the AUM confusion until a person says otherwise.
#:   fund(s)      A16z "Raises $15B In New Funds", Blackstone "raises $6.3B
#:                life sciences fund", Kingswood "Across Two Oversubscribed
#:                Middle-Market Funds". `\bfunds?\b` and NOT `funding`, which
#:                is the word every real round uses: xAI's "Series E funding
#:                round" and Databricks' "in latest funding" both pass clean.
#:   IPO          a public listing is not private funding.
#:   capex        ASE "lifts 2026 capex to record US$10.5 billion" and Turkish
#:                Airlines "injects $2bn" are money SPENT, not money raised.
#:
#: It is deliberately over-eager. A real round whose investor happens to be a
#: sovereign wealth fund trips `\bfund\b` and loses its auto-accept - and that
#: costs one human review, which is the direction this rule is allowed to be
#: wrong in. It NEVER creates a finding of its own and never quarantines
#: anything: it only withholds the shortcut. A new queue with nobody on it is
#: the defect this whole change exists to remove, not one to add.
NOT_A_COMPANY_ROUND = re.compile(
    r"\bassets?\s+under\s+management\b|\bAUM\b|\bassets\b"
    r"|\bfunds?\b"
    r"|\bIPO\b|initial\s+public\s+offering|\bgoes?\s+public\b"
    r"|\bcapex\b|capital\s+expenditure|capital\s+spending"
    r"|\binject(?:s|ed|ing|ion)\b",
    re.I,
)


def not_a_company_round(*texts: str | None) -> str | None:
    r"""The phrase that says this figure is not a company raise, or None.

    TWO VOCABULARIES, ON PURPOSE, and what separates them is what a mistake
    costs.

    The regex above vetoes an AUTO-ACCEPT and never refuses anything. Being
    wrong costs one human review, so it can afford `\bassets\b` and `\bfunds?\b`
    bare and be wrong about a sovereign wealth fund in an investor list.

    `pipeline/capital_event` decides what gets STORED. Being wrong there loses
    a real round silently and for ever, so it refuses only instruments that
    exist nowhere but the public and lender markets — and it therefore knows
    words this regex never learned, because they never appeared on a figure
    large enough to reach the amount queue: senior notes, sukuk, registered
    direct offerings, syndicated facilities, stock sales.

    Consulted here as well so the auto-accept cannot publish a mega-bond the
    store itself would have refused. Asked in cost order: the cheap over-eager
    one first, the careful one second.
    """
    for text in texts:
        hit = NOT_A_COMPANY_ROUND.search(text or "")
        if hit:
            return hit.group(0).strip()
    from . import capital_event
    verdict = capital_event.explain(*texts)
    return verdict[1] if verdict else None


def corroborating_outlets(conn, *, company_key: str, amount_usd: int,
                          signal_id: str = "") -> set[str]:
    """The independent outlets that state THIS employer at THIS figure.

    Two sources, unioned, because corroboration reaches us two ways and only
    one of them survives as a row:

      1. Other stored rows for the same company_key at the same amount. This
         is what a false split leaves behind, and it is real evidence.
      2. `funding_corroborations`, written at the moment dedup discards a
         second outlet's article. This is the main channel and by construction
         the only record of it - see pipeline/store.record_corroboration.

    "Same amount" uses dedupe.AMOUNT_TOLERANCE and not equality, so "$29.9bn"
    and "$30bn" are one round rounded two ways, exactly as the dedup layer
    already reads them. ONE definition; a corroboration rule that counted
    agreement differently from the layer that produced the agreement would be
    two systems with one name.

    Hosts are registrable domains. A count that treated `finance.example.com`
    and `www.example.com` as two outlets would be a rule that inflates itself
    on syndication, which is the one way this could publish a wrong figure.
    """
    from . import dedupe
    from .store import registrable_host

    hosts: set[str] = set()
    for row in conn.execute(
            "SELECT signal_id, source_url, funding_amount_usd v FROM signals "
            " WHERE is_current = 1 AND company_key = ? "
            "   AND funding_amount_usd IS NOT NULL", (company_key,)):
        if dedupe._same_amount_claim(amount_usd, row["v"]) is not True:
            continue
        host = registrable_host(row["source_url"] or "")
        if host:
            hosts.add(host)
        if not signal_id:
            signal_id = row["signal_id"]

    try:
        for row in conn.execute(
                "SELECT host, amount_usd FROM funding_corroborations "
                " WHERE signal_id = ?", (signal_id,)):
            # An outlet that reported a DIFFERENT figure is not corroboration.
            # It was recorded because dedup matched the round; whether it
            # matched the number is a separate question and this is where it
            # gets asked.
            if row["amount_usd"] is None:
                continue
            if dedupe._same_amount_claim(amount_usd, row["amount_usd"]) is True:
                hosts.add(row["host"])
    except Exception:
        # An older database with no such table corroborates nothing, which
        # leaves every flagged row exactly where it was: held for a human.
        pass
    return hosts


def amount_auto_accept(conn, row) -> dict:
    """Whether this over-threshold figure may publish without being asked about.

    Returns the reasoning, always, so the caller can print WHY a mega-round
    went out rather than asserting that it was fine.
    """
    outlets = corroborating_outlets(
        conn, company_key=row["company_key"] or "", amount_usd=row["v"],
        signal_id=row["signal_id"])
    veto = not_a_company_round(row["headline"], row["summary"])
    ok = len(outlets) >= CORROBORATION_MIN_OUTLETS and not veto
    return {"accept": ok, "outlets": sorted(outlets), "veto": veto}


def check_amounts(conn) -> tuple[list[Finding], dict]:
    """Flag any single funding amount the stored distribution cannot explain,
    EXCEPT one that independent outlets corroborate and no class rule vetoes.

    See the block above `CORROBORATION_MIN_OUTLETS` for why the exemption is
    two conditions and not a bigger number.
    """
    stats = derive_amount_threshold(stored_amounts(conn))
    threshold = stats["threshold"]
    rows = conn.execute(
        f"SELECT content_hash, signal_id, company, company_key, headline, "
        f"       summary, funding_amount_usd v, collector, source_url "
        f"  FROM signals WHERE {PUBLISHED_SQL} AND funding_amount_usd > ? "
        f" ORDER BY funding_amount_usd DESC", (threshold,)).fetchall()

    findings: list[Finding] = []
    corroborated: list[dict] = []
    for row in rows:
        verdict = amount_auto_accept(conn, row)
        if verdict["accept"]:
            corroborated.append({
                "content_hash": row["content_hash"],
                "label": f"{row['company']} ${row['v']:,}",
                "outlets": verdict["outlets"],
            })
            continue
        why = (f"Only {len(verdict['outlets'])} independent outlet(s) state this "
               f"figure; {CORROBORATION_MIN_OUTLETS} would publish it "
               f"automatically.")
        if verdict["veto"]:
            why = (f"Its own text says {verdict['veto']!r}, which is not a "
                   f"company raising money, so no number of outlets can "
                   f"publish it automatically.")
        findings.append(Finding(
            check=AMOUNT,
            subject=row["content_hash"],
            label=f"{row['company']} ${row['v']:,}",
            detail=(f"${row['v']:,} is above the derived threshold of "
                    f"${threshold:,} ({stats['reason']}). {why} Collector "
                    f"{row['collector']}. Read the filing at {row['source_url']} "
                    f"and accept it if the figure is real."),
            value=float(row["v"]),
        ))

    stats["corroborated"] = corroborated
    return findings, stats


# --------------------------------------------------------------------------
# 2. Period totals must reconcile
# --------------------------------------------------------------------------

# The metrics the glance matrix carries, mirroring tit_glance_matrix() in
# includes/shortcodes.php. Each is (key, label, SQL, is_subset_of_all).
#
# `money` is a SUM and not a count, so it takes part in the period ordering
# check and not in the subset check.
GLANCE_METRICS = (
    ("hiring", "Hiring up", "signal_direction = 'hiring'", True),
    ("funded", "Funding raised",
     "((funding_amount IS NOT NULL AND funding_amount <> '')"
     " OR (funding_stage IS NOT NULL AND funding_stage <> ''))", True),
    ("leadership", "Leadership moves", "pillar = 'leadership_change'", True),
    ("pay", "Pay news", "pillar = 'rewards_comp'", True),
    ("total", "All updates", "1 = 1", False),
)


def period_starts(today: date) -> list[tuple[str, date]]:
    """The tiles' period starts, derived exactly as the plugin derives them.

    Quarter start is computed, never hardcoded, and the year label carries the
    year rather than the words "so far".
    """
    q_month = ((today.month - 1) // 3) * 3 + 1
    return [
        ("This week", today - timedelta(days=6)),
        ("This month", date(today.year, today.month, 1)),
        ("This quarter", date(today.year, q_month, 1)),
        (f"{today.year} YTD", date(today.year, 1, 1)),
    ]


def glance_matrix(conn, today: date | None = None) -> dict:
    """Rebuild the page's at-a-glance matrix from the local database.

    Same date expression, same period derivation, same row set as the plugin,
    so an invariant that holds here holds on the page.
    """
    today = today or datetime.now(timezone.utc).date()
    periods = period_starts(today)
    where = f"{PUBLISHED_SQL} AND {NOTABLE_SQL}"

    cells: dict[str, list[int]] = {}
    for key, _label, sql, _subset in GLANCE_METRICS:
        row = []
        for _name, start in periods:
            row.append(int(conn.execute(
                f"SELECT COUNT(*) FROM signals WHERE {where} AND ({sql}) "
                f"  AND {DATE_EXPR} >= ?", (start.isoformat(),)).fetchone()[0]))
        cells[key] = row

    cells["money"] = [int(conn.execute(
        f"SELECT COALESCE(SUM(funding_amount_usd), 0) FROM signals "
        f" WHERE {where} AND {DATE_EXPR} >= ?", (start.isoformat(),)).fetchone()[0])
        for _name, start in periods]

    all_time = {key: int(conn.execute(
        f"SELECT COUNT(*) FROM signals WHERE {where} AND ({sql})").fetchone()[0])
        for key, _label, sql, _subset in GLANCE_METRICS}
    all_time["money"] = int(conn.execute(
        f"SELECT COALESCE(SUM(funding_amount_usd), 0) FROM signals "
        f" WHERE {where}").fetchone()[0])

    return {"periods": periods, "cells": cells, "all_time": all_time}


def check_period_totals(conn, today: date | None = None) -> list[Finding]:
    """Three invariants the page carried three contradicting numbers against.

    The live page once showed "this quarter 268" beside "2026 so far 6,018"
    while a headline said 14,019. Three figures that could not all be right, and
    nothing anywhere asked.

    1. ORDERING. Every cell is "rows on or after this period's start", so a
       period that starts LATER can never hold more rows than one that starts
       earlier. Derived from the start dates rather than assuming the periods
       nest: "this week" reaches back over a month boundary in the first days of
       a month, so week-inside-month is NOT an invariant and asserting it would
       have produced a false alarm six days in twelve.
    2. YEAR-TO-DATE NEVER EXCEEDS ALL-TIME.
    3. A SUBSET NEVER EXCEEDS THE WHOLE. Leadership moves, pay news, hiring and
       funding are all subsets of "All updates" in the same column. This is what
       catches a mis-scoped WHERE, which is how the 998 vehicles got counted as
       funding in the first place.
    """
    matrix = glance_matrix(conn, today)
    periods = matrix["periods"]
    cells = matrix["cells"]
    all_time = matrix["all_time"]
    findings: list[Finding] = []

    labels = {"money": "Money raised", **{k: lab for k, lab, _s, _sub in GLANCE_METRICS}}

    for key, row in cells.items():
        unit = "$" if key == "money" else ""
        # Every cell counts rows on or after its own start, so a period that
        # starts EARLIER contains every row a later one does and can never hold
        # less. `wide` is the earlier start, `narrow` the later one.
        for wide, (wide_name, wide_start) in enumerate(periods):
            for narrow, (narrow_name, narrow_start) in enumerate(periods):
                if wide_start >= narrow_start or row[wide] >= row[narrow]:
                    continue
                findings.append(Finding(
                    check=PERIOD_TOTALS,
                    subject=f"order/{key}/{narrow_name}/{wide_name}",
                    label=f"{labels[key]}: {narrow_name} exceeds {wide_name}",
                    detail=(f"{narrow_name} (from {narrow_start}) holds "
                            f"{unit}{row[narrow]:,} while {wide_name} (from "
                            f"{wide_start}) holds only {unit}{row[wide]:,}. "
                            f"{wide_name} starts earlier, so it already contains "
                            f"every row {narrow_name} does."),
                    value=float(row[narrow] - row[wide]),
                ))

        ytd = row[-1]
        if ytd > all_time[key]:
            findings.append(Finding(
                check=PERIOD_TOTALS,
                subject=f"ytd/{key}",
                label=f"{labels[key]}: year-to-date exceeds all-time",
                detail=(f"{periods[-1][0]} holds {unit}{ytd:,} against an "
                        f"all-time {unit}{all_time[key]:,}."),
                value=float(ytd - all_time[key]),
            ))

    for key, label, _sql, subset in GLANCE_METRICS:
        if not subset:
            continue
        for i, (name, _start) in enumerate(periods):
            if cells[key][i] <= cells["total"][i]:
                continue
            findings.append(Finding(
                check=PERIOD_TOTALS,
                subject=f"subset/{key}/{name}",
                label=f"{label} exceeds All updates in {name}",
                detail=(f"{label} reports {cells[key][i]:,} in {name} against "
                        f"{cells['total'][i]:,} updates in total. Every one of "
                        f"these rows is also an update, so one of the two "
                        f"clauses is scoped wrong."),
                value=float(cells[key][i] - cells["total"][i]),
            ))

    return findings


# --------------------------------------------------------------------------
# 3. The printed date span must match the data
# --------------------------------------------------------------------------

def span(conn, where: str) -> dict:
    """The date range one row set actually covers, from ONE query.

    Both bounds and the day count come from the same pair, because the incident
    this exists for is exactly what happens when they do not.
    """
    row = conn.execute(
        f"SELECT MIN({DATE_EXPR}) lo, MAX({DATE_EXPR}) hi, COUNT(*) n "
        f"  FROM signals WHERE {where}").fetchone()
    lo, hi, n = row["lo"], row["hi"], int(row["n"])
    days = None
    if lo and hi:
        days = (date.fromisoformat(hi[:10]) - date.fromisoformat(lo[:10])).days + 1
    return {"lo": lo, "hi": hi, "days": days, "n": n}


def span_scopes(conn) -> dict:
    """The two scopes the page holds at once, which is where it went wrong.

    `all` drives the date inputs (every row we hold, so the control can never
    refuse a date that exists); `view` is the default dashboard set and is what
    the sentence under the tiles describes. They are different sets and they are
    allowed to differ - what is not allowed is printing one scope's bounds beside
    the other scope's day count.
    """
    return {
        "all": span(conn, PUBLISHED_SQL),
        "view": span(conn, f"{PUBLISHED_SQL} AND {NOTABLE_SQL}"),
    }


def check_date_span(conn, today: date | None = None, live_span: dict | None = None) -> list[Finding]:
    """Assert the printed range and the computed span describe the same thing.

    The page once read "Everything here spans 3,318 days, 28 Jun to 28 Jul 2026"
    - nine years of days against thirty days of dates. Both halves came from one
    query and two SCOPES: the day count was measured across the whole table
    while the printed bounds came from the recent window.

    Four assertions, all on the same set the tiles count:

      a. the bounds are real, ordered dates
      b. the day count is derived from THOSE bounds and no others, so a scope
         swap shows up as a mismatch rather than as a plausible sentence
      c. the view scope sits inside the all scope; if it does not, the two
         queries are reading different tables
      d. the span reaches every period the tiles report a nonzero count for. A
         tile claiming rows this quarter over a span that ends last year is the
         self-contradiction in its other direction.

    `live_span` is the `span` object from a live /aggregate response. When it is
    given, the published bounds must equal the recomputed ones - which is the
    only assertion here that can see what a reader actually reads.
    """
    today = today or datetime.now(timezone.utc).date()
    scopes = span_scopes(conn)
    findings: list[Finding] = []

    for name, s in scopes.items():
        if not s["n"]:
            continue
        if not s["lo"] or not s["hi"]:
            findings.append(Finding(
                check=DATE_SPAN, subject=f"bounds/{name}",
                label=f"{name} scope holds {s['n']} rows with no date bounds",
                detail="Every row carries a published or captured date, so a "
                       "missing bound means the date expression changed shape.",
            ))
            continue
        lo = date.fromisoformat(s["lo"][:10])
        hi = date.fromisoformat(s["hi"][:10])
        if lo > hi:
            findings.append(Finding(
                check=DATE_SPAN, subject=f"order/{name}",
                label=f"{name} scope range runs backwards",
                detail=f"lo {lo} is after hi {hi}.",
            ))
        expected = (hi - lo).days + 1
        if s["days"] != expected:
            findings.append(Finding(
                check=DATE_SPAN, subject=f"days/{name}",
                label=f"{name} scope day count does not match its own bounds",
                detail=(f"{s['days']} days printed against {expected} days "
                        f"between {lo} and {hi}. This is the scope swap: a count "
                        f"measured over one row set beside bounds taken from "
                        f"another."),
                value=float((s["days"] or 0) - expected),
            ))

    a, v = scopes["all"], scopes["view"]
    if a["lo"] and v["lo"] and a["hi"] and v["hi"]:
        if v["lo"] < a["lo"] or v["hi"] > a["hi"]:
            findings.append(Finding(
                check=DATE_SPAN, subject="containment",
                label="the shown view reaches outside the full range",
                detail=(f"view {v['lo']}..{v['hi']} is not inside "
                        f"all {a['lo']}..{a['hi']}. The view is a subset of the "
                        f"table, so its span cannot be wider."),
            ))

    # (d) the span has to cover the periods the tiles count in.
    if v["hi"]:
        hi = date.fromisoformat(v["hi"][:10])
        matrix = glance_matrix(conn, today)
        for i, (name, start) in enumerate(matrix["periods"]):
            if matrix["cells"]["total"][i] and start > hi:
                findings.append(Finding(
                    check=DATE_SPAN, subject=f"coverage/{name}",
                    label=f"{name} counts rows the printed range excludes",
                    detail=(f"{name} reports {matrix['cells']['total'][i]:,} "
                            f"updates from {start} while the printed range ends "
                            f"{hi}."),
                ))

    # The live half: what a reader is actually reading. /aggregate computes its
    # span under the CALLER's filters, so an unfiltered request returns the all
    # scope and the dashboard's own `detail=notable` request returns the view
    # scope. Both are legitimate answers, so a published pair is wrong only when
    # it matches NEITHER - which is precisely the scope-swap shape.
    if live_span:
        published = tuple((live_span.get(b) or "")[:10] for b in ("lo", "hi"))
        if all(published):
            legitimate = {
                tuple((s[b] or "")[:10] for b in ("lo", "hi"))
                for s in (a, v) if s["lo"] and s["hi"]
            }
            if legitimate and published not in legitimate:
                findings.append(Finding(
                    check=DATE_SPAN, subject="live/bounds",
                    label="the live page prints a range the data does not have",
                    detail=(f"/aggregate prints {published[0]}..{published[1]}. "
                            f"The stored rows support "
                            f"{' or '.join(f'{lo}..{hi}' for lo, hi in sorted(legitimate))} "
                            f"and nothing else."),
                ))

    return findings


# --------------------------------------------------------------------------
# 4. Vehicle and SPV names on funding rows
# --------------------------------------------------------------------------

# The Form D defect had a signature: entities that employ nobody. A name that is
# a single street address, a numbered vehicle, an insurance separate account, a
# synthetic GIC.
#
# THE LESSON THIS IS WRITTEN FROM, and it is already paid for: the first fix
# spelled out "guaranteed investment contract" and therefore missed the trade's
# own abbreviation, leaving four GIC/BOLI/COLI rows worth $12.4bn sitting at the
# top of the money list. So the abbreviations are here, first-class.
#
# EVERY PATTERN BELOW WAS MEASURED, against the 998 rows the correction actually
# retracted (recoverable from `signals` where is_current = 0 and the retraction
# note) and against the 3,057 funding rows live today. A pattern earns its place
# only if what it recovers from the real defect beats what it costs in live
# review. Two candidates were tested and REJECTED on that rule, and are recorded
# here so nobody re-adds them from first principles:
#
#   \bseries\s+\d+$          2 retracted rows / $0.00bn, 16 live rows. All 16 are
#                            one employer's series LLCs. Cost exceeds yield.
#   \b\d{1,2}\s*(llc|lp)$    38 retracted rows / $0.23bn (0.3% of the defect),
#                            24 live rows including HawkEye 360, Inc. and other
#                            operating companies whose brand carries a number.
#
# Measured recall of what IS here, on the real retracted set: 229 of 998 rows,
# but $71.3bn of the $85.6bn - because the vehicles are exactly the large ones,
# which is the property that makes a name check worth running at publish time.
# On today's live rows it flags 3.
PUBLISH_TIME_VEHICLE_PATTERNS = re.compile(
    # A name that is a street address. "101 East Court Street LLC",
    # "1316 6TH STREET MB LLC", "259 East Broadway Owner LLC". An operating
    # employer is not named after the building it is in.
    r"^\s*\d+[-\d]*\s+(?:[NSEW]\.?\s+)?[\w'.-]+(?:\s+[\w'.-]+){0,3}\s+"
    r"(?:st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|ln|lane|way|pl|"
    r"place|ct|court|hwy|highway|pkwy|parkway|ter|terrace|cir|circle|sq|square|"
    r"broadway|main)\b\.?"
    # Insurance separate accounts and the product wrappers that ride on them.
    r"|separate\s+account|\bppvu?l\b|\bvul\b|variable\s+(?:life|annuity|account)"
    # Synthetic GICs and bank/company-owned life insurance, IN THE ABBREVIATIONS
    # THE INDUSTRY ACTUALLY USES. Measured: these match zero rows today AND zero
    # of the retracted ones, and that is a finding rather than a failure - see
    # the note under `check_vehicle_names` for where the signature really lives.
    r"|\bgics?\b|\bboli\b|\bcoli\b|synthetic\s+gic"
    r"|guaranteed\s+(?:investment|interest)\s+contract"
    r"|funding\s+agreement|institutional\s+life\b"
    # A numbered vehicle: a bare ordinal sitting where a company would put a
    # product. "NATIONWIDE PPVUL SEPARATE ACCOUNT 6", "Nationwide PPVUL Separate
    # Account 7". 6 retracted rows worth $12.83bn, zero live rows.
    r"|\b(?:separate\s+account|account|fund|portfolio|no\.?|number)\s+\d{1,4}\s*$"
    # An insurer's own name on a funding row. The correction log is explicit
    # that a NAME filter cannot decide this one - Metropolitan Life Insurance Co
    # is a real employer and its annuity product filings sit beside its real
    # corporate raises under the same name and the same CIK. That is an argument
    # against DROPPING it, not against flagging it: the discriminator lives in
    # the filing's own description, which the row does not carry by the time it
    # reaches here, so the only honest move at publish time is to make a person
    # look. Two live rows today, both Metropolitan Tower Life Insurance Co.
    r"|life\s+insurance\s+(?:co|company|corp)\b|insurance\s+&?\s*annuity"
    r"|\bannuit(?:y|ies)\b",
    re.I,
)


def _collector_vehicle_patterns():
    """The Form D collector's own exclusion list, reused and never restated.

    Imported lazily: `collectors` import `pipeline`, so a module-level import
    here would close the cycle. It also means a machine without `requests`
    installed still gets the publish-time patterns rather than an ImportError,
    which matters because `ops_status.py` runs with no dependencies at all.

    That fallback is a real divergence, so it is REPORTABLE rather than silent -
    see `collector_patterns_available`. A check that quietly narrows itself and
    then prints a smaller number is the exact failure this project keeps
    finding: healthy-looking and wrong.
    """
    try:
        from collectors.sec_form_d import EXCLUDED_NAME_PATTERNS
        return EXCLUDED_NAME_PATTERNS
    except Exception:
        return None


def collector_patterns_available() -> bool:
    """False when this interpreter cannot reach the collector's pattern set, so
    a caller can say the name check is running narrower than the pipeline's."""
    return _collector_vehicle_patterns() is not None


def vehicle_match(name: str) -> str | None:
    """The phrase that makes this name a vehicle, or None."""
    name = (name or "").strip()
    if not name:
        return None
    hit = PUBLISH_TIME_VEHICLE_PATTERNS.search(name)
    if hit:
        return hit.group(0).strip()
    collector = _collector_vehicle_patterns()
    if collector:
        hit = collector.search(name)
        if hit:
            return hit.group(0).strip()
    return None


def check_vehicle_names(conn) -> list[Finding]:
    """Funding rows whose issuer name says it employs nobody.

    Runs on EVERY funding row and not only on Form D ones. The collector's
    filter governs what Form D collects; this governs what reaches a headline
    figure, whatever route it arrived by, which is the difference between fixing
    one source and closing the class.

    A finding worth reading even when it is empty: the GIC / BOLI / COLI
    abbreviations match nothing here, because on the real retracted rows that
    wording lived in the filing's DESCRIPTIONOFOTHERTYPE field and never in the
    issuer's name (checked across every stored text column: zero hits). The
    abbreviation belongs in `collectors/sec_form_d_bulk.NOT_A_CAPITAL_RAISE`,
    where the description is actually read, and it is here as well because it
    costs nothing and the next vehicle to carry it in its NAME should not need a
    second incident to be caught.
    """
    rows = conn.execute(
        f"SELECT content_hash, company, funding_amount_usd v, collector, source_url "
        f"  FROM signals WHERE {PUBLISHED_SQL} AND funding_amount_usd > 0 "
        f" ORDER BY funding_amount_usd DESC").fetchall()

    findings = []
    for row in rows:
        phrase = vehicle_match(row["company"])
        if not phrase:
            continue
        findings.append(Finding(
            check=VEHICLE_NAME,
            subject=row["content_hash"],
            label=f"{row['company']} ${row['v']:,}",
            detail=(f"the issuer name matches {phrase!r}, the signature of an "
                    f"entity that employs nobody: a street address, a numbered "
                    f"vehicle, an insurance separate account or a synthetic GIC. "
                    f"Collector {row['collector']}. Read {row['source_url']} and "
                    f"either accept it as a real employer or retract the row."),
            value=float(row["v"] or 0),
        ))
    return findings


# --------------------------------------------------------------------------
# Running them, and the ledger
# --------------------------------------------------------------------------

def evaluate(conn, *, today: date | None = None, live_span: dict | None = None) -> dict:
    """Run every guardrail. Reads only; writes nothing.

    Returns the findings plus the derived threshold, so a caller can print the
    reasoning rather than a bare verdict.
    """
    amount_findings, amount_stats = check_amounts(conn)
    findings = (
        amount_findings
        + check_period_totals(conn, today)
        + check_date_span(conn, today, live_span)
        + check_vehicle_names(conn)
    )
    return {"findings": findings, "amount": amount_stats}


def record(conn, findings: list[Finding], *, checks=CHECKS) -> dict:
    """Write findings to the ledger, preserving every human decision.

    A finding that fires again keeps whatever state a person put it in, which is
    the entire reason ChangXin Memory's genuine $8.6bn raise does not have to be
    re-accepted every night. A previously open finding that stops firing is
    marked `resolved` rather than deleted, so the ledger keeps saying what was
    caught and what happened to it.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    seen: dict[str, set[str]] = {c: set() for c in checks}

    for f in findings:
        seen.setdefault(f.check, set()).add(f.subject)
        conn.execute(
            """INSERT INTO publish_guardrails
                 (check_name, subject, label, detail, value, state,
                  first_seen, last_seen, seen)
               VALUES (?, ?, ?, ?, ?, 'open', ?, ?, 1)
               ON CONFLICT(check_name, subject) DO UPDATE SET
                 label = excluded.label,
                 detail = excluded.detail,
                 value = excluded.value,
                 last_seen = excluded.last_seen,
                 seen = publish_guardrails.seen + 1,
                 -- A resolved finding that comes back is open again. An
                 -- accepted or rejected one keeps the human's answer.
                 state = CASE WHEN publish_guardrails.state = 'resolved'
                              THEN 'open' ELSE publish_guardrails.state END""",
            (f.check, f.subject, f.label, f.detail, f.value, now, now))

    resolved = 0
    for check in checks:
        subjects = seen.get(check, set())
        placeholders = ", ".join("?" for _ in subjects)
        sql = ("UPDATE publish_guardrails SET state = 'resolved', last_seen = ? "
               " WHERE check_name = ? AND state = 'open'")
        params = [now, check]
        if subjects:
            sql += f" AND subject NOT IN ({placeholders})"
            params += sorted(subjects)
        resolved += conn.execute(sql, params).rowcount
    conn.commit()
    return {"recorded": len(findings), "resolved": resolved}


def open_findings(conn) -> list[dict]:
    """Everything still waiting on a human, worst money first.

    OPEN ONLY, and the two states it leaves out are left out for opposite
    reasons: an accepted finding releases its row, a rejected one withholds it
    for good. `rejected_findings` is the other half and `quarantine` reads both.
    """
    try:
        rows = conn.execute(
            "SELECT * FROM publish_guardrails WHERE state = 'open' "
            " ORDER BY COALESCE(value, 0) DESC, check_name, subject").fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]


def rejected_findings(conn) -> list[dict]:
    """Every finding a human has answered with NO, worst money first.

    These are decided, so they are not a queue and never appear in one. They
    are read for one purpose: their rows must not publish. See rule 3 in the
    module docstring for why that is not the same as "released".
    """
    try:
        rows = conn.execute(
            "SELECT * FROM publish_guardrails WHERE state = 'rejected' "
            " ORDER BY COALESCE(value, 0) DESC, check_name, subject").fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]


#: One real-world event, seen twice. Both bounds were measured against the whole
#: ledger rather than chosen: at these values the matcher finds four pairs and
#: every one of them is genuine (DayOne/DayOne Data Centers, Kingswood
#: Capital/Kingswood Capital Management, the two Intel $20bn stock-sale rows,
#: and Alibaba/Alibaba Group Holding), with no false pair anywhere in it.
EVENT_AMOUNT_RATIO = 1.15
EVENT_WINDOW_DAYS = 14


def _event_tokens(company: str) -> list[str]:
    """The employer's key as tokens, for prefix comparison."""
    try:
        from pipeline.vocab import company_key
    except Exception:                                    # pragma: no cover
        return (company or "").lower().split()
    return company_key(company or "").split()


def same_event(a: dict, b: dict) -> bool:
    """Do two findings describe ONE announcement?

    THIS IS ADVISORY AND IT NEVER MERGES ANYTHING. It decides what a reviewer
    is shown, not what a row is worth, and it writes to no column. That is the
    whole reason it may use a looser employer test than `company_key`: getting
    it wrong costs a line of output, not a number in public.

    Three conditions, all required:

      employer  one key is a token-wise PREFIX of the other. This is what
                catches every shape the ledger actually holds -- `alibaba` vs
                `alibaba group holding`, `kingswood capital` vs `kingswood
                capital management`, `dayone` vs `dayone data centers` -- with
                one rule instead of a list of corporate suffixes to maintain.
      amount    within EVENT_AMOUNT_RATIO. Outlets round the same number
                differently: HK$80bn was reported as both $10bn and $10.2bn.
      date      within EVENT_WINDOW_DAYS. One announcement is covered over a
                fortnight, by wires first and weeklies later.

    WHY NOT FIX company_key INSTEAD. Because `company_key` feeds `content_hash`,
    which is the dedup identity of all 32k stored rows. Widening it there is a
    corpus-wide rewrite through `correct_company_key.py` and a decision about
    employer identity that belongs to the owner, not a side effect of a review
    tool. See docs/RULING-public-equity-proceeds.md.
    """
    for k in ("value", "company", "published_date"):
        if not a.get(k) or not b.get(k):
            return False
    hi, lo = max(a["value"], b["value"]), min(a["value"], b["value"])
    if lo <= 0 or hi / lo > EVENT_AMOUNT_RATIO:
        return False
    ta, tb = _event_tokens(a["company"]), _event_tokens(b["company"])
    n = min(len(ta), len(tb))
    if not n or ta[:n] != tb[:n]:
        return False
    try:
        da = date.fromisoformat(str(a["published_date"])[:10])
        db = date.fromisoformat(str(b["published_date"])[:10])
    except ValueError:
        return False
    return abs((da - db).days) <= EVENT_WINDOW_DAYS


def _findings_with_rows(conn) -> list[dict]:
    """Every finding, carrying its row's company and date.

    Deliberately NOT filtered on `is_current`: a sibling that was retracted or
    revised is still a decision somebody made about this event, and it is
    exactly the one a reviewer needs to see. Both Kingswood rows are is_current
    = 0 and they are the clearest precedent in the ledger.
    """
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM publish_guardrails")]
    except Exception:
        return []
    facts: dict[str, dict] = {}
    try:
        # is_current last, so a current row wins the key for a hash that has both.
        for r in conn.execute("SELECT content_hash, company, published_date "
                              "  FROM signals ORDER BY is_current"):
            facts[r["content_hash"]] = {"company": r["company"],
                                        "published_date": r["published_date"]}
    except Exception:
        return []
    for row in rows:
        row.update(facts.get(row["subject"], {}))
    return rows


def siblings_of(conn, subject: str, check_name: str | None = None) -> list[dict]:
    """Other findings describing the same event, decided ones first.

    WHY A REVIEWER MUST SEE THIS. A guardrail decision attaches to
    `content_hash`, which is company_key|pillar|date|normalised-headline. A
    second outlet writing a different headline about the same announcement
    therefore arrives as a NEW finding with no memory of the first answer. It
    has happened four times: two DayOne rows, two Kingswood rows, two Intel
    rows, and Alibaba, where the $10.2bn row was rejected and the $10.0bn row
    came back OPEN two days later as though nothing had been decided.
    """
    rows = _findings_with_rows(conn)
    me = next((r for r in rows
               if r["subject"] == subject
               and (check_name is None or r["check_name"] == check_name)), None)
    if me is None:
        return []
    out = [r for r in rows
           if r["subject"] != subject and same_event(me, r)]
    rank = {"rejected": 0, "accepted": 1, "resolved": 2, "open": 3}
    out.sort(key=lambda r: (rank.get(r.get("state"), 4), -(r.get("value") or 0)))
    return out


def _shared_note_clash(conn, subject: str, check: str, note: str) -> dict | None:
    """The first finding already carrying this exact note for another event."""
    rows = _findings_with_rows(conn)
    me = next((r for r in rows if r["subject"] == subject
               and r["check_name"] == check), None)
    want = (note or "").strip()
    for row in rows:
        if row["subject"] == subject and row["check_name"] == check:
            continue
        if (row.get("review_note") or "").strip() != want:
            continue
        if me is not None and same_event(me, row):
            continue                      # one event, one answer: legitimate
        return row
    return None


class SharedNoteRefused(Exception):
    """A note already used to decide a DIFFERENT event was reused verbatim."""


def review(conn, key: str, state: str, note: str, who: str = "",
           allow_shared_note: bool = False) -> int:
    """Accept or reject one finding. `key` is 'check/subject'.

    REFUSES A NOTE THAT ALREADY DECIDED A DIFFERENT EVENT, and that guard is
    not hypothetical. On 2026-08-22/23 an agent adjudicating the amount queue
    pasted one note across three unrelated findings, twice:

        "... timesofoman: 'Micron ... announced a $10 billion investment ...'"
             -> Micron $10bn        (the row the note is actually about)
             -> Alibaba Group Holding $10.2bn
             -> Lovable $13.3bn

        "... digitimes: 'Nitto Denko will invest JPY28 billion ...'"
             -> Nitto Denko $28bn   (the row the note is actually about)
             -> Nvidia $150bn
             -> Broadcom $60bn

    Six rows, $271.5bn, withheld for good; four of them on reasoning about a
    company they have nothing to do with. Nothing could catch it, because
    `review()` took any string. A rejection is permanent and invisible once
    made -- `--withheld` is the only place it shows -- so the note IS the
    evidence, and a note that was written about another company is not
    evidence about this one.

    Byte-identical reuse is the signal, deliberately: every note a human wrote
    in this ledger is unique, including the ones that cite a precedent in
    prose ("the same class as the Intel $20bn stock-sale row"). Citing another
    decision stays legal; copying one does not.

    Two escapes, both honest. Siblings of the SAME event may share a note --
    that is the correct way to answer a duplicate pair, and `siblings_of` is
    what decides it. Anything else needs `allow_shared_note`, which a person
    passes on purpose.
    """
    if state not in ("accepted", "rejected"):
        raise ValueError("state must be 'accepted' or 'rejected'")
    check, _, subject = key.partition("/")
    if note and not allow_shared_note:
        clash = _shared_note_clash(conn, subject, check, note)
        if clash:
            raise SharedNoteRefused(
                "this note was already recorded against %s/%s (%r), which is "
                "not the same event as %s/%s. A rejection is permanent, so the "
                "note is the only evidence anyone will ever see for it: write "
                "one about THIS row, or pass allow_shared_note if the two "
                "really are one announcement."
                % (clash["check_name"], clash["subject"][:12],
                   clash.get("label"), check, subject[:12]))
    cur = conn.execute(
        "UPDATE publish_guardrails SET state = ?, reviewed_at = ?, "
        "       reviewed_by = ?, review_note = ? "
        " WHERE check_name = ? AND subject = ?",
        (state, datetime.now(timezone.utc).isoformat(timespec="seconds"),
         who, note, check, subject))
    conn.commit()
    return cur.rowcount


#: Where a flagged row sits, which is what decides every consequence.
#:
#:   live      current and on the site. The figure is in public NOW; quarantine
#:             cannot pull it back and only `retract.py` can.
#:   pending   current and never sent. Withholding it is the entire fix.
#:   gone      no current row carries this hash - it was retracted or revised.
#:             Nothing to withhold and nobody to tell.
ROW_LIVE, ROW_PENDING, ROW_GONE = "live", "pending", "gone"


def _row_placement(conn, subjects: set[str]) -> dict[str, str]:
    """One query per 400 hashes: where each flagged row actually is.

    ONE definition, because the escalation, the withholding and the retraction
    advice all turn on the same three-way answer and two of them used to infer
    it separately from "is it published".
    """
    out: dict[str, str] = {}
    ordered = sorted(subjects)
    for start in range(0, len(ordered), 400):
        chunk = ordered[start:start + 400]
        placeholders = ", ".join("?" for _ in chunk)
        for row in conn.execute(
                f"SELECT content_hash, published_at FROM signals "
                f" WHERE content_hash IN ({placeholders}) AND is_current = 1",
                chunk):
            out[row[0]] = ROW_LIVE if row[1] else ROW_PENDING
    return {s: out.get(s, ROW_GONE) for s in subjects}


def _already_published(conn, subjects: set[str]) -> set[str]:
    """Of these content hashes, the ones the site already holds.

    The distinction the whole escalation turns on. A finding on a row we have
    never sent is a guardrail that WORKED: the number is not in public and
    quarantine keeps it that way. A finding on a row already on the site is a
    figure that is wrong in public right now, and no amount of holding rows back
    can pull it home - only a human retraction can.
    """
    return {s for s, where in _row_placement(conn, subjects).items()
            if where == ROW_LIVE}


def _hours_since(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        seen = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - seen).total_seconds() / 3600


def quarantine(conn, *, today: date | None = None, live_span: dict | None = None,
               write: bool = True) -> dict:
    """Evaluate, record, and say what must be held back.

    Returns a report and raises nothing. The decision about the run's exit
    status belongs to the caller, and `pipeline.publish` makes it in one place
    for every route: halt on an aggregate finding, quarantine the rows and carry
    on otherwise, escalate after the send when a finding has gone unanswered.

    The report:
      quarantined   content hashes that must not be sent this run
      held          open row findings whose row has never reached the site
      live          row findings, open OR rejected, whose row is already on the
                    site - the only place a rejection still needs a human,
                    because a published figure can only be retracted
      withheld      REJECTED row findings whose row has never reached the site.
                    Decided, permanently out, on no clock and in no queue.
      aggregate     open findings that name no row (these halt)
      overdue       row findings past their grace window (these escalate).
                    Never a withheld one: a decided finding cannot be neglected.
    """
    result = evaluate(conn, today=today, live_span=live_span)
    if write:
        result.update(record(conn, result["findings"]))

    if write:
        still_open = open_findings(conn)
    else:
        # Read-only: nothing was recorded, so this pass's findings ARE the open
        # set, minus anything a human has already answered. `first_seen` is read
        # from the ledger wherever the finding is already known, because
        # otherwise every read-only caller would compute an age of zero and no
        # ops tool could ever show a finding as overdue - which is exactly where
        # an overdue finding most needs to be visible.
        #
        # `answered` means "not open", and accepted and rejected are both that.
        # What they are NOT is the same outcome: below, the rejected ones come
        # back from the ledger as `withheld` and their rows are quarantined for
        # good. This set decides who is waiting on a human, and nothing else.
        # It used to decide what publishes, which is the defect rule 3 is
        # written from.
        answered: set[tuple[str, str]] = set()
        known: dict[tuple[str, str], str] = {}
        try:
            for row in conn.execute("SELECT check_name, subject, state, "
                                    "first_seen FROM publish_guardrails"):
                key = (row["check_name"], row["subject"])
                known[key] = row["first_seen"]
                if row["state"] in ("accepted", "rejected"):
                    answered.add(key)
        except Exception:
            pass
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        still_open = [
            {"check_name": f.check, "subject": f.subject, "label": f.label,
             "detail": f.detail, "value": f.value,
             "first_seen": known.get((f.check, f.subject), now)}
            for f in result["findings"] if (f.check, f.subject) not in answered]

    rows = [r for r in still_open if r["check_name"] in ROW_CHECKS]
    aggregate = [r for r in still_open if r["check_name"] in AGGREGATE_CHECKS]

    # Read from the ledger on BOTH paths, and deliberately not from this pass's
    # findings: a rejected row must stay out whether or not the check still
    # fires on it. A figure a person said no to does not become publishable
    # because the corpus grew a longer tail and the ceiling moved above it.
    decided = [r for r in rejected_findings(conn) if r["check_name"] in ROW_CHECKS]

    placement = _row_placement(
        conn, {r["subject"] for r in rows} | {r["subject"] for r in decided})
    held, live, withheld, overdue = [], [], [], []

    for row in rows:
        is_live = placement.get(row["subject"]) == ROW_LIVE
        row["already_live"] = is_live
        row["age_hours"] = _hours_since(row.get("first_seen"))
        row["grace_hours"] = (LIVE_FINDING_GRACE_HOURS if is_live
                              else HELD_FINDING_GRACE_HOURS)
        (live if is_live else held).append(row)
        if row["age_hours"] is not None and row["age_hours"] > row["grace_hours"]:
            overdue.append(row)

    for row in decided:
        where = placement.get(row["subject"], ROW_GONE)
        if where == ROW_GONE:
            # Already retracted or revised away. The rejection is history, not
            # a state anything still has to act on, and listing it would fill
            # every surface with settled corrections.
            continue
        row["rejected"] = True
        row["already_live"] = where == ROW_LIVE
        row["age_hours"] = _hours_since(row.get("first_seen"))
        if where == ROW_LIVE:
            # The one case rejection alone cannot finish. The figure is on the
            # page and withholding is not available, so it keeps the live
            # window and stays red until somebody retracts it.
            row["grace_hours"] = LIVE_FINDING_GRACE_HOURS
            live.append(row)
            if row["age_hours"] is not None and row["age_hours"] > row["grace_hours"]:
                overdue.append(row)
        else:
            # Never published, so the correction is complete. No clock: there
            # is nothing left for a person to do and a countdown on a decided
            # finding is how a red run stops meaning anything.
            row["grace_hours"] = None
            withheld.append(row)

    result.update({
        "open": still_open,
        "quarantined": ({r["subject"] for r in rows}
                        | {r["subject"] for r in withheld}
                        | {r["subject"] for r in live if r.get("rejected")}),
        "held": held,
        "live": live,
        "withheld": withheld,
        "aggregate": aggregate,
        "overdue": overdue,
    })
    return result


def unreviewed_amounts(rows: list[dict],
                       deadline_hours: float = AMOUNT_REVIEW_DEADLINE_HOURS
                       ) -> list[dict]:
    """The `amount` findings a person has now had long enough to answer.

    ONE definition, imported by ops_status.py [2d] and health_digest.py, for
    the same reason data_integrity has one: two tools that disagree about
    whether the queue is neglected are two tools that can each be reassuring on
    a day the other is not.

    Takes the ledger rows the caller already has (`held` + `live` from
    `quarantine`) rather than opening the database again, so it works
    identically on a read-only pass. A row whose age is unknown is NOT counted
    - absence of a timestamp is not evidence of freshness, and it would be the
    one shape that turns this into a check that always passes.

    Sorted worst money first: if a person answers exactly one of these before
    closing the laptop, it should be the biggest.

    A REJECTED finding is never in here however old it is. It has been
    answered; the queue is what nobody has answered. Passing `withheld` in with
    `held` and `live` is therefore harmless, and this is what makes it so.
    """
    out = [r for r in rows
           if r.get("check_name") == AMOUNT
           and not r.get("rejected")
           and r.get("age_hours") is not None
           and r["age_hours"] > deadline_hours]
    return sorted(out, key=lambda r: -(r.get("value") or 0))


def as_findings(rows: list[dict]) -> list[Finding]:
    """Ledger rows back into Findings, for an exception to carry."""
    return [Finding(check=r["check_name"], subject=r["subject"],
                    label=r.get("label") or "", detail=r.get("detail") or "",
                    value=r.get("value")) for r in rows]
