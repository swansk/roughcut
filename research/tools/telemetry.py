# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""What the camera felt — GoPro GPMF telemetry as numbers, and a scorer for R11.

The intake tracker (docs/INTAKE.md M6) allows telemetry only as an *optional* witness that
is weighted with the audio and visual passes and never trusted alone, and Karl's rule is
that it must not be over-indexed because it could be noisy or bad. This tool exists to
find out whether it is. It makes no pipeline decision; it produces the numbers R11 argues
from.

Two halves:

1. **Extract.** `ffprobe` finds the `gpmd` data stream, `ffmpeg -codec copy` dumps it, and
   a pure-Python KLV parser (4-char key, type, struct size, repeat; `DEVC`/`STRM` nesting;
   `SCAL` divisors; per-stream `STMP` microsecond clocks) recovers ACCL, GYRO, GRAV, GPS5
   (+GPSF/GPSP/GPSU). Derived at 10 Hz, matching the audio and motion tracks sample for
   sample: |accel| in g (mean/max/min per bin), gyro rate, an orientation estimate from
   low-passed ACCL (roll and pitch *relative to the clip's own mount*, because seven of
   these clips are mounted upside-down and auto-rotated — B2-orientation.json), GPS speed
   where there is a fix. Plus freefall runs (|a| < 0.3 g for >= 0.25 s) and impact peaks
   (|a| > 3 g with prominence) on the raw 200 Hz series.

2. **Score** (`--score`). The R10 adjudicated windows (nine by-eye checks plus the one
   Karl called correct) and R10's unaudited top-15 get their telemetry numbers reported;
   30 random 8 s windows per clip give the base rate; precision/recall at several
   thresholds against the adjudicated set. Nothing here decides anything — R11 does.

Usage:
    uv run research/tools/telemetry.py ~/footage/killington-neutral -o ~/work/app/telemetry/killington-neutral
    uv run research/tools/telemetry.py ~/footage/killington-neutral -o OUT --score --labels-out benchmarks/labels/B2-telemetry.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import struct
import subprocess
import sys
from pathlib import Path

G = 9.80665
HZ = 10.0                     # derived-series rate; R8 audio and R10 motion tracks are 10 Hz
LOWPASS_S = 1.0               # orientation = ACCL averaged over this; a jolt is not a tilt

FREEFALL_G = 0.3              # INTAKE M6 spec: |a| < 0.3 g ...
FREEFALL_MIN_S = 0.25         # ... for at least 0.25 s
FREEFALL_LOOSE_G = 0.5        # a second, looser floor stored so the scorer can sweep
FREEFALL_STORE_MIN_S = 0.15   # runs are stored from here; the scorer applies the 0.25/0.5 s cuts
IMPACT_G = 3.0                # INTAKE M6 spec: |a| > 3 g ...
IMPACT_PROMINENCE_G = 1.0     # ... with prominence (this much above the surrounding trough)
IMPACT_MIN_SEP_S = 0.5        # two peaks closer than this are one landing

# A stop: GPS speed falls from >= STOP_FROM_MS to <= STOP_TO_MS within STOP_WITHIN_S.
# Written after seeing the one adjudicated real impact (CLIP_04 271-272) do exactly this —
# so it is fitted on n=1 and R11 treats it that way.
STOP_FROM_MS = 4.0
STOP_TO_MS = 1.0
STOP_WITHIN_S = 3.0
STOP_MIN_SEP_S = 5.0

GPS_MIN_FIX = 2               # GPSF: 0 none, 2 = 2D, 3 = 3D (gpmf-parser README)
GPS_MAX_DOP100 = 500          # GPSP: DOP x 100, "under 500 is good" (README)

VIDEO_SUFFIXES = {".mp4", ".mov"}

# Keys that describe a STRM rather than carry its samples.
META_KEYS = {"STNM", "SCAL", "SIUN", "UNIT", "STMP", "TSMP", "TMPC", "ORIN", "ORIO", "TYPE",
             "MTRX", "RMRK", "TICK", "TOCK", "GPSF", "GPSP", "GPSU", "GPSA", "TIMO"}

# GoPro KLV type char -> struct format (big-endian). 'q'/'Q' are fixed point.
FMT = {"b": "b", "B": "B", "s": "h", "S": "H", "l": "i", "L": "I", "f": "f", "d": "d",
       "j": "q", "J": "Q", "q": "i", "Q": "q"}
FIXED = {"q": 65536.0, "Q": float(2 ** 32)}


# --------------------------------------------------------------------------- KLV parsing

def klv_walk(buf: bytes, start: int = 0, end: int | None = None):
    """Yield (key, type_char, struct_size, repeat, data_start, data_end) for one level."""
    end = len(buf) if end is None else end
    off = start
    while off + 8 <= end:
        key = buf[off:off + 4].decode("latin1")
        t = buf[off + 4]
        sz = buf[off + 5]
        rep = struct.unpack_from(">H", buf, off + 6)[0]
        n = sz * rep
        yield key, (chr(t) if t else ""), sz, rep, off + 8, min(off + 8 + n, end)
        off += 8 + n + ((-n) % 4)


def decode(t: str, sz: int, rep: int, data: bytes):
    """Decode a leaf into a list of samples; each sample is a scalar or a tuple."""
    if t == "c":
        return [data.decode("latin1").rstrip("\x00")]
    if t == "U":                                   # yymmddhhmmss.sss
        return [data[i:i + 16].decode("latin1") for i in range(0, len(data), 16)]
    if t == "F":
        return [data[i:i + 4].decode("latin1") for i in range(0, len(data), 4)]
    if t not in FMT:                               # '?', 'G' and anything unknown
        return None
    item = struct.calcsize(FMT[t])
    if item == 0 or sz % item:
        return None
    count = sz // item
    fmt = ">" + FMT[t] * count
    out = []
    scale = FIXED.get(t)
    for i in range(rep):
        vals = struct.unpack_from(fmt, data, i * sz)
        if scale:
            vals = tuple(v / scale for v in vals)
        out.append(vals[0] if count == 1 else vals)
    return out


