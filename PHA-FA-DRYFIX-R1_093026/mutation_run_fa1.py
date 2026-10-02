"""Mutants for the FA-1 fix. Each must be killed by test_dryrun_determinism.py.
Runs each mutant in a scratch copy; a crash or collection error is reported as ERROR, not a kill."""
import shutil, subprocess, sys, tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent
PIN = '    os.environ["SOURCE_DATE_EPOCH"] = DRYRUN_EPOCH\n'
MUTANTS = {
    "M1 pin removed": [(PIN, "")],
    "M2 pin applied only after the first figure": [
        (PIN, ""),
        ('        pdf = _pdf(_fig(**kw))\n',
         '        pdf = _pdf(_fig(**kw))\n        os.environ["SOURCE_DATE_EPOCH"] = DRYRUN_EPOCH\n')],
    "M3 prior environment not restored": [
        ('    finally:\n        if prior_epoch is None:', '    finally:\n        pass\n        if False:')],
    "M4 unset prior not removed": [('            os.environ.pop("SOURCE_DATE_EPOCH", None)\n', '            pass\n')],
    "M5 pin only for large runs": [(PIN, '    if n > 50:\n        os.environ["SOURCE_DATE_EPOCH"] = DRYRUN_EPOCH\n')],
    "M6 pin value changed": [('DRYRUN_EPOCH = "0"', 'DRYRUN_EPOCH = "86400"')],
}
# Guard mutants act on the test's runtime key, not on the harness.
TEST_MUTANTS = {
    "M7 runtime guard omits numpy": [('return {"python": platform.python_version(), "matplotlib": matplotlib.__version__, "numpy": numpy.__version__,',
                                      'return {"python": platform.python_version(), "matplotlib": matplotlib.__version__,')],
    "M8 runtime guard omits pillow": [('\n            "pillow": PIL.__version__, "pdfplumber": pdfplumber.__version__}\n\n\ndef _require_runtime',
                                       '\n            "pdfplumber": pdfplumber.__version__}\n\n\ndef _require_runtime')],
}

def run(mods, target="corpus_harness.py"):
    with tempfile.TemporaryDirectory() as t:
        d = Path(t) / "k"; shutil.copytree(SRC, d, ignore=shutil.ignore_patterns("__pycache__"))
        p = d / target; s = p.read_text()
        for old, new in mods:
            if s.count(old) != 1:
                return "ERROR (mutation site not unique)"
            s = s.replace(old, new)
        p.write_text(s)
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            "test_dryrun_determinism.py"], cwd=d, capture_output=True, text=True,
                           env={k: v for k, v in __import__("os").environ.items() if k != "SOURCE_DATE_EPOCH"})
        out = r.stdout
        if "error" in out.lower() and "failed" not in out:
            return "ERROR"
        return "KILLED" if r.returncode == 1 and " failed" in out else ("SURVIVED" if r.returncode == 0 else f"ERROR rc={r.returncode}")

if __name__ == "__main__":
    res = {k: run(v) for k, v in MUTANTS.items()}
    res.update({k: run(v, "test_dryrun_determinism.py") for k, v in TEST_MUTANTS.items()})
    for k, v in res.items(): print(f"{v:9} {k}")
    killed = sum(v == "KILLED" for v in res.values())
    print(f"{killed}/{len(res)} killed")
    sys.exit(0 if killed == len(res) else 1)
