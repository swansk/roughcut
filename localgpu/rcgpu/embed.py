"""PE-Core-L/14-336 embeddings for Find: frames at 2 fps squashed to square (no centre crop —
sky and skis at the edges matter), fp16, L2-normalised; text -> frame cosine, 2 s smoothing,
non-maximum suppression 3 s, top-k windows (the bake-off's recipe)."""
from __future__ import annotations

import numpy as np

from . import config


class Embedder:
    def __init__(self) -> None:
        config.setup_env()
        import open_clip
        import torch
        self.torch = torch
        name = "hf-hub:" + config.PE_REPO
        self.model, _, pre = open_clip.create_model_and_transforms(name)
        self.tok = open_clip.get_tokenizer(name)
        norm = pre.transforms[-1]
        self.size = 336
        self.mean = torch.tensor(norm.mean, dtype=torch.float32).view(1, 3, 1, 1)
        self.std = torch.tensor(norm.std, dtype=torch.float32).view(1, 3, 1, 1)
        self.model = self.model.cuda().half().eval()
        self.mean, self.std = self.mean.cuda(), self.std.cuda()

    def squash(self, y):
        """uint8 CHW RGB (GPU) -> uint8 CHW at size x size, area-resampled."""
        F = self.torch.nn.functional
        return F.interpolate(y.unsqueeze(0).float(), size=(self.size, self.size),
                             mode="area")[0].clamp(0, 255).byte()

    def images(self, batch, bs: int = 32) -> np.ndarray:
        """batch: uint8 tensor N,3,size,size on the GPU."""
        torch = self.torch
        out = []
        with torch.inference_mode():
            for i in range(0, batch.shape[0], bs):
                x = batch[i:i + bs].float() / 255.0
                x = ((x - self.mean) / self.std).half()
                e = self.model.encode_image(x)
                out.append(torch.nn.functional.normalize(e.float(), dim=-1).cpu())
        return torch.cat(out).numpy().astype(np.float16)

    def texts(self, qs: list[str]) -> np.ndarray:
        torch = self.torch
        with torch.inference_mode():
            e = self.model.encode_text(self.tok(qs).cuda())
            return torch.nn.functional.normalize(e.float(), dim=-1).cpu().numpy()


def search_windows(q: np.ndarray, items: list[tuple[str, np.ndarray, np.ndarray]], k: int = 3,
                   win: float = 2.0, nms: float = 3.0, embed_fps: float = config.EMBED_FPS) -> list[dict]:
    """items: [(clip, times, embeddings N x D)] -> top-k non-overlapping windows."""
    cands = []
    for clip, t, e in items:
        if len(t) == 0:
            continue
        s = e.astype(np.float32) @ q
        w = max(1, int(round(win * embed_fps)))
        sm = np.convolve(s, np.ones(w) / w, mode="same")
        cands += [(float(sm[i]), clip, float(t[i])) for i in range(len(t))]
    cands.sort(reverse=True)
    out: list[dict] = []
    for sc, clip, t in cands:
        if any(clip == o["clip"] and abs(t - (o["start"] + o["end"]) / 2) < nms for o in out):
            continue
        out.append({"clip": clip, "start": round(max(0.0, t - win / 2), 2),
                    "end": round(t + win / 2, 2), "score": round(sc, 4)})
        if len(out) >= k:
            break
    return out
