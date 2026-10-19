"""tools/journey.py's contract with tools/gate.py, and its new steps' mutation checks.

The journey itself is driven by the gate on the frozen snapshot (its `journey` line);
running all twenty-four steps again inside the suite would only spend a minute or two on
what the gate already measures. What the gate relies on is checked here: a usage error is rc 2,
anything that stops the journey is rc 1 with a `journey ✗ step K <name>: <why>` line and
a journey.json that says so, and gate.journey() turns that into its summary line. And
the GoPro-shaped fixture is what the upright steps need it to be: a picture read the
right way up passes, the same picture as stored (upside down) fails.

The steps wave 12 added for Karl's K3 (import, drop, ask, ai-menu, and the re-run's
new-clips offer) are proven not vacuous at the bottom of this file: one product change
that breaks a step's feature, a short session that reaches the step (`--steps`), and the
journey must go red AT that step, with that step's own reason.

And when Chromium goes away under a step (verdict 9, punch 1), the step's line says why;
a crash of Chromium itself (its session bus killed under it, as in the gate; the renderer)
is retried once and said in the gate's line, which the gate then reads as AMBER (verdict
10: a pass on a retry is not proven); a browser killed from outside is not retried.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JOURNEY = ROOT / "tools" / "journey.py"


def test_no_arguments_is_a_usage_error():
    r = subprocess.run([sys.executable, str(JOURNEY)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "journey.py <snapshot dir> <out dir>" in r.stdout


def test_an_unknown_step_is_a_usage_error(tmp_path):
    r = subprocess.run([sys.executable, str(JOURNEY), str(tmp_path), str(tmp_path / "out"),
                        "--steps=open,no-such-step"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "no such step: no-such-step" in r.stdout
    assert not (tmp_path / "out").exists()


def test_the_steps_are_the_plan_in_order():
    j = _journey_module()
    assert j.STEPS[:2] == ("open", "import")
    assert j.STEPS.index("warmer") < j.STEPS.index("drop") < j.STEPS.index("ask") \
        < j.STEPS.index("ai-menu") < j.STEPS.index("rerun-panel") < j.STEPS.index("rerun-accept")
    assert len(j.STEPS) == len(set(j.STEPS)) == 24
    # the gate's own words for the step count come from the docstring's list
    for k, name in enumerate(j.STEPS, 1):
        assert f"  {k:02d} {name} " in j.__doc__, name


def test_the_scripted_model_answers_an_ask_from_the_sequence_it_is_shown():
    """The AI prompt's call (its schema asks for a `kind`) gets four ops in three
    families on the shots the prompt lists; fewer than three shots, words instead."""
    j = _journey_module()
    prompt = ("The editor asked:\n“x”\n\n## Scope\nThe whole sequence\n\n## The sequence\n"
              "5 shots, 7.00 s (00:00:07:00) at 24 fps.\n"
              "   #  id  clip  clip range  film time  [effect controls, colour, why]\n"
              "    1  g01  CLIP_B.MP4  clip 4.40–6.00 s  film 0.00–1.60 s (00:00:00:00–00:00:01:14)\n"
              "    2  g02  CLIP_A.MP4  clip 0.50–2.00 s  film 1.60–3.10 s (00:00:01:14–00:00:03:02)  [hand balance]\n"
              "    3  g03  CLIP_B.MP4  clip 2.40–3.20 s  film 3.10–3.90 s (00:00:03:02–00:00:03:22)\n"
              "    4  g04  CLIP_B.MP4  clip 3.20–4.00 s  film 3.90–4.70 s (00:00:03:22–00:00:04:17)\n"
              "    5  g05  CLIP_C.MP4  clip 0.90–4.00 s  film 4.70–7.80 s (00:00:04:17–00:00:07:19)\n"
              "Markers: none\n\n## The clips in the project\n  CLIP_A.MP4  6.00 s\n")
    ops = j.assist_ops(prompt)
    assert ops == [{"op": "razor", "id": "g01", "at": 0.8},
                   {"op": "set", "id": "g03", "prop": "temperature", "value": -30},
                   {"op": "transition", "a": "g04", "b": "g05", "kind": "cross_dissolve", "dur": 0.5},
                   {"op": "set", "id": "g03", "prop": "exposure", "value": 0.3}]
    assert ops[j.REJECT_OP]["prop"] == "temperature"
    two = prompt.split("    3  g03")[0] + "Markers: none\n"
    assert j.assist_ops(two) is None


def test_a_snapshot_it_cannot_set_up_is_red_at_step_1_with_a_reason(tmp_path):
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(JOURNEY), str(tmp_path / "no-such-snapshot"), str(out)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 1, r.stdout + r.stderr
    last = r.stdout.strip().splitlines()[-1]
    assert last.startswith("journey ✗ step 1 setup: "), last
    rec = json.loads((out / "journey.json").read_text())
    assert rec["ok"] is False and rec["line"] == last and rec["steps"] == []


def test_the_gate_reads_the_journeys_line(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import gate

    empty = tmp_path / "empty-snapshot"         # a snapshot with no board in it
    empty.mkdir()
    green, line, amber = gate.journey(empty, tmp_path / "j",
                                      tmp_path / "journey.log", env=None, timeout=300)
    assert not green and not amber
    assert line.startswith("step 1 setup: "), line
    # the gate adds what it measured on the server (wave 8 SAVESAFE): nothing, with no board
    said, measured = line.split(" · slowest request", 1)
    assert measured == ": not measured (the server has no /api/debug/slow)", measured
    assert (tmp_path / "journey.log").read_text().strip().splitlines()[-1] == "journey ✗ " + said


def _journey_module():
    spec = importlib.util.spec_from_file_location("journey_tool_under_test", JOURNEY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_rotated_fixture_reads_upright_only_when_its_matrix_is_applied(tmp_path):
    np = pytest.importorskip("numpy")
    j = _journey_module()
    clip = tmp_path / j.ROT
    facts = j.make_rotated_clip(clip, tmp_path / "raw")
    assert facts["codec"] == "hevc" and facts["r_frame_rate"] == "60000/1001"
    assert abs(abs(float(facts["rotation"])) - 180) < 0.5, facts
    # displayed (ffmpeg applies the matrix, as a correct monitor must): upright
    seen = j.quadrants(j.file_frame_rgb(clip, 1.0), "the displayed frame")
    assert {k: v["looks"] for k, v in seen.items()} == {k: k for k in j.QUADS}
    # stored (the matrix ignored, the pre-ORIENT bug): every quadrant reads wrong
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-noautorotate", "-ss", "1",
                          "-i", str(clip), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    stored = np.frombuffer(raw, np.uint8).reshape(180, 320, 3)
    with pytest.raises(j.StepFailed, match="is not upright: tl looks br"):
        j.quadrants(stored, "the stored frame")


# ---------------------------------------------------------------- the mutation checks
#
# A copy of this tree (the dirs the board runs from) with ONE product change that breaks
# what a new step checks; a short session (journey.py --steps) that reaches that step;
# the journey must go red AT that step with that step's own reason, every step before it
# green. The footage is encoded once, by a control session on the unchanged copy (which
# must be green), and copied into each mutant's session (--fixture-from). One browser at
# a time, ~15-30 s a session. A mutant whose text is gone from its file fails with a
# line saying so: update the mutant with the code it was written against.

SNAP_DIRS = ("app", "roughcut", "research", "assets")
CONTROL = "open,import,drop,ai-menu"
ASK = "open,firstpass,run,reject,accept,ask"

# name: (file, the text, what it becomes, the session, the step that goes red, its reason)
MUTANTS = {
    # Ctrl+I's handler opens nothing (as a stubbed rcImport.open would)
    "ctrl-i-opens-nothing": (
        "app/static/import.js",
        "    e.stopImmediatePropagation();\n    open();\n  }, true);",
        "    e.stopImmediatePropagation();\n  }, true);",
        "open,import", "import", "Ctrl+I opened no Import dialog"),
    # a file dragged from the OS finds no drop target on the Project panel
    "no-drop-on-the-project-panel": (
        "app/static/import.js",
        "attach(document.getElementById('pProject'), 'project');",
        "void 0;",
        "open,drop", "drop", "showed no drop overlay"),
    # Accept applies every op of the answer, the one the editor rejected among them
    "the-review-applies-a-rejected-op": (
        "app/static/actions.js",
        "    if (!R.res || R.res.kind !== 'ops') return null;\n    const idx = accepted();",
        "    if (!R.res || R.res.kind !== 'ops') return null;\n    const idx = R.ops.map((_, i) => i);",
        ASK, "ask", "the rejected op was applied"),
    # one Accept lands as two undo entries
    "one-accept-is-two-undo-steps": (
        "app/static/actions.js",
        "    const out = applyOps(idx.map((i) => R.ops[i]), { label });",
        "    applyOps(idx.slice(0, 1).map((i) => R.ops[i]), { label });\n"
        "    const out = applyOps(idx.slice(1).map((i) => R.ops[i]), { label });",
        ASK, "ask", "one Ctrl+Z did not take back every AI edit"),
    # an AI menu item greyed out that does not say why
    "an-ai-menu-item-greyed-without-a-reason": (
        "app/static/aiprompt.js",
        "set('fx', 'Design an effect…', 'Designed Effects');",
        "set('fx', 'Design an effect…', 'Designed Effects', true, '');",
        "open,ai-menu", "ai-menu", "is greyed out and says no reason why"),
    # footage added after the pass is never offered to a re-run
    "no-new-clips-offer": (
        "app/static/firstpass.js",
        "const show = list.length && !FP.open",
        "const show = false && !FP.open",
        "open,firstpass,run,reject,accept,drop,rerun-panel", "rerun-panel",
        "offer under the first-pass control"),
}


def _session(snap: Path, out: Path, steps: str, fixture: Path | None = None,
             timeout: int = 420, env: dict | None = None) -> tuple[int, dict, str]:
    cmd = [sys.executable, str(JOURNEY), str(snap), str(out), f"--steps={steps}"]
    if fixture is not None:
        cmd.append(f"--fixture-from={fixture}")
    r = subprocess.run(cmd, cwd=snap, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **(env or {})})
    try:
        rec = json.loads((out / "journey.json").read_text())
    except (OSError, ValueError):
        rec = {}
    return r.returncode, rec, r.stdout + r.stderr


@pytest.fixture(scope="module")
def unchanged(tmp_path_factory):
    """The board's dirs copied once, and a green control session on them (it encodes the
    footage the mutants' sessions copy)."""
    pytest.importorskip("playwright")
    if shutil.which("ffmpeg") is None:
        pytest.skip("no ffmpeg")
    base = tmp_path_factory.mktemp("journey-mutants")
    snap = base / "snap"
    for d in SNAP_DIRS:
        if (ROOT / d).is_dir():
            shutil.copytree(ROOT / d, snap / d, ignore=shutil.ignore_patterns(
                "__pycache__", ".pytest_cache", "*.pyc"))
    rc, rec, said = _session(snap, base / "control", CONTROL)
    assert rc == 0 and rec.get("ok"), f"the unchanged copy is not green on {CONTROL}: " \
        f"{rec.get('line')}\n{said[-2000:]}"
    return snap, base / "control" / "fixture"