def parse_packet(buf: bytes, start: int, end: int) -> dict:
    """One gpmd packet -> {device: str, streams: {data_key: {...}}}."""
    out = {"device": None, "streams": {}}
    for key, t, sz, rep, ds, de in klv_walk(buf, start, end):
        if key != "DEVC" or t != "":
            continue
        for k2, t2, sz2, rep2, ds2, de2 in klv_walk(buf, ds, de):
            if k2 == "DVNM":
                out["device"] = decode(t2, sz2, rep2, buf[ds2:de2])[0]
            elif k2 == "STRM" and t2 == "":
                strm = {"meta": {}, "key": None, "samples": None, "type": None, "size": None}
                for k3, t3, sz3, rep3, ds3, de3 in klv_walk(buf, ds2, de2):
                    vals = decode(t3, sz3, rep3, buf[ds3:de3])
                    if k3 in META_KEYS:
                        strm["meta"][k3] = vals[0] if (vals and len(vals) == 1) else vals
                    else:
                        strm["key"], strm["samples"] = k3, vals
                        strm["type"], strm["size"] = t3, sz3
                if strm["key"]:
                    out["streams"][strm["key"]] = strm
    return out


# --------------------------------------------------------------------------- extraction

def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def gpmd_stream_index(video: Path) -> int | None:
    r = run(["ffprobe", "-v", "error", "-show_entries", "stream=index,codec_tag_string",
             "-of", "json", str(video)])
    if r.returncode != 0:
        return None
    for s in json.loads(r.stdout).get("streams", []):
        if s.get("codec_tag_string") == "gpmd":
            return int(s["index"])
    return None


def video_duration(video: Path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
             "csv=p=0", str(video)])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def packet_table(video: Path, index: int) -> list[tuple[float, float, int]]:
    """(pts_s, duration_s, size_bytes) per gpmd packet — the split points of the dump."""
    r = run(["ffprobe", "-v", "error", "-select_streams", str(index), "-show_entries",
             "packet=pts_time,duration_time,size", "-of", "csv=p=0", str(video)])
    rows = []
    for line in r.stdout.splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 3:
            try:
                rows.append((float(parts[0]), float(parts[1]), int(parts[2])))
            except ValueError:
                continue
    return rows


def dump_gpmd(video: Path, index: int, out: Path) -> bytes:
    r = run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(video), "-map",
             f"0:{index}", "-codec", "copy", "-f", "rawvideo", str(out)])
    if r.returncode != 0:
        raise RuntimeError(f"gpmd dump failed for {video.name}: {r.stderr[-300:]}")
    return out.read_bytes()


def apply_scale(samples, scal):
    if samples is None or scal is None:
        return samples
    if isinstance(scal, (int, float)):
        if isinstance(samples[0], tuple):
            return [tuple(v / scal for v in s) for s in samples]
        return [s / scal for s in samples]
    scal = list(scal)
    return [tuple(v / (scal[i] if i < len(scal) and scal[i] else 1.0)
                  for i, v in enumerate(s)) for s in samples]


def collect_streams(buf: bytes, packets: list[tuple[float, float, int]]) -> dict:
    """Walk every packet; per data key, a time-stamped sample list.

    Timing: each STRM carries STMP, the sensor clock in microseconds at the packet's first
    sample. Samples inside a packet are spread evenly to the next packet's STMP. The clock
    is anchored so the first packet's first sample sits at that packet's pts. Without STMP
    (older firmware) the packet pts and duration are used instead.
    """
    per_key: dict[str, dict] = {}
    device = None
    off = 0
    raw: dict[str, list] = {}
    for pi, (pts, dur, size) in enumerate(packets):
        pkt = parse_packet(buf, off, off + size)
        off += size
        device = device or pkt["device"]
        for key, strm in pkt["streams"].items():
            if strm["samples"] is None:
                continue
            d = per_key.setdefault(key, {"meta": {}, "packets": []})
            d["meta"].update({k: v for k, v in strm["meta"].items()
                              if k in ("STNM", "SIUN", "UNIT", "ORIN", "ORIO", "SCAL", "TYPE")})
            d["packets"].append({
                "pts": pts, "dur": dur, "stmp": strm["meta"].get("STMP"),
                "tsmp": strm["meta"].get("TSMP"), "n": len(strm["samples"]),
                "samples": apply_scale(strm["samples"], strm["meta"].get("SCAL")),
                "gpsf": strm["meta"].get("GPSF"), "gpsp": strm["meta"].get("GPSP"),
                "gpsu": strm["meta"].get("GPSU"),
            })
    streams = {}
    for key, d in per_key.items():
        pk = d["packets"]
        have_stmp = all(p["stmp"] is not None for p in pk) and len(pk) > 1
        times, vals, fix, dop = [], [], [], []
        base_pts = pk[0]["pts"]
        stmp0 = pk[0]["stmp"] if have_stmp else None
        for i, p in enumerate(pk):
            n = p["n"]
            if n == 0:
                continue
            if have_stmp:
                t_start = base_pts + (p["stmp"] - stmp0) / 1e6
                if i + 1 < len(pk):
                    span = (pk[i + 1]["stmp"] - p["stmp"]) / 1e6
                else:
                    span = p["dur"] if p["dur"] > 0 else (pk[i - 1]["stmp"] - pk[i - 2]["stmp"]) / 1e6 if i >= 2 else 1.0
            else:
                t_start, span = p["pts"], (p["dur"] if p["dur"] > 0 else 1.0)
            dt = span / n
            for j, s in enumerate(p["samples"]):
                times.append(t_start + j * dt)
                vals.append(s)
                fix.append(p["gpsf"])
                dop.append(p["gpsp"])
        span_total = (times[-1] - times[0]) if len(times) > 1 else 0.0
        stmp_drift = None
        if have_stmp:
            stmp_drift = round((pk[-1]["stmp"] - stmp0) / 1e6 - (pk[-1]["pts"] - base_pts), 3)
        streams[key] = {
            "name": d["meta"].get("STNM"), "unit": d["meta"].get("SIUN") or d["meta"].get("UNIT"),
            "orin": d["meta"].get("ORIN"), "scal": d["meta"].get("SCAL"),
            "samples": len(vals), "rate_hz": round(len(vals) / span_total, 2) if span_total else None,
            "timing": "STMP" if have_stmp else "packet pts", "stmp_minus_pts_drift_s": stmp_drift,
            "t": times, "v": vals, "fix": fix, "dop": dop,
        }
    return {"device": device, "streams": streams}


