# Third-Party Notices

This document lists third-party software components bundled in this repository,
along with their licenses and copyright notices.

---

## Admin UI Static Assets (`admin_ui/static/`)

### Bootstrap 5.3.3
- **License:** MIT
- **Copyright:** 2011–2024 The Bootstrap Authors
- **Source:** https://getbootstrap.com/

### Bootstrap Icons 1.11.3
- **License:** MIT
- **Copyright:** 2019–2024 The Bootstrap Authors
- **Source:** https://icons.getbootstrap.com/

### Chart.js 4.4.4
- **License:** MIT
- **Copyright:** 2024 Chart.js Contributors
- **Source:** https://www.chartjs.org/

---

## Shared UI license texts

The existing shared Admin/Portal asset notices and full MIT license text are
also shipped locally in `admin_ui/static/THIRD_PARTY_LICENSES.txt`. This includes
Cytoscape.js 3.29.2 (Copyright 2016–2024 The Cytoscape Consortium) and the embedded
MIT notices retained in that vendor file. The new `moe-ui.css` and `moe-ui.js`
are original project code under Apache-2.0 and add no dependencies.

---

## Documentation Search (`site/assets/javascripts/lunr/`)

### Lunr.js
- **License:** MIT
- **Copyright:** 2020 Oliver Nightingale
- **Source:** https://lunrjs.com/

---

## Open-Source Fonts (`skills-upstream/skills/canvas-design/canvas-fonts/`)

All 26 fonts bundled in this directory are licensed under the
**SIL Open Font License (OFL) v1.1**. Each font has its own `*-OFL.txt`
license file in the same directory. The OFL allows bundling and redistribution
of the font files; documents produced with these fonts may use any license.

Fonts included: Big Shoulders, Boldonse, Bricolage Grotesque, Crimson Pro,
DM Mono, Erica One, Geist Mono, Gloock, IBM Plex Mono, Instrument Sans,
Italiana, JetBrains Mono, Jura, Libre Baskerville, Lora, National Park,
Nothing You Could Do, Outfit, Pixelify Sans, Poiret One, Red Hat Mono,
Silkscreen, Smooch Sans, Tektur, Work Sans, Young Serif.

---

## JavaScript Libraries (`skills-upstream/`)

### p5.js 1.7.0
- **License:** LGPL-2.1
- **Copyright:** 2013–2023 the p5.js contributors
- **Source:** https://p5js.org/
- **Location:** `skills-upstream/skills/algorithmic-art/assets/p5.min.js`

### SheetJS Community Edition 0.20.3
- **License:** Apache 2.0
- **Copyright:** SheetJS LLC
- **Source:** https://sheetjs.com/
- **Location:** `skills-upstream/skills/skill-creator/eval-viewer/xlsx.full.min.js`

---

## Valkey 8

- **License:** BSD 3-Clause
- **Copyright:** 2024 The Valkey contributors, Linux Foundation
- **Source:** https://valkey.io/
- **Used by:** LangGraph checkpoint persistence, performance score cache (`terra_cache` container)
- **Note:** Valkey is a BSD-licensed fork of Redis, created after Redis changed its license to SSPL/RSALv2 in March 2024. It is API-compatible and maintained by the Linux Foundation.

---

## FFmpeg (GPLv3 — skills-upstream only)

### FFmpeg 7.0.2
- **License:** GNU General Public License v3.0 (GPLv3)
- **Copyright:** 2000–2024 the FFmpeg developers
- **Source:** https://ffmpeg.org/
- **Used by:** `slack-gif-creator` skill (GIF creation and video processing)

> **Important:** FFmpeg is licensed under GPLv3, which is a strong copyleft
> license. FFmpeg is an **optional runtime dependency** of the `slack-gif-creator`
> skill only — it is not bundled in the repository and is not a core dependency
> of the MoE Sovereign orchestrator. When using the `slack-gif-creator` skill,
> FFmpeg must be installed separately on the host system. Usage of FFmpeg is
> governed exclusively by the GPLv3 license; the Apache 2.0 license of this
> repository does not apply to FFmpeg or to any derivative work that incorporates it.

---

## Mermaid.js 10.9.3

- **License:** MIT
- **Copyright:** 2014–2024 Knut Sveidqvist and contributors
- **Source:** https://mermaid.js.org/
- **Location:** `docs/assets/js/mermaid.min.js`
- **Used by:** MkDocs documentation (offline diagram rendering)
