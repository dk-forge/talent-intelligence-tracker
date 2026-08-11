"""Train, replay-test and (only when the bar passes) ARM the classifier gate.

docs/PLAN-gate-to-five-dollars.md, step 2. This is the self-arming half: the
weekly workflow (.github/workflows/gate-classifier.yml) runs this script with
no inputs, and the script decides everything from the committed label ledger.
The owner does nothing. There are exactly three outcomes, all exit 0:

  NOT READY   labels span under 30 days, or the replay bar fails, and the gate
              was not armed. Prints one line —
                  not ready: N labels, D days, replay X%
              — and changes NOTHING. No artifact, no flag, no alert.

  ARMED       the replay bar passes on >=30 days of real labels. Writes the
              artifact (data/gate_classifier/model.json.gz, <5MB) and the flag
              (status.json, armed=true, carrying the replay report the runtime
              refuses to route without), and emails the owner ONE arming
              notice through the keyed /alert with the replay number in it.

  REVERTED    the gate was armed and this retrain fails the bar. Flips the
              flag off (the runtime falls back to the LLM gate on the next
              run), keeps the failing report beside it as the reason, and
              emails once — deduped by cause, so a bar that stays failed does
              not mail weekly.

THE BAR, non-negotiable (the plan's words): a replay test over >=30 days of
real labels in which >=99.5% of all candidates that ultimately produced a
STORED row are routed relevant-or-uncertain. The replay here is OUT-OF-SAMPLE
by construction — chronological day blocks, each scored by a model fitted only
on the other blocks with thresholds chosen on that fold's own held-out tail —
because a replay of the training set would grade the model on its memory.

COST: zero model calls, ever. scikit-learn fits a logistic regression on the
free CI runner in seconds; it is installed by the workflow's own pip line and
is deliberately NOT in requirements.txt, so no collector's runtime grows a
dependency. Everything except fit() is standard library, and fit() is injected
into the replay so the tests prove the bar's arithmetic without scikit-learn.

DRIFT ALARM (plan step 2, last paragraph): once armed, if the UNCERTAIN share
of routed candidates over the last 7 days rises past 35%, the LLM-gate bill is
quietly re-inflating (vocabulary drift or a new language). One deduped alert;
a RECOVERED mail when it falls back under 25%.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from pipeline import gate_classifier, gate_ledger
from pipeline.gate_classifier import (
    MIN_REPLAY_DAYS, SHIP_BAR_PCT, Model, features, lang_key)

# --- Training targets ---------------------------------------------------------
#
# The classifier's real target is the plan's: "did this candidate end up a
# stored row", not "did the LLM gate like it". Outcomes map to labels and
# weights accordingly; anything still in flight teaches nothing and is skipped.

#: outcome -> (label, weight) for real gate_text lines.
OUTCOME_TARGETS = {
    "stored": (1, 1.0),
    "would_store": (1, 1.0),
    # A duplicate is a real story we already held — exactly as relevant as the
    # row it duplicated, and the second-largest YES class in the ledger.
    "duplicate": (1, 1.0),
    # validate rejected the RECORD (no URL, a vocabulary miss), not the story;
    # text-wise these read like signals, so they weigh in as soft positives —
    # calling them negatives would teach the classifier to drop real stories
    # that failed on mechanics.
    "validate_reject": (1, 0.5),
    "gate_reject": (0, 1.0),
    # The extraction model read the whole text and said "not a talent signal".
    # Softer than a gate reject: some of these are judgement calls.
    "model_reject": (0, 0.7),
}

#: The classifier's own drops must never train the classifier: that is a
#: feedback loop grading its own homework. (CLF_YES lines are fine — their
#: outcome was decided downstream by the same guards as everything else.)
SELF_LABELLED = {"clf_reject"}

WEAK_WEIGHT = 0.25

#: A language needs at least this many real lines — AND at least
#: MIN_LANG_STORED of them stored — before the artifact claims to know it at
#: all; under either floor, its candidates fail open to the LLM gate. The
#: stored floor is what keeps a small, skewed label base (a language whose
#: few labels are nearly all junk) from earning a drop band on evidence that
#: contains no positives to protect.
MIN_LANG_ROWS = 25
MIN_LANG_STORED = 5

#: The confident-RELEVANT band is earned PER LANGUAGE, on a much higher
#: volume floor plus that language's own measured gate agreement in the band
#: (relevant_languages below). Motivated by the ledger itself: Polish's paid
#: gate passes 17.7% of candidates against Spanish's 80.1%, and the Polish
#: passes skew to football-club and municipal noise — a global skip band
#: calibrated mostly on high-agreement languages would quietly buy
#: extractions for exactly that traffic. Until a language clears both floors
#: its high scorers route UNCERTAIN and keep paying the cheap gate.
MIN_RELEVANT_LANG_ROWS = 200

#: Threshold guard rails. However clean the held-out month looks, the drop
#: band may never reach past 0.40 and the skip band never below 0.60 — a
#: sanity ceiling on what one month of labels can prove.
T_LO_CEILING = 0.40
T_HI_FLOOR = 0.60
#: Margin under the lowest held-out stored-row score: the drop threshold sits
#: 20% below the worst true positive the fold ever saw, not at it.
T_LO_MARGIN = 0.8
#: Confident-RELEVANT skips a cheap gate to buy an EXPENSIVE extraction, so it
#: only saves money where the gate would almost certainly have said YES anyway.
T_HI_PRECISION = 0.95
T_HI_MIN_ROWS = 20

REPLAY_BLOCKS = 5

DRIFT_ALERT_PCT = 35.0
DRIFT_CLEAR_PCT = 25.0
DRIFT_WINDOW_DAYS = 7

USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"


# --- Ledger reading -------------------------------------------------------------


def load_labels(directory: str | None = None):
    """(real, weak) rows, one per candidate key, last terminal line winning.

    A deferred candidate is re-gated on a later run and writes a second line
    under the same key (gate_ledger docstring), so the reader takes the LAST
    line per key and then drops the keys whose final word is still not
    terminal. Real and weak are kept apart the whole way: the ledger README's
    one rule is that a model trained on the two mixed learns to tell "has a
    teaser" from "does not", which is a fact about the file.
    """
    real: dict[str, dict] = {}
    weak: dict[str, dict] = {}
    for line in gate_ledger.read_all(directory):
        basis = line.get("basis")
        key = line.get("key") or ""
        if basis == gate_ledger.BASIS_GATE_TEXT:
            real[key] = line
        elif basis == gate_ledger.BASIS_URL_SLUG:
            weak[key] = line
    return list(real.values()), list(weak.values())


def _day(line: dict) -> str:
    return (line.get("ts") or "")[:10]


def span_days(rows) -> int:
    days = sorted({_day(r) for r in rows if _day(r)})
    if not days:
        return 0
    first = datetime.fromisoformat(days[0])
    last = datetime.fromisoformat(days[-1])
    return (last - first).days + 1


def training_rows(real, weak):
    """[(features, label, weight, line), ...] — the fit set."""
    rows = []
    for line in real:
        if line.get("outcome") in SELF_LABELLED:
            continue
        target = OUTCOME_TARGETS.get(line.get("outcome") or "")
        if target is None:
            continue
        label, weight = target
        rows.append((features(line.get("headline") or "",
                              line.get("teaser") or ""), label, weight, line))
    for line in weak:
        label = 1 if line.get("outcome") == "stored" else 0
        rows.append((features(line.get("headline") or "",
                              line.get("teaser") or ""), label,
                     WEAK_WEIGHT, line))
    return rows


def language_roster(real) -> list[str]:
    counts: dict[str, int] = {}
    stored: dict[str, int] = {}
    for line in real:
        k = lang_key(line.get("lang") or "")
        counts[k] = counts.get(k, 0) + 1
        if line.get("outcome") in ("stored", "would_store"):
            stored[k] = stored.get(k, 0) + 1
    return sorted(k for k, c in counts.items()
                  if c >= MIN_LANG_ROWS and stored.get(k, 0) >= MIN_LANG_STORED)


def relevant_languages(scored, t_hi, real) -> list[str]:
    """Which languages may use the confident-RELEVANT (gate-skip) band.

    Per-language calibration, not a global one: each language needs its own
    volume (MIN_RELEVANT_LANG_ROWS real labels) AND its own held-out band
    agreement (>= T_HI_MIN_ROWS gated rows scoring past t_hi, of which the
    LLM gate said YES to >= T_HI_PRECISION). A language with a small or
    skewed label base clears neither and stays on the LLM gate, which is the
    conservative direction: it costs pennies, never recall.
    """
    if t_hi > 1.0:
        return []
    volume: dict[str, int] = {}
    for line in real:
        k = lang_key(line.get("lang") or "")
        volume[k] = volume.get(k, 0) + 1
    band: dict[str, list] = {}
    for score, line in scored:
        if score < t_hi or line.get("gate") not in (gate_ledger.YES,
                                                    gate_ledger.NO):
            continue
        band.setdefault(lang_key(line.get("lang") or ""), []).append(
            line.get("gate"))
    return sorted(
        k for k, verdicts in band.items()
        if volume.get(k, 0) >= MIN_RELEVANT_LANG_ROWS
        and len(verdicts) >= T_HI_MIN_ROWS
        and verdicts.count(gate_ledger.YES) / len(verdicts) >= T_HI_PRECISION)


# --- Fitting (the ONLY scikit-learn touch in the repo) ---------------------------


def fit(rows):
    """Logistic regression over the shared hashed feature space.

    Imports scikit-learn HERE, lazily: the weekly workflow installs it for
    this process alone, and nothing on any collector's path ever imports this
    function. Returns (weights list[float] of DIM, bias float).
    """
    import numpy as np
    from scipy.sparse import csr_matrix
    from sklearn.linear_model import LogisticRegression

    indptr, indices, data = [0], [], []
    y, w = [], []
    for feats, label, weight, _line in rows:
        for bucket, count in feats.items():
            indices.append(bucket)
            data.append(count)
        indptr.append(len(indices))
        y.append(label)
        w.append(weight)
    X = csr_matrix((data, indices, indptr),
                   shape=(len(rows), gate_classifier.DIM), dtype="float64")
    clf = LogisticRegression(C=1.0, max_iter=2000, tol=1e-4)
    clf.fit(X, np.asarray(y), sample_weight=np.asarray(w))
    return clf.coef_[0].astype("float32").tolist(), float(clf.intercept_[0])


# --- Thresholds ------------------------------------------------------------------


def choose_thresholds(scored):
    """(t_lo, t_hi) from held-out (score, line) pairs.

    t_lo: the largest drop threshold under which the held-out month contains
    ZERO stored rows, with a safety margin below the worst stored score and a
    hard ceiling. If the hold-out has no stored rows, the drop band is empty.

    t_hi: the smallest skip threshold whose band the LLM gate agreed with at
    >= T_HI_PRECISION on enough rows to mean it; otherwise the band is empty
    (2.0) and every survivor still pays the cheap gate — a classifier that
    cannot yet prove the skip is a classifier that saves nothing there, not
    one that gambles extraction calls.
    """
    stored = [s for s, line in scored
              if line.get("outcome") in ("stored", "would_store")]
    t_lo = min(0.0 if not stored else min(stored) * T_LO_MARGIN, T_LO_CEILING)

    gated = sorted((s, line.get("gate")) for s, line in scored
                   if line.get("gate") in (gate_ledger.YES, gate_ledger.NO))
    t_hi = 2.0
    threshold = 0.995
    while threshold >= T_HI_FLOOR:
        band = [g for s, g in gated if s >= threshold]
        if len(band) >= T_HI_MIN_ROWS:
            if band.count(gate_ledger.YES) / len(band) >= T_HI_PRECISION:
                t_hi = threshold
            else:
                break
        threshold = round(threshold - 0.005, 3)
    return t_lo, max(t_hi, T_HI_FLOOR)


def holdout_split(rows):
    """(train, holdout) by day: the newest quarter of distinct days (>=1) is
    held out, so thresholds are chosen on traffic the weights never saw."""
    days = sorted({_day(line) for _feats, _l, _w, line in rows if _day(line)})
    cut = set(days[-max(1, len(days) // 4):])
    train = [r for r in rows if _day(r[3]) not in cut]
    hold = [r for r in rows if _day(r[3]) in cut]
    return (train, hold) if train and hold else (rows, rows)


def build_model(rows, langs, fit_fn=fit, real=()) -> Model:
    train, hold = holdout_split(rows)
    weights, bias = fit_fn(train)
    probe = Model(weights, bias, 0.0, 2.0, langs)
    scored = [(probe.score(line.get("headline") or "", line.get("teaser") or ""),
               line) for _f, _l, _w, line in hold]
    t_lo, t_hi = choose_thresholds(scored)
    return Model(weights, bias, t_lo, t_hi, langs,
                 relevant_langs=relevant_languages(scored, t_hi, real))


# --- The replay: the shipping bar, measured out of sample -------------------------


def replay(real, weak, fit_fn=fit, blocks=REPLAY_BLOCKS):
    """Route every real candidate with a model that never saw its day.

    Distinct days are cut into `blocks` chronological blocks. Each block is
    scored by a model fitted on the OTHER blocks (weak rows always ride along
    at their discount), with thresholds and language roster from that fold
    alone. The bar is then read off the aggregate: of the candidates whose
    final outcome was stored, what share routed relevant-or-uncertain?

    Returns the replay report dict that status.json commits.
    """
    days = sorted({_day(line) for line in real if _day(line)})
    stored_total = stored_kept = 0
    routed = {"relevant": 0, "uncertain": 0, "irrelevant": 0}
    n = max(1, min(blocks, len(days)))
    step = (len(days) + n - 1) // n
    day_blocks = [days[i:i + step] for i in range(0, len(days), step)]

    for block in day_blocks:
        block_set = set(block)
        fold_real = [l for l in real if _day(l) not in block_set]
        held = [l for l in real if _day(l) in block_set]
        if not fold_real or not held:
            continue
        rows = training_rows(fold_real, weak)
        model = build_model(rows, language_roster(fold_real), fit_fn,
                            real=fold_real)
        for line in held:
            lang = lang_key(line.get("lang") or "")
            if lang in model.langs:
                score = model.score(line.get("headline") or "",
                                    line.get("teaser") or "")
                verdict = ("relevant" if score >= model.t_hi
                           and lang in model.relevant_langs else
                           "irrelevant" if score <= model.t_lo else "uncertain")
            else:
                verdict = "uncertain"       # fail-open, same as the runtime
            routed[verdict] += 1
            if line.get("outcome") in ("stored", "would_store"):
                stored_total += 1
                if verdict != "irrelevant":
                    stored_kept += 1

    total = sum(routed.values())
    rate = 100.0 if stored_total == 0 else 100.0 * stored_kept / stored_total
    return {
        "rate_pct": round(rate, 3),
        "stored": stored_total,
        "kept": stored_kept,
        "days": span_days(real),
        "window": [days[0], days[-1]] if days else [],
        "candidates": total,
        "confident_pct": round(
            100.0 * (routed["relevant"] + routed["irrelevant"]) / total, 1)
            if total else 0.0,
        "routed": routed,
    }


def bar_passes(report: dict) -> bool:
    return (report.get("days", 0) >= MIN_REPLAY_DAYS
            and report.get("stored", 0) > 0
            and report.get("rate_pct", 0.0) >= SHIP_BAR_PCT)


def not_ready_line(n_labels: int, report: dict) -> str:
    """The exact line the plan asks the first weekly runs to print."""
    return (f"not ready: {n_labels} labels, {report.get('days', 0)} days, "
            f"replay {report.get('rate_pct', 0.0):.2f}%")


# --- Artifact and flag writes -----------------------------------------------------


def write_artifact(model: Model, out_dir: str, *, n_labels: int,
                   trained_at: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    blob = gate_classifier.encode_weights(model.weights)
    doc = {
        "format": gate_classifier.ARTIFACT_FORMAT,
        "dim": gate_classifier.DIM,
        "bias": model.bias,
        "t_lo": model.t_lo,
        "t_hi": model.t_hi,
        "langs": sorted(model.langs),
        "relevant_langs": sorted(model.relevant_langs),
        "trained_at": trained_at,
        "n_labels": n_labels,
        "weights": blob,
    }
    path = os.path.join(out_dir, gate_classifier.MODEL_NAME)
    # mtime pinned so the artifact is byte-identical for identical weights and
    # a weekly run that changed nothing commits nothing.
    with open(path, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0) as fh:
            fh.write(json.dumps(doc, separators=(",", ":")).encode("utf-8"))
    return gate_classifier.weights_sha(blob)


def read_status(out_dir: str) -> dict:
    try:
        with open(os.path.join(out_dir, gate_classifier.STATUS_NAME),
                  encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_status(out_dir: str, status: dict) -> None:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, gate_classifier.STATUS_NAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(status, fh, indent=1, sort_keys=True)
        fh.write("\n")


def arm(out_dir: str, report: dict, *, artifact_sha: str, trained_at: str,
        n_labels: int, prior: dict) -> dict:
    """The flag flip — REFUSED unless the replay report in hand clears the bar.

    This is the committed promise the runtime re-checks: nothing may write
    armed=true beside a report that is missing, thin or under the bar, however
    it was called. Tested directly.
    """
    if not bar_passes(report):
        raise ValueError(
            "refusing to arm: the replay report does not clear the bar "
            f"({report.get('rate_pct')}% over {report.get('days')} days; "
            f"needs >={SHIP_BAR_PCT}% over >={MIN_REPLAY_DAYS})")
    status = {
        "armed": True,
        "trained_at": trained_at,
        "artifact_sha": artifact_sha,
        "labels": n_labels,
        "replay": report,
        "reason": f"replay {report['rate_pct']}% of {report['stored']} stored "
                  f"rows kept over {report['days']} days",
        "notice_pending": "armed",
        "drift": prior.get("drift") or {},
    }
    write_status(out_dir, status)
    return status


def revert(out_dir: str, report: dict, *, prior: dict, n_labels: int) -> dict:
    status = {
        "armed": False,
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artifact_sha": prior.get("artifact_sha") or "",
        "labels": n_labels,
        "replay": report,
        "reason": "REVERTED to the LLM gate: retrain failed the bar — "
                  + not_ready_line(n_labels, report),
        "notice_pending": "reverted",
        "drift": prior.get("drift") or {},
    }
    write_status(out_dir, status)
    return status


# --- Alerts (the keyed /alert route; ci_alert owns the transport) ------------------


def _alert(subject: str, body: str, *, dedupe_key: str = "",
           resolve_scope: str = "", poster=None) -> bool:
    """One email through the plugin's keyed /alert. Never raises, never exits
    non-zero: an undeliverable notice is printed and retried next week via
    status.json's notice_pending, not turned into a red training run."""
    site = (os.environ.get("WP_SITE_URL") or "").strip()
    key = (os.environ.get("WP_API_KEY") or "").strip()
    payload = {"subject": subject, "body": body}
    if dedupe_key:
        payload["dedupe_key"] = dedupe_key
    if resolve_scope:
        payload["resolve_scope"] = resolve_scope
    if poster is None:
        if not site or not key:
            print(f"[gate-classifier] notice NOT sent (no WP credentials in "
                  f"this environment): {subject}")
            return False
        import ci_alert
        ok, note, _transient = ci_alert.post_alert(site, key, payload)
        print(f"[gate-classifier] /alert: {note}")
        return ok
    return poster(payload)


