"""Colour: correct, match, look (INTAKE M10).

The film has been the camera's picture untouched. This module owns everything that
changes it, under the rules EFFECTS.md set for effects and decision 5 set for ranges:

  * a closed vocabulary with clamped numbers — `balance` (gain r/g/b, exposure, knee,
    lift), `match` (a reference shot), `look` (a name from the looks library and a
    strength). Nothing here loads a filter string from a note; the renderer owns
    every string that reaches ffmpeg.
  * keyed to a segment id, expressed in parameters, never ranges: a trim re-derives the
    auto from the samples inside the new in/out and nothing bakes a range in.
  * one 33³ `.cube` per shot, applied on the part encode `assemble.py` already does,
    so the grade costs no generation of quality; the monitor applies the same LUT in
    WebGL, so the proxy and the master agree by construction.
  * the auto can only nudge: every parameter is clamped, it is computed once per shot
    from sampled frames (a fixed LUT cannot flicker), it is on for shots that show a
    white reference and off when they do not, and the human's off switch beats it.
  * colour state is per project (Karl): measurements per clip, the auto per shot, the
    film's mode / look / reference in the EDL; only the looks library is global.
  * mixed cameras normalise first (Karl): an HLG/PQ clip (iPhone by default) is tone
    mapped to SDR 709 from the probe's tags before anything else sees it, proxies
    included; a limited-range clip is read as limited. The balance sees SDR 709 only.

Pure module: numpy only, ffmpeg/ffprobe by subprocess for frames and tags. The lab
that fixed the numbers (clamps, the shoulder, the 45 dB LUT parity, the range trap) is
in `Projects/roughcut-lab/` outside the repo; INTAKE M10 records what it measured.
"""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Callable

import numpy as np

VERSION = 1

# --------------------------------------------------------------------- probe

# Transfer characteristics that are not SDR and need tone mapping before any grade.
HDR_TRC = {"arib-std-b67": "hlg", "smpte2084": "pq"}


