"""The flow — one model of where a project is, for every screen (INTAKE M14).

Karl, 2026-10-03 (#3): *"make the flow through various stages make more sense in the
UI."* Walking the three screens showed why it did not: three screens, three navigation
schemes (the open screen's six numbered steps, the board's five-step strip plus a
row of `open · pass · board` pills, and nothing at all on the pass), and the open
screen contradicting itself ("3 released · 9 queued" over a footer saying every clip
was released). Nothing anywhere answered *where am I, what is done, what should I do
next, and what is waiting on me*.

So the answer is computed once, here, from facts the server reads off the files, and
every screen reads it from `GET /api/flow`. Seven stages, in the order a film is made:

    Footage → Index → Brief → Pass → Cut → Polish → Render

Each stage has one `state`:

    done       nothing left to do here
    running    the machine is working on it (with `progress`, 0–1)
    ready      it can start, and nothing is stopping it
    waiting    it cannot start yet (`waiting_on` names the stage, `why` says why)
    needs-you  it is waiting on the editor's word (`needs` says what and the action)
    optional   it never blocks anything — the brief, the pass before a first cut, polish

a one-line `summary` in the editor's words, the `screen` where it is done (always the
editor, `/`, since wave 17 folded the footage and pass screens into it) and the board's
dock `tool` (or the page command, `do`, that opens its panel), and the `counts` the summary rests on — the machine's evidence
rather than an assertion (Karl's rule). Then **one** `next`: the single recommended
action, and `blockers`: the thing in the way of every priced stage (a signed-out CLI),
with the server's own fix (`server.backend_fix`) — never re-derived here.

The screens no longer draw the stages (INTAKE M16 decision 2: the seven-step bar came
off every screen): /flow.js draws only `next`, as the one chip in each header, and puts
the screen's one blue button on the element a page marks `data-next-for="<stage>"`. The
stages stay; `next` is chosen from them.

Pure: it never reads a file and never imports the server. `server.flow_facts()` builds
the facts dict; the tests build it by hand.
"""

from __future__ import annotations

from urllib.parse import quote

from . import config

STAGES = ("footage", "index", "brief", "pass", "cut", "polish", "render")

NAMES = {"footage": "Footage", "index": "Index", "brief": "Brief", "pass": "Pass",
         "cut": "Cut", "polish": "Polish", "render": "Render"}

# Where each stage is done: always the editor (Karl's K4, wave 17 FOLDIN: the footage
# screen /open and the pass /floor were folded into it and removed, so nothing Next says
# leaves `/`). Each stage names the dock tool its work is in, so a click on the Next chip
# lands with the right panel open (`/#tool=ask`): the footage in the Project panel, the
# brief (the story) in the Assistant, the pass's picks in Selects (`bin`). The index is
# the Index panel, a page command (`STAGE_DO`) rather than a dock tool: `/#do=index`.
SCREENS: dict[str, tuple[str, str | None]] = {
    "footage": ("/", "project"), "index": ("/", None), "brief": ("/", "ask"),
    "pass": ("/", "bin"), "cut": ("/", "ask"), "polish": ("/", "fx"),
    "render": ("/", "out"),
}

# The stages a model call drives. A signed-out CLI is in the way of exactly these; the
# others (proxies, the pass, trimming, the render) are local and carry on regardless.
PRICED_STAGES = ("index", "brief", "cut", "polish")

STATES = ("done", "running", "ready", "waiting", "needs-you", "optional")

# The elements a screen marks as an action's button: `data-next-for="cut render"`.
# /flow.js gives the first visible one the screen's one blue (`.is-next`), and presses
# it for a free action (`click`).
NEXT_FOR = '[data-next-for~="%s"]'

# What an action may land on (INTAKE M16 I16.0a): the shot (a segment id) and the
# effect (an fx id) of a waiting proposal, or the Ask job of a waiting cut proposal.
TARGET_KEYS = ("shot", "fx", "ask")

# The look interval in words, the open screen's slider's own vocabulary (shorter).
INTERVAL_WORDS = {4.0: "a frame every 4 s", 3.0: "a frame every 3 s",
                  2.0: "a frame every 2 s", 1.0: "a frame every second"}


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _clock(s: float | None) -> str:
    s = max(0.0, float(s or 0.0))
    m, sec = divmod(int(round(s)), 60)
    return f"{m}:{sec:02d}"


