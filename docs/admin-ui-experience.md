# Admin and Portal UI modernization

Status: implemented; validation results below. Scope: Admin and the integrated
`/user/` Portal. No changes to authentication, authorization, API contracts,
model routing, or the separate chat application.

## Implementation plan and result

1. Reuse Jinja, Bootstrap and vanilla JavaScript. Introduce shared local color,
   spacing, surface, focus and responsive primitives (`moe-ui.css`). Preserve
   the existing Admin navigation groups and all permission-dependent routes.
2. Improve workflows: a collapsible mobile Portal navigation, task shortcuts,
   configuration section navigation and a sticky save bar. Track unsaved
   configuration in memory, confirm discarding and warn before leaving.
   Submit the existing complete `/save` form, including filtered/hidden fields.
3. Add explicit opt-in search and compact/comfortable views to server,
   template and key lists. Templates also filter by existing privacy metadata;
   maintenance actions use native disclosure. Column resizing has click and
   keyboard alternatives and keeps total width fixed.
4. Load Chart.js only for a populated Portal dashboard chart and Cytoscape /
   pipeline code only on the usage page. Remove unconditional Chart.js service
   worker prefetch. Keep useful existing visualizations; add no dependencies.
5. Check layouts, interactions, local-only resource loading, syntax, existing
   resizing regressions and the affected local service.

## Resource and license constraints

- New CSS/JS uses system fonts and existing local Bootstrap icons. No remote
  fonts, analytics, decoration engines, assets or dependencies are introduced.
- New original files use Apache-2.0, the project license. Existing vendor
  banners are preserved; shared asset MIT notices are shipped in
  `admin_ui/static/THIRD_PARTY_LICENSES.txt`. This is not a platform-wide legal
  audit of unrelated optional integrations or model licenses.
- The Geo page no longer falls back to public OSM/Carto/Topo tile services.
  Existing local data layers remain functional with a neutral background.
  If a `tile_url` template context is provided, it must resolve to the same
  origin; otherwise no basemap tiles load. No new map dataset is bundled.
- AirGap here means the UI works without internet resources while its local
  backend is reachable. It does not mean a disconnected browser can manage
  servers or that configured external model providers become local.
- Existing service workers derive their version from static CSS/JS contents.
  Rebuilding/restarting the affected service updates their cache generation.

## Validation

`python3 -m unittest tests.test_ui_experience_browser tests.test_admin_table_columns_browser -v`

Browser tests use actual Jinja templates with synthetic data, local static
files and Chromium. They cover light/dark themes at 375/768/1024/1440 px,
page overflow, mobile navigation, no-JavaScript fallback, permissions on
shortcuts, full form submission while filtering, dirty state, discard cancel,
Enter in search not submitting, template/privacy filtering, width adjustment,
page-specific libraries, external request detection and JavaScript errors.
They never submit production configuration or create keys. Authenticated
production backend mutations are deliberately outside this visual validation.

Set `MOE_UI_SCREENSHOTS=/tmp/moe-ui-modernized` to generate screenshots and a
resource inventory alongside the browser tests. Byte measurements refer to
uncompressed source assets, not network transfer or loading-time benchmarks.

Validated on 2026-09-18: 13 browser tests passed, including 72 page/theme/width
states. The separate long-template-header interaction smoke also passed.
Shared new CSS/JS: 13,971 bytes; diagram code no longer loaded on keys,
connections and login pages: 626,918 bytes (both uncompressed). These figures
exclude HTML and existing common assets. No external requests were observed
in the synthetic browser cases; attempted external requests are blocked by
the test harness. Screenshots are retained in the workspace under
`ui-ux-review/implemented-20260918/`.

## Local runtime and rollback

Validated local service: only `moe-admin` rebuilt/recreated. Admin/Portal login,
both service workers and new local resources return HTTP 200. Served static
asset SHA-256 hashes match the source. Image/source details are recorded in
`ui-ux-review/implemented-20260918/runtime.json` and `source-sha256.json`.
The source is a dirty development checkout; no commit or push was made.

Rollback requires both the prior image and prior bind-mounted templates,
translations and `app.py`. This session's pre-edit copies are in
`/tmp/moe-ui-before/`; restore only the affected files after checking for newer
edits, then tag the previous image recorded in `runtime.json` as
`moe-infra-moe-admin` and recreate only `moe-admin` with `--no-deps`. New unused
static/include files can remain. Do not reset the worktree: it contains
unrelated operator changes.
