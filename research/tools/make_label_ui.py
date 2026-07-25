# /// script
# requires-python = ">=3.12"
# ///
"""Generate a click-to-label web page over a contact-sheet directory.

The CSV-by-hand workflow it replaces asked a human to read timestamps off images and
type arithmetic into a spreadsheet — mechanical work, which SPEC §0 principle 1 says is
a design bug. Here you click the frames that look interesting; contiguous clicks merge
into ranges, timestamps are derived, and the CSV is generated on export.

Cells are addressed as CSS sprites into the existing sheet JPEGs, so no frames are
re-extracted and the page is a single file next to the images.

Usage:
    uv run make_label_ui.py LABELING_DIR      # writes LABELING_DIR/label.html
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Roughcut — labeling: __BIN__</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font:14px/1.5 system-ui,sans-serif; background:#141416; color:#e8e8ea; }
  header { position:sticky; top:0; z-index:10; background:#1c1c20; border-bottom:1px solid #303036;
           padding:10px 16px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
  header h1 { font-size:15px; margin:0; font-weight:600; }
  .spacer { flex:1; }
  button { background:#2e2e36; color:#e8e8ea; border:1px solid #45454f; border-radius:6px;
           padding:6px 12px; font:inherit; cursor:pointer; }
  button.primary { background:#3b6ea5; border-color:#4d84c0; }
  button:hover { filter:brightness(1.15); }
  .count { color:#9a9aa4; }
  section { padding:14px 16px 26px; }
  h2 { font-size:14px; margin:22px 0 8px; font-weight:600; }
  h2 span { color:#9a9aa4; font-weight:400; }
  .grid { display:flex; flex-wrap:wrap; gap:4px; }
  .cell { position:relative; cursor:pointer; border:2px solid transparent; border-radius:3px;
          background-repeat:no-repeat; flex:0 0 auto; }
  .cell:hover { border-color:#6b6b78; }
  .cell.on { border-color:#ffb057; box-shadow:0 0 0 2px rgba(255,176,87,.28); }
  .cell .t { position:absolute; left:0; bottom:0; background:rgba(0,0,0,.72); color:#fff;
             font:11px/1.4 ui-monospace,monospace; padding:0 4px; border-radius:0 3px 0 0; }
  .ranges { margin-top:8px; display:flex; flex-direction:column; gap:6px; }
  .range { display:flex; gap:8px; align-items:center; background:#1c1c20; border:1px solid #303036;
           border-radius:6px; padding:6px 10px; }
  .range code { color:#ffb057; font:12px ui-monospace,monospace; white-space:nowrap; }
  .range .brief { color:#8ec07c; font-size:12px; }
  .range input { flex:1; background:#141416; color:#e8e8ea; border:1px solid #45454f;
                 border-radius:4px; padding:5px 8px; font:inherit; }
  .hint { color:#9a9aa4; max-width:70ch; }
  dialog { background:#1c1c20; color:#e8e8ea; border:1px solid #45454f; border-radius:8px;
           max-width:min(900px,90vw); }
  textarea { width:100%; height:44vh; background:#141416; color:#e8e8ea; border:1px solid #45454f;
             border-radius:6px; font:12px ui-monospace,monospace; padding:8px; }
</style>
<header>
  <h1>Labeling — __BIN__</h1>
  <span class="count" id="count">0 marked</span>
  <span class="spacer"></span>
  <button id="clear">Clear all</button>
  <button class="primary" id="export">Export CSV</button>
</header>
<section>
  <p class="hint">Click any frame that shows a moment you'd <em>consider</em> using. Click and drag,
  or shift-click, to sweep a run of frames. Neighbouring marks merge into one range automatically —
  add a note if you want to say why. Everything saves in this browser as you go, so you can close
  the tab and come back.</p>
  <div id="clips"></div>
</section>
<dialog id="out">
  <h2 style="margin-top:0">highlights.csv</h2>
  <textarea id="csv" readonly></textarea>
  <p style="display:flex;gap:8px;justify-content:flex-end">
    <button id="copy">Copy to clipboard</button>
    <button id="dl" class="primary">Download</button>
    <button id="close">Close</button>
  </p>
</dialog>
<script>
const DATA = __DATA__;
const KEY = "roughcut-labels-__BIN__";
let marks = JSON.parse(localStorage.getItem(KEY) || "{}");   // {clip: {cellIndex: true}}
let notes = JSON.parse(localStorage.getItem(KEY + "-notes") || "{}"); // {clip|start: note}

const save = () => {
  localStorage.setItem(KEY, JSON.stringify(marks));
  localStorage.setItem(KEY + "-notes", JSON.stringify(notes));
};
const hms = t => `${String(Math.floor(t/60)).padStart(2,"0")}:${String(Math.floor(t%60)).padStart(2,"0")}`;

// Contiguous marked cells collapse into one range; a gap starts a new one.
function ranges(clip) {
  const on = Object.keys(marks[clip.label] || {}).map(Number).sort((a,b)=>a-b);
  const out = [];
  for (const i of on) {
    const last = out[out.length-1];
    if (last && i === last.end + 1) last.end = i;
    else out.push({start:i, end:i});
  }
  return out.map(r => ({
    ...r,
    start_s: +(r.start * clip.interval_s).toFixed(2),
    // a single marked frame still covers the interval it represents
    end_s: +Math.min((r.end + 1) * clip.interval_s, clip.duration_s).toFixed(2),
  }));
}

function renderRanges(clip, host) {
  const rs = ranges(clip);
  host.innerHTML = "";
  for (const r of rs) {
    const dur = r.end_s - r.start_s;
    const row = document.createElement("div");
    row.className = "range";
    row.innerHTML = `<code>${hms(r.start_s)} – ${hms(r.end_s)}</code>` +
      (dur < 5 ? `<span class="brief">brief</span>` : "");
    const inp = document.createElement("input");
    inp.placeholder = "note (optional) — what makes this worth using?";
    const nk = clip.label + "|" + r.start;
    inp.value = notes[nk] || "";
    inp.oninput = () => { notes[nk] = inp.value; save(); };
    row.appendChild(inp);
    host.appendChild(row);
  }
  document.getElementById("count").textContent =
    Object.values(marks).reduce((n,m)=>n+Object.keys(m).length,0) + " marked";
}

const clipsEl = document.getElementById("clips");
let dragging = false, dragTo = true;
document.addEventListener("mouseup", () => dragging = false);

for (const clip of DATA.clips) {
  const g = clip.cell_geometry;
  const h2 = document.createElement("h2");
  h2.innerHTML = `${clip.label} <span>— ${Math.round(clip.duration_s)}s, ` +
                 `${clip.n_samples} frames every ${clip.interval_s}s</span>`;
  clipsEl.appendChild(h2);

  const grid = document.createElement("div");
  grid.className = "grid";
  marks[clip.label] ||= {};

  for (const sheet of clip.sheets) {
    for (const c of sheet.cells) {
      const d = document.createElement("div");
      d.className = "cell" + (marks[clip.label][c.index] ? " on" : "");
      d.style.width = clip.thumb_size[0] + "px";
      d.style.height = clip.thumb_size[1] + "px";
      d.style.backgroundImage = `url("${sheet.path}")`;
      d.style.backgroundPosition =
        `-${c.col * g.cell_w + g.pad}px -${c.row * g.cell_h + g.pad}px`;
      d.innerHTML = `<span class="t">${hms(c.t)}</span>`;
      const set = on => {
        if (on) marks[clip.label][c.index] = true; else delete marks[clip.label][c.index];
        d.classList.toggle("on", on); save(); renderRanges(clip, rangesEl);
      };
      d.onmousedown = e => {
        e.preventDefault();
        dragging = true; dragTo = !marks[clip.label][c.index]; set(dragTo);
      };
      d.onmouseenter = () => { if (dragging) set(dragTo); };
      grid.appendChild(d);
    }
  }
  clipsEl.appendChild(grid);
  const rangesEl = document.createElement("div");
  rangesEl.className = "ranges";
  clipsEl.appendChild(rangesEl);
  renderRanges(clip, rangesEl);
}

function toCSV() {
  const out = ["file,start_s,end_s,note"];
  for (const clip of DATA.clips)
    for (const r of ranges(clip)) {
      const n = (notes[clip.label + "|" + r.start] || "").replace(/"/g, '""');
      const brief = (r.end_s - r.start_s) < 5 ? "brief - " : "";
      out.push(`${clip.label},${r.start_s},${r.end_s},"${brief}${n}"`);
    }
  return out.join("\\n") + "\\n";
}

const dlg = document.getElementById("out");
document.getElementById("export").onclick = () => {
  document.getElementById("csv").value = toCSV(); dlg.showModal();
};
document.getElementById("close").onclick = () => dlg.close();
document.getElementById("copy").onclick = () =>
  navigator.clipboard.writeText(toCSV()).then(() =>
    document.getElementById("copy").textContent = "Copied");
document.getElementById("dl").onclick = () => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([toCSV()], {type:"text/csv"}));
  a.download = "highlights.csv"; a.click();
};
document.getElementById("clear").onclick = () => {
  if (!confirm("Clear every mark on this page?")) return;
  marks = {}; notes = {}; save(); location.reload();
};
</script>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a click-to-label page for a sheet directory.")
    ap.add_argument("labeling_dir", type=Path)
    args = ap.parse_args()

    index_path = args.labeling_dir / "index.json"
    if not index_path.exists():
        print(f"error: no index.json in {args.labeling_dir}")
        return 2
    data = json.loads(index_path.read_text(encoding="utf-8"))

    # Older index.json files predate cell_geometry; fall back to the tool's constants.
    for clip in data["clips"]:
        if "cell_geometry" not in clip:
            tw, th = clip["thumb_size"]
            clip["cell_geometry"] = {"pad": 2, "label_h": 18,
                                     "cell_w": tw + 4, "cell_h": th + 22}

    name = args.labeling_dir.name
    html = (PAGE.replace("__DATA__", json.dumps(data))
                .replace("__BIN__", name))
    out = args.labeling_dir / "label.html"
    out.write_text(html, encoding="utf-8")
    total = sum(c["n_samples"] for c in data["clips"])
    print(f"{out}  —  {len(data['clips'])} clips, {total} frames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