@pytest.mark.parametrize("name", list(MUTANTS))
def test_a_new_step_goes_red_at_itself_when_its_feature_is_broken(name, unchanged, tmp_path):
    path, text, becomes, steps, step, why = MUTANTS[name]
    snap0, fixture = unchanged
    snap = tmp_path / "snap"
    shutil.copytree(snap0, snap, symlinks=True)
    src = (snap / path).read_text(encoding="utf-8")
    assert src.count(text) == 1, f"mutant {name}: its text is gone from {path} (or not unique) — " \
                                 "update MUTANTS in test_journey_tool.py with the code it breaks now"
    (snap / path).write_text(src.replace(text, becomes), encoding="utf-8")
    rc, rec, said = _session(snap, tmp_path / "out", steps, fixture)
    order = steps.split(",")
    k = order.index(step) + 1
    line = rec.get("line", "")
    assert rc == 1 and line.startswith(f"journey ✗ step {k} {step}: "), \
        f"mutant {name} is not red at step {k} {step}: {line or said[-1500:]}"
    ran = rec["steps"]
    assert [s["name"] for s in ran] == order[:k] and all(s["ok"] for s in ran[:-1]), \
        [(s["name"], s["ok"], s["why"]) for s in ran]
    assert why in ran[-1]["why"], f"mutant {name}: step {step} is red for another reason: {ran[-1]['why']}"


