"""The local classifier gate: three-way routing in front of the paid LLM gate.

WHY THIS EXISTS
---------------
docs/PLAN-gate-to-five-dollars.md, step 2. The paid gate costs $5.70/month on
its own, which is more than the owner's whole $5 target, so the gate has to
mostly stop being an LLM. This module is the runtime half: a logistic
regression over character 3-5-grams and word unigrams (43 languages, no
tokenizer fights), trained by `train_gate_classifier.py` from the labels
`pipeline/gate_ledger.py` has been writing since step 1.

THREE-WAY ROUTING, AND WHY THE SHAPE IS THE SAFETY DESIGN
---------------------------------------------------------
    confident-RELEVANT   -> skip the LLM gate, straight to extraction
    UNCERTAIN            -> the LLM gate, exactly as today
    confident-IRRELEVANT -> drop (the only risky class)

Only the third class can cost recall, so the shipping bar is measured on it
alone: a replay over >=30 days of real labels in which >=99.5% of candidates
that produced a STORED row route relevant-or-uncertain. The bar is enforced
twice — by the trainer before it will arm the flag, and HERE at every load:
this module refuses to route on an artifact whose committed replay report is
missing, stale, under the bar, or about a different set of weights. Recall is
the moat; the owner would rather pay $9/month than dent it.

FAIL OPEN, EVERYWHERE
---------------------
Artifact missing, unreadable, corrupt, the wrong format, unarmed, stale, a
language the training set never saw, any exception at all -> UNCERTAIN, which
is the LLM gate, which is exactly yesterday's behaviour. A classifier failure
may cost money; it must never become a silent drop. This project's signature
failure is the thing that looks healthy while broken (TECHLOG 2026-07-29), so
every degraded load also says WHY, once, on stderr.

NO NEW RUNTIME DEPENDENCY
-------------------------
scikit-learn fits the weights inside the weekly training workflow and nowhere
else. What ships is a plain weight vector this module scores with the standard
library: the SAME `features()` below is imported by the trainer, so the bytes
that train are the bytes that serve and there is no vectoriser to keep in
sync. Scoring one candidate is ~1,000 CRC32 hashes and one dot product —
microseconds, beside a gate call that took a second and cost money.

THE FLAG IS COMMITTED, NOT CONFIGURED
-------------------------------------
`data/gate_classifier/status.json` is written only by the weekly trainer when
the replay bar passes, and reverted by it when a retrain fails the bar. A
human can force the LLM gate back with TIT_GATE_CLASSIFIER=off, but there is
deliberately no environment variable that can force the classifier ON past a
failed or missing replay report.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import struct
import sys
import unicodedata
import zlib
from datetime import datetime, timezone

# --- Routes -----------------------------------------------------------------

RELEVANT = "relevant"
UNCERTAIN = "uncertain"
IRRELEVANT = "irrelevant"

# --- The contract with the trainer -------------------------------------------

#: Feature space. 2^18 buckets keeps collisions rare against the ~10^5 distinct
#: n-grams a few months of labels produce, and the weight vector at 1 MB of
#: float32 — the artifact stays well under the plan's 5 MB commit ceiling.
DIM = 1 << 18

#: Character n-gram range. Chosen in the plan: char 3-5-grams because the
#: corpus spans 43 languages and word tokenizers fight half of them (the
#: Hebrew/CJK lessons in TECHLOG apply); word unigrams ride along for the
#: languages where words do work.
CHAR_NGRAMS = (3, 4, 5)

#: The shipping bar, as a percentage. Non-negotiable, from the plan: >=99.5%
#: of stored-row candidates must route relevant-or-uncertain in a replay over
#: >=30 days of real labels. Read by the trainer AND enforced at load here.
SHIP_BAR_PCT = 99.5
MIN_REPLAY_DAYS = 30

#: A replay report older than this no longer describes the traffic, so the
#: artifact fails open until the weekly retrain produces a fresh one. Four
#: weekly runs would all have to fail silently before this trips, and the
#: direction of failure is paying for the LLM gate again, never dropping.
STALE_DAYS = 28

ARTIFACT_FORMAT = 1

DEFAULT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "gate_classifier")

MODEL_NAME = "model.json.gz"
STATUS_NAME = "status.json"

STATS = {"relevant": 0, "uncertain": 0, "irrelevant": 0, "fail_open": 0}


def classifier_dir() -> str:
    return os.environ.get("TIT_GATE_CLASSIFIER_DIR") or DEFAULT_DIR


def model_path() -> str:
    return os.path.join(classifier_dir(), MODEL_NAME)


def status_path() -> str:
    return os.path.join(classifier_dir(), STATUS_NAME)


def enabled() -> bool:
    """Off switch only. There is no ON switch: arming is the trainer's write."""
    return (os.environ.get("TIT_GATE_CLASSIFIER") or "on").strip().lower() not in (
        "off", "0", "no", "false")


# --- Features -----------------------------------------------------------------
#
# ONE implementation, imported by the trainer. CRC32 rather than Python's
# built-in hash because hash() is salted per process (PYTHONHASHSEED) and a
# featurizer that moves between processes is a model that scores garbage.


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    return " ".join(text.split())