# --------------------------------------------------------------------------- derived series

def bins_for(duration: float, hz: float = HZ) -> int:
    return max(1, int(math.ceil(duration * hz)))


def binned(times, values, nbins, hz=HZ):
    """Per 10 Hz bin: (mean, max, min) of a scalar series; None where empty."""
    acc = [[0.0, 0, -math.inf, math.inf] for _ in range(nbins)]
    for t, v in zip(times, values):
        b = int(t * hz)
        if 0 <= b < nbins:
            a = acc[b]
            a[0] += v; a[1] += 1
            if v > a[2]: a[2] = v
            if v < a[3]: a[3] = v
    mean = [round(a[0] / a[1], 4) if a[1] else None for a in acc]
    mx = [round(a[2], 4) if a[1] else None for a in acc]
    mn = [round(a[3], 4) if a[1] else None for a in acc]
    return mean, mx, mn


def robust_z(vals: list[float]) -> list[float]:
    """Median/MAD, as roughcut/events.py: is this unusual *for this clip*."""
    xs = [v for v in vals if v is not None]
    if not xs:
        return [None] * len(vals)
    med = statistics.median(xs)
    mad = statistics.median([abs(v - med) for v in xs])
    scale = 1.4826 * mad or 1e-6
    return [None if v is None else round((v - med) / scale, 2) for v in vals]


def freefall_runs(times, mag_g, floor_g: float = FREEFALL_G,
                  min_s: float = FREEFALL_STORE_MIN_S) -> list[dict]:
    """Contiguous raw samples below `floor_g` lasting >= `min_s`."""
    runs, start, lo = [], None, None
    for i, (t, m) in enumerate(zip(times, mag_g)):
        if m < floor_g:
            if start is None:
                start, lo = t, m
            lo = min(lo, m)
        elif start is not None:
            dur = t - start
            if dur >= min_s:
                runs.append({"start": round(start, 2), "end": round(t, 2),
                             "dur": round(dur, 2), "min_g": round(lo, 3)})
            start = None
    if start is not None and times and times[-1] - start >= min_s:
        runs.append({"start": round(start, 2), "end": round(times[-1], 2),
                     "dur": round(times[-1] - start, 2), "min_g": round(lo, 3)})
    return runs


def stops(speed: list[float | None], hz: float = HZ) -> list[dict]:
    """Where the GPS says the camera came to a halt from speed."""
    out: list[dict] = []
    n = len(speed)
    win = int(STOP_WITHIN_S * hz)
    for i in range(n):
        v = speed[i]
        if v is None or v < STOP_FROM_MS:
            continue
        for j in range(i + 1, min(n, i + win + 1)):
            w = speed[j]
            if w is not None and w <= STOP_TO_MS:
                at = round(j / hz, 2)
                if out and at - out[-1]["at"] < STOP_MIN_SEP_S:
                    out[-1]["from_ms"] = max(out[-1]["from_ms"], round(v, 2))
                else:
                    out.append({"at": at, "from_ms": round(v, 2), "to_ms": round(w, 2),
                                "within_s": round((j - i) / hz, 1)})
                break
    return out


def prominence(series: list[float], i: int) -> float:
    """scipy-style: height above the higher of the two troughs before a taller sample."""
    v = series[i]
    left = v
    for j in range(i - 1, -1, -1):
        if series[j] > v:
            break
        left = min(left, series[j])
    right = v
    for j in range(i + 1, len(series)):
        if series[j] > v:
            break
        right = min(right, series[j])
    return v - max(left, right)


def impact_peaks(max_g: list[float | None], hz: float = HZ, floor_g: float = 1.0) -> list[dict]:
    """Every local maximum of the 10 Hz |a|max series above `floor_g`, with prominence
    and robust z. Thresholds are applied later by the scorer so several can be compared
    from one list."""
    s = [v if v is not None else 0.0 for v in max_g]
    z = robust_z(s)
    peaks = []
    n = len(s)
    for i in range(n):
        if s[i] < floor_g:
            continue
        if (i > 0 and s[i - 1] > s[i]) or (i + 1 < n and s[i + 1] >= s[i]):
            continue
        peaks.append({"at": round((i + 0.5) / hz, 2), "value": round(s[i], 3), "unit": "g",
                      "prominence": round(prominence(s, i), 3), "z": z[i]})
    # merge peaks closer than IMPACT_MIN_SEP_S, keeping the taller
    peaks.sort(key=lambda p: p["at"])
    merged: list[dict] = []
    for p in peaks:
        if merged and p["at"] - merged[-1]["at"] < IMPACT_MIN_SEP_S:
            if p["value"] > merged[-1]["value"]:
                merged[-1] = p
        else:
            merged.append(p)
    return merged