def probe(path: Path) -> dict:
    """The tags a grade must know: pixel format and bit depth, transfer, primaries,
    matrix, range, size, rotation, and which camera family shot it. Missing tags are
    reported as `None`, never guessed — `normalise_vf` and `in_range` decide from them."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries",
         "stream=pix_fmt,width,height,color_range,color_space,color_transfer,"
         "color_primaries:stream_side_data=rotation:format_tags",
         "-of", "json", str(path)],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path.name}: {r.stderr[-200:]}")
    info = json.loads(r.stdout or "{}")
    st = (info.get("streams") or [{}])[0]
    tags = {k.lower(): v for k, v in (info.get("format", {}).get("tags") or {}).items()}
    rot = 0
    for sd in st.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    pix = st.get("pix_fmt") or ""
    bits = 10 if "10" in pix else 12 if "12" in pix else 8
    rng = st.get("color_range")
    if rng is None and pix.startswith("yuvj"):
        rng = "pc"                      # yuvj is full range by definition
    trc = st.get("color_transfer")
    return {
        "pix_fmt": pix or None, "bits": bits,
        "width": st.get("width"), "height": st.get("height"), "rotation": rot,
        "range": {"pc": "full", "tv": "limited"}.get(rng, rng),
        "transfer": trc, "primaries": st.get("color_primaries"),
        "matrix": st.get("color_space"),
        "hdr": HDR_TRC.get(trc),
        "family": camera_family(tags),
        "make": tags.get("com.apple.quicktime.make") or tags.get("make"),
        "firmware": tags.get("firmware"),
    }


def camera_family(tags: dict) -> str:
    """`gopro`, `iphone` or `other`, from the container tags. GoPro writes its firmware
    (`HD9.01…` on the HERO9); Apple writes `com.apple.quicktime.make`."""
    fw = str(tags.get("firmware") or "")
    if fw[:2] in ("HD", "H2", "H1") or "gopro" in str(tags.get("encoder") or "").lower():
        return "gopro"
    make = str(tags.get("com.apple.quicktime.make") or tags.get("make") or "").lower()
    if "apple" in make:
        return "iphone"
    return "other"


def normalise_vf(p: dict) -> str | None:
    """The filter fragment that brings an HDR clip to SDR 709 limited range, or `None`
    for a clip that is SDR already. Input tags are stated explicitly so `zscale` never
    has to guess (it refuses untagged input) and the tone map is Hable with no
    desaturation — the standard ffmpeg HDR→SDR recipe."""
    if not p.get("hdr"):
        return None
    tin = p.get("transfer") or "arib-std-b67"
    pin = p.get("primaries") or "bt2020"
    m = p.get("matrix") or "bt2020nc"
    rin = "full" if p.get("range") == "full" else "limited"
    return (f"zscale=tin={tin}:pin={pin}:min={m}:rin={rin}:t=linear:npl=100,"
            f"format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
            f"zscale=t=bt709:m=bt709:r=limited,format=yuv420p")


def in_range(p: dict) -> str:
    """What the LUT chain must tell swscale about its input. After `normalise_vf` the
    picture is limited; otherwise the tag decides, and an untagged 8-bit clip is read
    as limited (the safe default — full-range GoPro is tagged `yuvj`)."""
    if p.get("hdr"):
        return "limited"
    return "full" if p.get("range") == "full" else "limited"


# --------------------------------------------------------------------- frames

def sample_frames(path: Path, every_s: float = 5.0, width: int = 320,
                  vf_before: str | None = None) -> tuple[list[float], np.ndarray]:
    """Decode one frame every `every_s` seconds as float RGB in [0, 1], `width` wide.
    Returns `(times, frames)` with `frames` shaped (n, h, w, 3). Autorotate is left on
    (the helmet camera was mounted inverted on Killington; the tag is right there)."""
    p = probe(path)
    w0, h0 = p["width"] or 16, p["height"] or 9
    if p["rotation"] % 180 != 0:
        w0, h0 = h0, w0
    h = max(2, int(round(width * h0 / w0 / 2)) * 2)
    chain = [f"fps=1/{every_s}", f"scale={width}:{h}:flags=area"]
    if vf_before:
        chain.insert(0, vf_before)
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-vf", ",".join(chain),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg could not sample {path.name}: "
                           f"{r.stderr.decode(errors='replace')[-200:]}")
    n = len(r.stdout) // (width * h * 3)
    if n == 0:
        return [], np.zeros((0, h, width, 3), np.float32)
    arr = np.frombuffer(r.stdout[: n * width * h * 3], np.uint8)
    frames = arr.reshape(n, h, width, 3).astype(np.float32) / 255.0
    return [i * every_s for i in range(n)], frames


# --------------------------------------------------------------------- colour maths

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
_M_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                       [0.2126729, 0.7151522, 0.0721750],
                       [0.0193339, 0.1191920, 0.9503041]], np.float32)
_M_XYZ2RGB = np.linalg.inv(_M_RGB2XYZ).astype(np.float32)
_WHITE = np.array([0.95047, 1.0, 1.08883], np.float32)


def luma(rgb: np.ndarray) -> np.ndarray:
    return rgb @ LUMA


def _srgb_to_linear(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB [0,1] → CIELAB (D65), same scale as OpenCV's float Lab: L 0–100."""
    xyz = _srgb_to_linear(rgb) @ _M_RGB2XYZ.T / _WHITE
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], -1).astype(np.float32)


def from_lab(lab: np.ndarray) -> np.ndarray:
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    f = np.stack([fx, fy, fz], -1)
    xyz = np.where(f ** 3 > 0.008856, f ** 3, (f - 16 / 116) / 7.787) * _WHITE
    return _linear_to_srgb(xyz @ _M_XYZ2RGB.T).astype(np.float32)


# --------------------------------------------------------------------- measure

WHITE_L, WHITE_C = 62.0, 14.0       # bright and near-neutral: snow, sky, a wall, sand
WHITE_MIN_FRAC = 0.20               # Karl: outdoor evidence, not one bright patch
GREY_MAX_CHROMA = 10.0              # the grey-world fallback needs a nearly-grey scene
CLIP_LEVEL = 0.98