# ---------------------------------------------------------------- Chromium going away
#
# Verdict 9, punch 1: in the gate the journey died at step 15 on "TargetClosedError: …
# Target page, context or browser has been closed" and passed alone. The cause (journalctl):
# GNOME Shell crashed, the user's D-Bus restarted, and Chromium, connected to it, aborted.
# The journey now names why a page went away, retries a step once only when Chromium itself
# crashed, and gives its browser no D-Bus at all.

# the browser's lines around the gate's step-15 death (gate-7436562-20261018T1747's
# journey.json trace), in the shape the driver writes them with DEBUG=pw:browser, plus one
# line of the noise a bus-less Chromium prints at start
GATE_LOG = """\
2026-10-18T22:28:27.840Z pw:browser <launched> pid=2132202
2026-10-18T22:28:27.849Z pw:browser [pid=2132202][err] [1018/182827.848689:WARNING:sandbox/policy/linux/sandbox_linux.cc:405] InitializeSandbox() called with multiple threads in process gpu-process.
2026-10-18T22:28:28.205Z pw:browser [pid=2132202][err] [1018/182828.205379:ERROR:gpu/command_buffer/service/gl_utils.cc:431] [.WebGL-0x298c00fda200]GL Driver Message (OpenGL, Performance, GL_CLOSE_PATH_NV, High): GPU stall due to ReadPixels
2026-10-18T22:28:29.001Z pw:browser [pid=2132202][err] [1018/182829.001000:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Failed to connect to socket /nonexistent/roughcut-journey-no-bus: No such file or directory
2026-10-18T22:28:49.096Z pw:browser [pid=2132202][err] [1018/182849.096232:FATAL:dbus/bus.cc:1245] D-Bus connection was disconnected. Aborting.
2026-10-18T22:28:49.230Z pw:browser [pid=2132202] <process did exit: exitCode=null, signal=SIGTRAP>
"""


