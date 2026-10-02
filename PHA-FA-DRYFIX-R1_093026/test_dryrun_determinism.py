"""FA-1 regression: the synthetic dry run must be byte-replayable.
Written before the fix and confirmed failing on the 0.3.1 harness."""
import hashlib
import json
import os
from pathlib import Path

import corpus_harness as h

N = 12


def _run(root: Path):
    h._dryrun(N, root)
    h.run_corpus(root)
    ledger = [json.loads(l) for l in (root / "ledger.jsonl").read_text().splitlines() if l.strip()]
    pdfs = {d.name: hashlib.sha256((d / "figure.pdf").read_bytes()).hexdigest()
            for d in sorted(root.iterdir()) if d.is_dir()}
    return ledger, pdfs


def test_two_runs_byte_identical(tmp_path):
    a_led, a_pdf = _run(tmp_path / "a")
    b_led, b_pdf = _run(tmp_path / "b")
    assert len(a_pdf) == N and a_pdf == b_pdf                      # every figure, not just the first
    assert [r["result_sha256"] for r in a_led] == [r["result_sha256"] for r in b_led]


def test_outcomes_unchanged_by_pin(tmp_path):
    led, _ = _run(tmp_path / "c")
    shipped = [json.loads(l) for l in Path("corpus_dryrun/ledger.jsonl").read_text().splitlines() if l.strip()][:N]
    keys = ("figure_id", "truth", "engine_state", "outcome", "finding_codes", "links")
    assert [{k: r[k] for k in keys} for r in led] == [{k: r[k] for k in keys} for r in shipped]


def test_environment_restored(tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    h._dryrun(2, tmp_path / "d")
    assert "SOURCE_DATE_EPOCH" not in os.environ
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1234")
    h._dryrun(2, tmp_path / "e")
    assert os.environ["SOURCE_DATE_EPOCH"] == "1234"


def _runtime():
    import platform
    import matplotlib, numpy, pdfplumber, PIL
    return {"python": platform.python_version(), "matplotlib": matplotlib.__version__, "numpy": numpy.__version__,
            "pillow": PIL.__version__, "pdfplumber": pdfplumber.__version__}


def _require_runtime(ref):
    import pytest
    rt = _runtime()
    if rt != ref["runtime"]:
        pytest.skip(f"UNRESOLVED: runtime {rt} differs from reference {ref['runtime']}")


def test_matches_pinned_reference(tmp_path):
    """Byte replay against the recorded reference. Skips (never passes silently) on a different runtime."""
    ref = json.loads(Path("corpus_dryrun/pinned_reference.json").read_text())
    _require_runtime(ref)
    led, _ = _run(tmp_path / "f")
    assert [r["result_sha256"] for r in led] == ref["result_sha256"]


def test_runtime_guard_covers_every_pinned_build(monkeypatch):
    """A change in any pinned build must turn the replay into UNRESOLVED, not a FAIL or a PASS."""
    import pytest, numpy, PIL, matplotlib, pdfplumber
    ref = json.loads(Path("corpus_dryrun/pinned_reference.json").read_text())
    assert set(ref["runtime"]) == {"python", "matplotlib", "numpy", "pillow", "pdfplumber"}
    import platform
    # Independent oracle: read the builds here, not through _runtime(), so a guard that drops a key cannot hide.
    actual = {"python": platform.python_version(), "matplotlib": matplotlib.__version__, "numpy": numpy.__version__,
              "pillow": PIL.__version__, "pdfplumber": pdfplumber.__version__}
    if actual != ref["runtime"]:
        pytest.skip("UNRESOLVED: not on the pinned runtime, so the guard cannot be exercised here")
    try:
        _require_runtime(ref)
    except pytest.skip.Exception:
        pytest.fail("guard skipped on the pinned runtime: its runtime key does not cover every pinned build")
    for mod in (numpy, PIL, matplotlib, pdfplumber):
        with monkeypatch.context() as m:
            m.setattr(mod, "__version__", "0.0.0-changed")
            with pytest.raises(pytest.skip.Exception):
                _require_runtime(ref)
