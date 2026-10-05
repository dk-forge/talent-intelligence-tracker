"""Free local classifiers vs the gate gold set (test-only experiment).

Same labelled rows as analysis/models/laya_eval.py (goldset-gate-2026-08,
field gold_is_talent_signal), split ONCE into a stratified 70/30 train/test
with a fixed seed, so every candidate is scored on the identical held-out
items:

  embed     sentence embeddings (MiniLM, bge-small) + logistic regression
  gliclass  Knowledgator GLiClass zero-shot (no training)
  fasttext  fastText supervised on the train split

Reports accuracy, precision/recall/F1 on the positive (YES) class, the
always-"no" baseline on the same test split, and the "pre-filter value": the
share of items that could skip the paid LLM gate while keeping >= 98% of YES.

Run by .github/workflows/classifier-eval.yml only (workflow_dispatch, no
secrets, permissions {}). It changes nothing about how the tracker classifies
in production. Results come back as ::notice annotations.
"""
import math
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
POSITIVE_LABEL = "talent-market news about a named employer (hiring, appointment, pay, site, work policy or funding)"
NEGATIVE_LABEL = "layoffs, roundup, opinion, job advert or not about one employer"


def load_items():
    from laya_eval import load_items as gold_items
    return [(text, gold) for _, text, gold in gold_items(10 ** 6)]


SEED = 20261005
TEST_FRACTION = 0.3
TARGET_RECALL = 0.98


def stratified_split(labels, test_fraction=TEST_FRACTION, seed=SEED):
    """Return (train_idx, test_idx). Same labels + seed -> same split, always.

    Each class contributes round(n_class * test_fraction) items to the test
    side (at least one when the class has two or more), so every candidate is
    scored on the identical held-out items.
    """
    rng = random.Random(seed)
    train, test = [], []
    for cls in sorted(set(labels)):
        idx = [i for i, y in enumerate(labels) if y == cls]
        rng.shuffle(idx)
        k = round(len(idx) * test_fraction)
        if k == 0 and len(idx) >= 2:
            k = 1
        test.extend(idx[:k])
        train.extend(idx[k:])
    return sorted(train), sorted(test)


def metrics(gold, pred):
    """Accuracy plus precision/recall/F1 on the positive (True) class."""
    tp = sum(1 for g, p in zip(gold, pred) if g and p)
    fp = sum(1 for g, p in zip(gold, pred) if not g and p)
    fn = sum(1 for g, p in zip(gold, pred) if g and not p)
    n = len(gold)
    acc = sum(1 for g, p in zip(gold, pred) if g == p) / n if n else 0.0
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"accuracy": acc, "precision": prec, "recall": rec, "f1": f1}


def always_no_accuracy(gold):
    return sum(1 for g in gold if not g) / len(gold) if gold else 0.0


def skippable_at_recall(gold, scores, target=TARGET_RECALL):
    """Pre-filter value: send an item to the paid LLM only when its score is
    >= t, with t the highest threshold that still keeps >= `target` of the
    positives. Returns (fraction of ALL items below t, i.e. skipped, t).
    The threshold is picked on the same items it is scored on, so this is
    an optimistic upper bound on a small set.
    """
    pos = sorted((s for g, s in zip(gold, scores) if g), reverse=True)
    if not pos:
        return 0.0, None
    need = math.ceil(target * len(pos) - 1e-9)
    t = pos[need - 1]
    skipped = sum(1 for s in scores if s < t)
    return skipped / len(scores), t


# ---- candidates: heavy imports stay inside, each runs in its own venv ----

def cand_embed(train, test):
    from sentence_transformers import SentenceTransformer
    from sklearn.linear_model import LogisticRegression
    out = {}
    for name in ("sentence-transformers/all-MiniLM-L6-v2", "BAAI/bge-small-en-v1.5"):
        enc = SentenceTransformer(name, device="cpu")
        xtr = enc.encode([t for t, _ in train], normalize_embeddings=True)
        xte = enc.encode([t for t, _ in test], normalize_embeddings=True)
        clf = LogisticRegression(class_weight="balanced", max_iter=5000,
                                 random_state=SEED)
        clf.fit(xtr, [y for _, y in train])
        out["embed-lr " + name.split("/")[1]] = [float(p) for p in clf.predict_proba(xte)[:, 1]]
    return out


def cand_gliclass(train, test):
    from gliclass import GLiClassModel, ZeroShotClassificationPipeline
    from transformers import AutoTokenizer
    name = os.environ.get("GLICLASS_MODEL", "knowledgator/gliclass-small-v1.0")
    model = GLiClassModel.from_pretrained(name)
    tok = AutoTokenizer.from_pretrained(name)
    pipe = ZeroShotClassificationPipeline(model, tok, classification_type="multi-label",
                                          device="cpu")
    scores = []
    for text, _ in test:
        res = pipe(text, [POSITIVE_LABEL, NEGATIVE_LABEL], threshold=0.0)[0]
        by = {r["label"]: float(r["score"]) for r in res}
        scores.append(by.get(POSITIVE_LABEL, 0.0))
    return {"gliclass zero-shot " + name.split("/")[1]: scores}


def cand_fasttext(train, test):
    import tempfile
    import fasttext
    def clean(t):
        return " ".join(t.lower().split())
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        for text, y in train:
            fh.write(f"__label__{int(y)} {clean(text)}\n")
        path = fh.name
    model = fasttext.train_supervised(path, epoch=50, lr=0.5, wordNgrams=2,
                                      dim=50, thread=1, verbose=0, seed=SEED)
    scores = []
    for text, _ in test:
        labels, probs = model.predict(clean(text), k=2)
        d = dict(zip(labels, probs))
        scores.append(float(d.get("__label__1", 0.0)))
    return {"fasttext": scores}


CANDIDATES = {"embed": cand_embed, "gliclass": cand_gliclass, "fasttext": cand_fasttext}


def esc(v):
    return str(v).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def report(name, gold, scores, seconds):
    pred = [s >= 0.5 for s in scores]
    m = metrics(gold, pred)
    skip, t = skippable_at_recall(gold, scores)
    line = (f"acc={100 * m['accuracy']:.1f}%, prec={m['precision']:.2f}, "
            f"recall={m['recall']:.2f}, f1={m['f1']:.2f}, "
            f"always_no={100 * always_no_accuracy(gold):.1f}%, "
            f"skip_at_98pct_recall={100 * skip:.1f}% (t={t if t is None else round(t, 3)}), "
            f"n_test={len(gold)}, pos_test={sum(gold)}, seconds={seconds:.0f}")
    print(f"::notice title={esc('clf ' + name)}::{esc(line)}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(f"- **{name}**: {line}\n")


def main(argv):
    which = argv[1] if len(argv) > 1 else "baseline"
    items = load_items()
    labels = [y for _, y in items]
    tr, te = stratified_split(labels)
    train = [items[i] for i in tr]
    test = [items[i] for i in te]
    gold = [y for _, y in test]
    if which == "baseline":
        report("always-no", gold, [0.0] * len(gold), 0)
        print(f"::notice title=clf split::n={len(items)}, train={len(train)} "
              f"(pos {sum(y for _, y in train)}), test={len(test)} (pos {sum(gold)}), seed={SEED}")
        return 0
    t0 = time.perf_counter()
    try:
        results = CANDIDATES[which](train, test)
    except Exception as exc:  # report, never hide
        print(f"::warning title={esc('clf ' + which + ' failed')}::{esc(repr(exc)[:500])}")
        return 1
    for name, scores in results.items():
        report(name, gold, scores, time.perf_counter() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
