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
           The perf test (test_perf_latency::test_latency_under_load) is not in that run:
           it runs alone BEFORE the suite and again AFTER it (verdict 7 punch 3), each
           writing a JSON (perf-pre.json, perf-post.json: outcome, load, burners, its
           numbers). The better-conditioned run that measured (the lower load before the
           test added anything) counts, and its line names which and the load. A pass is
           green wherever it ran — above 2x the cores ("shared box") it runs with 0
           burners rather than skip; a fail above 2x the cores is AMBER and named, a fail
           at or below it RED; nothing measured (ROUGHCUT_PERF_FORCE_SKIP=1) is AMBER,
           never green (verdict 6 punch 9). The after run waits up to
           $ROUGHCUT_PERF_RETRY_WAIT s (300) for load <= 2x cores unless the before run
           already measured on a box that was not shared.
  screens  test_budget.py again with ROUGHCUT_BUDGET_SHOTS set: the screen budget (one
           header row, one blue button, no board scroll, words/controls in view) plus a PNG
           of every screen and board state for the judge to look at.
  journey  tools/journey.py (REDESIGN-GOAL dimension 12): one scripted editing session in
           real Chromium on the frozen copy, the suite's fixture and a scripted model —
           open, Run first pass, accept, trim ×2, razor, move, warmer, Re-run first pass
           keeping the hand edits, Export (Ctrl+M), ffprobe. The gate's own journey.py
           drives the snapshot's board, so an old rev is judged by today's journey. A PNG
           per step + journey.json in journey/. Red on the first step that fails;
           AMBER (never green) when a step passed only on its retry after Chromium
           crashed, the step named on the line (verdict 10's ruling).
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
passed but one is not proven (a skipped perf test, one that failed on a shared box above
2x the cores, or a journey step that passed only on its retry after a Chromium crash);
else RED (exit 1).
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


def journey(snap: Path, out: Path, log: Path, env: dict, timeout: int = 900) -> tuple[bool, str, bool]:
    """tools/journey.py against the frozen copy; (green, the line after "journey ✓/✗ ",
    amber). See journey_read.

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
            return False, f"timed out after {timeout} s — see {log}", False
    return journey_read(out, log, rc, time.time() - t0)


def journey_retried(res: dict, line: str) -> list[str]:
    """The steps of a green journey that passed only on a retry after Chromium crashed,
    as "step N <name>" (journey.json's `retried`; a record without one: its line)."""
    rows = res.get("retried")
    if isinstance(rows, list):
        return [f"step {r.get('n')} {r.get('name')}" for r in rows if isinstance(r, dict)]
    return re.findall(r"(step \d+ \S+) retried once after", line)


def journey_read(out: Path, log: Path, rc: int, took_s: float = 0.0) -> tuple[bool, str, bool]:
    """(green, the line after "journey ✓/✗ ", amber) from a finished journey's
    journey.json (else its log's last "journey " line).

    Verdict 10's ruling (REDESIGN-GOAL, 2026-10-18): a step that passed only on its retry
    after a Chromium crash is not proven (a page can crash its own renderer, so an app bug
    that crashes half the time would pass three times in four). Such a journey is green
    but AMBER, and its line says which step and why: the run is never GREEN on a retry."""
    line, slow, res = "", None, {}
    try:
        res = json.loads((out / "journey.json").read_text())
        line, slow = res.get("line", ""), res.get("slow")
    except (OSError, ValueError):
        said = [ln for ln in log.read_text(errors="replace").splitlines() if ln.startswith("journey ")]
        line = said[-1] if said else ""
    if not line.startswith(("journey ✓ ", "journey ✗ ")):
        return False, f"step 0 setup: no result line (rc={rc}, {took_s:.0f} s) — see {log}", False
    green = rc == 0 and line.startswith("journey ✓ ")
    retried = journey_retried(res, line) if green else []
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
    if retried:
        line += (f" → the journey is AMBER: {', '.join(retried)} passed only on a retry after"
                 " Chromium crashed (verdict 10: a page can crash its own renderer), not proven")
    return green, line[len("journey ✓ "):], bool(green and retried)


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


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def perf_conditions(rec: dict) -> float | None:
    """The box's load before the perf test added anything: the test's own 1-minute reading
    just before its burners (`load_pre`, taken after it has let its fixture's 4K encodes
    drain for up to ROUGHCUT_PERF_SETTLE s whenever it read shared). Older revs' records
    have no `load_pre`: the gate's reading just before it launched the run
    (`load_launch`), else the fixture's `load_setup`."""
    for k in ("load_pre", "load_launch", "load_setup"):
        v = _num(rec.get(k))
        if v is not None:
            return v
    return None


def perf_shared(rec: dict, cores: int) -> bool:
    """Wave 10 (verdict 7 punch 3): a SHARED box is one already above 2x its cores before
    the test added anything. Other loops own it: a fail there says how busy it was."""
    c = perf_conditions(rec)
    return c is not None and c > 2 * cores


def perf_said(rec: dict | None, cores: int | None = None) -> str:
    """One perf record (test_perf_latency's $ROUGHCUT_PERF_JSON) in words, with its load."""
    if rec is None:
        return "no record: the test did not run (not collected, or no Playwright)"
    cores = cores or rec.get("cores") or os.cpu_count() or 4
    o = str(rec.get("outcome")).upper() if rec.get("outcome") != "passed" else "passed"
    if rec.get("outcome") not in ("passed", "failed"):
        return f"{o} at load {rec.get('load_setup')} on {rec.get('cores')} cores — {rec.get('reason')}"
    c = perf_conditions(rec)
    f = rec.get("facts") or {}
    play = f.get("play") or {}
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None   # noqa: E731
    shared = " (shared box: above 2× the cores before it started)" if perf_shared(rec, cores) else ""
    at = f"{c:.1f}" if c is not None else "?"
    s = _num(rec.get("settled_s"))
    settle = f" after {s:.0f} s letting its fixture's encodes drain" if s and s >= 1 else ""
    # verdict 8 punch 2: the graded canvas (what a coloured shot shows), same 67 ms limit
    cvs = f.get("canvas") or ({"1440": play["canvas"]} if play.get("canvas") else {})
    canvas = (" · graded canvas worst gap " + ", ".join(
        f"{(c or {}).get('longest_ms')} ms at {(c or {}).get('fps')} fps ({size})" for size, c in cvs.items())
        if cvs else " · graded canvas not measured")
    return (f"{o} at load {at} with {rec.get('burners', '?')} burners{shared}, {cores} cores"
            f" (load {rec.get('load_launch', '?')} at launch, {rec.get('load_pre', '?')} before it"
            f" measured{settle}; {rec.get('load_start', '?')} → {rec.get('load_end', '?')} while measuring)"
            f" · worst gap {play.get('longest_gap_ms')} ms"
            f"{canvas}"
            f" · seek median {med(f.get('seek') or [])} ms")


def perf_verdict(runs: list[tuple[str, dict | None]], cores: int) -> tuple[bool, bool, str]:
    """(ok, amber, line) from the perf test's runs, e.g. [("before the suite", rec),
    ("after the suite", rec)]. Wave 10 (verdict 7 punch 3):

      * the run that COUNTS is the better-conditioned one that measured (passed or failed):
        the lowest `perf_conditions` load; on a tie the earlier run. Its result stands even
        when the other run did better (a fail at the quieter moment is not rescued by a
        pass at a busier one);
      * passed → green, wherever it ran: on a shared box (> 2x cores before it started,
        0 burners) the line says the load;
      * failed at > 2x cores → AMBER, named (the box was not ours), never green;
      * failed at <= 2x cores → RED;
      * nothing measured (a skip, forced by ROUGHCUT_PERF_FORCE_SKIP=1, or no record)
        → AMBER: a skipped perf test is not a pass (verdict 6 punch 9)."""
    name = "perf " + PERF_ID.split("::")[1]
    said = [f"{label}: {perf_said(rec, cores)}" for label, rec in runs]
    measured = [(i, label, rec) for i, (label, rec) in enumerate(runs)
                if rec is not None and rec.get("outcome") in ("passed", "failed")]
    if not measured:
        return True, True, (f"{name}: " + "; ".join(said)
                            + " → the suite is AMBER: a skipped perf test is not a pass")
    i, label, rec = min(measured, key=lambda m: (perf_conditions(m[2]) if perf_conditions(m[2])
                                                is not None else float("inf"), m[0]))
    others = [s for j, s in enumerate(said) if j != i]
    why = "the better-conditioned run" if len(measured) > 1 else "the only run that measured"
    line = (f"{name}: {perf_said(rec, cores)} — counted: {label} ({why})"
            + (" · " + "; ".join(others) if others else ""))
    if rec["outcome"] == "passed":
        return True, False, line
    if perf_shared(rec, cores):
        return True, True, (line + " → the suite is AMBER: it failed on a shared box (above 2× the"
                            " cores before it started, other loops' load), not a pass and not"
                            " the app's red")
    return False, False, line


def perf_run(snap: Path, run_dir: Path, env: dict, tag: str) -> dict | None:
    """test_latency_under_load alone on the frozen copy; its record, with the load the gate
    read just before launching it (`load_launch`) added and written back."""
    pj = run_dir / f"perf-{tag}.json"
    launch = round(_load(), 2)
    try:
        run(UV + [PERF_ID, "-q", "-rs", "-p", "no:cacheprovider"], snap, run_dir / f"perf-{tag}.log",
            dict(env, ROUGHCUT_PERF_JSON=str(pj)), timeout=900)
    except subprocess.TimeoutExpired:
        pass
    rec = _perf_rec(pj)
    if rec is not None:
        rec["load_launch"] = launch
        pj.write_text(json.dumps(rec, indent=1))
    return rec


def perf_forced(env: dict) -> bool:
    return any(e.get("ROUGHCUT_PERF_FORCE_SKIP", "").strip() not in ("", "0") for e in (os.environ, env))


def perf_after(snap: Path, run_dir: Path, env: dict, before: dict | None) -> tuple[dict | None, float]:
    """The after-the-suite run. Unless the skip is forced or the before run already measured
    on a box that was not shared, it first waits (up to $ROUGHCUT_PERF_RETRY_WAIT s) for the
    load to fall to 2x the cores: a quieter moment can only make the counted run stricter."""
    cores = os.cpu_count() or 4
    t0 = time.time()
    good = before is not None and before.get("outcome") in ("passed", "failed") \
        and not perf_shared(before, cores)
    if not perf_forced(env) and not good:
        print(f"perf: waiting for load <= {2 * cores} (up to {PERF_RETRY_WAIT_S} s) before the"
              " after-the-suite run…", flush=True)
        while _load() > 2 * cores and time.time() - t0 < PERF_RETRY_WAIT_S:
            time.sleep(15)
    waited = time.time() - t0
    return perf_run(snap, run_dir, env, "post"), waited


def run_status(results: list[tuple[str, bool, str]], amber: set[str]) -> tuple[str, int]:
    """GREEN (exit 0) only when every check is green and none is amber; AMBER (exit 2) when
    every check passed but one is not proven; else RED (exit 1)."""
    if not all(g for _, g, _ in results):
        return "RED", 1
    if amber:
        return "AMBER", 2
    return "GREEN", 0


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
    # No D-Bus for anything the gate starts (wave 13 SMALL4): a GNOME Shell crash restarts the
    # user bus and every Chromium connected to it aborts ("FATAL:dbus/bus.cc … Aborting.",
    # SIGTRAP), which killed the verdict-9 gate's journey at step 15. The suite, the screens
    # and boot need no bus; the journey already gives its own browser none.
    env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/nonexistent/roughcut-gate-no-bus"
    env["DBUS_SYSTEM_BUS_ADDRESS"] = "unix:path=/nonexistent/roughcut-gate-no-bus"
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
        # wave 10 (verdict 7 punch 3): the perf test runs alone BEFORE the suite heats the
        # box and again AFTER it, not inside it; the better-conditioned run counts
        print("perf: test_latency_under_load before the suite…", flush=True)
        perf_before = perf_run(snap, run_dir, env, "pre")
        junit = run_dir / "suite.xml"
        suite = os.environ.get("ROUGHCUT_GATE_SUITE", "app/tests")
        rc = run(UV + [suite, "-q", "-p", "no:cacheprovider", f"--junitxml={junit}",
                       "--deselect", PERF_ID], snap, run_dir / "suite.log", env)
        c = counts(junit) if junit.exists() else {"passed": 0, "failed": -1, "skipped": 0}
        flaky, broken = [], []
        for tid in failed_ids(junit):
            n = len(flaky) + len(broken)
            ok = run(UV + [tid, "-q", "-p", "no:cacheprovider"], snap, run_dir / f"rerun-{n}.log",
                     env, timeout=900) == 0
            (flaky if ok else broken).append(tid)
        print("perf: test_latency_under_load after the suite…", flush=True)
        perf_after_rec, waited = perf_after(snap, run_dir, env, perf_before)
        after_label = "after the suite" + (f" (waited {waited:.0f} s for a quieter box)"
                                           if waited >= 1 else "")
        # verdict 6 punch 9 + verdict 7 punch 3: see perf_verdict
        perf_ok, perf_amber, perf_line = perf_verdict(
            [("before the suite", perf_before), (after_label, perf_after_rec)], os.cpu_count() or 4)
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
        j_green, j_line, j_amber = journey(snap, jdir, run_dir / "journey.log", env)
        results.append(("journey", j_green, j_line))
        if j_amber:
            amber.add("journey")

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

    status, code = run_status(results, amber)
    ok = code == 0
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
    return code


if __name__ == "__main__":
    sys.exit(main())