def _bucket(token: str) -> int:
    return zlib.crc32(token.encode("utf-8", "replace")) & (DIM - 1)


def features(headline: str, teaser: str = "") -> dict[int, int]:
    """Hashed feature counts for one candidate.

    The input is exactly what the label ledger holds — the headline and the
    cleaned teaser — which is itself a strict subset of what the LLM gate
    reads. Char n-grams are hashed under a "c:" prefix and word unigrams under
    "w:" so a three-letter word and its own trigram never share a bucket by
    construction (they still may by collision, which is what hashing is).
    """
    text = normalise((headline or "") + " " + (teaser or ""))
    if not text:
        return {}
    counts: dict[int, int] = {}
    padded = f" {text} "
    for n in CHAR_NGRAMS:
        for i in range(len(padded) - n + 1):
            bucket = _bucket("c:" + padded[i:i + n])
            counts[bucket] = counts.get(bucket, 0) + 1
    for word in text.split():
        bucket = _bucket("w:" + word)
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def lang_key(lang: str) -> str:
    """Fold the ledger's language spellings ("English", "en", "pt-BR") into one
    key. Deliberately crude: it only has to be the SAME crude on both sides of
    the train/serve boundary, and it is, because both sides call this."""
    value = (lang or "").strip().lower()
    if not value:
        return ""
    value = value.replace("_", "-").split("-", 1)[0].split(":", 1)[0]
    return {"english": "en", "spanish": "es", "french": "fr", "german": "de",
            "portuguese": "pt", "italian": "it", "dutch": "nl",
            "russian": "ru", "arabic": "ar", "turkish": "tr",
            "indonesian": "id", "vietnamese": "vi", "japanese": "ja",
            "korean": "ko", "chinese": "zh", "hebrew": "he",
            "polish": "pl", "greek": "el"}.get(value, value)


# --- Scoring ------------------------------------------------------------------


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + pow(2.718281828459045, -min(z, 60.0)))
    e = pow(2.718281828459045, max(z, -60.0))
    return e / (1.0 + e)


class Model:
    """A weight vector, a bias, two thresholds and two language rosters.

    `langs` is who may be routed confidently AT ALL; `relevant_langs` is the
    stricter roster for the confident-RELEVANT band alone. They are separate
    because the two bands fail differently: a wrong drop costs recall and is
    policed by the global replay bar, while a wrong skip buys an EXPENSIVE
    extraction for junk the cheap gate would have refused. The ledger showed
    exactly that shape in Polish — a 17.7% gate pass rate against Spanish's
    80.1%, with the passes skewed to football-club and municipal noise — so a
    language earns the skip band per-language, on its own volume and its own
    measured gate agreement, and until then its high scorers stay UNCERTAIN
    and keep paying the gate. An artifact without the field grants nobody."""

    __slots__ = ("weights", "bias", "t_lo", "t_hi", "langs", "relevant_langs",
                 "trained_at", "sha")

    def __init__(self, weights, bias, t_lo, t_hi, langs, trained_at="",
                 sha="", relevant_langs=None):
        self.weights = weights
        self.bias = float(bias)
        self.t_lo = float(t_lo)
        self.t_hi = float(t_hi)
        self.langs = frozenset(langs)
        self.relevant_langs = frozenset(relevant_langs or ())
        self.trained_at = trained_at
        self.sha = sha

    def score(self, headline: str, teaser: str = "") -> float:
        z = self.bias
        weights = self.weights
        for bucket, count in features(headline, teaser).items():
            z += weights[bucket] * count
        return sigmoid(z)


def encode_weights(weights) -> str:
    return base64.b64encode(
        struct.pack(f"<{len(weights)}f", *weights)).decode("ascii")


def decode_weights(blob: str):
    raw = base64.b64decode(blob.encode("ascii"), validate=True)
    if len(raw) != DIM * 4:
        raise ValueError(f"weight vector is {len(raw)} bytes, expected {DIM * 4}")
    return struct.unpack(f"<{DIM}f", raw)


def weights_sha(blob_b64: str) -> str:
    import hashlib
    return hashlib.sha256(blob_b64.encode("ascii")).hexdigest()[:16]


# --- Loading, with the refusals that make the flag trustworthy ----------------

_CACHE: dict = {"key": None, "model": None, "why": ""}


def _read_artifact(path: str) -> tuple[Model, str]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("format") != ARTIFACT_FORMAT:
        raise ValueError(f"unknown artifact format {doc.get('format')!r}")
    if int(doc.get("dim") or 0) != DIM:
        raise ValueError(f"artifact dim {doc.get('dim')} != runtime DIM {DIM}")
    blob = doc["weights"]
    model = Model(
        decode_weights(blob), doc["bias"], doc["t_lo"], doc["t_hi"],
        doc.get("langs") or [], doc.get("trained_at") or "",
        weights_sha(blob), relevant_langs=doc.get("relevant_langs") or [])
    return model, doc.get("trained_at") or ""


