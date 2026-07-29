#!/usr/bin/env python3
"""Measure what this tracker MISSES, against a sealed independent gold set.

    python3 measure_recall.py                 # measure against the live API
    python3 measure_recall.py --publish       # ...and write the public page's data
    python3 measure_recall.py --offline FILE  # replay stored API rows, no network
    python3 measure_recall.py --check         # validate the gold set only

Each run appends a dated result to analysis/recall/results/, which is the
published trend. It also writes data/recall_worklist.json: the countries that
held nothing, the source types under-delivering, and whether a fresh gold set is
due. That file is the point of automating this. A measurement that only produces
a number is a report; one that produces a work list is a loop.

Why this exists: a coverage claim nobody has tested is a marketing line. The
only honest version of "how complete is this?" is a number produced against a
reference set that was assembled from public sources before any matching ran,
broken out by the cells where the answer differs, and dated, because a recall
figure with no measurement date is worthless the moment coverage changes.

The output is deliberately shaped as recall PER CELL. A single blended
percentage hides the only interesting fact, which is that the same tracker can
be near-complete on mandatory filings and weak on categories where no filing
obligation exists anywhere in the world.

Read-only against the live API. It never writes a signal and never spends a
cent on the model.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from analysis.recall import goldset, match, series  # noqa: E402

API = os.environ.get(
    "TIT_API_BASE", "https://asktherecruiter.com/blog/wp-json/talent/v1"
).rstrip("/")

# ModSecurity on the WP host rejects the default urllib and requests agents
# outright. Every request to that host must look like a browser.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(HERE, "analysis", "recall", "results")
PLUGIN_DATA = os.path.join(
    HERE, "wordpress-plugin", "talent-intelligence-tracker", "data", "recall.json")
# Stable path, committed, so the health machinery and any future session can
# read the measurement's own to-do list without knowing this script exists.
WORKLIST_PATH = os.path.join(HERE, "data", "recall_worklist.json")


def api_query(params: dict, attempts: int = 3):
    """One /query call. Retries transient 5xx: this is shared hosting and a
    random 500 under load must not be recorded as a miss."""
    url = f"{API}/query?" + urllib.parse.urlencode(params)
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read().decode("utf-8")).get("rows", [])
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == attempts - 1:
                raise
        except Exception:
            if attempt == attempts - 1:
                raise
        time.sleep(2 * (attempt + 1))
    return []


def rows_for(item: dict) -> list:
    """Everything we hold that could be this employer.

    Two passes on purpose. The exact normalised name is the precise query; the
    first distinctive word is the wide one, so a gold 'Glow' still reaches a
    stored 'Glow Security'. Querying wide and filtering in `match` keeps the
    decision rule in one reviewable place.
    """
    seen, out = set(), []
    for term in (match.company_key(item["company"]), match.first_token(item["company"])):
        if not term:
            continue
        for row in api_query({"company": term, "per_page": 200}):
            key = row.get("signal_id")
            if key not in seen:
                seen.add(key)
                out.append(row)
        if out and term == match.company_key(item["company"]):
            # The precise query already found the employer; the wide one would
            # only add unrelated companies sharing a first word.
            break
    return out


def measure(data: dict, offline_rows: dict | None = None, verbose: bool = True) -> dict:
    results = []
    for index, item in enumerate(data["items"], 1):
        if offline_rows is not None:
            rows = offline_rows.get(item["id"], [])
        else:
            try:
                rows = rows_for(item)
            except Exception as exc:
                print(f"  ! {item['company']}: API error, {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                raise SystemExit(
                    "aborting: an unreachable API would be recorded as a total miss, "
                    "and a wrong recall number is worse than no recall number") from exc
            time.sleep(0.2)

        verdict = match.classify(item, rows)
        verdict["gold"] = item
        verdict["candidates_seen"] = len(rows)
        results.append(verdict)

        if verbose:
            mark = {"FOUND": "ok  ", "FOUND_PARTIAL": "part", "MISSED": "MISS"}[verdict["verdict"]]
            defects = (" [" + ", ".join(verdict["defects"]) + "]") if verdict["defects"] else ""
            print(f"  {index:>3}/{len(data['items'])} {mark} "
                  f"{item['company']} ({item['country']}, {item['signal_type']}){defects}")

    return {
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "measured_on": date.today().isoformat(),
        "api_base": API,
        "goldset": {
            "version": data.get("version"),
            "digest": data.get("_digest"),
            "window": data.get("window"),
            "assembled_on": data.get("assembled_on"),
            "url": data.get("public_url"),
            # Known weaknesses of the reference set itself, carried through to
            # the page. A benchmark that hides its own caveats is a brochure.
            "caveats": data.get("caveats", []),
            "counts": goldset.counts(data),
        },
        "summary": match.summarise(results),
        "items": [
            {
                "id": r["gold"]["id"],
                "company": r["gold"]["company"],
                "country": r["gold"]["country"],
                "signal_type": r["gold"]["signal_type"],
                "size_band": r["gold"]["size_band"],
                "source_type": r["gold"]["source_type"],
                "source_name": r["gold"]["source_name"],
                "source_url": r["gold"]["source_url"],
                "event_date": r["gold"]["event_date"],
                "detail": r["gold"]["detail"],
                "verdict": r["verdict"],
                "defects": r["defects"],
                "matched": r["matched_row"],
            }
            for r in results
        ],
    }


def _line(label, cell):
    return (f"  {label:<26} held {cell['held']:>3}/{cell['total']:<3} "
            f"({cell['held_pct'] if cell['held_pct'] is not None else 'n/a'}%)   "
            f"clean {cell['found']:>3}/{cell['total']:<3} "
            f"({cell['clean_pct'] if cell['clean_pct'] is not None else 'n/a'}%)")


def report(out: dict) -> None:
    summary = out["summary"]
    print("\n" + "=" * 72)
    print(f"RECALL, measured {out['measured_on']} against gold set "
          f"{out['goldset']['version']} ({out['goldset']['digest']})")
    print(f"window {out['goldset']['window']['start']} to {out['goldset']['window']['end']}")
    print("=" * 72)
    print("\n'held' = the event is in the tracker at all.")
    print("'clean' = it is there with country, amount, date and source all right.\n")
    print(_line("OVERALL", summary["overall"]))
    for group in ("by_segment", "by_signal_type", "by_geography", "by_source_type",
                  "by_size_band", "by_country"):
        print(f"\n{group.replace('by_', 'by ').replace('_', ' ')}:")
        for key, cell in summary[group].items():
            print(_line(key, cell))
    if summary["defects"]:
        print("\nfield defects on events we DO hold:")
        for name, count in summary["defects"].items():
            print(f"  {name:<26} {count}")
    missed = [i for i in out["items"] if i["verdict"] == "MISSED"]
    if missed:
        print(f"\nMISSED ({len(missed)}), which is the discovery roadmap:")
        for item in missed:
            print(f"  - {item['company']} ({item['country']}, {item['signal_type']}, "
                  f"{item['source_type']}) {item['event_date']}")


def publish(out: dict, points: list) -> str:
    """Write the page's data file: the latest measurement plus the whole trend.

    The page renders this and nothing else, so what a reader sees is exactly
    what was measured. The series travels with it because a lone percentage is
    a verdict and a percentage with a history is a system.
    """
    payload = dict(out)
    payload["series"] = points
    os.makedirs(os.path.dirname(PLUGIN_DATA), exist_ok=True)
    with open(PLUGIN_DATA, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=False)
        handle.write("\n")
    return PLUGIN_DATA


def write_worklist(worklist: dict) -> str:
    """The measurement's own to-do list, at a path other tooling can rely on.

    This is what makes the loop a loop. A country scoring zero is not a fact to
    display, it is an instruction to go and find a route into that country's
    press, and it belongs somewhere the health machinery can read rather than
    only in a report somebody has to remember to open.
    """
    os.makedirs(os.path.dirname(WORKLIST_PATH), exist_ok=True)
    with open(WORKLIST_PATH, "w", encoding="utf-8") as handle:
        json.dump(worklist, handle, indent=1)
        handle.write("\n")
    return WORKLIST_PATH


def report_health(out: dict, worklist: dict) -> str:
    """File the run in the same ledger every collector reports to.

    `degraded` when a fresh gold set is due, because a measurement running
    happily against a converged set is the failure mode this whole design is
    guarding against, and it should look wrong in the health page rather than
    green.
    """
    try:
        from pipeline import schema, store
    except Exception as exc:                      # pragma: no cover - import guard
        return f"skipped ({type(exc).__name__}: {exc})"

    overall = out["summary"]["overall"]
    due = worklist["next_goldset"]
    detail = (f"held {overall['held']}/{overall['total']} ({overall['held_pct']}%), "
              f"clean {overall['found']}/{overall['total']} ({overall['clean_pct']}%) "
              f"against {out['goldset']['version']}")
    if due["due"]:
        detail += f" | new gold set due: {due['reason']}"

    conn = schema.connect()
    store.report_health(
        conn, "recall",
        status="degraded" if due["due"] else "ok",
        items_found=overall["total"],
        items_stored=overall["held"],
        detail=detail,
    )
    conn.commit()
    return "recorded"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goldset", default=goldset.DEFAULT_PATH)
    parser.add_argument("--offline", help="JSON file of {gold_id: [rows]}, no network")
    parser.add_argument("--publish", action="store_true",
                        help="write the public page's data file too")
    parser.add_argument("--check", action="store_true",
                        help="validate the gold set and stop")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--no-health", action="store_true",
                        help="skip the source_health entry (keeps the database untouched)")
    parser.add_argument("--results-dir", default=None,
                        help="where dated results are written (default analysis/recall/results)")
    args = parser.parse_args()

    data = goldset.load(args.goldset)
    problems = goldset.validate(data)
    if problems:
        print("gold set is not valid:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    shape = goldset.counts(data)
    print(f"gold set {data['version']} ({data['_digest']}): {shape['total']} events, "
          f"{shape['geography'].get('US', 0)} US / "
          f"{shape['geography'].get('non-US', 0)} non-US, "
          f"{shape['signal_type'].get('funding', 0)} funding / "
          f"{shape['signal_type'].get('leadership', 0)} leadership, "
          f"{len(shape['country'])} countries")
    if args.check:
        print("gold set is valid.")
        return 0

    offline_rows = None
    results_dir = args.results_dir or RESULTS_DIR
    if args.offline:
        with open(args.offline, encoding="utf-8") as handle:
            offline_rows = json.load(handle)
        print(f"offline replay from {args.offline}")
        if not args.results_dir:
            # A replay is a plumbing check, not a measurement. Letting it land
            # in the real results directory would put a fabricated point in the
            # published trend, under today's date, silently.
            results_dir = tempfile.mkdtemp(prefix="recall-replay-")
            print(f"replay results go to {results_dir}, not the published series")
    else:
        print(f"measuring against {API}")

    out = measure(data, offline_rows=offline_rows, verbose=not args.quiet)
    report(out)

    os.makedirs(results_dir, exist_ok=True)
    stamp = out["measured_on"]
    path = os.path.join(results_dir, f"recall-{stamp}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1)
        handle.write("\n")
    print(f"\nwrote {os.path.relpath(path, HERE)}")

    # The series is loaded AFTER this run's file lands, so the trend always
    # includes the measurement that just produced it.
    points = series.load_series(results_dir)
    worklist = series.build_worklist(out, points, data)
    # A replay's work list would name the whole world as a gap. Only a real
    # measurement gets to overwrite the file other tooling reads.
    if results_dir == RESULTS_DIR:
        print(f"wrote {os.path.relpath(write_worklist(worklist), HERE)}")
    else:
        print("work list not written: this was a replay, not a measurement")

    if len(points) > 1:
        print("\ntrend, oldest first:")
        for point in points:
            cell = point["overall"] or {}
            print(f"  {point['measured_on']}  held {cell.get('held')}/{cell.get('total')} "
                  f"({cell.get('held_pct')}%)  set {point['goldset_version']}")

    due = worklist["next_goldset"]
    if due["due"]:
        print(f"\nA NEW GOLD SET IS DUE: {due['reason']}")
        print(f"  suggested window: {due['suggested_window']['start']} to "
              f"{due['suggested_window']['end']}")

    zeros = worklist["zero_countries"]
    if zeros:
        print(f"\nwork list: {len(zeros)} countries held nothing "
              f"({', '.join(c['key'] for c in zeros[:15])}"
              f"{'...' if len(zeros) > 15 else ''})")

    if not args.no_health:
        print(f"health: {report_health(out, worklist)}")

    if args.publish:
        print(f"wrote {os.path.relpath(publish(out, points), HERE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