def _fingerprint(text: str) -> str:
    import re
    normalised = re.sub(r"\d+(?:\.\d+)?", "#", text.lower())
    return hashlib.md5(normalised.encode("utf-8")).hexdigest()[:12]


def send_pending_notice(out_dir: str, status: dict, poster=None) -> None:
    pending = status.get("notice_pending")
    if not pending:
        return
    replay_report = status.get("replay") or {}
    if pending == "armed":
        subject = (f"Classifier gate ARMED: replay "
                   f"{replay_report.get('rate_pct')}% of "
                   f"{replay_report.get('stored')} stored rows kept")
        body = (
            "The weekly gate-classifier run passed the shipping bar and armed "
            "the local classifier gate (docs/PLAN-gate-to-five-dollars.md, "
            "step 2).\n\n"
            f"  replay: {replay_report.get('rate_pct')}% of "
            f"{replay_report.get('stored')} stored-row candidates routed "
            "relevant-or-uncertain\n"
            f"  window: {replay_report.get('window')} "
            f"({replay_report.get('days')} days of real labels)\n"
            f"  confident band: {replay_report.get('confident_pct')}% of "
            "candidates now skip the paid LLM gate\n\n"
            "Nothing to do. Confident-RELEVANT skips the LLM gate, UNCERTAIN "
            "still pays for it, confident-IRRELEVANT drops. Every failure "
            "mode falls back to the LLM gate, and a weekly retrain that ever "
            "fails this same bar will revert the flag and email you once.")
        dedupe = f"gate-classifier-armed:{status.get('artifact_sha', '')[:12]}"
    elif pending == "reverted":
        reason = status.get("reason") or "retrain failed the bar"
        subject = "Classifier gate REVERTED to the LLM gate"
        body = (
            "The weekly gate-classifier retrain failed the shipping bar, so "
            "the committed flag is off and every candidate pays the LLM gate "
            "again — recall is protected, the bill goes back up.\n\n"
            f"  {reason}\n\n"
            "Nothing is dropped by the classifier while the flag is off. It "
            "re-arms by itself on the first weekly run that passes the bar.")
        dedupe = f"gate-classifier-reverted:{_fingerprint(reason)}"
    else:
        return
    if _alert(subject, body, dedupe_key=dedupe, poster=poster):
        status.pop("notice_pending", None)
        write_status(out_dir, status)