def _usd(x: float | None) -> str:
    return f"${float(x or 0.0):.2f}"


def _price(x: float | None) -> str:
    """A price in a sentence, worded the way `rcCost.tag` (cli.js) words a button: on
    the subscription backend nothing is billed, so no bare dollars (Karl, 2026-10-05)."""
    return f"~{_usd(x)}" if config.charges_money() else "free on your plan"


def interval_words(intervals: dict | None, project: float | None) -> str:
    """How closely the bin was looked at, from the coarse sidecars' own intervals
    (`{"4": 9, "2": 3}`) — one phrase when they agree, the mix when they do not."""
    seen = {float(k): int(v) for k, v in (intervals or {}).items() if int(v) > 0}
    if not seen:
        return INTERVAL_WORDS.get(float(project), f"a frame every {project:g} s") \
            if project else ""
    if len(seen) == 1:
        (i, _n), = seen.items()
        return INTERVAL_WORDS.get(i, f"a frame every {i:g} s")
    return " · ".join(f"{n} at {i:g} s" for i, n in sorted(seen.items(), reverse=True))


def _job(facts: dict, *kinds: str) -> dict | None:
    jobs = facts.get("jobs") or {}
    for k in kinds:
        if jobs.get(k):
            return jobs[k]
    return None


def _stage(key: str, state: str, summary: str, *, counts: dict | None = None,
           progress: float | None = None, needs: dict | None = None,
           waiting_on: str | None = None, why: str | None = None,
           recommended: bool = False) -> dict:
    screen, tool = SCREENS[key]
    out = {"key": key, "name": NAMES[key], "state": state, "summary": summary,
           "screen": screen, "tool": tool,
           "href": f"{screen}#do={STAGE_DO[key]}" if key in STAGE_DO else href(screen, tool),
           "counts": counts or {}}
    if progress is not None:
        out["progress"] = round(max(0.0, min(1.0, float(progress))), 3)
    if needs:
        out["needs"] = needs
    if waiting_on:
        out["waiting_on"] = waiting_on
    if why:
        out["why"] = why
    if recommended:
        out["recommended"] = True
    return out


def href(screen: str, tool: str | None, target: dict | None = None) -> str:
    """`/#tool=fx`, and with a target the thing it lands on: `/#tool=fx&shot=<segment
    id>&fx=<effect id>` (or `&ask=<job>`) — the board's hash handler reads it."""
    if not tool:
        return screen
    t = target or {}
    extra = "".join(f"&{k}={quote(str(t[k]), safe='')}" for k in TARGET_KEYS if t.get(k))
    return f"{screen}#tool={tool}{extra}"


# The page commands an action may name (`do`): what /flow.js runs where the page has
# it, instead of navigating — Import's dialog (window.rcImport, the board's) and the
# Index panel (window.rcIndex, wave 17: where /open's index controls went).
DO = ("import", "index")

# The stages whose place is a page command, not a dock tool: an action of the stage
# with no tool or click of its own runs it (`#do=index` opens the Index panel).
STAGE_DO = {"index": "index"}


def action(stage: str, sentence: str, verb: str, *, tool: str | None = None,
           screen: str | None = None, click: str | None = None,
           usd: float | None = None, kind: str = "go",
           target: dict | None = None, do: str | None = None) -> dict:
    """One thing to do. `click` is a selector the bar may click *on its own screen*
    — only ever for free, non-spending actions (a render on the board); anything
    priced navigates to the button that carries its price, because nothing spends
    without the click on that button (Karl's rule). `target` is what it lands on
    (`{shot, fx}` or `{ask}`), carried in the href too, so Next opens the waiting
    proposal itself and not whatever shot the board had selected (INTAKE M16). `do`
    is a free page command (`DO`): the chip runs it on a page that has it, and from
    another screen goes to `screen#do=<it>`, where the board runs it on arrival."""
    scr, default_tool = SCREENS[stage]
    scr = screen or scr
    t = tool if tool is not None else default_tool
    tgt = {k: str(v) for k, v in (target or {}).items() if k in TARGET_KEYS and v}
    cmd = do if do in DO else (STAGE_DO.get(stage) if t is None and not click else None)
    out = {"stage": stage, "sentence": sentence, "verb": verb, "screen": scr,
           "tool": t, "href": f"{scr}#do={cmd}" if cmd else href(scr, t, tgt), "kind": kind}
    if cmd:
        out["do"] = cmd
    if tgt and t:
        out["target"] = tgt
    if click:
        out["click"] = click
    if usd is not None:
        out["usd"] = round(float(usd), 2)
    return out


