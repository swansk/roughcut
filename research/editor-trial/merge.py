"""merge.py [--min-sev N] [--editor NAME] [--full]: every editor's findings in one list, worst first."""
import json
import os
import sys
from pathlib import Path

R = Path(os.environ.get("RC_ROOT", "/mnt/roughcut/space/roughcut-loop/copper")) / "editors"
args = sys.argv[1:]
min_sev = int(args[args.index("--min-sev") + 1]) if "--min-sev" in args else 1
only = args[args.index("--editor") + 1] if "--editor" in args else None
full = "--full" in args
rows = []
for f in sorted(R.glob("*/out/findings.jsonl")):
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
rows = [r for r in rows if r.get("severity", 0) >= min_sev and (not only or r.get("editor") == only)]
rows.sort(key=lambda r: (-int(r.get("severity", 0)), r.get("editor", ""), r.get("id", "")))
by = {}
for r in rows:
    by.setdefault(r.get("editor"), [0, 0, 0, 0, 0])[int(r.get("severity", 0))] += 1
print("editor: sev4 sev3 sev2 sev1 | ", "  ".join(f"{e}: {c[4]} {c[3]} {c[2]} {c[1]}" for e, c in sorted(by.items())), f"| total {len(rows)}")
for r in rows:
    sus = " (automation?)" if r.get("automation_suspect") else ""
    print(f"[{r.get('severity')}] {r.get('id')} {r.get('kind')} @ {r.get('where', '')[:40]}{sus}: {r.get('title')}")
    if full:
        for k in ("wanted", "did", "happened", "expected", "suggestion"):
            if r.get(k):
                print(f"      {k}: {str(r[k])[:600]}")
        if r.get("shots"):
            print("      shots:", ", ".join(Path(s).name for s in r["shots"][:6]))
