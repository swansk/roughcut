"""Wave 19 LOOPHOLD (verdict 15, punch 7): the event loop never waits on page faults.

Verdict 15 sampled a 2.5 s hold in FastAPI's per-request `_get_param_aliases` with 36 major
faults while 10 GB of files were copied on the box, with the board's lock reading "locked
131 MB the event loop runs on". What the loop touches was not what was locked: Python's
1 MiB object arenas sit side by side and the kernel merges them, and the old rule locked
only maps under 4 MiB, so the startup objects (module dicts, functions, code) were in two
unlocked maps of 8 and 4 MiB; a thread's malloc arena and the heap's growth were never
locked.

These run the real board (`server.py`'s main(): gc.freeze, the memory lock, uvicorn on the
main thread) as a child on the suite's fixture, then take from it every page the kernel can
take — what a box under page-cache pressure does to a board over minutes:

  * `POST /api/debug/pageout` (on only with ROUGHCUT_DEBUG_PAGEOUT=1): MADV_PAGEOUT over every
    mapping, a locked page staying. One round of the board's reads after it may bring back
    almost nothing from swap (counted page by page in /proc/<pid>/pagemap). The same board
    with the lock off (ROUGHCUT_MLOCK=0) brings back megabytes: the count sees faults.
  * Where the box allows it (a systemd user scope, cgroup v2 `memory.reclaim`), the board in
    its own scope while a dd streams 256 MB through the page cache beside it and the scope's
    memory is reclaimed every 0.3 s: a swap-in is then a real disk read behind the dd's
    writeback, so a page the loop lacks is a hold. None may be a "faults" hold of 200 ms or
    more, and the loop thread may fault back only a handful of pages.

Sized so the file stays fast (~20 s): the fixture's three 6 s clips with --no-proxies, a 256 MB
dd at nice 19 / ionice -c3 (about a second of writes, deleted after), ~5 s of reads under
the stress, and ROUGHCUT_RELOCK_S=0.5 so the board's relock follows its warm-up at once.

    uv run --with pytest --with fastapi --with uvicorn --with httpx --with numpy \\
        --with playwright pytest app/tests/test_loop_hold.py -q
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[2]
PAGE = os.sysconf("SC_PAGE_SIZE")
# the board's own reads, about what one screen polls, plus the page itself
READS = ["/", "/api/project", "/api/status", "/api/jobs", "/api/flow", "/api/firstpass",
         "/api/picks", "/api/fx", "/api/clips", "/api/index", "/api/index/clips", "/api/backend",
         "/api/assist/price?text=warm%20up%20shot%202", "/api/debug/slow"]
BACK_MB = 0.5                  # the most a round of reads may bring back, locked
CONTROL_MB = 2.0               # the least it brings back with the lock off (measured: 7-10 MB)
LOOP_FAULTS = 8                # the loop thread's major faults under the reclaim and the dd
HOLD_MS = 200.0                # verdict 10's loop budget


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _swap_total_kb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("SwapTotal:"):
            return int(line.split()[1])
    return 0


def _scopes_work() -> bool:
    """A systemd user scope this test can reclaim: systemd-run --user and memory.reclaim."""
    if shutil.which("systemd-run") is None:
        return False
    probe = ("cg=$(cut -d: -f3 /proc/self/cgroup); test -w /sys/fs/cgroup$cg/memory.reclaim")
    try:
        r = subprocess.run(["systemd-run", "--user", "--scope", "-q",
                            f"--unit=rc-loophold-probe-{uuid.uuid4().hex[:8]}", "sh", "-c", probe],
                           capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def _limit_blocks_lock() -> bool:
    """True only where RLIMIT_MEMLOCK is too small to lock the board (a skip); with room
    for it, a board that locked nothing is a failure (the lock off is the mutant)."""
    import resource
    soft = resource.getrlimit(resource.RLIMIT_MEMLOCK)[0]
    return soft != resource.RLIM_INFINITY and soft < (512 << 20)


def _start(tmp: Path, project: dict, *, mlock: bool = True, scope: bool = False) -> dict:
    """The board as a child (its main(), as Karl's runs it) on a copy of the suite's cut."""
    tmp.mkdir(parents=True, exist_ok=True)
    edl = tmp / "edl.json"
    shutil.copy(project["edl"], edl)
    sidecars = tmp / "sidecars"
    shutil.copytree(project["sidecars"], sidecars)
    work = tmp / "work"
    port = _free_port()
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "ROUGHCUT_DEBUG_PAGEOUT": "1",
           "ROUGHCUT_RELOCK_S": "0.5", "ROUGHCUT_LEDGER": str(tmp / "ledger.jsonl"),
           "ROUGHCUT_MLOCK": "1" if mlock else "0"}
    cmd = [sys.executable, str(REPO / "app" / "server.py"), "--footage", str(project["footage"]),
           "--edl", str(edl), "--sidecars", str(sidecars), "--work", str(work),
           "--visual", str(work / "no-visual"), "--assets", str(project["assets"]),
           "--port", str(port), "--no-probe", "--no-proxies"]
    unit = None
    if scope:
        unit = f"rc-loophold-{uuid.uuid4().hex[:8]}"
        cmd = ["systemd-run", "--user", "--scope", "-q", f"--unit={unit}", *cmd]
    log = open(tmp / "server.log", "w")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env,
                            start_new_session=True)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 150
    while time.time() < deadline:
        try:
            if httpx.get(base + "/api/project", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    else:
        _stop({"proc": proc, "log": log})
        raise AssertionError((tmp / "server.log").read_text())
    if proc.poll() is not None:
        log.close()
        raise AssertionError((tmp / "server.log").read_text())
    return {"proc": proc, "log": log, "base": base, "pid": proc.pid, "tmp": tmp, "unit": unit}


def _stop(b: dict) -> None:
    proc = b["proc"]
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
    b["log"].close()


def _reads(c: httpx.Client, rounds: int = 1) -> None:
    for _ in range(rounds):
        for p in READS:
            assert c.get(p).status_code == 200, p


def _settle(c: httpx.Client) -> dict:
    """Warm the board's reads, then wait for two relocks after them (what they made locked)."""
    _reads(c, 3)
    m = c.get("/api/debug/slow").json()["malloc"]
    if not m.get("locked_mb") and _limit_blocks_lock():
        time.sleep(1.0)                       # the lock is off: nothing to wait for
        return m
    start = m.get("relocks", 0)
    deadline = time.time() + 10
    while time.time() < deadline:
        m = c.get("/api/debug/slow").json()["malloc"]
        if m.get("relocks", 0) >= start + 2:
            break
        time.sleep(0.1)
    return m


def _anon_pages(pid: int) -> dict:
    """Per private anonymous mapping, each page's state: 2 swapped, 1 resident, 0 neither."""
    maps = []
    for line in Path(f"/proc/{pid}/maps").read_text().splitlines():
        parts = line.split(maxsplit=5)
        name = parts[5].strip() if len(parts) > 5 else ""
        if parts[1].startswith("---") or parts[1][3] != "p" or (name and name != "[heap]"
                                                                and name != "[stack]"):
            continue
        a, b = (int(x, 16) for x in parts[0].split("-"))
        maps.append((a, b))
    out = {}
    with open(f"/proc/{pid}/pagemap", "rb") as fh:
        for a, b in maps:
            n = (b - a) // PAGE
            fh.seek(a // PAGE * 8)
            raw = fh.read(n * 8)
            ents = struct.unpack(f"{len(raw) // 8}Q", raw)
            out[(a, b)] = bytes(2 if e >> 62 & 1 else (1 if e >> 63 & 1 else 0) for e in ents)
    return out


def _back_mb(before: dict, after: dict) -> float:
    """MB of pages swapped out in `before` and resident again in `after`: faulted back."""
    n = 0
    for k, sb in before.items():
        sa = after.get(k)
        if sa is None:
            continue
        n += sum(1 for x, y in zip(sb, sa) if x == 2 and y == 1)
    return n * PAGE / 2**20


def _loop_majflt(pid: int) -> int:
    st = Path(f"/proc/{pid}/task/{pid}/stat").read_bytes()
    return int(st[st.rindex(b")") + 2:].split()[9])


def _pageout_round(b: dict) -> dict:
    with httpx.Client(base_url=b["base"], timeout=30) as c:
        m = _settle(c)
        r = c.post("/api/debug/pageout")
        assert r.status_code == 200, r.text
        out = r.json()
        swapped = _anon_pages(b["pid"])
        _reads(c, 1)
        back = _back_mb(swapped, _anon_pages(b["pid"]))
    return {"pageout": out, "back_mb": back, "malloc": m,
            "swapped_mb": sum(s.count(2) for s in swapped.values()) * PAGE / 2**20}


@pytest.fixture
def need_swap():
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux only (mlock2, MADV_PAGEOUT, /proc)")
    if _swap_total_kb() == 0:
        pytest.skip("no swap: anonymous pages cannot be taken")


def test_after_the_kernel_takes_what_it_can_the_boards_reads_need_almost_nothing_back(
        tmp_path, project, need_swap):
    locked = _start(tmp_path / "locked", project)
    try:
        on = _pageout_round(locked)
    finally:
        _stop(locked)
    log = (tmp_path / "locked" / "server.log").read_text()
    if ("locked" not in log or not on["malloc"].get("locked_mb")) and _limit_blocks_lock():
        pytest.skip(f"the memory lock is refused here (RLIMIT_MEMLOCK): {on['malloc']}")
    if on["pageout"]["advised"] == 0:
        pytest.skip(f"MADV_PAGEOUT refused here: {on['pageout']}")
    # the same board, the lock off: the count sees the faults it should
    unlocked = _start(tmp_path / "unlocked", project, mlock=False)
    try:
        off = _pageout_round(unlocked)
    finally:
        _stop(unlocked)
    print(f"locked: {on['malloc'].get('locked_kinds')} — paged out {on['swapped_mb']:.1f} MB, "
          f"a round of reads brought back {on['back_mb']:.2f} MB; unlocked: paged out "
          f"{off['swapped_mb']:.1f} MB, brought back {off['back_mb']:.2f} MB")
    assert off["back_mb"] >= CONTROL_MB, off
    assert on["back_mb"] <= BACK_MB, on
    # what the loop runs on is locked whatever its map's size: the object arenas the kernel
    # merged, the threads' malloc arenas and stacks, the heap
    kinds = on["malloc"]["locked_kinds"]
    for k in ("objects", "malloc", "code"):
        assert kinds.get(k), kinds


def test_a_dd_and_the_kernel_reclaiming_the_board_hold_the_loop_less_than_its_budget(
        tmp_path, project, need_swap):
    if not _scopes_work():
        pytest.skip("no systemd user scope with memory.reclaim here")
    b = _start(tmp_path / "board", project, scope=True)
    stress = tmp_path / "stress.bin"
    stop = threading.Event()
    dd = None
    try:
        cg = Path("/sys/fs/cgroup" + Path(f"/proc/{b['pid']}/cgroup").read_text().split(":", 2)[2].strip())
        assert (cg / "memory.reclaim").exists(), cg

        def reclaim():                      # whatever of the board the kernel can take, taken
            while not stop.is_set():
                try:
                    (cg / "memory.reclaim").write_text("256M")
                except OSError:
                    pass                    # EAGAIN: it took what it could
                stop.wait(0.3)

        with httpx.Client(base_url=b["base"], timeout=60) as c:
            m = _settle(c)
            if not m.get("locked_mb") and _limit_blocks_lock():
                pytest.skip(f"the memory lock is refused here (RLIMIT_MEMLOCK): {m}")
            t0 = time.time()
            f0 = _loop_majflt(b["pid"])
            dd = subprocess.Popen(["nice", "-n", "19", "ionice", "-c3", "dd", "if=/dev/zero",
                                   f"of={stress}", "bs=1M", "count=256", "conv=fsync",
                                   "status=none"])
            th = threading.Thread(target=reclaim, daemon=True)
            th.start()
            rounds = 0
            while time.time() - t0 < 5.0 or dd.poll() is None and time.time() - t0 < 30:
                _reads(c, 1)
                rounds += 1
            stop.set()
            th.join(timeout=10)
            faults = _loop_majflt(b["pid"]) - f0
            slow = c.get("/api/debug/slow").json()
        holds = [r for r in slow["over_budget"] if r["method"] == "LOOP" and r["at"] >= t0]
        report = (f"{rounds} rounds, load {os.getloadavg()[0]:.1f}, loop thread faults {faults}; "
                  f"holds {[(h['ms'], (h.get('where') or {}).get('cause'), (h.get('where') or {}).get('majflt')) for h in holds]}; "
                  f"slowest {slow['slowest_loop'] and slow['slowest_loop']['ms']} ms")
        print(report)
        assert rounds >= 3, report
        assert faults <= LOOP_FAULTS, report
        assert not [h for h in holds if (h.get("where") or {}).get("cause") == "faults"
                    and h["ms"] >= HOLD_MS], report
    finally:
        stop.set()
        if dd is not None and dd.poll() is None:
            dd.kill()
            dd.wait(timeout=10)
        stress.unlink(missing_ok=True)
        _stop(b)