# ------------------------------------------------------------------ the stages


def footage_stage(f: dict) -> dict:
    n = int(f.get("clips") or 0)
    px = f.get("proxies") or {}
    done, total = int(px.get("done") or 0), int(px.get("total") or n)
    counts = {"clips": n, "previews": done}
    if n == 0:
        # Next is Import (File › Import, Ctrl+I, as in Premiere): the board's dialog, run
        # in place (wave 12, REDESIGN-GOAL dimension 16: "no footage → Import")
        return _stage("footage", "needs-you", "No footage in this bin yet.",
                      counts=counts, needs={
                          "reason": "There is no video in this bin.",
                          "action": action("footage", "Import footage (Ctrl+I)", "Import",
                                           screen="/", do="import")})
    if done < total:
        return _stage("footage", "running",
                      f"{_plural(n, 'clip')} — building previews {done}/{total}",
                      counts=counts, progress=done / max(1, total))
    return _stage("footage", "done", f"{_plural(n, 'clip')}, previews built",
                  counts=counts)


def honest_pct(counts: dict) -> float:
    """The share of the index the counts prove done, 0–1: clips heard, clips looked at,
    close looks and previews (proxies) built, each clip-stage weighted the same. Every
    percentage the flow shows for the index is at most this — it is computed from the
    counts it is shown beside, so it can never be ahead of them."""
    n = int(counts.get("clips") or 0)
    look_need = int(counts.get("look_need") or 0)
    close_need = int(counts.get("close_need") or 0)
    denom = n + look_need + close_need + n
    if not denom:
        return 0.0
    done = (min(int(counts.get("listened") or 0), n)
            + min(int(counts.get("looked") or 0), look_need)
            + min(int(counts.get("close") or 0), close_need)
            + min(int(counts.get("proxied") or 0), n))
    return done / denom


# The four clip-stages `honest_pct` counts, as the journal names them.
JOURNAL_COUNTED = ("asr", "look", "close", "proxy")


def journal_counts(prog: dict | None) -> dict:
    """The journal's own progress (`journal.progress()`) as `honest_pct`'s counts: a
    clip-stage counts once it is settled (done or skipped). Only the four stages the
    flow counts; probes and telemetry are never work done."""
    prog = prog or {}
    stages = prog.get("stages") or {}
    n = max(0, int(prog.get("clips") or 0) - int(prog.get("missing") or 0))

    def settled(s: str) -> int:
        st = stages.get(s) or {}
        return int(st.get("done") or 0) + int(st.get("skipped") or 0)

    return {"clips": n, "listened": settled("asr"), "looked": settled("look"),
            "look_need": n, "close": settled("close"), "close_need": n,
            "proxied": settled("proxy")}


def index_eta(prog: dict | None, elapsed_s: float, since_s: float = 0.0) -> float | None:
    """The index's time left, only when it is measured (verdict 2: "about 0:07 left" at
    6 min with 0 of 12 ready, and a fixed 60 s guess called "measured"). None until
    every counted stage still to run has finished on at least one clip (the journal's
    per-stage means), and never less than the run's own pace says: at a share `f`
    done after `elapsed_s`, the rest takes at least `elapsed_s · (1 − f) / f`.
    `since_s` is how long ago the journal's numbers were read."""
    prog = prog or {}
    if set(prog.get("eta_unmeasured") or []) & set(JOURNAL_COUNTED):
        return None
    eta = prog.get("eta_s")
    f = honest_pct(journal_counts(prog))
    if eta is None or f <= 0.0 or f >= 1.0:
        return None
    pace = float(elapsed_s) * (1.0 - f) / f
    return round(max(float(eta) - max(0.0, float(since_s)), pace), 1)