def lowpass(series: list[float | None], half: int) -> list[float | None]:
    out = []
    n = len(series)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        xs = [v for v in series[lo:hi] if v is not None]
        out.append(sum(xs) / len(xs) if xs else None)
    return out


def orientation(ax, ay, az, vertical: int, lateral: int, forward: int) -> dict:
    """Gravity direction from low-passed ACCL, expressed relative to the clip's mount.

    A resting accelerometer reads +g *upward* (it measures the support force), so the
    low-passed ACCL unit vector is the camera's "up" in its own axes. The clip's mount
    baseline is the sign of the median vertical component: +1 when the camera body is the
    right way up, -1 when it is mounted inverted (the seven rotation=-180 clips in
    B2-orientation.json). Roll and pitch are then measured *against that baseline*, which
    is what the auto-rotated image actually shows: on an inverted mount a level shot has
    roll 0, not 180.
    """
    half = int(LOWPASS_S * HZ / 2)
    comps = [lowpass(ax, half), lowpass(ay, half), lowpass(az, half)]
    n = len(ax)
    unit = []
    for i in range(n):
        v = [comps[k][i] for k in range(3)]
        if any(x is None for x in v):
            unit.append(None); continue
        m = math.sqrt(sum(x * x for x in v)) or 1e-9
        unit.append([x / m for x in v])
    verts = [u[vertical] for u in unit if u]
    median_vec = [statistics.median([u[k] for u in unit if u]) for k in range(3)] if verts else None
    baseline = 1 if (verts and statistics.median(verts) >= 0) else -1
    roll, pitch, tilt = [], [], []
    for u in unit:
        if not u:
            roll.append(None); pitch.append(None); tilt.append(None); continue
        v = u[vertical] * baseline
        roll.append(round(math.degrees(math.atan2(u[lateral] * baseline, v)), 1))
        pitch.append(round(math.degrees(math.atan2(u[forward], v)), 1))
        tilt.append(round(math.degrees(math.acos(max(-1.0, min(1.0, v)))), 1))
    valid = [t for t in tilt if t is not None]
    def frac(pred):
        return round(sum(1 for t in valid if pred(t)) / len(valid), 3) if valid else None
    return {
        "convention": {"vertical": vertical, "lateral": lateral, "forward": forward,
                       "note": "ACCL data order per ORIN; vertical/lateral/forward "
                               "assignment is an input, checked empirically in R11"},
        "median_up_vector": [round(x, 3) for x in median_vec] if median_vec else None,
        "mount": "upright" if baseline > 0 else "inverted",
        "mount_sign": baseline,
        "tilt_from_mount_deg": tilt, "roll_deg": roll, "pitch_deg": pitch,
        "fraction_level": frac(lambda t: t < 30), "fraction_tilted_30_90": frac(lambda t: 30 <= t < 90),
        "fraction_past_horizontal": frac(lambda t: t >= 90),
        "fraction_abs_roll_over_30": (round(sum(1 for r in roll if r is not None and abs(r) > 30)
                                            / len(valid), 3) if valid else None),
        "fraction_abs_roll_over_45": (round(sum(1 for r in roll if r is not None and abs(r) > 45)
                                            / len(valid), 3) if valid else None),
    }