def measure(frame: np.ndarray) -> dict:
    """One frame's colour facts (the lab's `measure()`): luma percentiles, clipped
    fraction, mean chroma, the whole frame's Lab mean, and the white reference if a
    bright near-neutral surface covers enough of the frame."""
    rgb = np.clip(frame.reshape(-1, 3), 0, 1)
    y = luma(rgb)
    lab = to_lab(rgb)
    C = np.hypot(lab[:, 1], lab[:, 2])
    p = np.percentile(y, [0.5, 50, 99.5])
    white = (lab[:, 0] > WHITE_L) & (C < WHITE_C)
    out = {
        "y_lo": round(float(p[0]), 4), "y_mid": round(float(p[1]), 4),
        "y_hi": round(float(p[2]), 4),
        "clip": round(float((rgb.max(-1) >= CLIP_LEVEL).mean()), 4),
        "chroma": round(float(C.mean()), 2),
        "lab_mean": [round(float(v), 2) for v in lab.mean(0)],
        "mean_rgb": [round(float(v), 4) for v in rgb.mean(0)],
        "white_frac": round(float(white.mean()), 4),
    }
    if white.mean() >= WHITE_MIN_FRAC:
        out["white"] = {"L": round(float(np.median(lab[white, 0])), 1),
                        "a": round(float(lab[white, 1].mean()), 2),
                        "b": round(float(lab[white, 2].mean()), 2),
                        "rgb": [round(float(v), 4) for v in rgb[white].mean(0)]}
    # Shades-of-grey (Minkowski p=6) illuminant over the unclipped pixels: the fallback
    # white when no surface qualifies, used at half the clamp.
    ok = rgb.max(-1) < CLIP_LEVEL
    if ok.sum() > 100:
        e = np.power(np.mean(np.power(rgb[ok], 6.0), 0), 1 / 6.0)
        out["grey_rgb"] = [round(float(v), 4) for v in e]
    return out


def summarise(samples: list[dict]) -> dict:
    """The medians over a shot's samples, and which white the auto may use:
    `surface` when the white reference shows in most samples, `grey` as the fallback,
    `None` when the shot is too dark or too clipped to trust either."""
    if not samples:
        return {"n": 0, "white_source": None}
    med = lambda k: float(np.median([s[k] for s in samples if k in s])) if any(k in s for s in samples) else None
    out = {"n": len(samples), "y_lo": med("y_lo"), "y_mid": med("y_mid"),
           "y_hi": med("y_hi"), "clip": med("clip"), "chroma": med("chroma"),
           "lab_mean": [float(np.median([s["lab_mean"][i] for s in samples])) for i in range(3)],
           "mean_rgb": [float(np.median([s["mean_rgb"][i] for s in samples])) for i in range(3)]}
    whites = [s["white"] for s in samples if "white" in s]
    if len(whites) * 2 >= len(samples):
        out["white"] = {"L": float(np.median([w["L"] for w in whites])),
                        "a": float(np.median([w["a"] for w in whites])),
                        "b": float(np.median([w["b"] for w in whites])),
                        "rgb": [float(np.median([w["rgb"][i] for w in whites])) for i in range(3)]}
        out["white_source"] = "surface"
    elif (out["y_mid"] is not None and out["y_mid"] >= 0.12
          and out["chroma"] is not None and out["chroma"] < GREY_MAX_CHROMA
          and any("grey_rgb" in s for s in samples)):
        # Grey-world only for a scene that is nearly grey already. Copper's airport bar
        # (chroma 16–26) averaged warm and the fallback cooled it by the whole half-clamp;
        # a colourful scene gives the auto no evidence, so it stays as shot.
        greys = [s["grey_rgb"] for s in samples if "grey_rgb" in s]
        out["grey_rgb"] = [float(np.median([g[i] for g in greys])) for i in range(3)]
        out["white_source"] = "grey"
    else:
        out["white_source"] = None
    return out


