#!/usr/bin/env python3
"""The closed-loop gate: every automated check in ROUGHCUT-GOAL.md, on a frozen commit.

    python3 tools/gate.py [ref=HEAD] [--quick]

Why a frozen copy: the board's suite writes into its own temp dirs, but a lane editing the
tree while the gate runs would change what is measured halfway through. So `ref` is
`git archive`d into $ROUGHCUT_LOOP/snap-<sha> and every check runs there; the working tree
is never touched.

Checks (each one line in summary.md, exit 0 only if all are green):
  browser  `playwright install chromium` with the suite's own Playwright, then one launch,
           so a Playwright bump never shows up as dozens of "flaky" browser tests. Red
           here means suite and screens are not run (they would only measure the box).
  suite    pytest app/tests (API + real Chromium). A test that fails in the full run is
           re-run alone; it counts as flaky (green, but named) only if it passes alone.
           The perf test (test_perf_latency::test_latency_under_load) writes a JSON
           (perf-*.json: outcome, load, burners, its numbers); its line names it with the
           load it ran at. Skipped (load-aware, or ROUGHCUT_PERF_FORCE_SKIP=1) it is
           retried once after the rest of the suite at a quieter moment, and a second
           skip makes the suite AMBER, not green (verdict 6 punch 9).
  screens  test_budget.py again with ROUGHCUT_BUDGET_SHOTS set: the screen budget (one
           header row, one blue button, no board scroll, words/controls in view) plus a PNG
           of every screen and board state for the judge to look at.
  journey  tools/journey.py (REDESIGN-GOAL dimension 12): one scripted editing session in
           real Chromium on the frozen copy, the suite's fixture and a scripted model —
           open, Run first pass, accept, trim ×2, razor, move, warmer, Re-run first pass
           keeping the hand edits, Export (Ctrl+M), ffprobe. The gate's own journey.py
           drives the snapshot's board, so an old rev is judged by today's journey. A PNG
           per step + journey.json in journey/. Red on the first step that fails.
  boot     "board shows its cut N s after a proxy lands" (verdict 7 blocker 3): the suite's
           test_boot_wait.py writes $ROUGHCUT_BOOT_JSON (boot.json) — a fresh page on a
           bin whose proxy just landed, the colour measurement patched to 10 s; green when
           the timeline's blocks and the Program frame are up in <= BOOT_CUT_MAX_S. No
           record (--quick, a narrowed suite) runs the test alone first.
  hygiene  no model ID literal outside roughcut/config.py; no `import anthropic` outside
           roughcut/inference; no Windows path literal in code.
--quick skips the full suite (screens + journey + hygiene, ~2 min; the journey alone is
~40-60 s on the fixture, so it runs in both).

The run is GREEN (exit 0) only when every check is green; AMBER (exit 2) when every check
passed but one is not proven (a skipped perf test); else RED (exit 1).
ROUGHCUT_GATE_SUITE=<path> narrows the suite (for checking the gate itself; the summary
says so).

Output: $ROUGHCUT_LOOP/gate-<sha>-<stamp>/summary.md + one log per check + screens/*.png
+ journey/NN-step.png + journey/journey.json.
$ROUGHCUT_LOOP defaults to ~/roughcut-loop (outside the repo, never committed).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import signal
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


def journey(snap: Path, out: Path, log: Path, env: dict, timeout: int = 900) -> tuple[bool, str]:
    """tools/journey.py against the frozen copy; (green, the line after "journey ✓/✗ ").

    Its own process group, so a timeout takes the browser, the server and any ffmpeg
    the journey started with it."""
    t0 = time.time()
    cmd = [sys.executable, str(ROOT / "tools" / "journey.py"), str(snap), str(out)]
    with log.open("w") as f:
        p = subprocess.Popen(cmd, cwd=snap, stdout=f, stderr=subprocess.STDOUT, env=env,
                             start_new_session=True)
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            return False, f"timed out after {timeout} s — see {log}"
    line, slow = "", None
    try:
        res = json.loads((out / "journey.json").read_text())
        line, slow = res.get("line", ""), res.get("slow")
    except (OSError, ValueError):
        said = [ln for ln in log.read_text(errors="replace").splitlines() if ln.startswith("journey ")]
        line = said[-1] if said else ""
    if not line.startswith(("journey ✓ ", "journey ✗ ")):
        return False, f"step 0 setup: no result line (rc={rc}, {time.time() - t0:.0f} s) — see {log}"
    green = rc == 0 and line.startswith("journey ✓ ")
    # wave 8 SAVESAFE: the slowest request the server answered during the journey (to its
    # first byte; `GET /api/debug/slow`), red over JOURNEY_SLOWEST_S
    top = (slow or {}).get("slowest_api") if isinstance(slow, dict) else None
    if top:
        s = float(top.get("ms") or 0) / 1000.0
        line += f" · slowest request {s:.2f} s {top.get('method')} {top.get('path')}"
        if s > JOURNEY_SLOWEST_S:
            green = False
            line += f" (over {JOURNEY_SLOWEST_S:g} s)"
    else:
        line += " · slowest request: not measured (the server has no /api/debug/slow)"
    return green, line[len("journey ✓ "):]


JOURNEY_SLOWEST_S = 1.0


PERF_ID = "app/tests/test_perf_latency.py::test_latency_under_load"
PERF_RETRY_WAIT_S = int(os.environ.get("ROUGHCUT_PERF_RETRY_WAIT", "300"))


def _load() -> float:
    try:
        return os.getloadavg()[0]
    except OSError:
        return -1.0


def _perf_rec(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def perf_said(rec: dict | None) -> str:
    """One perf record (test_perf_latency's $ROUGHCUT_PERF_JSON) in words, with its load."""
    if rec is None:
        return "no record: the test did not run (not collected, or no Playwright)"
    o = str(rec.get("outcome")).upper() if rec.get("outcome") != "passed" else "passed"
    if rec.get("outcome") == "skipped":
        return f"{o} at load {rec.get('load_setup')} on {rec.get('cores')} cores — {rec.get('reason')}"
    f = rec.get("facts") or {}
    play = f.get("play") or {}
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None   # noqa: E731
    return (f"{o} at load {rec.get('load_start', rec.get('load_setup'))} → {rec.get('load_end', '?')}"
            f" with {rec.get('burners', '?')} burners ({rec.get('load_setup')} before them,"
            f" {rec.get('cores')} cores) · worst gap {play.get('longest_gap_ms')} ms"
            f" · seek median {med(f.get('seek') or [])} ms")


def perf_check(snap: Path, run_dir: Path, env: dict, jsons: list[Path]) -> tuple[bool, bool, str]:
    """(ok, amber, line) for test_latency_under_load — verdict 6 punch 9: the perf gate
    cannot pass by skipping. The last record (the full run's, or its re-run alone when it
    failed there) decides; a skip, forced or load-aware, or no record at all, is retried
    once after the rest of the suite, once the load is under 2x the cores (up to
    $ROUGHCUT_PERF_RETRY_WAIT s). A retry that passes is green; one that skips again
    leaves the suite AMBER, named here; one that fails is broken."""
    recs = [r for r in (_perf_rec(p) for p in jsons if p.exists()) if r is not None]
    rec = recs[-1] if recs else None
    name = "perf " + PERF_ID.split("::")[1]
    if rec is not None and rec.get("outcome") in ("passed", "failed"):
        return rec["outcome"] == "passed", False, f"{name}: {perf_said(rec)}"
    cores = os.cpu_count() or 4
    forced = os.environ.get("ROUGHCUT_PERF_FORCE_SKIP", "").strip() not in ("", "0") or \
        env.get("ROUGHCUT_PERF_FORCE_SKIP", "").strip() not in ("", "0")
    t0 = time.time()
    if not forced:
        print(f"suite: the perf test did not measure ({perf_said(rec)}); waiting for load "
              f"<= {2 * cores} to retry it…", flush=True)
        while _load() > 2 * cores and time.time() - t0 < PERF_RETRY_WAIT_S:
            time.sleep(15)
    waited = time.time() - t0
    pj = run_dir / "perf-retry.json"
    run(UV + [PERF_ID, "-q", "-rs", "-p", "no:cacheprovider"], snap, run_dir / "perf-retry.log",
        dict(env, ROUGHCUT_PERF_JSON=str(pj)), timeout=900)
    retry = _perf_rec(pj)
    line = (f"{name}: in the full run {perf_said(rec)}; retried after the suite"
            f"{f' (waited {waited:.0f} s for a quieter box)' if waited >= 1 else ''}: {perf_said(retry)}")
    if retry is not None and retry.get("outcome") == "passed":
        return True, False, line
    if retry is not None and retry.get("outcome") == "failed":
        return False, False, line
    return True, True, line + " → the suite is AMBER: a skipped perf test is not a pass"


def browser_preflight(cwd: Path, log: Path, env: dict) -> tuple[bool, str]:
    """Make Playwright's Chromium the one the suite's Playwright wants, before the suite.

    Verdict 1: a new Playwright wheel wanted a Chromium revision that was not on the
    box, and 37 browser tests "failed in the full run, passed alone" — counted as flaky,
    when the cause was the machine. `playwright install chromium` is a no-op when the
    right revision is there and a download when it is not; run with the same `uv run
    --with playwright` the suite resolves, so both see the same wheel. Then one launch,
    so a missing system library shows here too, not as a wall of red tests."""
    t0 = time.time()
    uv_py = ["uv", "run", "--with", "playwright", "python"]
    smoke = ("from playwright.sync_api import sync_playwright\n"
             "with sync_playwright() as p:\n"
             "    b = p.chromium.launch(); v = b.version; b.close()\n"
             "print('chromium', v, 'launches')\n")
    steps = [("version", uv_py + ["-m", "playwright", "--version"]),
             ("install", uv_py + ["-m", "playwright", "install", "chromium"]),
             ("launch", uv_py + ["-c", smoke])]
    out: dict[str, str] = {}
    with log.open("w") as f:
        for name, cmd in steps:
            f.write(f"$ {' '.join(cmd)}\n")
            f.flush()
            try:
                p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                                   timeout=900)
            except subprocess.TimeoutExpired:
                f.write("timed out after 900 s\n")
                return False, (f"cannot get a Chromium for this Playwright: `{name}` timed out "
                               f"after 900 s — see {log}")
            except OSError as e:                     # no uv on PATH
                f.write(f"{e}\n")
                return False, f"cannot get a Chromium for this Playwright: `{name}` could not start: {e}"
            f.write(p.stdout + p.stderr + f"[rc={p.returncode}]\n\n")
            said = (p.stdout.strip() or p.stderr.strip()).splitlines()
            out[name] = said[-1].strip() if said else ""
            if p.returncode != 0:
                return False, (f"cannot get a Chromium for this Playwright: `{name}` failed "
                               f"(rc={p.returncode}): {out[name][:200]} — see {log}")
    return True, f"{out.get('version', '?')} · {out.get('launch', '?')} · {time.time() - t0:.0f} s"


