"""Frames: NVDEC decode, display-rotation fix, levelling by the GoPro gravity roll, sharpest-of-5.

What the bake-off learned (RESULTS.md, "Two bugs worth knowing"):
  1. GoPro clips from an inverted mount carry a display-matrix rotation of 180 degrees. NVDEC
     ignores it (ffmpeg autorotates), so decoded frames are upside down: flip them here.
  2. Levelling counter-rotates by the GRAV roll, `base * roll` where base is +1 for an
     upright mount and -1 for an inverted one; roll is interpolated circularly and is 0
     where gravity is near the optical axis. Black corners stay (no crop).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import config


@dataclass
class Sensors:
    """The roll series a clip is levelled with: `tg` seconds, `roll` degrees (the camera's
    GRAV roll with the mount sign applied), `gxy` (gravity's in-plane share; roll is
    meaningless below 0.35) and `base` (+1 upright mount, -1 inverted)."""
    tg: np.ndarray
    roll: np.ndarray
    gxy: np.ndarray
    base: float

    @classmethod
    def load(cls, path: str | Path | None) -> "Sensors | None":
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            return None
        z = np.load(p, allow_pickle=False)
        return cls(z["tg"], z["roll"], z["gxy"], float(z["base"]))

    def roll_at(self, times) -> np.ndarray:
        r = np.radians(self.roll)
        s = np.interp(times, self.tg, np.sin(r))
        c = np.interp(times, self.tg, np.cos(r))
        out = np.degrees(np.arctan2(s, c))
        g = np.interp(times, self.tg, self.gxy)
        return np.where(g < 0.35, 0.0, out)

    def angle_at(self, t: float) -> float:
        """The rotation (degrees) that levels the frame at `t`."""
        return float(self.base * self.roll_at([t])[0])


def level_image(img: np.ndarray, angle_deg: float) -> np.ndarray:
    if abs(angle_deg) < 0.5 or abs(angle_deg) > 179:
        return img
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), float(angle_deg), 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))


def display_rotation_180(video: str | Path) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                        "stream_side_data=rotation", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True)
    return r.stdout.strip().lstrip("-") == "180"


class Source:
    """One clip on the GPU decoder. `small(i)` is source frame i as uint8 CHW on the GPU at
    `width` px, flipped upright when the file says so."""

    def __init__(self, video: str | Path, width: int = config.FRAME_W) -> None:
        import PyNvVideoCodec as nvc
        import torch
        self.torch = torch
        self.video = str(video)
        self.dec = nvc.SimpleDecoder(self.video, gpu_id=0, use_device_memory=True,
                                     output_color_type=nvc.OutputColorType.RGBP)
        md = self.dec.get_stream_metadata()
        self.n = int(md.num_frames)
        self.fps = float(md.average_fps)
        self.width = width
        self.height = int(round(width * md.height / md.width / 2) * 2)
        self.rot180 = display_rotation_180(video)
        self.duration = self.n / self.fps
        self.step = max(1, round(self.fps / config.GRID_FPS))     # source frames per 10 fps grid frame
        self._lap = torch.tensor([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=torch.float32,
                                 device="cuda").view(1, 1, 3, 3)

    def small(self, i: int):
        torch, F = self.torch, self.torch.nn.functional
        x = torch.from_dlpack(self.dec[i])
        y = F.interpolate(x.unsqueeze(0).float(), size=(self.height, self.width), mode="area")[0]
        y = y.clamp(0, 255).byte()
        if self.rot180:
            y = y.flip(-1).flip(-2)
        return y

    def sharpness(self, y):
        """Variance of the Laplacian of the grey image — a GPU scalar (no sync)."""
        f = y.float()
        g = (0.299 * f[0] + 0.587 * f[1] + 0.114 * f[2])[None, None]
        return self.torch.nn.functional.conv2d(g, self._lap).var()

    def to_bgr(self, y) -> np.ndarray:
        return np.ascontiguousarray(y.permute(1, 2, 0).flip(-1).cpu().numpy())

    def t_of(self, i: int) -> float:
        return i / self.fps


@dataclass
class Grabbed:
    frames: list = field(default_factory=list)       # [(t, BGR ndarray)]
    decoded: int = 0


def grid_centres(src: Source, t0: float, t1: float) -> list[int]:
    """Source-frame indices of the 10 fps grid frames inside [t0, t1]."""
    a = max(0, int(np.ceil(t0 * src.fps / src.step - 1e-9)))
    b = min((src.n - 1) // src.step, int(np.floor(t1 * src.fps / src.step + 1e-9)))
    return [n * src.step for n in range(a, b + 1)]


def grab(src: Source, centres: list[int], sensors: Sensors | None = None, sharp: bool = True,
         level: bool = True) -> dict[int, np.ndarray]:
    """Sharpest-of-(+-2) levelled BGR frames for the given grid centres (source indices).
    Decodes only the neighbourhoods (sequential runs), so a 3 s window costs ~100 frames."""
    out: dict[int, np.ndarray] = {}
    need: set[int] = set()
    half = config.SHARP_HALF if sharp else 0
    for c in centres:
        need.update(j for j in range(c - half, c + half + 1) if 0 <= j < src.n)
    cache: dict[int, tuple] = {}
    for i in sorted(need):
        y = src.small(i)
        cache[i] = (y, src.sharpness(y) if sharp else None)
    for c in centres:
        cands = [j for j in range(c - half, c + half + 1) if j in cache]
        if not cands:
            continue
        best = max(cands, key=lambda j: float(cache[j][1])) if sharp else c
        img = src.to_bgr(cache[best][0])
        if level and sensors is not None:
            img = level_image(img, sensors.angle_at(src.t_of(c)))
        out[c] = img
    return out