def measure_clip(path: Path, every_s: float = 5.0, width: int = 320,
                 clip_probe: dict | None = None, normalise: str | None = None) -> dict:
    """The per-clip colour file: the probe, one measurement per sampled frame and the
    clip's summary. Reads the proxy when the caller passes one (statistics do not
    need 4K); `normalise` is the HDR→SDR fragment when the source needs it."""
    times, frames = sample_frames(path, every_s, width, vf_before=normalise)
    samples = []
    for t, f in zip(times, frames):
        m = measure(f)
        m["t"] = t
        samples.append(m)
    return {"version": VERSION, "clip": path.name, "every_s": every_s,
            "probe": clip_probe, "samples": samples, "summary": summarise(samples)}


def segment_summary(clip_colour: dict, t_in: float, t_out: float) -> dict:
    """The summary over the samples inside a shot's range — the auto re-derives from
    this on every render, so a trim moves the grade with it (decision 5). Fewer than
    two samples inside the range falls back to the clip's own summary."""
    inside = [s for s in clip_colour.get("samples", []) if t_in <= s.get("t", -1) <= t_out]
    if len(inside) >= 2:
        return summarise(inside)
    return dict(clip_colour.get("summary") or summarise(clip_colour.get("samples", [])))


# --------------------------------------------------------------------- balance

# Per camera family: how far the auto may go. GoPro's own auto exposure and WB are
# the coarser, so it gets the wider clamps; the lab's ranges for the GoPro column.
CLAMPS = {
    "gopro": {"gain": 0.15, "exposure": (0.75, 1.5), "lift": 0.04},
    "iphone": {"gain": 0.10, "exposure": (0.80, 1.30), "lift": 0.03},
    "other": {"gain": 0.10, "exposure": (0.80, 1.30), "lift": 0.03},
}
WHITE_TARGET = 0.86      # the white reference's luma after exposure — textured, not clipped
HI_TARGET = 0.95         # without a white: where the 99.5th percentile goes
BLACK_TARGET = 0.03
KNEE = 0.80              # the shoulder starts here: exposure never adds clipping


def balance_params(summary: dict, family: str = "gopro") -> dict | None:
    """The auto for one shot, from its summary: gains from the white reference (or at
    half strength from the grey-world fallback), exposure to put the white at
    `WHITE_TARGET`, a shoulder, and blacks moved half-way to `BLACK_TARGET`. Every
    number clamped for the camera family. `None` when there is nothing to trust —
    the shot then renders as the camera shot it."""
    if not summary or summary.get("white_source") is None:
        return None
    c = CLAMPS.get(family, CLAMPS["other"])
    g_lo, g_hi = 1 - c["gain"], 1 + c["gain"]
    if summary["white_source"] == "surface":
        r, g, b = summary["white"]["rgb"]
        mean = (r + g + b) / 3
        gain = [float(np.clip(mean / max(ch, 1e-3), g_lo, g_hi)) for ch in (r, g, b)]
        white_y = float(np.dot(LUMA, [r, g, b]))
        exposure = float(np.clip(WHITE_TARGET / max(white_y, 1e-3), *c["exposure"]))
    else:
        r, g, b = summary["grey_rgb"]
        mean = (r + g + b) / 3
        half = c["gain"] / 2
        gain = [float(np.clip(mean / max(ch, 1e-3), 1 - half, 1 + half)) for ch in (r, g, b)]
        exposure = float(np.clip(HI_TARGET / max(summary["y_hi"] or 1.0, 1e-3), *c["exposure"]))
    lo = (summary.get("y_lo") or 0.0) * exposure
    lift = float(np.clip(0.5 * (BLACK_TARGET - lo), -c["lift"], c["lift"]))
    return {"gain": [round(v, 4) for v in gain], "exposure": round(exposure, 4),
            "knee": KNEE, "lift": round(lift, 4), "source": summary["white_source"]}


def soft_shoulder(x: np.ndarray, knee: float) -> np.ndarray:
    over = x > knee
    y = x.copy()
    y[over] = knee + (1 - knee) * (1 - np.exp(-(x[over] - knee) / (1 - knee)))
    return y


def apply_balance(rgb: np.ndarray, P: dict | None) -> np.ndarray:
    if not P:
        return np.clip(rgb, 0, 1)
    x = rgb * np.array(P["gain"], np.float32) * float(P["exposure"])
    x = soft_shoulder(x, float(P.get("knee", KNEE)))
    return np.clip(x + float(P.get("lift", 0.0)), 0, 1)