BOOT_ID = "app/tests/test_boot_wait.py::test_the_cut_shows_before_the_colour_is_measured"
BOOT_CUT_MAX_S = 2.0


def boot_check(snap: Path, run_dir: Path, env: dict) -> tuple[bool, str]:
    """(green, line) for "board shows its cut N s after a proxy lands" — verdict 7
    blocker 3. Reads the record test_boot_wait wrote during the suite ($ROUGHCUT_BOOT_JSON);
    with none (--quick, a narrowed suite) it runs that test alone first. Green only on a
    measured cut at or under BOOT_CUT_MAX_S from a test that passed."""
    rec_path = Path(env["ROUGHCUT_BOOT_JSON"])
    alone = ""
    if not rec_path.exists():
        run(UV + [BOOT_ID, "-q", "-p", "no:cacheprovider"], snap, run_dir / "boot.log", env,
            timeout=600)
        alone = " (run alone)"
    try:
        rec = json.loads(rec_path.read_text())
    except (OSError, ValueError):
        return False, f"board shows its cut: not measured{alone} — no record, see {run_dir / 'boot.log'}"
    cut = rec.get("cut_s")
    if cut is None:
        return False, (f"board shows its cut: not measured{alone} — the test {rec.get('outcome')}"
                       f" before the cut showed (GET /api/junk {rec.get('api_junk_s')} s)")
    green = rec.get("outcome") == "passed" and float(cut) <= BOOT_CUT_MAX_S
    return green, (f"board shows its cut {float(cut):.2f} s after a proxy lands{alone}"
                   f" (limit {BOOT_CUT_MAX_S:g} s; blocks {rec.get('blocks_s')} s, Program frame"
                   f" {rec.get('frame_s')} s; junk marks {rec.get('junk_s')} s with the colour"
                   f" measurement patched to {rec.get('measure_patch_s')} s; GET /api/junk"
                   f" {rec.get('api_junk_s')} s; load {rec.get('load')} on {rec.get('cores')} cores)"
                   f"{'' if green else ' — ' + str(rec.get('outcome')).upper()}")


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
    env["ROUGHCUT_BOOT_JSON"] = str(run_dir / "boot.json")     # test_boot_wait's record (boot_check)
    results: list[tuple[str, bool, str]] = []
    amber: set[str] = set()          # green, but not proven: the run is AMBER, not GREEN
    t_all = time.time()

    # ---- browser ----
    print("browser: making Playwright's Chromium match (playwright install chromium)…", flush=True)
    browser_ok, browser_line = browser_preflight(snap, run_dir / "browser.log", env)
    results.append(("browser", browser_ok, browser_line))
    if not browser_ok:
        # every UI test would fail on the browser, not on the code: say that once
        why = "not run: no Chromium matching this Playwright (see browser)"
        if not quick:
            results.append(("suite", False, why))
        results.append(("screens", False, why))
        results.append(("journey", False, why))

    # ---- suite ----
    if not quick and browser_ok:
        t0 = time.time()
        junit = run_dir / "suite.xml"
        perf_jsons = [run_dir / "perf-suite.json"]
        suite = os.environ.get("ROUGHCUT_GATE_SUITE", "app/tests")
        rc = run(UV + [suite, "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
                 snap, run_dir / "suite.log", dict(env, ROUGHCUT_PERF_JSON=str(perf_jsons[0])))
        c = counts(junit) if junit.exists() else {"passed": 0, "failed": -1, "skipped": 0}
        flaky, broken = [], []
        for tid in failed_ids(junit):
            n = len(flaky) + len(broken)
            pj = run_dir / f"perf-rerun-{n}.json"
            ok = run(UV + [tid, "-q", "-p", "no:cacheprovider"], snap, run_dir / f"rerun-{n}.log",
                     dict(env, ROUGHCUT_PERF_JSON=str(pj)), timeout=900) == 0
            (flaky if ok else broken).append(tid)
            if pj.exists():
                perf_jsons.append(pj)
        # verdict 6 punch 9: the perf test cannot pass by skipping (see perf_check)
        perf_ok, perf_amber, perf_line = perf_check(snap, run_dir, env, perf_jsons)
        if not perf_ok and PERF_ID not in broken:
            broken.append(PERF_ID)
        green = c["failed"] >= 0 and not broken and (rc == 0 or c["failed"] > 0)
        line = (f"{c['passed']} passed, {c['failed']} failed in the full run, {c['skipped']} skipped"
                f" · {len(flaky)} flaky (pass alone) · {len(broken)} broken · {time.time() - t0:.0f} s")
        if suite != "app/tests":
            line += f" · NARROWED to {suite} (ROUGHCUT_GATE_SUITE): not the gate's suite"
        line += "\n    " + perf_line
        if flaky:
            line += "\n    flaky: " + ", ".join(flaky)
        if broken:
            line += "\n    BROKEN: " + ", ".join(broken)
        results.append(("suite", green, line))
        if green and (perf_amber or suite != "app/tests"):
            amber.add("suite")

    # ---- screens ----
    shots = run_dir / "screens"
    if browser_ok:
        t0 = time.time()
        env_s = dict(env, ROUGHCUT_BUDGET_SHOTS=str(shots))
        rc = run(UV + ["app/tests/test_budget.py", "-q", "-s", "-p", "no:cacheprovider"], snap,
                 run_dir / "screens.log", env_s)
        budget = [ln.strip() for ln in (run_dir / "screens.log").read_text().splitlines()
                  if ln.strip().startswith("budget ")]
        pngs = sorted(p.name for p in shots.glob("*.png")) if shots.exists() else []
        results.append(("screens", rc == 0,
                        f"test_budget rc={rc} · {len(pngs)} PNGs · {time.time() - t0:.0f} s\n    "
                        + "\n    ".join(budget)))

    # ---- journey ----
    jdir = run_dir / "journey"
    if browser_ok:
        print("journey: one editing session, end to end…", flush=True)
        results.append(("journey", *journey(snap, jdir, run_dir / "journey.log", env)))

    # ---- boot (verdict 7 blocker 3) ----
    if browser_ok:
        results.append(("boot", *boot_check(snap, run_dir, env)))

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

    passing = all(g for _, g, _ in results)
    ok = passing and not amber
    status = "GREEN" if ok else "AMBER" if passing else "RED"
    summary = [f"# gate {sha} · {stamp} · {status} · {time.time() - t_all:.0f} s",
               f"ref {ref} = {sha}; frozen copy {snap}", ""]
    summary += [f"- **{name}** {'◐ AMBER' if name in amber else '✓' if g else '✗'} {line}"
                for name, g, line in results]
    summary += ["", f"screens: {shots}", f"journey: {jdir}"]
    (run_dir / "summary.md").write_text("\n".join(summary) + "\n")
    (run_dir / "summary.json").write_text(json.dumps(
        {"sha": sha, "green": ok, "status": status,
         "results": [{"name": n, "green": g and n not in amber, "amber": n in amber, "line": l}
                     for n, g, l in results]},
        indent=1))
    print("\n".join(summary))
    print(f"\n{run_dir / 'summary.md'}")
    return 0 if ok else 2 if passing else 1


if __name__ == "__main__":
    sys.exit(main())
