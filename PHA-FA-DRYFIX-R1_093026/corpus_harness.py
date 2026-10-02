"""Corpus harness for the 100-figure BuildRisk evaluation (SPEC §9).

Inputs (per figure), in a directory `corpus/<figure_id>/`:
    figure.pdf            exported artifact
    declaration.json      figureattest.declaration.v2
    dataset.csv           raw dataset (canonicalized by the engine)
    adjudication.json     BLIND human ruling, written BEFORE the engine runs:
        {"truth": "honest" | "defective",
         "category": "extraction|calibration|lineage|rendering|none",
         "reviewer": "R1", "second_reviewer": "R2"|null,
         "second_truth": ...|null}

Output: ledger.jsonl (one row per figure) + metrics.json.

Release gate (§9): false_pass == 0. Everything else is reported, not gated.

`python corpus_harness.py run corpus/` runs the corpus.
`python corpus_harness.py dryrun N` generates a synthetic corpus of N
figures with known truth (honest + one of seven attacks) and runs it, so
the harness itself is validated before real reports arrive.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

from attest2 import attest_v2
from declaration import Declaration

# Fixed PDF timestamp for the synthetic dry run (FA-1). Seconds since 1970-01-01T00:00:00Z.
DRYRUN_EPOCH = "0"

CATEGORIES = ("extraction", "calibration", "lineage", "rendering", "none")

# Which engine finding codes map to which failure category
_CODE_CATEGORY = {
    "TICK_LABEL_MISMATCH": "calibration", "TICK_SET_MISMATCH": "calibration",
    "UNDECLARED_MULTIPLIER": "calibration", "MISSING_MULTIPLIER": "calibration",
    "MULTIPLIER_MISMATCH": "calibration", "AXIS_LABEL_MISMATCH": "calibration",
    "UNIT_LABEL_INCONSISTENT": "calibration", "HIDDEN_SECONDARY_AXIS": "calibration",
    "AXIS_UNREADABLE": "extraction", "NO_AXES_FRAME": "extraction",
    "MARK_COUNT_MISMATCH": "extraction", "UNDECLARED_INK": "extraction",
    "DATASET_HASH_MISMATCH": "lineage", "SOURCE_INCONSISTENT": "lineage",
    "LINEAGE_UNVERIFIED": "lineage", "TRANSFORMATION_UNSUPPORTED": "lineage",
    "VALUE_MISMATCH": "rendering", "POSITION_MISMATCH": "rendering",
    "BASELINE_SHIFTED": "rendering", "DATA_CLIPPED": "rendering", "INK_CLIPPED": "rendering",
    "ARTIFACT_HASH_MISMATCH": "lineage", "DECLARATION_INVALID": "extraction",
}


def run_one(d: Path) -> dict:
    pdf = (d / "figure.pdf").read_bytes()
    decl = Declaration.from_dict(json.loads((d / "declaration.json").read_text()))
    ds = (d / "dataset.csv").read_bytes() if (d / "dataset.csv").exists() else None
    adj = json.loads((d / "adjudication.json").read_text())
    t0 = time.perf_counter()
    res = attest_v2(pdf, decl, dataset=ds)
    dt = time.perf_counter() - t0
    cats = sorted({_CODE_CATEGORY.get(f.code, "extraction") for f in res.findings})
    truth = adj["truth"]
    state = res.state
    if state == "PASS":
        outcome = "true_pass" if truth == "honest" else "false_pass"
    elif state == "FAIL":
        outcome = "true_fail" if truth == "defective" else "false_fail"
    else:
        outcome = state.lower()  # unsupported | indeterminate -> manual review
    disagreement = adj.get("second_truth") is not None and adj["second_truth"] != truth
    return {
        "figure_id": decl.figure_id,
        "truth": truth,
        "adjudicated_category": adj.get("category", "none"),
        "engine_state": state,
        "links": res.links,
        "outcome": outcome,
        "engine_categories": cats,
        "finding_codes": [f.code for f in res.findings],
        "runtime_s": round(dt, 4),
        "reviewer_disagreement": disagreement,
        "second_reviewed": adj.get("second_reviewer") is not None,
        "result_sha256": res.sha256(),
    }


def metrics(rows: list[dict]) -> dict:
    n = len(rows)
    count = lambda k: sum(1 for r in rows if r["outcome"] == k)
    rt = [r["runtime_s"] for r in rows]
    cat_hist = {c: 0 for c in CATEGORIES}
    for r in rows:
        if r["engine_state"] != "PASS":
            for c in r["engine_categories"]:
                cat_hist[c] = cat_hist.get(c, 0) + 1
    manual = count("false_fail") + count("unsupported") + count("indeterminate")
    return {
        "n": n, "true_pass": count("true_pass"), "true_fail": count("true_fail"),
        "false_pass": count("false_pass"), "false_fail": count("false_fail"),
        "manual_review": manual, "category_histogram": cat_hist,
        "median_runtime_s": round(statistics.median(rt), 4) if rt else 0,
        "reviewer_disagreements": sum(r["reviewer_disagreement"] for r in rows),
        "second_review_coverage": round(sum(r["second_reviewed"] for r in rows) / n, 4) if n else 0,
        "release_gate_pass": count("false_pass") == 0,
    }


def run_corpus(root: Path) -> dict:
    dirs = sorted([p for p in root.iterdir() if p.is_dir()])
    rows = []
    for d in dirs:
        try:
            rows.append(run_one(d))
        except Exception as e:
            rows.append({"figure_id": d.name, "truth": "error", "engine_state": "ERROR",
                         "outcome": "error", "error": f"{type(e).__name__}: {e}",
                         "runtime_s": 0, "finding_codes": [], "engine_categories": [],
                         "reviewer_disagreement": False, "second_reviewed": False})
    with open(root / "ledger.jsonl", "w", encoding="utf-8") as f:
        for r in rows: f.write(json.dump(r, sort_keys=True) + "\n")
    m = metrics(rows)
    (root / "metrics.json").write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
    return m


# --- synthetic corpus ---
# These helpers use the BuildRisk test fixture factories directly. They are not production code.
def _dryrun(n: int, root: Path):
    """Generate n figures deterministically: 1/3 honest, 2/3 defective.
    Defective figures cycle the seven hostile cases; each is a single-issue test.
    Requires buildrisk test_hostile.py (from the real suite) on PYTHONPATH.
    """
    import random
    from hashlib import sha256 as _sha
    from test_hostile import Y, _dataset, _decl, _fig, _pdf

    random.seed(20260916)
    # FA-1: pin the PDF CreationDate so every run of the dry run is byte-identical.
    prior_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    os.environ["SOURCE_DATE_EPOCH"] = DRYRUN_EPOCH
    try:
        _dryrun_body(n, root, random, sha256_bytes, Y, _dataset, _decl, _fig, _pdf)
    finally:
        if prior_epoch is None:
            os.environ.pop("SOURCE_DATE_EPOCH", None)
        else:
            os.environ["SOURCE_DATE_EPOCH"] = prior_epoch



def _dryrun_body(n, root, random, sha256_bytes, Y, _dataset, _decl, _fig, _pdf):
    attacks = ["relabel", "baseline", "units", "invert", "multiplier", "twin", "clip", "source"]
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        d = root / f"figure_{i+1:03d}"
        d.kdir(parents=True, exist_ok=True)
      if i % 3 == 0:
            attack = None
        else:
            attack = attacks[((i // 3) * 2 + i) % len(attacks)]
        y = Y[i % len(Y)]
        dataset = _dataset(y)
        decl = _decl(f"fixture_{i+1:03d}", dataset, y)
        fig = _fig(y, decl, attack=attack)
        pdf = _pdf(fig)
        (d / "figure.pdf").write_bytes(pdf)
        (d / "declaration.json").write_text(json.dumps(decl.to_dict(), indent=2))
        (d / "dataset.csv").write_bytes(dataset)
        truth = "honest" if attack is None else "defective"
        cat = "none" if attack is None else {
            "relabel": "calibration", "baseline": "rendering", "units": "calibration",
            "invert": "rendering", "multiplier": "calibration", "twin": "calibration",
            "clip": "rendering", "source": "lineage"
}[attack]
        adj = {"truth": truth, "category": cat, "reviewer": "R1", "second_reviewer": "R2", "second_truth": truth}
        (d / "adjudication.json").write_text(json.dumps(adj, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("run", "dryrun"):
        print("usage: corpus_harness.py run <corpus_dir> | dryrun <N>", file=sys.stderr)
        sys.exit(2)
    if sys.argv[1] == "dryrun":
        n = int(sys.argv[2])
        root = Path("corpus_dryrun")
        _dryrun(n, root)
        print(json.dumps(run_corpus(root), indent=2))
    else:
        print(json.dumps(run_corpus(Path(sys.argv[2])), indent=2))