def honest_index_job(job) -> None:
    """Hold an index job (`progress.Job`) to its counts: its bar never above the share
    the journal's clip-stages prove, its time left `index_eta` or nothing. The job's
    `journal` key is the progress the index last reported."""
    import time as _time

    seen: dict = {"prog": None, "at": 0.0}

    def prog_of(j) -> dict | None:
        prog = j.get("journal")
        if prog is not seen["prog"]:
            seen.update(prog=prog, at=_time.time())
        return prog

    def bound(j) -> float:
        return 100.0 * honest_pct(journal_counts(prog_of(j)))

    def eta(j) -> float | None:
        prog = prog_of(j)
        left = index_eta(prog, j.elapsed(), _time.time() - seen["at"]) if prog else None
        j["eta_source"] = "measured" if left is not None else "none"
        return left

    job.bound_fn = bound
    job.eta_fn = eta


def index_counts_words(counts: dict) -> str:
    """"heard 3/12 · looked at 1/12 · previews 2/12" — the counts a progress line rests
    on, in the order the index buys them."""
    n = int(counts.get("clips") or 0)
    return " · ".join([f"heard {int(counts.get('listened') or 0)}/{n}",
                       f"looked at {int(counts.get('looked') or 0)}/"
                       f"{int(counts.get('look_need') or 0)}",
                       f"previews {int(counts.get('proxied') or 0)}/{n}"])


def index_stage(f: dict) -> dict:
    ix = f.get("index") or {}
    n = int(f.get("clips") or 0)
    junk = int(f.get("junk") or 0)
    looked_need = max(0, n - junk)
    listened, looked, close = (int(ix.get(k) or 0) for k in ("listened", "looked", "close"))
    close_need = ix.get("close_need")
    close_need = looked_need if close_need is None else int(close_need)
    how = interval_words(ix.get("intervals"), ix.get("interval_s"))
    px = f.get("proxies") or {}
    proxied = min(n, int(px.get("done") or 0))
    counts = {"clips": n, "listened": listened, "looked": looked, "close": close,
              "proxied": proxied,
              "junk": junk, "look_need": looked_need, "close_need": close_need,
              "on_pass": int(ix.get("on_pass") or 0), "released": int(ix.get("released") or 0),
              "parked": int(ix.get("parked") or 0)}
    if how:
        counts["granularity"] = how
    parts = [f"heard {listened}/{n}", f"looked at {looked}/{looked_need}"
             + (f" ({how})" if looked and how else ""),
             f"close looks {close}/{close_need}"]
    summary = " · ".join(parts)
    pct = honest_pct(counts)
    counts["pct"] = round(100.0 * pct, 1)
    if n == 0:
        return _stage("index", "waiting", "Nothing to index yet.", counts=counts,
                      waiting_on="footage", why="the folder has no clips")
    job = _job(f, "index", "analyse", "visual")
    if job:
        detail = str(job.get("detail") or "").strip()
        # The run's own % (the journal's settled stages over all stages) counts probes
        # and skipped telemetry as work done, so it read 87% while 0/12 clips were heard
        # (judge verdict 1). The bar never says more than the counts support: it is the
        # smaller of the two, and the counts' share when the run gives none.
        run_pct = job.get("pct")
        progress = pct if run_pct is None else min(pct, float(run_pct) / 100.0)
        return _stage("index", "running", f"Indexing — {summary}"
                      + (f" · {detail}" if detail else ""), counts=counts,
                      progress=progress)
    complete = listened >= n and looked >= looked_need and close >= close_need
    if complete:
        return _stage("index", "done", f"Indexed — {summary}", counts=counts, progress=1.0)
    waiting = ix.get("waiting") or {}
    if ix.get("paused") and int(waiting.get("clips") or 0):
        k = int(waiting["clips"])
        what = "Looks" if int(waiting.get("looks") or 0) else "Close looks"
        usd = waiting.get("usd")
        reason = (f"{what} are paused — {_plural(k, 'clip')} wait, {_price(usd)}. "
                  f"The pass already shows them from what was heard.")
        return _stage("index", "needs-you", reason, counts={**counts, "waiting": k},
                      progress=pct, needs={
                          "reason": reason,
                          "action": action("index", f"Resume the {what.lower()} — "
                                           f"{_plural(k, 'clip')}, {_price(usd)}",
                                           "Resume", usd=usd)})
    usd = ix.get("pending_usd")
    verb = "Index" if not ix.get("journal") and listened == 0 else "Index the rest"
    sentence = ("Index the footage" if verb == "Index"
                else "Index what isn't done") + (f" — {_price(usd)}" if usd else "")
    return _stage("index", "ready", summary, counts=counts, progress=pct,
                  recommended=True) | {"action": action(
                      "index", sentence, verb, usd=usd)}