def test_the_browser_log_says_how_chromium_ended(tmp_path):
    j = _journey_module()
    log = tmp_path / "browser.log"
    log.write_text(GATE_LOG, encoding="utf-8")
    said = j.browser_said(log)
    assert said["pid"] == 2132202
    assert said["exit"] == {"code": None, "signal": "SIGTRAP"}
    # the reason, with its place in Chromium; GPU chatter and the bus-less noise left out
    assert said["lines"] == ["FATAL:dbus/bus.cc:1245 D-Bus connection was disconnected. Aborting."]
    assert "SIGTRAP" in j.CRASH_SIGNALS and "SIGKILL" not in j.CRASH_SIGNALS
    # read from a byte offset: an earlier step's lines are not this step's
    assert j.browser_said(log, len(GATE_LOG.encode()))["lines"] == []


def test_the_browser_log_is_read_per_browser_and_as_it_died(tmp_path):
    """A retry's fresh browser writes to the same log: each browser's end is read by its
    pid, and only what it said in its last 5 s counts (not an old line from an hour before)."""
    j = _journey_module()
    log = tmp_path / "browser.log"
    log.write_text(GATE_LOG
                   + "2026-10-18T22:29:00.000Z pw:browser <launched> pid=4000\n"
                   + "2026-10-18T22:29:01.000Z pw:browser [pid=4000][err] [1018/182901.000000:ERROR:media/x.cc:12]"
                     " a complaint ten minutes before\n"
                   + "2026-10-18T22:39:01.000Z pw:browser [pid=4000] <process did exit: exitCode=null, signal=SIGKILL>\n",
                   encoding="utf-8")
    assert j.browser_said(log)["pid"] == 4000                      # the last one launched
    gate = j.browser_said(log, 0, 2132202)
    assert gate["exit"] == {"code": None, "signal": "SIGTRAP"}
    assert gate["lines"] == ["FATAL:dbus/bus.cc:1245 D-Bus connection was disconnected. Aborting."]
    later = j.browser_said(log, 0, 4000)
    assert later["exit"] == {"code": None, "signal": "SIGKILL"} and later["lines"] == []


