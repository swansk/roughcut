"""Wave 16 SRVBUDGET2 (the lead, from lane AIHONEST): a clip's card poster is one URL.

test_workspace::test_open_cards_show_the_project_panels_posters failed 2 of 3 runs at load
~24: an /open card's poster was `?t=0.00` and the Project panel's `?t=1.00`. `poster_time`
said 0 until the clip's length was cached, and /api/clips gave `?t=0.00` to a clip whose free
facts were still being probed when it answered (it waits CLIP_FACTS_WAIT_S, 0.1 s, for them):
a card drawn then kept a frame nobody pre-builds, and a picture different from the panel's.
Now the answer before the length is known is the one after it for any clip of 2 s or more.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_the_poster_time_is_the_same_before_the_length_is_known(monkeypatch):
    import server
    durations: dict = {}
    monkeypatch.setitem(server.STATE, "durations", durations)
    assert server.poster_time("CLIP_X.MP4") == server.POSTER_AT_S          # not probed yet
    durations["CLIP_X.MP4"] = None                                         # a probe that failed
    assert server.poster_time("CLIP_X.MP4") == server.POSTER_AT_S
    durations["CLIP_X.MP4"] = 6.0
    assert server.poster_time("CLIP_X.MP4") == server.POSTER_AT_S          # the same URL
    durations["CLIP_X.MP4"] = 2 * server.POSTER_AT_S
    assert server.poster_time("CLIP_X.MP4") == server.POSTER_AT_S
    durations["CLIP_X.MP4"] = 1.2                                          # a short clip: its middle
    assert server.poster_time("CLIP_X.MP4") == 0.6


def test_a_clip_still_being_probed_gets_the_poster_it_will_keep(client, project, monkeypatch):
    """/api/clips answering while one clip's facts are still being probed (a loaded box):
    that clip's row has no length yet, and the poster URL it will have once it has one."""
    import server
    from concurrent.futures import wait
    # The `client` fixture's configure() starts probing every clip on CLIP_FACTS' pool and
    # returns. Alone, its ensure_proxies() then builds three proxies, so those probes are done
    # long before the reset below; in the full suite the proxies are already built, configure()
    # returns at once, and the real probe of CLIP_B, still running, wrote its facts back after
    # the reset: the "still probing" row had its length (verdict 13: `6.0 is None`, 2 gates of
    # 3; and 2 of 2 here when this file runs a second time in one session). So: every probe
    # already started ends first, then the caches it fills are emptied.
    with server.CLIP_FACTS["lock"]:
        started = list(server.CLIP_FACTS["running"].values())
    wait(started, timeout=30)
    assert all(f.done() for f in started), "a clip-facts probe from configure() never ended"
    for k in ("clip_facts", "durations", "capture", "telemetry"):
        server.STATE.pop(k, None)
    server.CLIP_FACTS["running"].clear()
    gate = threading.Event()
    real = server._probe_clip_facts

    def slow(clip, footage):
        if clip == "CLIP_B.MP4":
            gate.wait(10)                    # the probe of one clip outlasts the wait
        return real(clip, footage)

    monkeypatch.setattr(server, "_probe_clip_facts", slow)
    try:
        first = {c["clip"]: c for c in client.get("/api/clips").json()["clips"]}
        assert first["CLIP_B.MP4"]["duration"] is None, first["CLIP_B.MP4"]   # still probing
        gate.set()
        deadline = time.time() + 10
        while time.time() < deadline:
            later = {c["clip"]: c for c in client.get("/api/clips").json()["clips"]}
            if later["CLIP_B.MP4"]["duration"] is not None:
                break
            time.sleep(0.1)
        assert later["CLIP_B.MP4"]["duration"] is not None
        for clip in first:
            assert first[clip]["poster"] == later[clip]["poster"], (clip, first[clip], later[clip])
            assert first[clip]["poster"].endswith(f"?t={server.POSTER_AT_S:.2f}"), first[clip]
    finally:
        gate.set()
