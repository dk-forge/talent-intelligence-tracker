#!/usr/bin/env python3
"""Withdraw published records, locally and on WordPress.

    python retract.py <signal_id> "why it was withdrawn"
    python retract.py "<id>,<id>,<id>" "why they were withdrawn"
    python retract.py --ids-file corrections.txt "why they were withdrawn"
    python retract.py --bare-domains "source link was an outlet homepage"

Nothing is deleted. The row is marked not-current with the reason, so the
corrections log can show what was published and when it was withdrawn.

ONE RUN WITHDRAWS A LIST, and that is not a convenience. retract.yml's
concurrency group is `talent-collect` with `cancel-in-progress: false`, and
GitHub keeps only ONE pending run per group: dispatching a 27-row correction as
27 runs silently drops most of them. Twenty-seven sequential dispatches would
also be twenty-seven rebase-and-push commits against a 72MB binary database —
twenty-seven chances at the binary conflict this workflow already fails on.

ONE REASON APPLIES TO THE WHOLE LIST. A correction is one editorial act; a
per-row reason would be a different feature and a much worse audit trail.

Two ways to give a list, because they are two different situations:
`<id>,<id>` is what a workflow input can carry (one string, through the
environment, no new `${{ }}` surface); `--ids-file` is for the human holding
twenty-seven ids, where a command line is the wrong container — it keeps the
list reviewable in a diff, keeps it out of shell history, and survives being
built by a query. Repeated positional ids were the third option and are not
here on purpose: this script's second positional argument is the reason, so
`retract.py a b c "why"` cannot be told apart from a typo'd reason without
argparse guesswork about the last token.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

from pipeline import publish, schema


def find_bare_domain_rows(conn) -> list[dict]:
    """Rows whose source link is an outlet homepage rather than an article.

    These cannot support the claim they are attached to, which is the one
    failure this product cannot carry.
    """
    rows = []
    for row in conn.execute(
        "SELECT signal_id, company, source_url FROM signals WHERE is_current = 1"
    ):
        if not urlparse(row["source_url"]).path.strip("/"):
            rows.append(dict(row))
    return rows


def parse_ids(raw: str) -> list[str]:
    """Split a comma (or newline) separated list of signal_ids.

    An EMPTY entry is an error, never a skip. `a,,b` is a list somebody built
    wrong — a spreadsheet column with a hole in it, a join over a missing
    value — and quietly withdrawing two rows when the human meant three is
    exactly the silence a correction workflow cannot afford.

    Duplicates collapse, order preserved: the same id twice is one withdrawal,
    and reporting the second as "already withdrawn" would be true but noise.
    """
    entries = [part for chunk in raw.split("\n") for part in chunk.split(",")]
    if not entries:
        raise ValueError("no signal_ids given")
    ids: list[str] = []
    for position, entry in enumerate(entries, start=1):
        cleaned = entry.strip()
        if not cleaned:
            raise ValueError(
                f"empty signal_id at position {position} of {len(entries)} "
                f"in {raw!r} — an empty entry is a list built wrong, not a row to skip"
            )
        if cleaned not in ids:
            ids.append(cleaned)
    return ids


def read_ids_file(path: str | Path) -> list[str]:
    """One signal_id per line (commas allowed too). `#` comments and blank
    lines are not entries and are dropped; an empty entry BETWEEN separators on
    a real line still raises, as in parse_ids."""
    text = Path(path).read_text(encoding="utf-8")
    lines = [line.split("#", 1)[0].strip() for line in text.splitlines()]
    kept = [line for line in lines if line]
    if not kept:
        raise ValueError(f"{path} contains no signal_ids")
    ids: list[str] = []
    for line in kept:
        for sid in parse_ids(line):
            if sid not in ids:
                ids.append(sid)
    return ids


#: Retries for a TRANSIENT host failure, and the pauses between them.
#:
#: Shared hosting 500s and 504s under load — it is gotcha 8 in CLAUDE.md and it
#: was walked into anyway: on 2026-07-29 a scope correction withdrew three rows
#: and lost four to `504` from the gateway, one request at a time, with nothing
#: wrong with the requests. A withdrawal that fails leaves a record live on a
#: page that promises it is not there, so this is the one place where "the host
#: was busy" must not be a final answer.
#:
#: Only 5xx and a dropped connection are retried. A 4xx is our fault — a bad
#: key, a signal_id that does not exist — and repeating it just asks the same
#: wrong question five times.
#:
#: PER ROW, not per list. A list of 27 is 27 independent requests: a wobble on
#: row 2 must not spend the allowance row 3 will need, and must not be read as
#: the host being down.
RETRY_PAUSES = (2, 5, 12, 30)

#: Wall-clock budget for the withdrawal loop, in seconds.
#:
#: The workflow's timeout was measured over runs that withdrew a handful of
#: rows; a 27-row list is a case nothing has timed. The nominal cost is small
#: (one POST per row, ~1s), but the WORST case is not: a row that exhausts the
#: ladder is 5 attempts x 45s timeout + 49s of pauses ~= 4.6 minutes, so 27
#: rows against a dying host is ~2 hours. No sane timeout-minutes covers that,
#: and being KILLED by the platform is the worst outcome available here — the
#: commit step never runs, and the database forgets withdrawals the site has
#: already applied.
#:
#: So the script stops ITSELF first. Past the budget the remaining rows are
#: reported by name as "not attempted", counted as failures, and printed in the
#: re-run list. Worst-case overrun is budget + one row's ladder (~4.6 min),
#: which is what retract.yml's timeout-minutes is sized against.
RUN_BUDGET_SECONDS = int(os.environ.get("TIT_RETRACT_BUDGET", 20 * 60))


def retract_remote(signal_id: str, reason: str) -> dict:
    site, key = publish._config()
    last = ""
    for attempt in range(len(RETRY_PAUSES) + 1):
        try:
            resp = requests.post(
                f"{site}/wp-json/talent/v1/retract",
                json={"signal_id": signal_id, "reason": reason},
                headers={
                    "X-Talent-API-Key": key,
                    "User-Agent": publish.USER_AGENT,
                    "Content-Type": "application/json",
                },
                timeout=45,
            )
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"[:200]
        else:
            if resp.status_code < 400:
                return resp.json()
            last = f"{resp.status_code}: {resp.text[:200]}"
            if resp.status_code < 500:
                raise publish.PublishError(last)
        if attempt < len(RETRY_PAUSES):
            time.sleep(RETRY_PAUSES[attempt])
    raise publish.PublishError(f"{last} (after {len(RETRY_PAUSES) + 1} attempts)")


def retract_local(conn, signal_id: str, reason: str) -> int:
    cur = conn.execute(
        "UPDATE signals SET is_current = 0, notes = ? WHERE signal_id = ? AND is_current = 1",
        (f"retracted: {reason}", signal_id),
    )
    conn.commit()
    return cur.rowcount


def known_locally(conn, signal_id: str) -> bool:
    """Has this database EVER held this signal_id, current or not?"""
    return conn.execute(
        "SELECT 1 FROM signals WHERE signal_id = ? LIMIT 1", (signal_id,)
    ).fetchone() is not None


@dataclass
class Result:
    signal_id: str
    outcome: str        # withdrawn | already withdrawn | not attempted | failed
    detail: str
    label: str = ""

    @property
    def failed(self) -> bool:
        return self.outcome in ("failed", "not attempted")


def retract_one(conn, signal_id: str, reason: str, label: str = "") -> Result:
    """Withdraw one row, and say honestly which of four things happened.

    IDEMPOTENCE. tit_api_retract() (wordpress-plugin/.../includes/api.php)
    scopes its UPDATE `WHERE signal_id = %s AND is_current = 1` and answers
    HTTP 200 with `{"retracted": 0}` when that matches nothing. So the host
    replies IDENTICALLY to "you already withdrew this" and to "there is no such
    row" — that is read off the route's source, not guessed, and not probed.
    The local database is what tells them apart, because it knows every
    signal_id it has ever stored:

      - anything actually flipped, here or there  -> withdrawn
      - nothing flipped, but we hold the id       -> already withdrawn (fine:
        a re-run of a partly-applied list must not fail on the rows that landed)
      - nothing flipped and we have never held it -> FAILED. A typo in a
        correction list must not be reported green, or a row stays live under a
        page that promises it is not there.
    """
    label = label or signal_id
    try:
        result = retract_remote(signal_id, reason)
    except (publish.PublishError, requests.RequestException) as exc:
        return Result(signal_id, "failed", str(exc)[:300], label)

    remote_n = int(result.get("retracted") or 0)
    local_n = retract_local(conn, signal_id, reason)
    if remote_n or local_n:
        return Result(signal_id, "withdrawn",
                      f"wordpress={remote_n} local={local_n}", label)
    if known_locally(conn, signal_id):
        return Result(signal_id, "already withdrawn",
                      "no current revision here or on the site", label)
    return Result(signal_id, "failed",
                  "unknown signal_id: nothing withdrawn here or on the site, "
                  "and this database has never held it", label)


def retract_many(conn, signal_ids, reason: str, *, labels=None,
                 budget: int | None = None) -> tuple[int, list[Result]]:
    """Withdraw a list. One reason, one row at a time, nothing swallowed.

    Returns (failure count, per-row results). Every id is accounted for in the
    results whether it was attempted or not.
    """
    labels = labels or {}
    budget = RUN_BUDGET_SECONDS if budget is None else budget
    started = time.monotonic()
    results: list[Result] = []
    total = len(signal_ids)

    for index, signal_id in enumerate(signal_ids, start=1):
        label = labels.get(signal_id, signal_id)
        if time.monotonic() - started > budget:
            results.append(Result(
                signal_id, "not attempted",
                f"run budget of {budget}s exhausted before row {index} of {total}",
                label))
            continue
        result = retract_one(conn, signal_id, reason, label)
        results.append(result)
        stream = sys.stderr if result.failed else sys.stdout
        print(f"[{index}/{total}] {result.outcome}: {label} — {result.detail}",
              file=stream)

    return sum(1 for r in results if r.failed), results


def report(results: list[Result], reason: str) -> None:
    """Say what happened, then hand back a paste-ready retry.

    Twenty-seven lines of per-row output is legible only if the human does not
    then have to grep them to build the second attempt. The re-run line is the
    exact argument the failures need, and nothing else.
    """
    counts: dict[str, int] = {}
    for r in results:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    summary = ", ".join(f"{n} {outcome}" for outcome, n in sorted(counts.items()))
    print(f"\n{len(results)} row(s): {summary}")

    failed = [r for r in results if r.failed]
    if not failed:
        return
    for r in failed:
        print(f"  FAILED {r.label}: {r.detail}", file=sys.stderr)
    ids = ",".join(r.signal_id for r in failed)
    print("\nRe-run just the failures:")
    print(f"  python retract.py {shlex.quote(ids)} {shlex.quote(reason)}")
    print("  (or dispatch retract.yml with signal_id set to)")
    print(f"  {ids}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Withdraw published records.")
    parser.add_argument("signal_id", nargs="?",
                        help="one signal_id, or a comma-separated list")
    parser.add_argument("reason", nargs="?")
    parser.add_argument("--ids-file", metavar="PATH",
                        help="file of signal_ids, one per line (# comments ok)")
    parser.add_argument("--bare-domains", metavar="REASON",
                        help="retract every row whose source link is a homepage")
    args = parser.parse_args(argv)

    conn = schema.connect()
    labels: dict[str, str] = {}

    try:
        if args.bare_domains:
            if args.signal_id or args.ids_file:
                parser.error("--bare-domains chooses the rows itself")
            rows = find_bare_domain_rows(conn)
            if not rows:
                print("No bare-domain rows. Nothing to retract.")
                return 0
            ids = [r["signal_id"] for r in rows]
            labels = {r["signal_id"]: f"{r['company']} ({r['source_url']})"
                      for r in rows}
            reason = args.bare_domains
        elif args.ids_file:
            # The reason is the only positional left when --ids-file carries
            # the ids, so accept it in either slot rather than making the
            # caller remember which — but refuse BOTH, because then one of the
            # two is an id list the file has already overruled and silently
            # dropping it is how a correction goes half-applied.
            if args.signal_id and args.reason:
                parser.error("--ids-file carries the ids; give only a reason")
            reason = args.reason or args.signal_id
            if not reason:
                parser.error("--ids-file still needs a reason")
            ids = read_ids_file(args.ids_file)
        elif args.signal_id and args.reason:
            ids = parse_ids(args.signal_id)
            reason = args.reason
        else:
            parser.error("give signal_id(s) and a reason, or --ids-file, "
                         "or --bare-domains REASON")
    except ValueError as exc:
        # A malformed list is refused BEFORE anything is withdrawn. Half a
        # correction applied off a list we could not read is the worst way to
        # learn the list was wrong.
        print(f"FAILED to read the list: {exc}", file=sys.stderr)
        return 2

    print(f"Withdrawing {len(ids)} row(s), one reason: {reason}")
    failures, results = retract_many(conn, ids, reason, labels=labels)
    report(results, reason)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