# --------------------------------------------------------------------- match

MATCH_STRENGTH = 0.7            # move most of the way, never all: two shots stay two shots
SPREAD_CLAMP = (0.85, 1.15)     # the lab: a 0.75 ratio flattened the trees


def match_params(summary: dict, reference: dict, balance: dict | None,
                 ref_balance: dict | None) -> dict | None:
    """Lab means matched to a reference shot after both balances, the spread of L
    gently. Returns `{dL, da, db, spread, pivot}` or `None` without a reference."""
    if not summary or not reference:
        return None
    src = to_lab(apply_balance(np.array(summary["mean_rgb"], np.float32), balance))
    ref = to_lab(apply_balance(np.array(reference["mean_rgb"], np.float32), ref_balance))
    d = (ref - src) * MATCH_STRENGTH
    s_spread = (summary.get("y_hi") or 1) - (summary.get("y_lo") or 0)
    r_spread = (reference.get("y_hi") or 1) - (reference.get("y_lo") or 0)
    spread = float(np.clip(r_spread / max(s_spread, 1e-3), *SPREAD_CLAMP))
    return {"dL": round(float(d[0]), 2), "da": round(float(d[1]), 2),
            "db": round(float(d[2]), 2), "spread": round(spread, 3),
            "pivot": round(float(src[0]), 2)}


def apply_match(rgb: np.ndarray, M: dict | None) -> np.ndarray:
    if not M:
        return rgb
    lab = to_lab(rgb)
    L = (lab[..., 0] - M["pivot"]) * M["spread"] + M["pivot"] + M["dL"]
    lab = np.stack([L, lab[..., 1] + M["da"], lab[..., 2] + M["db"]], -1)
    return np.clip(from_lab(lab), 0, 1)


# --------------------------------------------------------------------- looks

# The formula looks: parameters for `apply_look`. A `.cube` in the library is the same
# thing baked by someone else. Strength blends toward identity inside the bake.
FORMULA_LOOKS: dict[str, dict] = {
    "crisp": {"contrast": 0.22, "vibrance": 0.18, "hl_desat": 0.25, "knee": 0.85},
    "alpine": {"contrast": 0.28, "vibrance": 0.15, "hl_desat": 0.35, "split": 4.0,
               "warm": 1.5, "knee": 0.85},
    "filmic": {"contrast": 0.18, "sat": 0.92, "vibrance": 0.10, "hl_desat": 0.45,
               "split": 3.0, "warm": 2.5, "knee": 0.80},
}
LOOK_KEYS = {"contrast", "vibrance", "sat", "hl_desat", "split", "warm", "knee"}
LOOK_RANGES = {"contrast": (0, 0.5), "vibrance": (0, 0.5), "sat": (0.5, 1.5),
               "hl_desat": (0, 0.8), "split": (0, 8.0), "warm": (-6.0, 6.0),
               "knee": (0.6, 1.0)}


def s_curve(x: np.ndarray, s: float) -> np.ndarray:
    return x + s * 4 * x * (1 - x) * (x - 0.5)


def apply_look(rgb: np.ndarray, L: dict | None) -> np.ndarray:
    if not L:
        return np.clip(rgb, 0, 1)
    x = np.clip(rgb, 0, 1)
    x = np.clip(s_curve(x, float(L.get("contrast", 0.0))), 0, 1)
    if L.get("knee"):
        x = soft_shoulder(x, float(L["knee"]))
    lab = to_lab(x)
    Ln, a, b = lab[..., 0] / 100.0, lab[..., 1], lab[..., 2]
    C = np.hypot(a, b) + 1e-6
    k = 1 + float(L.get("vibrance", 0.0)) * np.clip(1 - C / 60.0, 0, 1)
    k = k * float(L.get("sat", 1.0))
    k = k * (1 - float(L.get("hl_desat", 0.0)) * Ln ** 3)
    a, b = a * k, b * k
    sp = float(L.get("split", 0.0))
    b = b - sp * (1 - Ln) ** 2 + sp * Ln ** 2 * 0.6
    a = a + sp * Ln ** 2 * 0.25
    b = b + float(L.get("warm", 0.0))
    return np.clip(from_lab(np.stack([Ln * 100, a, b], -1)), 0, 1)