def analyse(clip: str, video: Path, dump_dir: Path, args) -> dict:
    index = gpmd_stream_index(video)
    duration = video_duration(video)
    out = {"clip": clip, "file": str(video.resolve()), "duration_s": round(duration, 3),
           "hz": HZ, "gpmd": index is not None}
    if index is None:
        out["error"] = "no gpmd stream"
        return out
    packets = packet_table(video, index)
    buf = dump_gpmd(video, index, dump_dir / f"{clip}.gpmd.bin")
    if sum(p[2] for p in packets) != len(buf):
        out["warning"] = (f"packet sizes sum to {sum(p[2] for p in packets)} but dump is "
                          f"{len(buf)} bytes; splitting by size may be off")
    coll = collect_streams(buf, packets)
    out["device"] = coll["device"]
    out["packets"] = len(packets)
    out["packet_hz"] = round(len(packets) / duration, 3) if duration else None
    streams = coll["streams"]
    out["streams"] = {k: {kk: v[kk] for kk in ("name", "unit", "orin", "scal", "samples",
                                                "rate_hz", "timing", "stmp_minus_pts_drift_s")}
                      for k, v in streams.items()}
    nb = bins_for(duration)
    series: dict[str, list] = {}

    # --- accelerometer
    acc = streams.get("ACCL")
    if acc and acc["v"] and isinstance(acc["v"][0], tuple):
        t = acc["t"]
        mag = [math.sqrt(sum(c * c for c in s)) / G for s in acc["v"]]
        mean, mx, mn = binned(t, mag, nb)
        series["accel_g_mean"], series["accel_g_max"], series["accel_g_min"] = mean, mx, mn
        series["accel_g_max_z"] = robust_z(mx)
        comps = [[s[k] / G for s in acc["v"]] for k in range(3)]
        cm = [binned(t, c, nb)[0] for c in comps]
        out["freefall"] = freefall_runs(t, mag, FREEFALL_G)
        out["freefall_loose"] = freefall_runs(t, mag, FREEFALL_LOOSE_G)
        out["impacts"] = impact_peaks(mx)
        out["orientation"] = orientation(cm[0], cm[1], cm[2], args.vertical, args.lateral,
                                         args.forward)
        series["roll_deg"] = out["orientation"].pop("roll_deg")
        series["pitch_deg"] = out["orientation"].pop("pitch_deg")
        series["tilt_deg"] = out["orientation"].pop("tilt_from_mount_deg")
        out["accel"] = {"median_g": round(statistics.median(mag), 3),
                        "p99_g": round(sorted(mag)[int(0.99 * (len(mag) - 1))], 3),
                        "max_g": round(max(mag), 3), "min_g": round(min(mag), 3)}
    else:
        out["freefall"], out["freefall_loose"], out["impacts"] = [], [], []

    # --- gravity vector as the camera computed it (HERO8+), for cross-checking
    grav = streams.get("GRAV")
    if grav and grav["v"] and isinstance(grav["v"][0], tuple):
        gm = [binned(grav["t"], [s[k] for s in grav["v"]], nb)[0] for k in range(3)]
        valid = [i for i in range(nb) if all(gm[k][i] is not None for k in range(3))]
        out["grav"] = {"median_vector": [round(statistics.median([gm[k][i] for i in valid]), 3)
                                         for k in range(3)] if valid else None}
        if "accel_g_mean" in series and valid:
            # GRAV's component order is undocumented (gpmf-parser README); the full
            # correlation matrix against the ACCL low-pass shows which axis is which.
            ag = [lowpass(cm[k], int(LOWPASS_S * HZ / 2)) for k in range(3)]
            out["grav"]["corr_accl_x_grav"] = [
                [round(_pearson([ag[a][i] for i in valid], [gm[g][i] for i in valid]), 3)
                 for g in range(3)] for a in range(3)]

    # --- gyro
    gyr = streams.get("GYRO")
    if gyr and gyr["v"] and isinstance(gyr["v"][0], tuple):
        rate = [math.sqrt(sum(c * c for c in s)) for s in gyr["v"]]
        mean, mx, _ = binned(gyr["t"], rate, nb)
        series["gyro_rads_mean"], series["gyro_rads_max"] = mean, mx
        out["gyro"] = {"median_rads": round(statistics.median(rate), 3),
                       "max_rads": round(max(rate), 3)}

    # --- GPS
    gps = streams.get("GPS5") or streams.get("GPS9")
    if gps and gps["v"] and isinstance(gps["v"][0], tuple):
        key = "GPS5" if "GPS5" in streams else "GPS9"
        ts, sp, good = [], [], 0
        for t, s, f, d in zip(gps["t"], gps["v"], gps["fix"], gps["dop"]):
            if key == "GPS9":
                f, d = s[8], s[7] * 100
            ok = (f is not None and f >= GPS_MIN_FIX) and (d is None or d < GPS_MAX_DOP100)
            if ok:
                ts.append(t); sp.append(s[3]); good += 1
        mean, mx, _ = binned(ts, sp, nb) if ts else ([None] * nb, [None] * nb, None)
        series["speed_ms"] = mean
        out["stops"] = stops(mean)
        out["gps"] = {"source": key, "fix_fraction": round(good / len(gps["v"]), 3),
                      "samples_with_fix": good,
                      "max_speed_ms": round(max(sp), 2) if sp else None,
                      "median_speed_ms": round(statistics.median(sp), 2) if sp else None,
                      "fix_values": sorted({int(f) for f in gps["fix"] if f is not None})}
    out["series"] = series
    # picks.felt_witnesses shape: numbers only
    out["peaks"] = [{"at": p["at"], "value": p["value"], "unit": "g"}
                    for p in out["impacts"] if p["value"] >= IMPACT_G
                    and p["prominence"] >= IMPACT_PROMINENCE_G]
    return out


def _pearson(a, b):
    n = len(a)
    if n < 2:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a)) * math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / da if da else 0.0


# --------------------------------------------------------------------------- scoring

# R10 Result 3: nine windows adjudicated by eye, plus the one Karl called correct.
# `felt` = would the *camera* have felt an impact or an air here (telemetry only knows
# the body it is bolted to; a subject 30 m away falling is invisible to it by construction).
# `tilted` = R10 attributed the vision claim to a rolled camera (helmet/chest POV).
R10_ADJUDICATED = [
    {"clip": "CLIP_01", "start": 104, "end": 108, "claim": "fall (person down in snow) — Karl: CORRECT",
     "truth": "static subject in a static frame; real event, not camera-felt", "felt": False, "tilted": False},
    {"clip": "CLIP_04", "start": 19, "end": 28, "claim": "fine: fall 22-23",
     "truth": "a face fills frame, arms out — a reaction, not a fall", "felt": False, "tilted": False},
    {"clip": "CLIP_04", "start": 249, "end": 259, "claim": "coarse: jump/backflip 252-256; fine: glove 253-255",
     "truth": "a rider over a roller 250-251, a glove at 254; no backflip", "felt": False, "tilted": False},
    {"clip": "CLIP_04", "start": 263, "end": 274, "claim": "coarse: fall 264-272; fine: glare 270-271",
     "truth": "the camera goes into the snow at 271-272", "felt": True, "tilted": False},
    {"clip": "CLIP_07", "start": 12, "end": 21, "claim": "fine: fall/flip 14-16",
     "truth": "camera being unmounted and handled; a face at 17", "felt": False, "tilted": False},
    {"clip": "CLIP_07", "start": 27, "end": 34, "claim": "coarse: action 28-32; fine: fall 28-30",
     "truth": "a gloved hand adjusting the camera at the top of a lift", "felt": False, "tilted": False},
    {"clip": "CLIP_07", "start": 168, "end": 196, "claim": "coarse: jump, airborne across 8 frames",
     "truth": "two people traversing a bank on a follow-cam tilted ~40°", "felt": False, "tilted": True},
    # R10 wrote "helmet-cam POV through trees; the horizon rolls, the rider does not" and
    # scored it felt=False. R11 re-read it at 0.5 s after the accelerometer put a 6.7 g
    # peak at 144.3: spray on the lens at 144.0, the wearer's skis against the sky at
    # 145.0-145.5, tree canopy from below to 149, a pole strap being sorted at 155. The
    # wearer fell. The "upside-down rider" of the coarse pass was the camera on its back.
    {"clip": "CLIP_11", "start": 144, "end": 158, "claim": "coarse: jump 136-160, upside-down 148-156",
     "truth": "R11 by eye at 0.5 s: the wearer falls at 144.3 and lies on his back to ~150; "
              "R10 had read the canopy-from-below frames as a rolled POV", "felt": True, "tilted": True},
    {"clip": "CLIP_11", "start": 199, "end": 208, "claim": "coarse: inverted aerial trick; fine: helmet-cam cut 201-202",
     "truth": "POV with a pole and ski in frame, tilted horizon", "felt": False, "tilted": True},
    {"clip": "CLIP_11", "start": 256, "end": 265, "claim": "coarse: fall/wipeout 252-260; fine: jump 260-262",
     "truth": "a glove at 256-258, then a chairlift passenger", "felt": False, "tilted": False},
]

