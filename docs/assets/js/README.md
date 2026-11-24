# `docs/assets/js/` — JavaScript assets

## `mermaid.min.js`

Kept **only** so that mkdocs-material's built-in Mermaid loader can reuse it
instead of fetching from `https://unpkg.com/mermaid@11/dist/mermaid.min.js`
on every page load. Material's loader (in
`assets/javascripts/bundle.*.min.js`) checks `typeof mermaid == "undefined"`
and reuses whatever global is already present.

Benefits of keeping the local copy:

- Offline / air-gapped builds still render diagrams.
- No per-visitor HTTP hit on unpkg.com.
- Pinned, reproducible mermaid version (currently v11.17.2; v11+ is required for `look: handDrawn`).

## `mermaid-look.js`

Switches all diagrams to Mermaid's `handDrawn` look (Excalidraw-like sketch
style). It only wraps `mermaid.initialize()` so that mkdocs-material's own
call carries `look: "handDrawn"` — it never calls `initialize()`/`run()` on
its own, so it does not conflict with the rule below. To return to the clean
look, remove it from `extra_javascript` in `mkdocs.yml`. Sequence, Gantt and
pie diagrams ignore `look` and render as before.

## Do NOT add a `mermaid-init.js`

mkdocs-material **9.x** already calls `mermaid.initialize()` with the correct
`startOnLoad: false` + shadow-DOM render pipeline. A hand-rolled init script
that calls `mermaid.run()` on top of material's output will re-process
already-rendered `<div class="mermaid">` wrappers and overwrite their SVG
content with "Syntax error in text" banners — for every diagram on every
page.

A previous attempt at this regressed all 71 diagrams across 33 pages. See
the commit history around the mermaid sweep if you need context.

If you think mermaid is broken, check in this order:

1. `mermaid.min.js` is reachable (`curl -I /assets/js/mermaid.min.js` → 200).
2. No custom init script lives in this directory.
3. `mkdocs.yml` lists **only** `mermaid.min.js` under `extra_javascript`.
4. The broken diagram has an actual source-level Mermaid syntax error. Test
   with a per-block parser in a headless browser (`mermaid.parse(src)`).