def validate_look_params(p: dict) -> dict:
    out = {}
    for k, v in (p or {}).items():
        if k not in LOOK_KEYS:
            raise ValueError(f"unknown look parameter {k!r}")
        lo, hi = LOOK_RANGES[k]
        v = float(v)
        if not (lo <= v <= hi):
            raise ValueError(f"look parameter {k}={v} outside {lo}..{hi}")
        out[k] = v
    return out


def load_looks(root: Path | None) -> dict[str, dict]:
    """The looks library: the formula looks, plus whatever `assets/looks/manifest.json`
    describes — `{"name", "description", "params": {...}}` or `{"name", "description",
    "cube": "relative/path.cube"}`. A manifest entry that names a missing cube or an
    out-of-range parameter is dropped with its reason, not raised: a library is read
    on every render."""
    looks = {k: {"name": k, "kind": "formula", "params": dict(v),
                 "description": FORMULA_DESCRIPTIONS[k]} for k, v in FORMULA_LOOKS.items()}
    if root is None:
        return looks
    manifest = Path(root) / "looks" / "manifest.json"
    if not manifest.exists():
        return looks
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8"))
    except ValueError:
        return looks
    for e in entries if isinstance(entries, list) else entries.get("looks", []):
        name = str(e.get("name") or "").strip()
        if not name or len(name) > 40:
            continue
        try:
            if e.get("cube"):
                cube = (manifest.parent / e["cube"]).resolve()
                if not cube.exists():
                    raise ValueError("cube missing")
                table = read_cube(cube)
                looks[name] = {"name": name, "kind": "cube", "cube": str(cube),
                               "size": int(round(len(table) ** (1 / 3))),
                               "description": str(e.get("description") or "")}
            else:
                looks[name] = {"name": name, "kind": "formula",
                               "params": validate_look_params(e.get("params") or {}),
                               "description": str(e.get("description") or "")}
        except ValueError as exc:
            looks[name] = {"name": name, "kind": "broken", "error": str(exc),
                           "description": str(e.get("description") or "")}
    return looks


FORMULA_DESCRIPTIONS = {
    "crisp": "A clean S-curve and a little vibrance; highlights kept neutral. The "
             "default when the picture only needs to look finished.",
    "alpine": "Crisp plus cool shadows and warm highlights and a touch of warmth: the "
              "ski-edit look, snow white and skies deep without going teal.",
    "filmic": "Softer curve, saturation pulled, highlights desaturated, warmer overall. "
              "For the travel and the debriefs more than the skiing.",
}


# --------------------------------------------------------------------- LUTs

def bake_grid(n: int) -> np.ndarray:
    """The (n³, 3) input grid in .cube order: red fastest, then green, then blue."""
    g = np.linspace(0, 1, n, dtype=np.float32)
    B, G, R = np.meshgrid(g, g, g, indexing="ij")
    return np.stack([R, G, B], -1).reshape(-1, 3)


def cube_table(fn: Callable[[np.ndarray], np.ndarray], n: int = 33) -> np.ndarray:
    """`fn` evaluated on the grid: an (n³, 3) float32 table, .cube order."""
    return np.clip(fn(bake_grid(n)), 0, 1).astype(np.float32)


def write_cube(table: np.ndarray, path: Path, title: str = "roughcut") -> Path:
    n = int(round(len(table) ** (1 / 3)))
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in table]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Path(path)


def read_cube(path: Path) -> np.ndarray:
    """A .cube file as an (n³, 3) table. Only what `write_cube` writes plus comments
    and a DOMAIN line is understood; anything else is a ValueError."""
    rows = []
    n = None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("TITLE") or s.startswith("DOMAIN"):
            continue
        if s.startswith("LUT_3D_SIZE"):
            n = int(s.split()[1])
            continue
        if s.startswith("LUT_1D_SIZE"):
            raise ValueError("1D LUTs are not supported")
        parts = s.split()
        if len(parts) != 3:
            raise ValueError(f"bad cube line: {s[:40]}")
        rows.append([float(v) for v in parts])
    if n is None or len(rows) != n ** 3 or n > 65:
        raise ValueError("cube size does not match its rows")
    return np.clip(np.array(rows, np.float32), 0, 1)


