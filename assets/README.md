# assets

Effect material — music, sound effects, overlays — referenced **by name** from an EDL's
`effects` list. See [docs/EFFECTS.md](../docs/EFFECTS.md).

```
assets/
  music/     tracks for a bed under a cut       (.mp3 .m4a .wav .flac)
  sfx/       short sounds placed at a moment    (.wav preferred — sharp attack, no gap)
  overlay/   transparent PNGs drawn on a frame  (square, ideally >= 512px)
```

**Nothing here is fetched from the internet, ever.** You put files in; the tool uses what it
finds. Licensing is yours to decide, which is the main reason it works this way.

Media is gitignored, so this directory ships empty. That is intentional — the manifest describes
what a file *is* so a model can choose between three whooshes, but the file itself belongs to
whoever owns it.
