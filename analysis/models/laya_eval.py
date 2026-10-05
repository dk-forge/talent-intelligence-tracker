"""Evaluate the PyPI package `laya` (local CPU classifier) against the
hand-labelled gate gold set (analysis/models/goldset-gate-2026-08.json).

The question is classify.GATE_SYSTEM read from source with ast (no pipeline
imports, so the venv needs only laya); the gold field is gold_is_talent_signal.

Run by .github/workflows/laya-eval.yml only: no keys, no network beyond pip
and the Hugging Face checkpoint download, writes nothing but the job summary.
"""
import ast
import json
import os
import resource
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def gate_system():
    tree = ast.parse((ROOT / "pipeline" / "classify.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "GATE_SYSTEM" for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit("GATE_SYSTEM not found in pipeline/classify.py")


QUESTION = {"signal": {"type": "noul", "instructions": gate_system()}}


def load_items(limit):
    d = json.loads((ROOT / "analysis" / "models" / "goldset-gate-2026-08.json").read_text())
    out = []
    for it in d["items"]:
        text = it.get("text") or ""
        out.append((text.split("\n", 1)[0][:120], text, bool(it["gold_is_talent_signal"])))
    return out[:limit]


def peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0  # Linux: KiB


def main():
    limit = int(os.environ.get("LAYA_LIMIT", "200") or 200)
    items = load_items(min(limit, 200))
    from laya import Router  # imported late: load time is not per-item time

    router = Router()
    times, agree, disagree, errors = [], 0, [], 0
    for title, text, gold in items:
        t0 = time.perf_counter()
        try:
            res = router.predict(text, QUESTION)
            p = float(res["answers"]["signal"]["noul"])
        except Exception as exc:  # report, never hide
            errors += 1
            print(f"laya error on {title}: {exc!r}", file=sys.stderr)
            continue
        times.append(time.perf_counter() - t0)
        pred = p >= 0.5
        if pred == gold:
            agree += 1
        else:
            disagree.append((title, gold, p))
    scored = len(times)
    lines = [
        "## laya vs gate gold set (talent signal YES/NO)",
        "",
        "| metric | value |",
        "|---|---|",
        f"| items scored | {scored} (errors {errors}) |",
        f"| agreement vs gold | {100.0 * agree / scored:.1f}% |" if scored else "| agreement | n/a |",
        f"| gold positives | {sum(1 for _, _, g in items if g)} of {len(items)} |",
        f"| median s/item | {statistics.median(times):.3f} |" if times else "| median s/item | n/a |",
        f"| peak RSS | {peak_rss_mb():.0f} MB |",
        "",
        "### First 5 disagreements",
        "",
    ]
    for title, gold, p in disagree[:5]:
        lines.append(f"- {title} - gold={gold}, laya p(yes)={p:.2f}")
    report = "\n".join(lines) + "\n"
    print(report)
    # Annotations: the only part of a run the API can read back (the step
    # summary is not exposed). Public headlines only, no PII.
    def esc(v):
        return str(v).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    pct = f"{100.0 * agree / scored:.1f}" if scored else "n/a"
    med = f"{statistics.median(times):.3f}" if times else "n/a"
    print(f"::notice title=laya-result::agreement={pct}%25, n={scored}, "
          f"median_s={med}, peak_rss_mb={peak_rss_mb():.0f}, errors={errors}")
    for title, gold, p in disagree[:5]:
        print(f"::notice title=laya-disagreement::{esc(title)} gold={gold} "
              f"laya={p >= 0.5} p={p:.2f}")
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as fh:
            fh.write(report)
    return 0 if scored else 1


if __name__ == "__main__":
    sys.exit(main())