def apply_cube(rgb: np.ndarray, table: np.ndarray) -> np.ndarray:
    """Trilinear lookup of an (n³, 3) table, for applying a library `.cube` inside a
    bake (the render applies the baked result with ffmpeg's tetrahedral lut3d)."""
    n = int(round(len(table) ** (1 / 3)))
    t = table.reshape(n, n, n, 3)          # [b, g, r]
    x = np.clip(rgb, 0, 1) * (n - 1)
    i0 = np.floor(x).astype(int)
    i1 = np.minimum(i0 + 1, n - 1)
    f = (x - i0)[..., None]
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    r1, g1, b1 = i1[..., 0], i1[..., 1], i1[..., 2]
    fr, fg, fb = f[..., 0, :], f[..., 1, :], f[..., 2, :]
    c00 = t[b0, g0, r0] * (1 - fr) + t[b0, g0, r1] * fr
    c01 = t[b0, g1, r0] * (1 - fr) + t[b0, g1, r1] * fr
    c10 = t[b1, g0, r0] * (1 - fr) + t[b1, g0, r1] * fr
    c11 = t[b1, g1, r0] * (1 - fr) + t[b1, g1, r1] * fr
    c0 = c00 * (1 - fg) + c01 * fg
    c1 = c10 * (1 - fg) + c11 * fg
    return np.clip(c0 * (1 - fb) + c1 * fb, 0, 1)


def lut_vf(cube: Path, src_range: str = "full") -> str:
    """The part's filter fragment: the range stated on the way into RGB float, the LUT,
    the range stated on the way back to limited yuv420p. `src_range` is `in_range()`
    of the source (after `normalise_vf`, limited). The naive `lut3d,format=yuv420p`
    squeezed a full-range frame's levels silently — the lab measured YAVG 146.8 → 141.5;
    this chain passes the grey self-test (a full-range 128 reads 126 limited)."""
    return (f"scale=in_range={src_range}:out_range=full:flags=spline+accurate_rnd+full_chroma_int,"
            f"format=gbrpf32le,lut3d=file={cube}:interp=tetrahedral,"
            f"scale=in_range=full:out_range=limited:flags=accurate_rnd,format=yuv420p")


# --------------------------------------------------------------------- resolve

DEFAULT_STRENGTH = 0.5
MODES = ("auto", "off")


def validate_colour(body: dict | None, looks: dict[str, dict] | None = None) -> dict:
    """The EDL's `colour` block, checked the way `effects_music` is: an unknown mode,
    look, or an override outside the vocabulary is a ValueError and nothing is
    written. Shape:

        {"mode": "auto"|"off", "look": name|null, "strength": 0..1,
         "reference": segment id|null,
         "shots": {id: {"auto": bool, "balance": {...}, "look": name|null,
                        "strength": 0..1, "match": "reference"|"previous"|null}}}
    """
    if not body:
        return {}
    if not isinstance(body, dict):
        raise ValueError("colour must be an object")
    out: dict = {}
    mode = body.get("mode", "auto")
    if mode not in MODES:
        raise ValueError(f"colour.mode must be one of {MODES}")
    out["mode"] = mode

    def look_ok(name):
        if name is None or name == "":
            return None
        name = str(name)
        if looks is not None and (name not in looks or looks[name].get("kind") == "broken"):
            raise ValueError(f"unknown look {name!r}")
        return name

    def strength_ok(v):
        v = float(v)
        if not (0.0 <= v <= 1.0):
            raise ValueError("strength must be 0..1")
        return round(v, 3)

    out["look"] = look_ok(body.get("look"))
    out["strength"] = strength_ok(body.get("strength", DEFAULT_STRENGTH))
    ref = body.get("reference")
    out["reference"] = str(ref)[:40] if ref else None
    shots: dict = {}
    for sid, o in (body.get("shots") or {}).items():
        if not isinstance(o, dict):
            raise ValueError("a shot override must be an object")
        s: dict = {}
        if "auto" in o:
            s["auto"] = bool(o["auto"])
        if o.get("balance"):
            s["balance"] = validate_balance(o["balance"])
        if "look" in o:
            s["look"] = look_ok(o["look"])
        if "strength" in o and o["strength"] is not None:
            s["strength"] = strength_ok(o["strength"])
        if o.get("match") is not None:
            if o["match"] not in ("reference", "previous"):
                raise ValueError("match must be 'reference' or 'previous'")
            s["match"] = o["match"]
        if s:
            shots[str(sid)[:40]] = s
    if shots:
        out["shots"] = shots
    return out