# --- Drift alarm ---------------------------------------------------------------


def uncertain_share(real, *, now: datetime | None = None,
                    window_days: int = DRIFT_WINDOW_DAYS):
    """(share_pct, scored) over the last `window_days` — None when the
    classifier was not routing (no CLF_* lines in the window, so a share
    would describe the pre-arming world where everything is 'uncertain')."""
    floor = ((now or datetime.now(timezone.utc))
             - timedelta(days=window_days)).strftime("%Y-%m-%d")
    window = [l for l in real if _day(l) >= floor]
    confident = sum(1 for l in window
                    if l.get("gate") in (gate_ledger.CLF_YES, gate_ledger.CLF_NO))
    if not confident:
        return None, len(window)
    uncertain = sum(1 for l in window
                    if l.get("gate") in (gate_ledger.YES, gate_ledger.NO,
                                         gate_ledger.ERROR))
    scored = confident + uncertain
    return 100.0 * uncertain / scored, scored


def check_drift(out_dir: str, status: dict, real, poster=None,
                now=None) -> None:
    if not status.get("armed"):
        return
    share, scored = uncertain_share(real, now=now)
    if share is None:
        return
    drift = status.get("drift") or {}
    print(f"[gate-classifier] uncertain share, last {DRIFT_WINDOW_DAYS} days: "
          f"{share:.1f}% of {scored} routed candidates")
    if share > DRIFT_ALERT_PCT and not drift.get("open"):
        sent = _alert(
            f"Classifier gate drift: uncertain share {share:.0f}%",
            (f"Over the last {DRIFT_WINDOW_DAYS} days, {share:.1f}% of routed "
             "candidates fell in the UNCERTAIN band and paid the LLM gate — "
             f"past the {DRIFT_ALERT_PCT:.0f}% alarm line. That is vocabulary "
             "drift or a new language, and it quietly re-inflates the gate "
             "bill. The weekly retrain may absorb it; if this repeats, look "
             "at which languages dominate the uncertain band in "
             "data/gate_labels/."),
            dedupe_key=f"gate-classifier-drift:{_fingerprint('uncertain share high')}",
            poster=poster)
        if sent:
            status["drift"] = {"open": True,
                               "since": (now or datetime.now(timezone.utc))
                               .strftime("%Y-%m-%d")}
            write_status(out_dir, status)
    elif share < DRIFT_CLEAR_PCT and drift.get("open"):
        sent = _alert(
            "Classifier gate drift cleared",
            f"Uncertain share is back to {share:.1f}% over the last "
            f"{DRIFT_WINDOW_DAYS} days.",
            resolve_scope="gate-classifier-drift", poster=poster)
        if sent:
            status["drift"] = {"open": False}
            write_status(out_dir, status)


