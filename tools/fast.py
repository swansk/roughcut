#!/usr/bin/env python3
"""Tier 1 of the gate (2026-11-14, Karl: "yes, tier the tests"): the tests a change can reach,
then the quick gate. Minutes, not the full suite's 75.

    python3 tools/fast.py [--base REF] [--list] [--no-quick] [-n N]

What changed = `git diff --name-only <base>` plus the working tree's uncommitted and new files.
The base is the upstream of the branch when it has one and is behind HEAD, else HEAD~1.

Which tests that reaches:
  app/tests/test_*.py      itself
  roughcut/<mod>.py        every test file that imports <mod> (`from roughcut import mod`,
                           `roughcut.mod`, `import mod`)
  app/server.py            every test file naming a function the diff touched (its hunk
                           headers and the `def`s it adds or changes) or a route a changed line
                           holds (`/api/...`)
  app/static/<x>.js/.css   every test file naming `<x>.js` / `<x>.css` or the stem in a UI test's
                           name (test_<x>*_ui.py)
  tools/<x>.py             every test file naming <x>
  anything else            nothing by name; the quick gate still runs

Then `tools/gate.py HEAD --quick` (screens, journey, boot, understanding, hygiene: ~2 min) on
the committed tree, unless --no-quick or the tree has uncommitted changes (the gate archives a
commit; it says so and skips).

The full suite (tools/gate.py, ~75 min) is tier 2: once per wave of merges, before a build is
handed to Karl, and always before main moves. See judge/LOOP.md "Test tiers".
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "app" / "tests"
UV = ["uv", "run", "--with", "pytest", "--with", "fastapi", "--with", "uvicorn", "--with", "httpx",
      "--with", "numpy", "--with", "playwright", "--with", "pillow"]
PERF_ID = "app/tests/test_perf_latency.py::test_latency_under_load"


def git(*a: str) -> str:
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout


def base_ref(arg: str | None) -> str:
    if arg:
        return arg
    up = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}").strip()
    if up and git("rev-list", "--count", f"{up}..HEAD").strip() not in ("", "0"):
        return up
    return "HEAD~1"


def changed(base: str) -> list[str]:
    names = set(git("diff", "--name-only", base).split())
    names |= set(git("ls-files", "--others", "--exclude-standard").split())
    return sorted(n for n in names if (ROOT / n).exists())


def server_routes(base: str) -> tuple[set[str], set[str]]:
    """The routes the diff of app/server.py can reach: a changed handler's own route, and the
    routes of handlers that call a changed helper (two levels up). Tests reach the server by
    URL, not by function name. Also the changed function names, for tests that import them."""
    diff = git("diff", "-U0", base, "--", "app/server.py")
    touched: set[str] = set()
    for line in diff.splitlines():
        m = re.match(r"@@ .* @@\s*(?:async\s+)?def\s+(\w+)", line)
        if m:
            touched.add(m.group(1))
        m = re.match(r"[+-]\s*(?:async\s+)?def\s+(\w+)", line)
        if m and not line.startswith(("+++", "---")):
            touched.add(m.group(1))
    src = (ROOT / "app/server.py").read_text(encoding="utf-8")
    funcs: dict[str, tuple[list[str], str]] = {}      # name → (its routes, its body)
    parts = re.split(r"\n(?=(?:@app\.\w+\(|(?:async\s+)?def\s+\w+))", src)
    routes: list[str] = []
    for part in parts:
        m = re.match(r"@app\.\w+\(\s*[\"']([^\"']+)", part)
        if m:
            routes.append(m.group(1))
            continue
        m = re.match(r"(?:async\s+)?def\s+(\w+)", part)
        if m:
            funcs[m.group(1)] = (routes, part)
            routes = []
    reach, frontier = set(touched), set(touched)
    for _ in range(2):                                 # callers of the changed, and theirs
        nxt = {n for n, (_, body) in funcs.items() if n not in reach
               and any(re.search(rf"\b{re.escape(f)}\(", body) for f in frontier)}
        reach |= nxt
        frontier = nxt
    out = set()
    for n in reach:
        for r in funcs.get(n, ([], ""))[0]:
            r = r.split("{")[0].rstrip("/")
            if r.count("/") >= 2:                     # "/" and "/api" would match everything
                out.add(r)
    return out, {n for n in touched if len(n) > 3}


def select(paths: list[str], base: str) -> tuple[list[Path], dict[str, list[str]]]:
    files = sorted(TESTS.glob("test_*.py"))
    text = {f: f.read_text(encoding="utf-8", errors="replace") for f in files}
    picked: dict[Path, list[str]] = {}

    def add(f: Path, why: str) -> None:
        picked.setdefault(f, []).append(why)

    for p in paths:
        path = Path(p)
        if p.startswith("app/tests/") and path.name.startswith("test_") and path.suffix == ".py":
            add(ROOT / p, "changed")
        elif p.startswith("roughcut/") and path.suffix == ".py":
            mod = path.stem
            pat = re.compile(rf"(?:from\s+roughcut(?:\.\w+)*\s+import\s+[^\n]*\b{mod}\b|roughcut\.{mod}\b|"
                             rf"^\s*import\s+{mod}\b|from\s+\.?{mod}\s+import)", re.M)
            for f, t in text.items():
                if mod != "__init__" and (pat.search(t) or f.name.startswith(f"test_{mod}")):
                    add(f, p)
        elif p == "app/server.py":
            routes, names = server_routes(base)
            for f, t in text.items():
                hits = [r for r in routes if r in t] + [n for n in names if re.search(rf"\bserver\.{re.escape(n)}\b", t)]
                if hits:
                    add(f, "server: " + ", ".join(sorted(hits)[:3]))
        elif p.startswith("app/static/") and path.suffix in (".js", ".css"):
            stem = path.stem
            for f, t in text.items():
                if path.name in t or (f.name.startswith(f"test_{stem}") and f.name.endswith("_ui.py")):
                    add(f, p)
        elif p.startswith("tools/") and path.suffix == ".py":
            for f, t in text.items():
                if f"tools/{path.name}" in t or f"tools.{path.stem}" in t or re.search(rf"\bimport\s+{path.stem}\b", t):
                    add(f, p)
    return sorted(picked), {str(k.relative_to(ROOT)): v for k, v in picked.items()}


def main() -> int:
    argv = sys.argv[1:]
    base = base_ref(argv[argv.index("--base") + 1] if "--base" in argv else None)
    workers = argv[argv.index("-n") + 1] if "-n" in argv else None
    paths = changed(base)
    tests, why = select(paths, base)
    print(f"fast: {len(paths)} changed file(s) against {base}; {len(tests)} test file(s) reach them")
    for k, v in why.items():
        print(f"  {k}  ← {'; '.join(sorted(set(v)))[:120]}")
    if "--list" in argv:
        return 0
    env = dict(os.environ)
    deps = Path.home() / ".local/chromium-deps/root/usr/lib/x86_64-linux-gnu"
    if deps.exists():
        env["LD_LIBRARY_PATH"] = f"{deps}:{env.get('LD_LIBRARY_PATH', '')}"
    env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/nonexistent/roughcut-fast-no-bus"
    rc = 0
    if tests:
        t0 = time.time()
        cmd = UV + (["--with", "pytest-xdist"] if workers else []) + [
            "pytest", "-q", "-p", "no:cacheprovider", "--deselect", PERF_ID,
            *(["-n", workers] if workers else []), *[str(t.relative_to(ROOT)) for t in tests]]
        rc = subprocess.run(cmd, cwd=ROOT, env=env).returncode
        print(f"fast: targeted tests {'GREEN' if rc == 0 else 'RED'} in {time.time() - t0:.0f} s")
    if "--no-quick" in argv:
        return rc
    if git("status", "--porcelain", "--untracked-files=no").strip():
        print("fast: uncommitted changes; the quick gate runs on a commit, so it is skipped (commit, then "
              "`python3 tools/gate.py HEAD --quick`)")
        return rc
    q = subprocess.run([sys.executable, str(ROOT / "tools" / "gate.py"), "HEAD", "--quick"], cwd=ROOT).returncode
    return rc or q


if __name__ == "__main__":
    sys.exit(main())