def brief_stage(f: dict) -> dict:
    b = f.get("brief") or {}
    themes = int(b.get("themes") or 0)
    story = bool(b.get("story"))
    counts = {"themes": themes, "story": story}
    if _job(f, "themes"):
        return _stage("brief", "running", "Proposing themes from the transcripts",
                      counts=counts)
    if b.get("proposal"):
        # The brief never blocks anything (M14 decision 1), so proposed themes are not
        # waiting on you: no amber, and Next never routes to them (INTAKE M16 I16.0b —
        # the Sep 8 proposal held "! Brief" and Next on every screen).
        return _stage("brief", "optional",
                      "Proposed themes wait — keep or discard them whenever you like",
                      counts={**counts, "proposal": True})
    if themes or story:
        bits = (["story set"] if story else []) + ([_plural(themes, "theme")] if themes else [])
        return _stage("brief", "done", " · ".join(bits), counts=counts)
    return _stage("brief", "optional",
                  "Optional — say what the film is about; it orders the index and tags "
                  "the pass", counts=counts)


def pass_stage(f: dict) -> dict:
    p = f.get("pass") or {}
    on_pass = int(p.get("on_pass") or 0)
    passed = int(p.get("passed") or 0)
    keeps, heroes = int(p.get("keeps") or 0), int(p.get("heroes") or 0)
    counts = {"on_pass": on_pass, "passed": passed, "keeps": keeps, "heroes": heroes}
    tally = " · ".join([_plural(keeps, "keep"), _plural(heroes, "hero", "heroes")])
    if on_pass == 0:
        if keeps:
            return _stage("pass", "done", tally, counts=counts)
        return _stage("pass", "waiting", "Opens on the first clip the index releases",
                      counts=counts, waiting_on="index",
                      why="no clip has been heard and previewed yet")
    seen = f"{min(passed, on_pass)}/{on_pass} clips passed"
    if passed >= on_pass:
        return _stage("pass", "done", f"{seen} · {tally}", counts=counts, progress=1.0)
    if passed or keeps:
        return _stage("pass", "optional", f"{seen} · {tally}", counts=counts,
                      progress=passed / on_pass)
    shots = int((f.get("cut") or {}).get("shots") or 0)
    return _stage("pass", "optional",
                  f"Optional, recommended before a first cut — {_plural(on_pass, 'clip')} "
                  "ready to pass" if not shots else
                  f"{_plural(on_pass, 'clip')} ready to pass", counts=counts,
                  progress=0.0, recommended=not shots)


def cut_stage(f: dict) -> dict:
    c = f.get("cut") or {}
    shots = int(c.get("shots") or 0)
    length = float(c.get("length_s") or 0.0)
    target = c.get("target") or None
    heroes_out = int((f.get("pass") or {}).get("heroes_out") or 0)
    listened = int((f.get("index") or {}).get("listened") or 0)
    counts = {"shots": shots, "length_s": round(length, 2), "target": target,
              "heroes_out": heroes_out}
    prop = c.get("proposal")
    if _job(f, "ask"):
        return _stage("cut", "running", "The model is working on the cut",
                      counts=counts, progress=(_job(f, "ask").get("pct") or 0) / 100.0)
    if prop and not prop.get("colour_only"):
        n = int(prop.get("shots") or 0)
        reason = f"A proposed cut ({_plural(n, 'shot')}) waits for Accept or Discard"
        return _stage("cut", "needs-you", reason, counts={**counts, "proposal": n},
                      needs={"reason": reason,
                             "action": action("cut", "Read the proposal and accept or "
                                              "discard it", "Answer",
                                              target={"ask": prop.get("job")})})
    if shots == 0:
        if listened == 0:
            return _stage("cut", "waiting", "Needs the footage heard first",
                          counts=counts, waiting_on="index",
                          why="the model cuts from what it has heard")
        keeps = int((f.get("pass") or {}).get("keeps") or 0)
        sentence = (f"Make the first cut from your {_plural(keeps, 'keep')}" if keeps
                    else "Ask for the first cut")
        return _stage("cut", "ready", "No cut yet", counts=counts,
                      recommended=True) | {"action": action("cut", sentence, "Cut")}
    bits = [_plural(shots, "shot"), _clock(length)]
    if target and len(target) == 2:
        lo, hi = float(target[0]), float(target[1])
        if length > hi:
            bits.append(f"{_clock(length - hi)} over the {_clock(lo)}–{_clock(hi)} target")
        elif length < lo:
            bits.append(f"{_clock(lo - length)} under the {_clock(lo)}–{_clock(hi)} target")
        else:
            bits.append(f"inside the {_clock(lo)}–{_clock(hi)} target")
    if heroes_out:
        bits.append(f"{_plural(heroes_out, 'hero', 'heroes')} not in it")
    return _stage("cut", "done", " · ".join(bits), counts=counts)