# R10 Result 5: the top-15 ranked events, unaudited unless marked. Reported, not scored.
R10_TOP15 = [
    (1, "CLIP_06", 204, 220, "crash", "unaudited"), (2, "CLIP_04", 22, 23, "fall", "by eye: a reaction"),
    (3, "CLIP_07", 14, 16, "fall", "by eye: camera handling"), (4, "CLIP_09", 192, 200, "crash", "shot 12"),
    (5, "CLIP_09", 120, 128, "jump", "unaudited"), (6, "CLIP_11", 136, 160, "jump", "by eye: tilted POV"),
    (7, "CLIP_11", 260, 262, "jump", "by eye: chairlift passenger"), (8, "CLIP_09", 68, 76, "fall", "unaudited"),
    (9, "CLIP_06", 8, 16, "fall", "shot 7"), (10, "CLIP_04", 4, 8, "jump", "unaudited"),
    (11, "CLIP_01", 104, 108, "fall", "shot 1 — Karl: correct"), (12, "CLIP_06", 304, 312, "jump", "shot 20"),
    (13, "CLIP_07", 28, 30, "fall", "by eye: camera handling"), (14, "CLIP_05", 4, 12, "jump", "unaudited"),
    (15, "CLIP_11", 100, 108, "jump", "unaudited"),
]


def window_stats(summary: dict, start: float, end: float) -> dict:
    s = summary.get("series", {})
    hz = summary.get("hz", HZ)
    lo, hi = int(start * hz), int(math.ceil(end * hz))
    def sl(key):
        return [v for v in s.get(key, [])[lo:hi] if v is not None]
    mx = sl("accel_g_max"); mn = sl("accel_g_min"); gz = sl("accel_g_max_z")
    roll = sl("roll_deg"); tilt = sl("tilt_deg"); gy = sl("gyro_rads_max"); sp = sl("speed_ms")
    ff = [r for r in summary.get("freefall", []) if r["end"] > start and r["start"] < end]
    ffl = [r for r in summary.get("freefall_loose", []) if r["end"] > start and r["start"] < end]
    im = [p for p in summary.get("impacts", []) if start <= p["at"] < end]
    st = [s for s in summary.get("stops", []) if start <= s["at"] < end]
    # largest speed drop over any 3 s inside the window
    spd = s.get("speed_ms", [])[lo:hi]
    drop = 0.0
    win = int(STOP_WITHIN_S * hz)
    for i, v in enumerate(spd):
        if v is None:
            continue
        later = [w for w in spd[i + 1:i + 1 + win] if w is not None]
        if later:
            drop = max(drop, v - min(later))
    return {
        "accel_max_g": round(max(mx), 2) if mx else None,
        "accel_max_z": round(max(gz), 1) if gz else None,
        "accel_min_g": round(min(mn), 2) if mn else None,
        "freefall_0.3g_longest_s": round(max((r["dur"] for r in ff), default=0.0), 2),
        "freefall_0.5g_longest_s": round(max((r["dur"] for r in ffl), default=0.0), 2),
        "impact_peaks_3g": sum(1 for p in im if p["value"] >= 3.0 and p["prominence"] >= IMPACT_PROMINENCE_G),
        "impact_peaks_5g": sum(1 for p in im if p["value"] >= 5.0 and p["prominence"] >= IMPACT_PROMINENCE_G),
        "impact_best": max(im, key=lambda p: p["value"], default=None),
        "gyro_max_rads": round(max(gy), 2) if gy else None,
        "roll_abs_max_deg": round(max(abs(r) for r in roll), 1) if roll else None,
        "roll_over_30_frac": round(sum(1 for r in roll if abs(r) > 30) / len(roll), 2) if roll else None,
        "tilt_max_deg": round(max(tilt), 1) if tilt else None,
        "speed_max_ms": round(max(sp), 1) if sp else None,
        "speed_mean_ms": round(sum(sp) / len(sp), 1) if sp else None,
        "speed_drop_3s_ms": round(drop, 1),
        "stops": len(st),
    }


def fires(ws: dict, rule: str) -> bool:
    b = ws.get("impact_best")
    if rule == "freefall_0.3g_0.25s":
        return ws["freefall_0.3g_longest_s"] >= 0.25
    if rule == "freefall_0.5g_0.25s":
        return ws["freefall_0.5g_longest_s"] >= 0.25
    if rule == "freefall_0.5g_0.5s":
        return ws["freefall_0.5g_longest_s"] >= 0.5
    if rule == "impact_3g":
        return ws["impact_peaks_3g"] > 0
    if rule == "impact_5g":
        return ws["impact_peaks_5g"] > 0
    if rule == "impact_7g":
        return bool(b) and b["value"] >= 7.0 and b["prominence"] >= IMPACT_PROMINENCE_G
    if rule == "stop_4to1":
        return ws["stops"] > 0
    if rule == "speed_drop_3ms":
        return ws["speed_drop_3s_ms"] >= 3.0
    raise KeyError(rule)