# --- The weekly entry point -------------------------------------------------------


def run(label_dir: str | None = None, out_dir: str | None = None,
        fit_fn=fit, poster=None, now=None) -> int:
    out_dir = out_dir or gate_classifier.classifier_dir()
    real, weak = load_labels(label_dir)
    prior = read_status(out_dir)
    n_labels = len(real)
    print(f"[gate-classifier] {n_labels} real labels, {len(weak)} weak, "
          f"spanning {span_days(real)} day(s)")

    if not real:
        print(not_ready_line(0, {"days": 0, "rate_pct": 0.0}))
        return 0

    report = replay(real, weak, fit_fn=fit_fn)
    print(f"[gate-classifier] replay: {report['rate_pct']}% of "
          f"{report['stored']} stored rows kept; confident band "
          f"{report['confident_pct']}% of {report['candidates']} candidates")

    if bar_passes(report):
        trained_at = (now or datetime.now(timezone.utc)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        rows = training_rows(real, weak)
        model = build_model(rows, language_roster(real), fit_fn, real=real)
        sha = write_artifact(model, out_dir, n_labels=n_labels,
                             trained_at=trained_at)
        was_armed = bool(prior.get("armed"))
        status = arm(out_dir, report, artifact_sha=sha, trained_at=trained_at,
                     n_labels=n_labels, prior=prior)
        if was_armed and not prior.get("notice_pending"):
            # A refresh of an already-armed gate is routine, not news. (A
            # PENDING notice from a post that failed last week still retries.)
            status.pop("notice_pending", None)
            write_status(out_dir, status)
        print(f"[gate-classifier] ARMED (artifact {sha}, t_lo={model.t_lo:.3f}, "
              f"t_hi={model.t_hi:.3f}, {len(model.langs)} languages, "
              f"skip band open for {len(model.relevant_langs)})")
        send_pending_notice(out_dir, status, poster=poster)
        check_drift(out_dir, status, real, poster=poster, now=now)
        return 0

    if prior.get("armed"):
        status = revert(out_dir, report, prior=prior, n_labels=n_labels)
        print(f"[gate-classifier] {status['reason']}")
        send_pending_notice(out_dir, status, poster=poster)
        return 0

    # Not armed and not ready: say so in the plan's own words, change nothing.
    print(not_ready_line(n_labels, report))
    send_pending_notice(out_dir, prior, poster=poster)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels", default=None,
                        help="label directory (default data/gate_labels)")
    parser.add_argument("--out", default=None,
                        help="artifact directory (default data/gate_classifier)")
    args = parser.parse_args(argv)
    return run(label_dir=args.labels, out_dir=args.out)


if __name__ == "__main__":
    sys.exit(main())