def polish_stage(f: dict) -> dict:
    p = f.get("polish") or {}
    c = f.get("cut") or {}
    shots = int(c.get("shots") or 0)
    fx_proposed = int(p.get("fx_proposed") or 0)
    effects = int(p.get("effects") or 0)
    colour_only = bool((c.get("proposal") or {}).get("colour_only"))
    counts = {"effects": effects, "fx_proposed": fx_proposed,
              "look": p.get("look"), "music": bool(p.get("music"))}
    if shots == 0:
        return _stage("polish", "waiting", "Colour, effects and music, once there is a cut",
                      counts=counts, waiting_on="cut", why="there is nothing to polish yet")
    if _job(f, "fx"):
        return _stage("polish", "running", "An effect is being designed or verified",
                      counts=counts)
    if fx_proposed or colour_only:
        what = []
        if fx_proposed:
            what.append(f"{_plural(fx_proposed, 'effect')} proposed")
        if colour_only:
            what.append("a colour change proposed")
        reason = " · ".join(what) + " — accept or discard"
        tool = "fx" if fx_proposed else "ask"
        # Land on the proposal: the effect's shot and card (the server picks the one on
        # the earliest shot), or the waiting colour-only Ask.
        target = (p.get("target") if fx_proposed
                  else {"ask": (c.get("proposal") or {}).get("job")})
        return _stage("polish", "needs-you", reason, counts=counts, needs={
            "reason": reason,
            "action": action("polish", reason[0].upper() + reason[1:], "Answer",
                             tool=tool, target=target)})
    bits = []
    if p.get("look"):
        bits.append(f"look: {p['look']}")
    if effects:
        bits.append(_plural(effects, "effect"))
    if p.get("music"):
        bits.append("music")
    return _stage("polish", "optional",
                  " · ".join(bits) if bits else "Colour, effects and music — whenever you like",
                  counts=counts)


def render_stage(f: dict) -> dict:
    r = f.get("render") or {}
    shots = int((f.get("cut") or {}).get("shots") or 0)
    count = int(r.get("count") or 0)
    counts = {"renders": count, "latest_matches": bool(r.get("latest_matches")),
              "this_cut": bool(r.get("matches"))}
    if shots == 0:
        return _stage("render", "waiting", "Once there is a cut", counts=counts,
                      waiting_on="cut", why="nothing to render yet")
    job = _job(f, "render")
    if job:
        return _stage("render", "running", "Rendering", counts=counts,
                      progress=(job.get("pct") or 0) / 100.0)
    if r.get("latest_matches"):
        return _stage("render", "done", "The newest render is this cut", counts=counts)
    # Next presses the film tool's quick look itself: free, on the board, the button
    # the board marks for this stage (M16 C2) — never the final 4K, which is Karl's.
    sentence = "Make the film — quick look"
    if count:
        summary = ("A render of this cut exists, but newer renders differ"
                   if r.get("matches") else
                   f"Stale — the cut changed since the last render ({_plural(count, 'render')})")
        counts["stale"] = True
    else:
        summary = "Not rendered yet"
    return _stage("render", "ready", summary, counts=counts) | {"action": action(
        "render", sentence, "Render", click=NEXT_FOR % "render")}


# ------------------------------------------------------------------- the whole


def blockers(f: dict) -> list[dict]:
    """What stands in the way of every priced stage. Today: the CLI, as the server's
    own `backend_fix` says it (kind, title, why, command) — reused, not re-derived."""
    fix = f.get("fix")
    if not fix:
        return []
    return [{"kind": "cli", "title": fix.get("title") or "The Claude CLI needs you",
             "why": fix.get("why") or "", "command": fix.get("command") or "",
             "fix": fix, "stages": list(PRICED_STAGES)}]