def _fresh(trained_at: str, now: datetime | None = None) -> bool:
    try:
        stamp = datetime.fromisoformat(trained_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = (now or datetime.now(timezone.utc)) - stamp
    return age.days <= STALE_DAYS


def _replay_ok(status: dict) -> str:
    """Empty string when the committed replay report clears the bar; otherwise
    the reason it does not. The refusals here are the runtime enforcement of
    'the flag flip is refused when the replay report is missing or stale'."""
    if not status.get("armed"):
        return "status.json says the classifier gate is not armed"
    replay = status.get("replay")
    if not isinstance(replay, dict):
        return "no replay report beside the armed flag"
    try:
        rate = float(replay["rate_pct"])
        days = int(replay["days"])
    except (KeyError, TypeError, ValueError):
        return "replay report is malformed"
    if days < MIN_REPLAY_DAYS:
        return f"replay covers {days} days, bar needs >= {MIN_REPLAY_DAYS}"
    if rate < SHIP_BAR_PCT:
        return f"replay {rate}% is under the {SHIP_BAR_PCT}% bar"
    if not _fresh(status.get("trained_at") or ""):
        return (f"replay report is stale (trained {status.get('trained_at')!r}, "
                f"ceiling {STALE_DAYS} days)")
    return ""


def load(now: datetime | None = None) -> tuple[Model | None, str]:
    """The armed model, or (None, why-not). Never raises.

    Cached on the (path, mtimes) pair so a collect run pays the 1 MB decode
    once, while a test that swaps TIT_GATE_CLASSIFIER_DIR gets a fresh read.
    """
    mpath, spath = model_path(), status_path()
    try:
        key = (mpath, os.path.getmtime(mpath), os.path.getmtime(spath))
    except OSError:
        return None, "no committed artifact (model or status file missing)"
    if _CACHE["key"] == key:
        return _CACHE["model"], _CACHE["why"]

    model, why = None, ""
    try:
        with open(spath, encoding="utf-8") as fh:
            status = json.load(fh)
        why = _replay_ok(status)
        if not why:
            model, trained_at = _read_artifact(mpath)
            if status.get("artifact_sha") != model.sha:
                model, why = None, ("replay report describes different weights "
                                    f"(status {status.get('artifact_sha')!r} != "
                                    f"artifact {model.sha!r})" if model else "")
    except Exception as exc:  # noqa: BLE001 — fail open, always
        model, why = None, f"artifact unreadable ({exc})"
    _CACHE.update(key=key, model=model, why=why)
    if model is None and why:
        print(f"[gate-classifier] failing open to the LLM gate — {why}",
              file=sys.stderr)
    return model, why


def reset_cache() -> None:
    _CACHE.update(key=None, model=None, why="")
    for name in STATS:
        STATS[name] = 0


# --- Routing -------------------------------------------------------------------


def route(headline: str, teaser: str = "", lang: str = "") -> str:
    """One of RELEVANT / UNCERTAIN / IRRELEVANT. Never raises.

    UNCERTAIN is the answer to every doubt: switched off, nothing armed, a
    language the training set never saw, an empty headline, any exception.
    UNCERTAIN means the LLM gate, which is the pre-classifier behaviour.
    """
    try:
        if not enabled():
            return UNCERTAIN
        model, _why = load()
        if model is None:
            STATS["fail_open"] += 1
            return UNCERTAIN
        language = lang_key(lang)
        if language not in model.langs:
            # A new language is exactly the drift the plan says must reach the
            # LLM gate: the classifier has never seen a byte of it and its
            # score would be a shrug wearing a decimal point.
            STATS["uncertain"] += 1
            return UNCERTAIN
        if not normalise(headline or ""):
            STATS["uncertain"] += 1
            return UNCERTAIN
        score = model.score(headline, teaser)
        if score >= model.t_hi and language in model.relevant_langs:
            # The skip band is PER LANGUAGE (see Model): a high score in a
            # language that has not earned it stays UNCERTAIN and pays the
            # cheap gate rather than gambling an extraction on it.
            STATS["relevant"] += 1
            return RELEVANT
        if score <= model.t_lo:
            STATS["irrelevant"] += 1
            return IRRELEVANT
        STATS["uncertain"] += 1
        return UNCERTAIN
    except Exception as exc:  # noqa: BLE001 — a router must never take down a run
        STATS["fail_open"] += 1
        print(f"[gate-classifier] failing open to the LLM gate — {exc}",
              file=sys.stderr)
        return UNCERTAIN


def route_item(item: dict) -> str:
    """Route one raw candidate dict, deriving the SAME headline, teaser and
    language the label ledger records — the train/serve contract in one call."""
    from . import gate_ledger
    try:
        headline = gate_ledger._clean(item.get("headline") or "")
        teaser = gate_ledger.teaser(item.get("raw_text") or "", headline)
        lang = gate_ledger._lang(item)
    except Exception:  # noqa: BLE001
        STATS["fail_open"] += 1
        return UNCERTAIN
    return route(headline, teaser, lang)