def test_the_journeys_browser_has_no_d_bus():
    j = _journey_module()
    env = j.browser_env()
    assert env["DBUS_SESSION_BUS_ADDRESS"] == env["DBUS_SYSTEM_BUS_ADDRESS"] == j.NO_BUS
    assert not Path(j.NO_BUS.split("=", 1)[1]).exists()


CRASHED = {
    # the gate's death itself: the browser's session bus goes away under it
    "bus": ("Chromium crashed (browser process SIGTRAP · FATAL:dbus/bus.cc:1245 "
            "D-Bus connection was disconnected. Aborting.)"),
    # the renderer: chrome://crash
    "renderer": "the page's renderer crashed (the browser still up",
}


@pytest.mark.parametrize("how", list(CRASHED))
def test_a_chromium_crash_under_a_step_is_named_and_the_step_retried_once(how, unchanged, tmp_path):
    if how == "bus" and shutil.which("dbus-daemon") is None:
        pytest.skip("no dbus-daemon")
    snap, fixture = unchanged
    rc, rec, said = _session(snap, tmp_path / "out", "open,firstpass", fixture,
                             env={"ROUGHCUT_JOURNEY_CRASH": f"firstpass:{how}"})
    line = rec.get("line", "")
    assert rc == 0 and line.startswith("journey ✓ 2 steps"), f"{line or said[-1500:]}"
    # the step's own line says what happened and that it ran again
    step = rec["steps"][1]
    assert step["name"] == "firstpass" and step["ok"], step
    assert step["why"].startswith(CRASHED[how]) and step["why"].endswith("; retried: ✓"), step["why"]
    assert step["retried"]["first_why"], step["retried"]
    # and the gate's line carries it: a retry is never silent
    assert f" · step 2 firstpass retried once after {CRASHED[how]}" in line, line
    assert rec["retried"] == [{"n": 2, "name": "firstpass", "words": step["retried"]["after"]["words"],
                               "ok": True}]
    # verdict 10's ruling: a pass on a retry is not proven: the journey says AMBER, and the
    # gate reads it so (its run is AMBER, never GREEN)
    assert rec["amber"] == "1 step passed only on a retry after Chromium crashed: step 2 firstpass"
    assert line.endswith(" · AMBER: " + rec["amber"]), line
    sys.path.insert(0, str(ROOT / "tools"))
    import gate
    green, gline, amber = gate.journey_read(tmp_path / "out", tmp_path / "journey.log", rc)
    assert green and amber, gline
    assert "→ the journey is AMBER: step 2 firstpass passed only on a retry after Chromium crashed" \
        in gline, gline
    assert gate.run_status([("journey", green, gline)], {"journey"}) == ("AMBER", 2)


def test_a_browser_killed_from_outside_is_named_and_not_retried(unchanged, tmp_path):
    snap, fixture = unchanged
    rc, rec, said = _session(snap, tmp_path / "out", "open,firstpass", fixture,
                             env={"ROUGHCUT_JOURNEY_CRASH": "firstpass:kill"})
    line = rec.get("line", "")
    assert rc == 1 and line.startswith("journey ✗ step 2 firstpass: "), f"{line or said[-1500:]}"
    assert "the browser was killed from outside (SIGKILL: not a crash" in line, line
    assert "retried" not in line and rec["retried"] == [] and rec["amber"] is None, line
