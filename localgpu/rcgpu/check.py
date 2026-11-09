"""Qwen3.5-4B yes/no CHECK: P(yes) from the first-token logits over a short window of
levelled, sharpest-of-5 frames at 10 fps. Thinking is off; the vision tower and lm_head stay
bf16, the language model is nf4 (written once by install-local-models.sh)."""
from __future__ import annotations

import time

import numpy as np

from . import config

QUESTIONS = {
    # validated in the bake-off (AUROC on the labelled windows 0.86-0.94 for fall; air is a coin flip)
    "fall": "Does the camera wearer fall or crash into the snow in these frames?",
    "air": "Is the camera wearer airborne (skis off the snow, in the air) in these frames?",
    # wave 21 OTHERAIR: another rider, from any camera (measured on CLIP_10 vs the 19 negatives: research/R12-understanding)
    "other_air": ("Is a person other than the camera wearer airborne in these frames, for example "
                  "a skier or snowboarder jumping or flying through the air?"),
    # not scored in the bake-off: advisory only
    "handling": "Is the camera wearer holding, touching or throwing the camera in these frames?",
    "impact": "Does the camera wearer land hard, fall, or crash in these frames?",
    "stop": "Does the camera wearer come to a stop in these frames?",
    "cheer": "Is someone laughing, cheering or reacting excitedly in these frames?",
}
VALIDATED = {"fall"}        # a question whose P(yes) may be read as evidence (air never settles)


class Checker:
    def __init__(self) -> None:
        config.setup_env()
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor
        self.torch = torch
        path = str(config.qwen_path())
        t = time.time()
        self.proc = AutoProcessor.from_pretrained(path)
        self.model = AutoModelForImageTextToText.from_pretrained(
            path, device_map="cuda", dtype=torch.bfloat16).eval()
        self.load_s = time.time() - t
        tok = self.proc.tokenizer
        ids = lambda ws: sorted({tok.encode(w, add_special_tokens=False)[0] for w in ws})  # noqa: E731
        self.yes_ids = ids(["Yes", "yes", " Yes", " yes", "YES"])
        self.no_ids = ids(["No", "no", " No", " no", "NO"])

    def _inputs(self, frames, text: str, fps: float):
        from transformers.video_utils import VideoMetadata
        torch = self.torch
        vid = np.stack([f[:, :, ::-1] for f in frames])                 # T,H,W,3 RGB
        vid = torch.from_numpy(vid).permute(0, 3, 1, 2).contiguous()
        md = VideoMetadata(total_num_frames=len(frames), fps=fps,
                           frames_indices=list(range(len(frames))), duration=len(frames) / fps)
        msgs = [{"role": "user", "content": [{"type": "video"}, {"type": "text", "text": text}]}]
        try:
            prompt = self.proc.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False,
                                                   enable_thinking=False)
        except TypeError:
            prompt = self.proc.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
        inp = self.proc(text=[prompt], videos=[vid], video_metadata=[md], do_sample_frames=False,
                        return_tensors="pt")
        return inp.to("cuda")

    def pyes(self, frames, question: str, fps: float = config.GRID_FPS) -> float:
        torch = self.torch
        with torch.inference_mode():
            inp = self._inputs(frames, question + " Answer with only Yes or No.", fps)
            # only the last position's logits: the full 248k-vocab x sequence tensor was the VRAM peak
            lg = self.model(**inp, logits_to_keep=1).logits[0, -1].float().log_softmax(-1)
            y = torch.logsumexp(lg[self.yes_ids], 0)
            n = torch.logsumexp(lg[self.no_ids], 0)
            return float(torch.sigmoid(y - n))

    def check(self, grid, q: str, scan: bool = True, scan_from: float = config.SCAN_FROM_P) -> dict:
        """grid: [(t, BGR)] at 10 fps (one window). -> {p_yes, n_frames, [scan_peak_t, scan_peak_p]}.
        The timing scan (1.0 s sub-windows by 0.5 s, then 0.5 s by 0.1 s round the peak) runs only
        when the whole window is not a clear no."""
        question = QUESTIONS[q]
        fps = config.GRID_FPS
        if len(grid) < 6:
            return {"p_yes": None, "n_frames": len(grid), "why": "fewer than 6 frames"}
        p_all = self.pyes([im for _, im in grid], question, fps)
        res = {"p_yes": round(p_all, 4), "n_frames": len(grid)}
        if scan and p_all >= scan_from:
            def sub(a, b):
                return [im for tt, im in grid if a <= tt < b]
            coarse = []
            a = grid[0][0]
            while a + 1.0 <= grid[-1][0] + 0.11:
                fr = sub(a, a + 1.0)
                if len(fr) >= 6:
                    coarse.append((a + 0.5, self.pyes(fr, question, fps)))
                a += 0.5
            if coarse:
                pk = max(coarse, key=lambda x: x[1])[0]
                fine = []
                a = pk - 0.5
                while a <= pk + 0.001:
                    fr = sub(a - 0.25, a + 0.5)
                    if len(fr) >= 4:
                        fine.append((a + 0.25, self.pyes(fr, question, fps)))
                    a += 0.1
                best = max(fine or coarse, key=lambda x: x[1])
                res.update(scan_peak_t=round(best[0], 2), scan_peak_p=round(best[1], 4))
        return res
