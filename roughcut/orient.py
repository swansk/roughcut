"""Which way up a clip is, decided per clip (wave 23, lane FILM; COLD-08, COLD-13,
WORDS-23, FINISH-22).

A rotation flag in the file is the camera's claim about how to show the picture. For a
GoPro mounted upside down it is right (the sensor says the same: gravity points out of
the bottom of the frame). For Copper's three clips flagged +-90 it is spurious: the raw
frame is upright, and honouring the flag lays the people on their side. The telemetry
pass already measures gravity in the camera's own axes (`orientation.median_up_vector`,
`mount`), so the answer is on disk: where it disagrees with the flag, the flag is ignored.

Pure functions, no I/O: the server reads the sidecars and the file's flag and calls these.

The sensor can only *veto* a flag, never add a turn: a clip with no flag is never turned
(a camera that is inverted but carries no flag is left as the file says), and a clip whose
sensor is not conclusive (looking at the ground, held on its side) keeps its flag, as it
always did. A clip with no telemetry keeps its flag too.
"""

from __future__ import annotations

# |gravity along the camera's vertical| at least this much: the camera is clearly upright
# (up) or upside down (down). Killington's CLIP_10 (-0.274, pitched at the ground) is
# below it on purpose: not conclusive, so its -180 flag stays honoured as it was.
CONCLUSIVE = 0.5

MODES = ("auto", "none")      # auto: the file's flag is applied; none: it is ignored


def flag_degrees(rotation) -> int:
    """The rotation flag as ffprobe reports it (-90, 90, -180, 270, ...) in the
    counter-clockwise degrees the pipeline uses (0, 90, 180, 270)."""
    try:
        return int(round(float(rotation or 0))) % 360
    except (TypeError, ValueError):
        return 0


def sensor_view(up, mount, convention: dict | None = None) -> tuple[int | None, str | None]:
    """What the camera's own sensor says: `(turn the file would need, plain word)`.

    The turn is 0 for upright, 180 for upside down, None when the sensor is not
    conclusive or the camera is on its side. `up` is the median up vector the telemetry
    pass wrote (the camera's "up" in its own axes), `convention` its axis order
    (vertical / lateral / forward, default 0 / 1 / 2). Without a vector the telemetry
    pass's `mount` ("upright" / "inverted") is used.
    """
    conv = convention or {}
    if isinstance(up, (list, tuple)) and len(up) >= 3:
        try:
            v = float(up[int(conv.get("vertical", 0))])
            lat = float(up[int(conv.get("lateral", 1))])
        except (TypeError, ValueError, IndexError):
            return None, None
        if abs(v) >= CONCLUSIVE:
            return (0, "upright") if v > 0 else (180, "upside down")
        if abs(lat) >= CONCLUSIVE:
            return None, "on its side"
        return None, None
    if mount == "upright":
        return 0, "upright"
    if mount == "inverted":
        return 180, "upside down"
    return None, None


def decide(rotation, up=None, mount=None, convention: dict | None = None,
           hand: str | None = None) -> dict:
    """One clip's rotation decision.

    `mode` is "auto" (the flag is applied, as ffmpeg's autorotate does) or "none" (it is
    ignored); `why` says on what ground:
      noflag   the file carries no flag: nothing to decide
      agrees   a flag, and the sensor says the same
      sensor   a flag the sensor contradicts: ignored
      nosensor a flag and no telemetry: honoured (as before)
      unclear  a flag and a sensor that is not conclusive: honoured
      hand     the editor said which, over all of the above
    """
    flag = flag_degrees(rotation)
    expect, word = sensor_view(up, mount, convention)
    if hand in MODES:
        mode, why = hand, "hand"
    elif flag == 0:
        mode, why = "auto", "noflag"
    elif expect is None:
        mode, why = "auto", ("unclear" if (word or up is not None) else "nosensor")
    elif expect == flag:
        mode, why = "auto", "agrees"
    else:
        mode, why = "none", "sensor"
    return {"mode": mode, "why": why, "flag": flag, "expect": expect, "sensor": word,
            "hand": hand if hand in MODES else None}


def words(rec: dict | None) -> str:
    """The decision in the editor's words; "" when the file has no flag to talk about."""
    if not rec or not rec.get("flag"):
        return ""
    flag, why, sensor = rec["flag"], rec.get("why"), rec.get("sensor")
    turn = {90: "90° one way", 180: "180°", 270: "90° the other way"}.get(flag, f"{flag}°")
    if why == "hand":
        if rec.get("mode") == "none":
            return f"Rotation flag ignored (the file says turn it {turn}): you set this by hand."
        return f"Turned {turn} by the file's rotation flag: you set this by hand."
    if why == "sensor":
        return (f"Rotation flag ignored: the file says turn it {turn}, but the camera's "
                f"sensor says it was shot {sensor or 'upright'}.")
    if why == "agrees":
        return f"Turned {turn} by the file's rotation flag; the camera's sensor says {sensor}, which agrees."
    if why == "unclear":
        return (f"Turned {turn} by the file's rotation flag; the camera's sensor is not "
                "conclusive for this clip, so the flag is trusted.")
    return (f"Turned {turn} by the file's rotation flag; there is no sensor reading for "
            "this clip, so the flag is trusted.")
