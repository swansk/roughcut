#!/usr/bin/env python3
"""The closed-loop gate: every automated check in ROUGHCUT-GOAL.md, on a frozen commit.

    python3 tools/gate.py [ref=HEAD] [--quick]

Why a frozen copy: the board's suite writes into its own temp dirs, but a lane editing the
tree while the gate runs would change what is measured halfway through. So `ref` is
`git archive`d into $ROUGHCUT_LOOP/snap-<sha> and every check runs there; the working tree
is never touched.

Checks (each one line in summary.md, exit 0 only if all are green):
  suite    pytest app/tests (API + real Chromium). A test that fails in the full run is
           re-run alone; it counts as flaky (green, but named) only if it passes alone.
  screens  test_budget.py again with ROUGHCUT_BUDGET_SHOTS set: the screen budget (one
           header row, one blue button, no board scroll, words/controls in view) plus a PNG
           of every screen and board state for the judge to look at.
  hygiene  no model ID literal outside roughcut/config.py; no `import anthropic` outside
           roughcut/inference; no Windows path literal in code.
--quick skips the full suite (screens + hygiene only, ~1 min).

Output: $ROUGHCUT_LOOP/gate-<sha>-<stamp>/summary.md + one log per check + screens/*.png.
$ROUGHCUT_LOOP defaults to ~/roughcut-loop (outside the repo, never committed).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("ROUGHCUT_LOOP", Path.home() / "roughcut-loop"))
UV = ["uv", "run", "--with", "pytest", "--with", "fastapi", "--with", "uvicorn", "--with", "httpx",
      "--with", "numpy", "--with", "playwright", "pytest"]


def sh(*cmd: str, **kw) -> str:
    return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True, **kw).stdout.strip()


def run(cmd: list[str], cwd: Path, log: Path, env: dict | None = None, timeout: int = 3600) -> int:
    with log.open("w") as f:
        p = subprocess.run(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, env=env, timeout=timeout)
    return p.returncode


def failed_ids(junit: Path) -> list[str]:
    if not junit.exists():
        return []
    ids = []
    for case in ET.parse(junit).getroot().iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            mod = case.get("classname", "").replace(".", "/")
            ids.append(f"{mod}.py::{case.get('name')}")
    return ids


def counts(junit: Path) -> dict:
    r = ET.parse(junit).getroot()
    s = r if r.tag == "testsuite" else r.find("testsuite")
    tests, fail, err, skip = (int(s.get(k, 0)) for k in ("tests", "failures", "errors", "skipped"))
    return {"passed": tests - fail - err - skip, "failed": fail + err, "skipped": skip}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    quick = "--quick" in sys.argv
    ref = args[0] if args else "HEAD"
    sha = sh("git", "rev-parse", "--short=7", ref)
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M")
    run_dir = OUT / f"gate-{sha}-{stamp}"
    snap = OUT / f"snap-{sha}"
    run_dir.mkdir(parents=True, exist_ok=True)
    if snap.exists():
        shutil.rmtree(snap)
    snap.mkdir(parents=True)
    archive = subprocess.run(["git", "archive", sha], cwd=ROOT, check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(snap)], input=archive, check=True)

    env = dict(os.environ)
    deps = Path.home() / ".local/chromium-deps/root/usr/lib/x86_64-linux-gnu"
    if deps.exists():
        env["LD_LIBRARY_PATH"] = f"{deps}:{env.get('LD_LIBRARY_PATH', '')}"
    results: list[tuple[str, bool, str]] = []
    t_all = time.time()

    # ---- suite ----
    if not quick:
        t0 = time.time()
        junit = run_dir / "suite.xml"
        rc = run(UV + ["app/tests", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
                 snap, run_dir / "suite.log", env)
        c = counts(junit) if junit.exists() else {"passed": 0, "failed": -1, "skipped": 0}
        flaky, broken = [], []
        for tid in failed_ids(junit):
            ok = run(UV + [tid, "-q", "-p", "no:cacheprovider"], snap,
                     run_dir / f"rerun-{len(flaky) + len(broken)}.log", env, timeout=900) == 0
            (flaky if ok else broken).append(tid)
        green = c["failed"] >= 0 and not broken and (rc == 0 or c["failed"] > 0)
        line = (f"{c['passed']} passed, {c['failed']} failed in the full run, {c['skipped']} skipped"
                f" · {len(flaky)} flaky (pass alone) · {len(broken)} broken · {time.time() - t0:.0f} s")
        if flaky:
            line += "\n    flaky: " + ", ".join(flaky)
        if broken:
            line += "\n    BROKEN: " + ", ".join(broken)
        results.append(("suite", green, line))

    # ---- screens ----
    t0 = time.time()
    shots = run_dir / "screens"
    env_s = dict(env, ROUGHCUT_BUDGET_SHOTS=str(shots))
    rc = run(UV + ["app/tests/test_budget.py", "-q", "-s", "-p", "no:cacheprovider"], snap,
             run_dir / "screens.log", env_s)
    budget = [ln.strip() for ln in (run_dir / "screens.log").read_text().splitlines()
              if ln.strip().startswith("budget ")]
    pngs = sorted(p.name for p in shots.glob("*.png")) if shots.exists() else []
    results.append(("screens", rc == 0,
                    f"test_budget rc={rc} · {len(pngs)} PNGs · {time.time() - t0:.0f} s\n    "
                    + "\n    ".join(budget)))

    # ---- hygiene ----
    problems = []
    model_lit = re.compile(r"claude-(opus|sonnet|haiku|fable)-\d")
    for p in list((snap / "roughcut").rglob("*.py")) + list((snap / "app").rglob("*.py")) \
            + list((snap / "app/static").rglob("*.js")):
        rel = p.relative_to(snap).as_posix()
        text = p.read_text(errors="replace")
        if rel != "roughcut/config.py" and not rel.startswith("app/tests/") and model_lit.search(text):
            problems.append(f"model literal in {rel}")
        if not rel.startswith(("roughcut/inference", "app/tests/")) and \
                re.search(r"^\s*(import anthropic|from anthropic)", text, re.M):
            problems.append(f"anthropic import in {rel}")
        if re.search(r"['\"][A-Za-z]:\\\\", text):
            problems.append(f"Windows path literal in {rel}")
    results.append(("hygiene", not problems, "clean" if not problems else "; ".join(problems)))

    ok = all(g for _, g, _ in results)
    summary = [f"# gate {sha} · {stamp} · {'GREEN' if ok else 'RED'} · {time.time() - t_all:.0f} s",
               f"ref {ref} = {sha}; frozen copy {snap}", ""]
    summary += [f"- **{name}** {'✓' if g else '✗'} {line}" for name, g, line in results]
    summary += ["", f"screens: {shots}"]
    (run_dir / "summary.md").write_text("\n".join(summary) + "\n")
    (run_dir / "summary.json").write_text(json.dumps(
        {"sha": sha, "green": ok, "results": [{"name": n, "green": g, "line": l} for n, g, l in results]},
        indent=1))
    print("\n".join(summary))
    print(f"\n{run_dir / 'summary.md'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