def _next(stages: dict[str, dict], f: dict, blocked: list[dict]) -> dict:
    """The one recommended action, in a fixed order of precedence:

    1. footage missing — nothing else means anything;
    2. a proposal waiting (already paid for: answering it costs nothing) — a cut or
       an effect; proposed themes are the brief's, which never blocks (M16 I16.0b);
    3. the index, until every clip has at least been heard (the free part) — or the
       open screen's first *Index the footage* when nothing has been indexed;
    4. no cut yet → make the first cut (from the keeps when there are any);
    5. the cut is not rendered → render it (free — the bar may press the button);
    6. priced stages paused on your word → resume them, with the price;
    7. otherwise refine: ask for a change.

    A running stage the next action depends on turns it into a wait, said as such —
    except the pass, which can start on the clips already released while the rest
    index. A priced next action with the CLI signed out becomes the CLI's fix.
    """
    def priced(a: dict) -> dict:
        if blocked and a["stage"] in PRICED_STAGES and a.get("kind") != "wait":
            b = blocked[0]
            return {"stage": a["stage"], "sentence": f"{b['title']} — then: "
                    f"{a['sentence'][0].lower()}{a['sentence'][1:]}",
                    "verb": "Fix the CLI", "screen": a["screen"], "tool": a.get("tool"),
                    "href": a["href"], "kind": "cli", "command": b.get("command"),
                    "then": a}
        return a

    if stages["footage"]["state"] == "needs-you":
        return stages["footage"]["needs"]["action"]
    for key in ("cut", "polish"):
        if stages[key]["state"] == "needs-you":
            return stages[key]["needs"]["action"]
    ix = stages["index"]
    c = ix["counts"]
    never = c.get("listened", 0) == 0 and c.get("looked", 0) == 0
    shots = stages["cut"]["counts"].get("shots", 0)
    if ix["state"] == "running" and (never or c.get("listened", 0) < c.get("clips", 0)
                                     or shots == 0):
        on_pass = stages["pass"]["counts"].get("on_pass", 0)
        if on_pass and shots == 0 and stages["pass"]["counts"].get("passed", 0) < on_pass:
            return action("pass", f"Start the pass on {_plural(on_pass, 'clip')} — the "
                          "rest keep indexing", "Pass")
        # The counts first, the % after and only as computed from them (verdict 1: the
        # chip read "Indexing — 87%" while 0 of 12 clips had been heard).
        pct = int(min(ix.get("progress") or 0.0, honest_pct(c)) * 100)
        return action("index", f"Indexing — {index_counts_words(c)} ({pct}%); "
                      "the pass opens on the first clip released", "Watch", kind="wait")
    if ix["state"] == "ready" and (never or c.get("listened", 0) < c.get("clips", 0)):
        return priced(ix["action"])
    if shots == 0:
        if stages["cut"]["state"] == "running":
            return action("cut", "The first cut is being made", "Watch", kind="wait")
        if stages["cut"]["state"] == "ready":
            return priced(stages["cut"]["action"])
    rs = stages["render"]
    if rs["state"] == "running":
        pass
    elif rs["state"] == "ready":
        return rs["action"]
    if ix["state"] == "needs-you":
        return priced(ix["needs"]["action"])
    if ix["state"] == "ready":
        return priced(ix["action"])
    return priced(action("cut", "Refine — trim, or ask for a change", "Refine"))


def compute(facts: dict) -> dict:
    """Facts in (see `server.flow_facts`), the flow out: `stages` in order, `next`,
    `blockers`, and `running` (whether anything is — the bar polls faster then)."""
    f = facts or {}
    built = [footage_stage(f), index_stage(f), brief_stage(f), pass_stage(f),
             cut_stage(f), polish_stage(f), render_stage(f)]
    stages = {s["key"]: s for s in built}
    blocked = blockers(f)
    if blocked:
        for key in PRICED_STAGES:
            s = stages[key]
            # Only where a model call is what comes next: a done index or a cut that
            # exists is not blocked by a signed-out CLI.
            if s["state"] in ("ready", "needs-you") or (
                    key == "polish" and s["state"] == "optional"
                    and stages["cut"]["counts"].get("shots")):
                s["blocked"] = "cli"
    nxt = _next(stages, f, blocked)
    return {"stages": built, "next": nxt, "blockers": blocked,
            "running": any(s["state"] == "running" for s in built)}