def validate_balance(b: dict) -> dict:
    """A hand-set balance: the same keys as the auto, the widest clamps of any family."""
    if not isinstance(b, dict):
        raise ValueError("balance must be an object")
    wide = CLAMPS["gopro"]
    out: dict = {}
    gain = b.get("gain", [1, 1, 1])
    if not (isinstance(gain, list) and len(gain) == 3):
        raise ValueError("balance.gain must be [r, g, b]")
    out["gain"] = [round(float(np.clip(float(v), 1 - wide["gain"], 1 + wide["gain"])), 4) for v in gain]
    out["exposure"] = round(float(np.clip(float(b.get("exposure", 1.0)), *wide["exposure"])), 4)
    out["knee"] = round(float(np.clip(float(b.get("knee", KNEE)), 0.6, 1.0)), 3)
    out["lift"] = round(float(np.clip(float(b.get("lift", 0.0)), -wide["lift"], wide["lift"])), 4)
    out["source"] = "hand"
    return out


def resolve_shot(edl: dict, seg: dict, clip_colour: dict | None, looks: dict[str, dict],
                 reference: dict | None = None, previous: dict | None = None) -> dict:
    """Everything the render (and the monitor) needs for one shot: the balance, the
    match, the look and strength, and `fn` — the composed mapping to bake.

    `clip_colour` is the clip's colour file (or None: unmeasured → the camera's
    picture, look still applies). `reference` / `previous` are `{"summary", "balance"}`
    of the reference shot and the shot before, for `match`. The order is the
    colourist's: balance, then match, then the look at its strength."""
    colour = edl.get("colour") or {}
    mode = colour.get("mode", "auto")
    over = (colour.get("shots") or {}).get(seg.get("id") or "", {})
    family = ((clip_colour or {}).get("probe") or {}).get("family") or "gopro"
    summary = (segment_summary(clip_colour, float(seg["in"]), float(seg["out"]))
               if clip_colour else None)

    if over.get("balance"):
        balance = over["balance"]
    elif mode == "auto" and over.get("auto", True) and summary:
        balance = balance_params(summary, family)
    else:
        balance = None

    match = None
    want = over.get("match")
    if want == "previous" and previous and summary:
        match = match_params(summary, previous["summary"], balance, previous.get("balance"))
    elif want == "reference" and reference and summary:
        match = match_params(summary, reference["summary"], balance, reference.get("balance"))

    look_name = over["look"] if "look" in over else colour.get("look")
    strength = float(over.get("strength", colour.get("strength", DEFAULT_STRENGTH)))
    look = looks.get(look_name) if look_name else None
    if look and look.get("kind") == "broken":
        look = None

    def fn(x: np.ndarray) -> np.ndarray:
        y = apply_match(apply_balance(x, balance), match)
        if not look or strength <= 0:
            return y
        if look["kind"] == "cube":
            z = apply_cube(y, read_cube(Path(look["cube"])))
        else:
            z = apply_look(y, look["params"])
        return np.clip(y + strength * (z - y), 0, 1)

    identity = balance is None and match is None and (not look or strength <= 0)
    return {"id": seg.get("id"), "balance": balance, "match": match,
            "look": look_name if look else None, "strength": round(strength, 3),
            "summary": summary, "family": family, "identity": identity, "fn": fn}


def shot_context(res: dict) -> dict | None:
    """What a later shot needs from this one to match to it."""
    if not res.get("summary"):
        return None
    return {"summary": res["summary"], "balance": res.get("balance")}