RULES = ["freefall_0.3g_0.25s", "freefall_0.5g_0.25s", "freefall_0.5g_0.5s",
         "impact_3g", "impact_5g", "impact_7g", "stop_4to1", "speed_drop_3ms"]


def air_then_impact(summary: dict, start: float, end: float) -> bool:
    """A loose freefall run (< 0.5 g for >= 0.25 s) followed within 1.5 s by a >= 3 g
    peak — the shape a landed air should have."""
    for r in summary.get("freefall_loose", []):
        if r["dur"] < 0.25 or not (r["end"] > start and r["start"] < end):
            continue
        for p in summary.get("impacts", []):
            if r["end"] <= p["at"] <= r["end"] + 1.5 and p["value"] >= 3.0 \
                    and p["prominence"] >= IMPACT_PROMINENCE_G:
                return True
    return False


def score(summaries: dict[str, dict], n_random: int, window_s: float, seed: int) -> dict:
    rng = random.Random(seed)
    adjudicated = []
    for a in R10_ADJUDICATED:
        sm = summaries.get(a["clip"])
        if not sm or "series" not in sm:
            continue
        ws = window_stats(sm, a["start"], a["end"])
        ws["air_then_impact"] = air_then_impact(sm, a["start"], a["end"])
        adjudicated.append({**a, "stats": ws,
                            "fires": {r: fires(ws, r) for r in RULES} | {"air_then_impact": ws["air_then_impact"]}})
    top15 = []
    for rank, clip, s, e, kind, note in R10_TOP15:
        sm = summaries.get(clip)
        if not sm or "series" not in sm:
            continue
        ws = window_stats(sm, s, e)
        ws["air_then_impact"] = air_then_impact(sm, s, e)
        top15.append({"rank": rank, "clip": clip, "start": s, "end": e, "kind": kind, "note": note,
                      "stats": ws, "fires": {r: fires(ws, r) for r in RULES}
                      | {"air_then_impact": ws["air_then_impact"]}})
    randoms = []
    for clip, sm in sorted(summaries.items()):
        if "series" not in sm:
            continue
        dur = sm["duration_s"]
        for _ in range(n_random):
            st = rng.uniform(0, max(0.0, dur - window_s))
            ws = window_stats(sm, st, st + window_s)
            ws["air_then_impact"] = air_then_impact(sm, st, st + window_s)
            randoms.append({"clip": clip, "start": round(st, 1), "end": round(st + window_s, 1),
                            "stats": ws, "fires": {r: fires(ws, r) for r in RULES}
                            | {"air_then_impact": ws["air_then_impact"]}})
    # precision / recall against the adjudicated set, per rule
    pr = {}
    for r in RULES + ["air_then_impact"]:
        tp = sum(1 for a in adjudicated if a["felt"] and a["fires"][r])
        fp = sum(1 for a in adjudicated if not a["felt"] and a["fires"][r])
        fn = sum(1 for a in adjudicated if a["felt"] and not a["fires"][r])
        base = sum(1 for w in randoms if w["fires"][r]) / len(randoms) if randoms else None
        pr[r] = {"tp": tp, "fp": fp, "fn": fn,
                 "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                 "recall": round(tp / (tp + fn), 3) if tp + fn else None,
                 "random_window_fire_rate": round(base, 3) if base is not None else None,
                 "random_windows_fired": sum(1 for w in randoms if w["fires"][r])}
    return {"n_random_per_clip": n_random, "window_s": window_s, "seed": seed,
            "adjudicated": adjudicated, "top15": top15, "random": randoms, "precision_recall": pr}


def print_score(sc: dict) -> None:
    def fmt(ws):
        b = ws["impact_best"]
        best = f"{b['value']:.1f}g/z{b['z']:.0f}" if b else "—"
        return (f"max {ws['accel_max_g'] or 0:4.1f}g "
                f"min {ws['accel_min_g'] if ws['accel_min_g'] is not None else 0:4.2f}g "
                f"ff.3 {ws['freefall_0.3g_longest_s']:.2f}s ff.5 {ws['freefall_0.5g_longest_s']:.2f}s "
                f"imp3g {ws['impact_peaks_3g']:>2} best {best:>10} "
                f"roll {ws['roll_abs_max_deg'] or 0:5.1f}° ({ws['roll_over_30_frac'] or 0:.2f}) "
                f"spd {ws['speed_max_ms'] if ws['speed_max_ms'] is not None else '—':>4} "
                f"drop {ws['speed_drop_3s_ms']:.1f} stops {ws['stops']}")
    print("\n== R10 adjudicated windows")
    for a in sc["adjudicated"]:
        print(f"{a['clip']} {a['start']:>4}-{a['end']:<4} felt={'Y' if a['felt'] else 'n'} "
              f"tilt={'Y' if a['tilted'] else 'n'} | {fmt(a['stats'])} | {a['truth'][:48]}")
    print("\n== R10 top-15 (unaudited unless noted)")
    for a in sc["top15"]:
        print(f"#{a['rank']:<2} {a['clip']} {a['start']:>4}-{a['end']:<4} {a['kind']:<6} | "
              f"{fmt(a['stats'])} | {a['note']}")
    print(f"\n== random windows: {len(sc['random'])} × {sc['window_s']} s")
    rs = sc["random"]
    for key in ("accel_max_g", "accel_min_g", "roll_abs_max_deg", "speed_drop_3s_ms",
                "freefall_0.5g_longest_s"):
        xs = sorted(w["stats"][key] for w in rs if w["stats"][key] is not None)
        if xs:
            q = lambda p: xs[int(p * (len(xs) - 1))]
            print(f"  {key:<18} p10 {q(.1):6.2f}  p50 {q(.5):6.2f}  p90 {q(.9):6.2f}  p99 {q(.99):6.2f}  max {xs[-1]:6.2f}")
    print("\n== precision / recall vs adjudicated (positives = camera-felt), and random base rate")
    print(f"{'rule':<18} {'tp':>3} {'fp':>3} {'fn':>3} {'P':>6} {'R':>6}  random fire rate")
    for r, v in sc["precision_recall"].items():
        P = "—" if v["precision"] is None else f"{v['precision']:.2f}"
        R = "—" if v["recall"] is None else f"{v['recall']:.2f}"
        print(f"{r:<18} {v['tp']:>3} {v['fp']:>3} {v['fn']:>3} {P:>6} {R:>6}  "
              f"{v['random_window_fire_rate']:.3f} ({v['random_windows_fired']}/{len(rs)})")


# --------------------------------------------------------------------------- main

def clip_label(summary: dict) -> dict:
    """The per-clip record for benchmarks/labels: everything but the 10 Hz series."""
    keep = {k: v for k, v in summary.items() if k != "series"}
    keep["impacts_over_3g"] = [p for p in summary.get("impacts", [])
                               if p["value"] >= IMPACT_G and p["prominence"] >= IMPACT_PROMINENCE_G]
    keep["impacts"] = sorted(summary.get("impacts", []), key=lambda p: -p["value"])[:10]
    return keep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("footage", type=Path, help="directory of GoPro clips (symlinks fine)")
    ap.add_argument("-o", "--out", type=Path, required=True,
                    help="output dir: <stem>.telemetry.json with 10 Hz series, raw dumps, score.json")
    ap.add_argument("--only", default="", help="comma-separated stems")
    ap.add_argument("--force", action="store_true", help="re-extract even if a summary exists")
    ap.add_argument("--vertical", type=int, default=0, help="ACCL component index that is up/down")
    ap.add_argument("--lateral", type=int, default=1, help="ACCL component index that is right/left")
    ap.add_argument("--forward", type=int, default=2, help="ACCL component index that is forward/back")
    ap.add_argument("--score", action="store_true", help="score against R10 and random windows")
    ap.add_argument("--random", type=int, default=30, help="random windows per clip")
    ap.add_argument("--window", type=float, default=8.0, help="random window length, s")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--labels-out", type=Path, default=None,
                    help="write per-clip summaries (no series) as one JSON, e.g. benchmarks/labels/B2-telemetry.json")
    args = ap.parse_args()

    if not args.footage.is_dir():
        print(f"error: no footage at {args.footage}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    only = {s.strip().upper() for s in args.only.split(",") if s.strip()}
    clips = sorted(p for p in args.footage.iterdir()
                   if p.suffix.lower() in VIDEO_SUFFIXES and (not only or p.stem.upper() in only))
    summaries: dict[str, dict] = {}
    for video in clips:
        stem = video.stem.upper()
        path = args.out / f"{stem}.telemetry.json"
        if path.exists() and not args.force:
            summaries[stem] = json.loads(path.read_text(encoding="utf-8"))
            print(f"{stem}: cached", flush=True)
            continue
        try:
            sm = analyse(stem, video, args.out, args)
        except Exception as e:  # noqa: BLE001 — a research tool reports and moves on
            sm = {"clip": stem, "file": str(video), "gpmd": None, "error": f"{type(e).__name__}: {e}"}
        summaries[stem] = sm
        path.write_text(json.dumps(sm, separators=(",", ":")), encoding="utf-8")
        if "error" in sm:
            print(f"{stem}: {sm['error']}", flush=True)
            continue
        st = sm["streams"]
        acc = st.get("ACCL", {}); gps = sm.get("gps", {})
        ori = sm.get("orientation", {})
        print(f"{stem}: {sm['device']} {sm['duration_s']:.0f}s {sm['packets']} pkts | "
              f"ACCL {acc.get('rate_hz')} Hz drift {acc.get('stmp_minus_pts_drift_s')}s | "
              f"|a| med {sm['accel']['median_g']:.2f} max {sm['accel']['max_g']:.1f} g | "
              f"ff.3 {len(sm['freefall'])} ff.5 {len(sm['freefall_loose'])} "
              f"imp>3g {len(sm['peaks'])} stops {len(sm.get('stops', []))} | "
              f"mount {ori.get('mount')} up {ori.get('median_up_vector')} "
              f"roll>30° {ori.get('fraction_abs_roll_over_30')} | "
              f"GPS fix {gps.get('fix_fraction')} vmax {gps.get('max_speed_ms')} m/s", flush=True)

    if args.labels_out:
        args.labels_out.parent.mkdir(parents=True, exist_ok=True)
        payload = {"bin": "B2 / killington-neutral", "tool": "research/tools/telemetry.py",
                   "study": "R11", "hz": HZ,
                   "thresholds": {"freefall_g": FREEFALL_G, "freefall_min_s": FREEFALL_MIN_S,
                                  "impact_g": IMPACT_G, "impact_prominence_g": IMPACT_PROMINENCE_G,
                                  "gps_min_fix": GPS_MIN_FIX, "gps_max_dop100": GPS_MAX_DOP100},
                   "clips": [clip_label(s) for _, s in sorted(summaries.items())]}
        args.labels_out.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {args.labels_out}")

    if args.score:
        sc = score(summaries, args.random, args.window, args.seed)
        (args.out / "score.json").write_text(json.dumps(sc, indent=1), encoding="utf-8")
        print_score(sc)
        print(f"\nwrote {args.out / 'score.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
