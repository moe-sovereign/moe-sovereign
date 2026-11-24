/*
 * pipeline_diagram.js — Live-Pipeline-Visualisierung (Augmented Tool Path Blackbox fix)
 *
 * Renders a fixed two-branch node/edge diagram (interactive LangGraph pipeline
 * vs. agentic tool-calling path) and colors nodes/edges by the most recent
 * stage-trace entry fetched on-demand from /api/live/request-trace/{chat_id}
 * (admin) or /user/api/live/request-trace/{chat_id} (portal).
 *
 * Reuses the vendored cytoscape.min.js already used by link_analysis.html.
 * Requires: <script src="/static/js/cytoscape.min.js"></script> loaded first.
 */

(function () {
  'use strict';

  const TOPOLOGY = {
    nodes: [
      // Interactive LangGraph pipeline (existing StateGraph, main.py)
      { id: 'cache',              label: 'Cache (L0–L4)',     branch: 'interactive' },
      { id: 'semantic_router',    label: 'Semantic Router',   branch: 'interactive' },
      { id: 'knowledge_broker',   label: 'Knowledge Broker',  branch: 'interactive' },
      { id: 'planner',            label: 'Planner',           branch: 'interactive' },
      { id: 'fuzzy_router',       label: 'Fuzzy Router',      branch: 'interactive' },
      { id: 'expert',             label: 'Experts',           branch: 'interactive' },
      { id: 'research',           label: 'Research',          branch: 'interactive' },
      { id: 'mcp',                label: 'MCP Tools',         branch: 'interactive' },
      { id: 'graph_rag',          label: 'GraphRAG',          branch: 'interactive' },
      { id: 'research_fallback',  label: 'Research Fallback', branch: 'interactive' },
      { id: 'thinking',           label: 'Thinking',          branch: 'interactive' },
      { id: 'strategy_review',    label: 'Strategy Review',   branch: 'interactive' },
      { id: 'merger',             label: 'Merger',            branch: 'interactive' },
      { id: 'resolve_conflicts',  label: 'Resolve Conflicts', branch: 'interactive' },
      { id: 'self_critique',      label: 'Self-Critique',     branch: 'interactive' },
      { id: 'critic',             label: 'Critic',            branch: 'interactive' },
      // Agentic tool-calling fast path (Augmented Tool Path enrichment)
      { id: 'tool_entry',         label: 'Tool Entry',        branch: 'agent' },
      { id: 'agent_cache',        label: 'Agent Cache',       branch: 'agent' },
      { id: 'agent_graphrag',     label: 'Agent GraphRAG',    branch: 'agent' },
      { id: 'tool_model_call',    label: 'Tool Model',        branch: 'agent' },
      { id: 'agent_writeback',    label: 'Write-Back (async)',branch: 'agent' },
    ],
    edges: [
      ['cache', 'semantic_router'], ['semantic_router', 'knowledge_broker'], ['knowledge_broker', 'planner'], ['planner', 'fuzzy_router'],
      ['fuzzy_router', 'expert'], ['fuzzy_router', 'research'], ['fuzzy_router', 'mcp'], ['fuzzy_router', 'graph_rag'],
      ['expert', 'research_fallback'], ['research', 'research_fallback'],
      ['mcp', 'research_fallback'], ['graph_rag', 'research_fallback'],
      ['research_fallback', 'thinking'], ['thinking', 'strategy_review'], ['strategy_review', 'merger'],
      // merger's conditional edge (_should_replan) picks exactly one of these
      // three — not a strict DAG forward-edge, planner is a loop-back.
      ['merger', 'planner'], ['merger', 'resolve_conflicts'], ['merger', 'self_critique'],
      ['self_critique', 'merger'],
      ['resolve_conflicts', 'critic'],
      ['tool_entry', 'agent_cache'], ['agent_cache', 'agent_graphrag'],
      ['agent_graphrag', 'tool_model_call'], ['tool_model_call', 'agent_writeback'],
    ],
  };

  // Titled group boxes (Cytoscape compound-node parents) around each branch
  // — a request only ever travels through exactly one of the two, never
  // both, so without a label the untouched branch reads as "broken" rather
  // than "not applicable to this request".
  const GROUPS = {
    interactive: { id: 'grp_interactive', baseLabel: 'Interaktive Pipeline (LangGraph)' },
    agent:       { id: 'grp_agent',       baseLabel: 'Agent Tool Path (Claude Code / OpenCode)' },
  };
  const UNUSED_SUFFIX = ' — nicht genutzt in dieser Anfrage';

  // Three self-contained palettes, independent of the page's own light/dark
  // theme toggle — selectable per-user, persisted in localStorage. idle/skip
  // fills must always stay clearly distinguishable from canvasBg, since the
  // not-yet-reached nodes are the majority at any given moment.
  const PALETTES = {
    dark: {
      label: 'Dunkel',
      canvasBg: '#0d1117',
      idle:   { bg: '#374151', border: '#6b7280', text: '#d1d5db' },
      active: { bg: '#f59e0b', border: '#fbbf24', text: '#1f2937' },
      done:   { bg: '#16a34a', border: '#22c55e', text: '#e5e7eb' },
      skip:   { bg: '#374151', border: '#6b7280', text: '#9ca3af' },
      error:  { bg: '#dc2626', border: '#ef4444', text: '#e5e7eb' },
      edge: '#6b7280', edgeOpacity: 0.45, litEdge: '#22c55e', dimOpacity: 0.55,
    },
    light: {
      label: 'Hell',
      canvasBg: '#f1f5f9',
      idle:   { bg: '#e2e8f0', border: '#94a3b8', text: '#1e293b' },
      active: { bg: '#f59e0b', border: '#d97706', text: '#1f2937' },
      done:   { bg: '#16a34a', border: '#15803d', text: '#ffffff' },
      skip:   { bg: '#e2e8f0', border: '#94a3b8', text: '#64748b' },
      error:  { bg: '#dc2626', border: '#b91c1c', text: '#ffffff' },
      edge: '#64748b', edgeOpacity: 0.5, litEdge: '#15803d', dimOpacity: 0.45,
    },
    contrast: {
      label: 'Hoher Kontrast',
      canvasBg: '#000000',
      idle:   { bg: '#000000', border: '#ffffff', text: '#ffffff' },
      active: { bg: '#ffeb3b', border: '#ffffff', text: '#000000' },
      done:   { bg: '#00e676', border: '#ffffff', text: '#000000' },
      skip:   { bg: '#000000', border: '#9e9e9e', text: '#9e9e9e' },
      error:  { bg: '#ff1744', border: '#ffffff', text: '#ffffff' },
      edge: '#ffffff', edgeOpacity: 0.7, litEdge: '#00e676', dimOpacity: 0.35,
    },
  };
  const PALETTE_STORAGE_KEY = 'moe-pipeline-diagram-palette';

  function loadPaletteName() {
    const stored = localStorage.getItem(PALETTE_STORAGE_KEY);
    return PALETTES[stored] ? stored : 'dark';
  }

  let currentPaletteName = loadPaletteName();
  let cy = null;
  let pollTimer = null;
  let panelEl = null;
  let windowCtl = null;

  // ── Heartbeat: "time since last successful poll" ────────────────────────
  // A poll interval firing on schedule proves nothing by itself — the fetch
  // inside it could be failing silently every tick. Only a timestamp that
  // actually advances on a *successful* response tells the admin the
  // connection is alive rather than stuck; a stalled fetch leaves it
  // growing, which is exactly the "hängt oder tut nichts" ambiguity this
  // exists to resolve.
  let lastTraceOkTs = 0;
  let lastLogsOkTs = 0;
  let heartbeatTimer = null;

  // Set when polling auto-stops because the trace genuinely stopped
  // growing (request finished/expired) — a deliberate, expected stop, not
  // a hang. Without this the heartbeat would age past the warning
  // threshold and show a false "hängt?" for every completed request left
  // open in the background.
  let traceFinished = false;

  // ── Session replay (mindwalk-inspired) ───────────────────────────────────
  // Once a request finishes, its stage_trace (already fetched live while
  // polling) is kept around so the diagram can be scrubbed/replayed instead
  // of just showing the final static state — same data, no extra endpoint.
  let lastTrace = [];        // last-fetched stage_trace, sorted by ts ascending
  let replayMode = false;    // true once replay controls are shown (request finished)
  let replayIndex = 0;       // how many of lastTrace's entries are currently revealed
  let replayPlaying = false;
  let replayTimer = null;
  let replaySpeedMs = 500;   // ms per step at 1x

  function sortByTs(trace) {
    return (trace || []).slice().sort((a, b) => (a.ts || 0) - (b.ts || 0));
  }

  function _heartbeatText(ts, finished) {
    if (finished) return { text: 'beendet', color: '#6b7280' };
    if (!ts) return { text: '–', color: '#6b7280' };
    const age = (Date.now() - ts) / 1000;
    if (age < 3)  return { text: 'aktiv',              color: '#22c55e' };
    if (age < 8)  return { text: `vor ${age.toFixed(0)}s`, color: '#f59e0b' };
    return              { text: `⚠ vor ${age.toFixed(0)}s — hängt?`, color: '#ef4444' };
  }

  function renderHeartbeats() {
    const t = _heartbeatText(lastTraceOkTs, traceFinished);
    const traceEl = document.getElementById('pd-heartbeat');
    if (traceEl) { traceEl.textContent = '● ' + t.text; traceEl.style.color = t.color; }
    if (logsOpen) {
      const l = _heartbeatText(lastLogsOkTs, false);
      const logsEl = document.getElementById('pd-logs-heartbeat');
      if (logsEl) { logsEl.textContent = '● ' + l.text; logsEl.style.color = l.color; }
    }
  }

  function ensurePanel() {
    if (panelEl) return panelEl;
    panelEl = document.createElement('div');
    panelEl.id = 'pipeline-diagram-modal';
    panelEl.className = 'modal';
    panelEl.tabIndex = -1;
    const options = Object.keys(PALETTES).map(key =>
      `<option value="${key}"${key === currentPaletteName ? ' selected' : ''}>${PALETTES[key].label}</option>`
    ).join('');
    panelEl.innerHTML = `
      <div class="modal-dialog modal-xl modal-dialog-centered" id="pd-dialog">
        <div class="modal-content" style="position:relative">
          <div class="modal-header py-2" id="pd-header"
               style="cursor:move;user-select:none;flex-wrap:wrap;row-gap:.35rem">
            <h6 class="modal-title mb-0" style="flex:1 1 100%;min-width:0">
              <i class="bi bi-diagram-3 me-2"></i>Live-Pipeline —
              <code id="pd-chat-id" style="font-size:.8rem"></code>
            </h6>
            <div class="d-flex align-items-center flex-wrap ms-auto" style="row-gap:.35rem">
              <span class="badge bg-secondary ms-2" id="pd-status">…</span>
              <span id="pd-heartbeat" class="ms-2" style="font-size:.7rem;white-space:nowrap" title="Zeit seit dem letzten erfolgreichen Abruf — wächst diese Zahl ungewöhnlich stark, hängt die Verbindung">–</span>
              <select id="pd-palette" class="form-select form-select-sm ms-2" style="width:auto" title="Farbschema">
                ${options}
              </select>
              <button type="button" class="btn btn-sm btn-outline-secondary ms-1" id="pd-experts-toggle" title="Experten &amp; MCP-Tools des Templates (verfügbar vs. benutzt)">
                <i class="bi bi-people"></i>
              </button>
              <button type="button" class="btn btn-sm btn-outline-secondary ms-1" id="pd-files-toggle" title="Berührte Dateien dieser Anfrage (Agent Tool Path)">
                <i class="bi bi-folder2-open"></i>
              </button>
              <button type="button" class="btn btn-sm btn-outline-secondary ms-1" id="pd-logs-toggle" title="Log-Zeilen dieser Anfrage">
                <i class="bi bi-terminal"></i>
              </button>
              <button type="button" class="btn btn-sm btn-outline-secondary ms-1" id="pd-maximize" title="Maximieren">
                <i class="bi bi-arrows-fullscreen"></i>
              </button>
              <button type="button" class="btn-close ms-2" onclick="window.closePipelineDiagram()"></button>
            </div>
          </div>
          <div class="modal-body p-0">
            <div id="pd-cy-row" style="display:flex;width:100%">
              <div id="pd-cy" style="flex:1 1 auto;min-width:0;height:600px;"></div>
              <div id="pd-experts" class="d-none border-start" style="width:300px;flex-shrink:0;height:600px;display:flex;flex-direction:column;background:#0d1117">
                <div class="d-flex justify-content-between align-items-center px-2 py-1 border-bottom border-secondary"
                     style="font-size:.68rem;background:#161b22;flex-shrink:0">
                  <span class="text-light"><i class="bi bi-people me-1"></i>Experten &amp; MCP-Tools (Template)</span>
                </div>
                <div id="pd-experts-content" class="text-light m-0 p-2" style="overflow-y:auto;flex:1 1 auto;font-size:.72rem;line-height:1.5"></div>
              </div>
            </div>
            <div id="pd-replay-bar" class="d-none border-top d-flex align-items-center px-2 py-1"
                 style="font-size:.72rem;background:#161b22;color:#c9d1d9;gap:.5rem;flex-wrap:wrap">
              <button type="button" class="btn btn-sm btn-outline-light py-0 px-2" id="pd-replay-playpause" title="Play/Pause (Leertaste)">
                <i class="bi bi-play-fill"></i>
              </button>
              <button type="button" class="btn btn-sm btn-outline-light py-0 px-2" id="pd-replay-back" title="Ein Schritt zurück (←)">
                <i class="bi bi-skip-backward-fill"></i>
              </button>
              <button type="button" class="btn btn-sm btn-outline-light py-0 px-2" id="pd-replay-fwd" title="Ein Schritt vor (→)">
                <i class="bi bi-skip-forward-fill"></i>
              </button>
              <input type="range" id="pd-replay-slider" min="0" max="0" value="0" style="flex:1 1 160px;min-width:120px">
              <span id="pd-replay-counter" style="white-space:nowrap">0/0</span>
              <select id="pd-replay-speed" class="form-select form-select-sm py-0" style="width:auto">
                <option value="1000">0.5×</option>
                <option value="500" selected>1×</option>
                <option value="250">2×</option>
                <option value="125">4×</option>
              </select>
              <span style="white-space:nowrap;opacity:.75"><i class="bi bi-clock-history me-1"></i>Historische Ansicht — Anfrage abgeschlossen</span>
            </div>
            <div id="pd-files" class="d-none border-top" style="height:180px;display:flex;flex-direction:column;background:#0d1117">
              <div class="d-flex justify-content-between align-items-center px-2 py-1 border-bottom border-secondary"
                   style="font-size:.68rem;background:#161b22;flex-shrink:0">
                <span class="text-light"><i class="bi bi-folder2-open me-1"></i>Berührte Dateien (Agent Tool Path)</span>
              </div>
              <div id="pd-files-content" class="text-light m-0 p-2" style="overflow-y:auto;flex:1 1 auto;font-size:.72rem;line-height:1.5"></div>
            </div>
            <div id="pd-logs" class="d-none border-top" style="height:180px;display:flex;flex-direction:column;background:#0d1117">
              <div class="d-flex justify-content-between align-items-center px-2 py-1 border-bottom border-secondary"
                   style="font-size:.68rem;background:#161b22;flex-shrink:0">
                <span class="text-light"><i class="bi bi-terminal me-1"></i>Log-Zeilen dieser Anfrage</span>
                <span id="pd-logs-heartbeat" style="white-space:nowrap" title="Zeit seit dem letzten erfolgreichen Log-Abruf">–</span>
              </div>
              <pre id="pd-logs-content" class="text-light m-0 p-2" style="overflow-y:auto;flex:1 1 auto;
                   font-size:.72rem;line-height:1.4;white-space:pre-wrap;word-break:break-all"></pre>
            </div>
          </div>
          <div id="pd-resize-handle" title="Größe ändern"
               style="position:absolute;right:0;bottom:0;width:18px;height:18px;cursor:nwse-resize;
                      display:flex;align-items:center;justify-content:center;opacity:.6;z-index:5">
            <i class="bi bi-arrows-angle-expand" style="font-size:.75rem"></i>
          </div>
        </div>
      </div>`;
    document.body.appendChild(panelEl);
    panelEl.querySelector('#pd-palette').addEventListener('change', (e) => applyPalette(e.target.value));
    windowCtl = makeWindowControls(
      panelEl.querySelector('#pd-dialog'),
      panelEl.querySelector('#pd-header'),
      'pd-cy',
    );
    panelEl.querySelector('#pd-resize-handle').addEventListener('mousedown', (e) => windowCtl.startResize(e));
    panelEl.querySelector('#pd-maximize').addEventListener('click', () => windowCtl.toggleMaximize());
    panelEl.querySelector('#pd-logs-toggle').addEventListener('click', toggleLogs);
    panelEl.querySelector('#pd-files-toggle').addEventListener('click', toggleFiles);
    panelEl.querySelector('#pd-experts-toggle').addEventListener('click', toggleExperts);
    panelEl.querySelector('#pd-replay-playpause').addEventListener('click', () => {
      replayPlaying ? pauseReplay() : playReplay();
    });
    panelEl.querySelector('#pd-replay-back').addEventListener('click', () => stepReplay(-1));
    panelEl.querySelector('#pd-replay-fwd').addEventListener('click', () => stepReplay(1));
    panelEl.querySelector('#pd-replay-slider').addEventListener('input', (e) => {
      pauseReplay();
      applyReplayStep(parseInt(e.target.value, 10) || 0);
    });
    panelEl.querySelector('#pd-replay-speed').addEventListener('change', (e) => {
      replaySpeedMs = parseInt(e.target.value, 10) || 500;
      if (replayPlaying) { pauseReplay(); playReplay(); } // restart interval at new speed
    });
    return panelEl;
  }

  // Available in both Admin and Portal — /user/api/live/process-logs/
  // enforces the same chat_id ownership check as the trace endpoint, so
  // portal users only ever see log lines tagged with a chat_id they own.
  // Loaded strictly on click, never as part of the regular 1.5s diagram
  // poll, to avoid doubling request volume for anyone who never opens it.
  let currentChatId = null;
  let logsUrlBase = null;
  let logsOpen = false;
  let logsPollTimer = null;

  async function fetchLogsOnce() {
    if (!currentChatId || !logsUrlBase) return;
    const content = document.getElementById('pd-logs-content');
    try {
      const r = await fetch(`${logsUrlBase}${encodeURIComponent(currentChatId)}`);
      const data = await r.json();
      const wasAtBottom = content.scrollHeight - content.scrollTop <= content.clientHeight + 20;
      content.textContent = (data.lines || []).join('\n') || '(noch keine Log-Zeilen für diese Anfrage)';
      if (wasAtBottom) content.scrollTop = content.scrollHeight;
      lastLogsOkTs = Date.now();
    } catch (e) {
      console.warn('process-logs fetch failed:', e);
      // Deliberately not updating lastLogsOkTs — a failing fetch must let
      // the heartbeat age instead of masking the failure.
    }
  }

  function toggleLogs() {
    const box = document.getElementById('pd-logs');
    logsOpen = !logsOpen;
    box.classList.toggle('d-none', !logsOpen);
    if (logsOpen) {
      fetchLogsOnce();
      if (logsPollTimer) clearInterval(logsPollTimer);
      logsPollTimer = setInterval(fetchLogsOnce, 1500);
    } else if (logsPollTimer) {
      clearInterval(logsPollTimer);
      logsPollTimer = null;
    }
  }

  // ── Datei-Touch-Ansicht (Agent Tool Path) ────────────────────────────────
  // Same on-demand pattern as the Logs section above: fetched only while the
  // panel's files section is open, never part of the regular diagram poll.
  // Always shows the full current file list (not filtered by the replay
  // position from the section below — see plan doc for the reasoning).
  let filesUrlBase = null;
  let filesOpen = false;
  let filesPollTimer = null;

  const _FILE_ACTION_META = {
    write:  { icon: '✏️', label: 'Write', color: '#f59e0b' },
    read:   { icon: '👁️', label: 'Read',  color: '#60a5fa' },
    search: { icon: '🔍', label: 'Search', color: '#9ca3af' },
    exec:   { icon: '▶️', label: 'Exec',  color: '#a78bfa' },
    other:  { icon: '•',  label: 'Other', color: '#6b7280' },
  };

  function _relTime(ts) {
    if (!ts) return '';
    const s = Math.max(0, (Date.now() / 1000) - ts);
    if (s < 60) return `vor ${s.toFixed(0)}s`;
    if (s < 3600) return `vor ${(s / 60).toFixed(0)}min`;
    return `vor ${(s / 3600).toFixed(1)}h`;
  }

  function _escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  async function fetchFilesOnce() {
    if (!currentChatId || !filesUrlBase) return;
    const content = document.getElementById('pd-files-content');
    try {
      const r = await fetch(`${filesUrlBase}${encodeURIComponent(currentChatId)}`);
      const data = await r.json();
      const files = (data.files || []).slice().sort((a, b) => (b.ts || 0) - (a.ts || 0));
      if (!files.length) {
        content.innerHTML = '<span style="opacity:.6">(noch keine Dateien berührt)</span>';
        return;
      }
      content.innerHTML = files.map(f => {
        const meta = _FILE_ACTION_META[f.action] || _FILE_ACTION_META.other;
        return `<div style="display:flex;gap:.5rem;align-items:baseline;padding:1px 0;white-space:nowrap;overflow:hidden">
          <span title="${meta.label}">${meta.icon}</span>
          <span style="color:${meta.color};flex-shrink:0">${meta.label}</span>
          <span style="overflow:hidden;text-overflow:ellipsis" title="${_escapeHtml(f.path || '')}">${_escapeHtml(f.path || '')}</span>
          <span style="opacity:.5;margin-left:auto;flex-shrink:0">${_escapeHtml(f.tool || '')} · ${_relTime(f.ts)}</span>
        </div>`;
      }).join('');
    } catch (e) {
      console.warn('file-touches fetch failed:', e);
    }
  }

  function toggleFiles() {
    const box = document.getElementById('pd-files');
    filesOpen = !filesOpen;
    box.classList.toggle('d-none', !filesOpen);
    if (filesOpen) {
      fetchFilesOnce();
      if (filesPollTimer) clearInterval(filesPollTimer);
      filesPollTimer = setInterval(fetchFilesOnce, 1500);
    } else if (filesPollTimer) {
      clearInterval(filesPollTimer);
      filesPollTimer = null;
    }
  }

  // ── Experts & MCP Tools panel ───────────────────────────────────────────
  // Unlike files/logs, this needs no dedicated fetch/poll loop: the trace
  // endpoint (already polled every ~1.5s for the diagram itself) was
  // extended to also return resolved_tmpl_name/available_experts/
  // available_mcp_tools alongside stage_trace — pollOnce() caches those
  // into the module-level variables below, and applyTrace() (which already
  // runs on every poll) re-renders this panel's content for free. Toggling
  // only shows/hides the already-current content.
  let expertsOpen = false;
  let resolvedTmplName = '';
  let availableExperts = {};   // {category: [model, ...]}
  let availableMcpTools = [];  // [tool_name, ...]

  function toggleExperts() {
    const box = document.getElementById('pd-experts');
    expertsOpen = !expertsOpen;
    box.classList.toggle('d-none', !expertsOpen);
  }

  function _parseExpertDetail(detail) {
    // services/tracking.py::_record_stage writes "model/category" for
    // started/done expert entries, but a bare "model" (no category) for
    // error entries — see graph/expert.py's two _record_stage("expert",
    // "error", model_name) call sites. Model names use ':' for tags
    // (e.g. "qwen3.6:35b"), never '/', so splitting on the last '/' is safe.
    const idx = (detail || '').lastIndexOf('/');
    if (idx === -1) return { model: detail || '', category: '' };
    return { model: detail.slice(0, idx), category: detail.slice(idx + 1) };
  }

  function _usedExpertsFromByStage(byStage) {
    const used = new Set(); // "category::model"
    (byStage['expert'] || []).forEach(e => {
      const { model, category } = _parseExpertDetail(e.detail);
      if (model) used.add(`${category}::${model}`);
    });
    return used;
  }

  function _usedMcpToolsFromByStage(byStage) {
    const used = new Set();
    (byStage['mcp'] || []).forEach(e => {
      (e.detail || '').split(',').map(s => s.trim()).filter(Boolean).forEach(t => used.add(t));
    });
    return used;
  }

  function renderExpertsPanel(byStage) {
    const content = document.getElementById('pd-experts-content');
    if (!content) return;
    const usedExperts = _usedExpertsFromByStage(byStage);   // Set of "category::model"
    const usedMcp = _usedMcpToolsFromByStage(byStage);      // Set of tool name

    // Only actually-used entries are shown — the full catalog (13+ expert
    // categories, 50+ MCP tools) made this panel a scroll-fest with almost
    // everything greyed out and irrelevant to a single request. Confirmed
    // by the user live: "die Liste der MCP-Tools ist zu lang... nur die
    // Tools und Experten einblenden die verwendet werden". availableExperts
    // is still fetched/cached (used below only to resolve a used model's
    // category label — the trace's "category::model" key is already
    // self-describing, so this is now a light nicety, not load-bearing).
    let html = '';
    if (resolvedTmplName) {
      html += `<div style="opacity:.7;margin-bottom:.4rem">Template: <strong>${_escapeHtml(resolvedTmplName)}</strong></div>`;
    }
    const _row = (label, sub) => `
      <div style="display:flex;gap:.4rem;align-items:baseline;padding:1px 0">
        <span>✅</span>
        <span style="color:#4ade80">${_escapeHtml(label)}</span>
        ${sub ? `<span style="opacity:.6;font-size:.68rem">(${_escapeHtml(sub)})</span>` : ''}
      </div>`;

    if (usedExperts.size) {
      html += '<div style="margin-bottom:.5rem"><div style="opacity:.6;font-size:.65rem;text-transform:uppercase;margin-bottom:.2rem">Experten</div>';
      Array.from(usedExperts).sort().forEach(key => {
        const [cat, model] = key.split('::');
        html += _row(model, cat);
      });
      html += '</div>';
    }
    if (usedMcp.size) {
      html += '<div><div style="opacity:.6;font-size:.65rem;text-transform:uppercase;margin-bottom:.2rem">MCP Tools</div>';
      Array.from(usedMcp).sort().forEach(tool => {
        html += _row(tool, '');
      });
      html += '</div>';
    }
    content.innerHTML = html || '<span style="opacity:.6">(noch keine Experten/Tools in diesem Lauf benutzt)</span>';
  }

  // Turns the modal dialog into a free-floating, draggable, resizable,
  // maximizable window instead of Bootstrap's fixed centered/sized modal.
  // No new dependency — plain mouse-event dragging, matching this file's
  // existing "reuse what's vendored, add nothing new" approach.
  function makeWindowControls(dialogEl, headerEl, cyContainerId) {
    let dragging = false, resizing = false, maximized = false;
    let dragStartX = 0, dragStartY = 0, originX = 0, originY = 0;
    let resizeStartX = 0, resizeStartY = 0, startW = 0, startH = 0;
    let savedRect = null;

    function toFreePosition() {
      if (dialogEl.dataset.free === '1') return;
      const r = dialogEl.getBoundingClientRect();
      dialogEl.classList.remove('modal-dialog-centered');
      Object.assign(dialogEl.style, {
        position: 'fixed', margin: '0', maxWidth: 'none',
        left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`,
      });
      dialogEl.dataset.free = '1';
    }

    headerEl.addEventListener('mousedown', (e) => {
      if (e.target.closest('select, button, .btn-close')) return;
      toFreePosition();
      dragging = true;
      dragStartX = e.clientX; dragStartY = e.clientY;
      const r = dialogEl.getBoundingClientRect();
      originX = r.left; originY = r.top;
      e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
      if (dragging) {
        dialogEl.style.left = `${Math.max(0, originX + (e.clientX - dragStartX))}px`;
        dialogEl.style.top  = `${Math.max(0, originY + (e.clientY - dragStartY))}px`;
      } else if (resizing) {
        const newW = Math.max(480, startW + (e.clientX - resizeStartX));
        const newH = Math.max(360, startH + (e.clientY - resizeStartY));
        dialogEl.style.width = `${newW}px`;
        const cyEl = document.getElementById(cyContainerId);
        if (cyEl) cyEl.style.height = `${Math.max(200, newH - headerEl.offsetHeight)}px`;
        if (cy) cy.resize();
      }
    });

    document.addEventListener('mouseup', () => {
      if (!dragging && !resizing) return;
      dragging = false; resizing = false;
      if (cy) cy.fit(cy.elements(), 30);
    });

    return {
      startResize(e) {
        toFreePosition();
        resizing = true;
        resizeStartX = e.clientX; resizeStartY = e.clientY;
        const r = dialogEl.getBoundingClientRect();
        startW = r.width; startH = r.height;
        e.preventDefault(); e.stopPropagation();
      },
      toggleMaximize() {
        const cyEl = document.getElementById(cyContainerId);
        if (!maximized) {
          toFreePosition();
          savedRect = {
            left: dialogEl.style.left, top: dialogEl.style.top,
            width: dialogEl.style.width, cyHeight: cyEl.style.height,
          };
          dialogEl.style.left = '8px';
          dialogEl.style.top = '8px';
          dialogEl.style.width = `${window.innerWidth - 16}px`;
          cyEl.style.height = `${window.innerHeight - 16 - headerEl.offsetHeight}px`;
          maximized = true;
        } else if (savedRect) {
          dialogEl.style.left = savedRect.left;
          dialogEl.style.top = savedRect.top;
          dialogEl.style.width = savedRect.width;
          cyEl.style.height = savedRect.cyHeight;
          maximized = false;
        }
        if (cy) { cy.resize(); cy.fit(cy.elements(), 30); }
      },
    };
  }

  function buildStyle(p) {
    return [
      {
        // Compound "group box" nodes — titled containers around the
        // interactive-pipeline and agent-tool-path node sets, mirroring the
        // labelled "Parallel Execution" subgraph box style already used in
        // docs/ARCHITECTURE.md's mermaid diagram. Without these the two
        // node clusters had no indication of what they represent.
        selector: ':parent',
        style: {
          'background-opacity': 0.08,
          'background-color':   p.idle.text,
          'border-width':       1,
          'border-color':       p.edge,
          'border-style':       'dashed',
          'label':              'data(label)',
          'color':              p.idle.text,
          'font-size':          12,
          'font-weight':        'bold',
          'text-valign':        'top',
          'text-halign':        'center',
          'text-margin-y':      -8,
          'padding':            '28px',
        },
      },
      {
        // :childless excludes the group-box parent nodes above — without it
        // this rule (later in the array) would win over the :parent style
        // for group nodes too, since they also match the plain 'node' tag.
        selector: 'node:childless',
        style: {
          'background-color': p.idle.bg,
          'border-color':     p.idle.border,
          'border-width':     2,
          'label':            'data(label)',
          'color':            p.idle.text,
          'font-size':        10,
          'text-valign':      'bottom',
          'text-margin-y':    4,
          'width':            34,
          'height':           34,
          'transition-property': 'background-color, border-color',
          // Long enough to visibly bridge the ~1.5s poll gap — combined with
          // the afterglow fade in applyTrace(), a stage that fired and
          // completed between two polls doesn't just snap straight to the
          // flat "done" color, it visibly glows out toward it.
          'transition-duration': '1200ms',
        },
      },
      {
        selector: 'edge',
        style: {
          'width': 1.5,
          'line-color': p.edge,
          'target-arrow-color': p.edge,
          'target-arrow-shape': 'triangle',
          'curve-style': 'bezier',
          'opacity': p.edgeOpacity,
        },
      },
      {
        selector: '.pd-active',
        style: { 'background-color': p.active.bg, 'border-color': p.active.border, 'color': p.active.text },
      },
      {
        selector: '.pd-done',
        style: { 'background-color': p.done.bg, 'border-color': p.done.border, 'color': p.done.text },
      },
      {
        selector: '.pd-skip',
        style: { 'background-color': p.skip.bg, 'border-color': p.skip.border, 'border-style': 'dashed', 'color': p.skip.text },
      },
      {
        selector: '.pd-error',
        style: { 'background-color': p.error.bg, 'border-color': p.error.border, 'color': p.error.text },
      },
      {
        // Dim opacity is palette-specific: a flat 0.25 blended a lightened
        // fill back into near-invisibility on the dark palette, so each
        // palette tunes its own value instead of sharing one constant.
        selector: '.pd-dim',
        style: { 'opacity': p.dimOpacity },
      },
      {
        selector: 'edge.pd-lit',
        style: { 'line-color': p.litEdge, 'target-arrow-color': p.litEdge, 'opacity': 0.9 },
      },
    ];
  }

  function applyPalette(name) {
    if (!PALETTES[name]) return;
    currentPaletteName = name;
    localStorage.setItem(PALETTE_STORAGE_KEY, name);
    const cyEl = document.getElementById('pd-cy');
    if (cyEl) cyEl.style.background = PALETTES[name].canvasBg;
    if (cy) cy.style(buildStyle(PALETTES[name])).update();
    const sel = document.getElementById('pd-palette');
    if (sel && sel.value !== name) sel.value = name;
  }

  function initCy() {
    const elements = [
      { data: { id: GROUPS.interactive.id, label: GROUPS.interactive.baseLabel } },
      { data: { id: GROUPS.agent.id,       label: GROUPS.agent.baseLabel } },
      ...TOPOLOGY.nodes.map(n => ({
        data: { id: n.id, label: n.label, branch: n.branch, parent: GROUPS[n.branch].id },
      })),
      ...TOPOLOGY.edges.map(([s, t]) => ({ data: { id: `${s}__${t}`, source: s, target: t } })),
    ];
    const palette = PALETTES[currentPaletteName];
    document.getElementById('pd-cy').style.background = palette.canvasBg;
    if (cy) cy.destroy();
    cy = cytoscape({
      container: document.getElementById('pd-cy'),
      elements,
      style: buildStyle(palette),
      layout: { name: 'preset', positions: presetPositions() },
      userZoomingEnabled: true,
      userPanningEnabled: true,
      boxSelectionEnabled: false,
    });
    // The DAG layout's absolute coordinates span wider than the modal —
    // scale+center everything into view instead of relying on a fixed zoom.
    cy.fit(cy.elements(), 30);
  }

  // Explicit position map mirroring docs/ARCHITECTURE.md's mermaid flowchart
  // for the LangGraph pipeline: a genuine DAG shape with a visually grouped
  // "parallel execution" fan-out block (expert/research/mcp/graph_rag run
  // concurrently after fuzzy_router, not one after another), and merger's
  // conditional branch shown as siblings rather than a single line — a
  // straight row previously made every request look purely sequential even
  // though most of the pipeline genuinely isn't.
  const NODE_POSITIONS = {
    cache:              { x:   40, y: 260 },
    semantic_router:    { x:  170, y: 260 },
    knowledge_broker:   { x:  300, y: 260 },
    planner:            { x:  430, y: 260 },
    fuzzy_router:       { x:  560, y: 260 },
    // Parallel Execution block — same x (depth), stacked vertically
    expert:             { x:  700, y: 100 },
    research:           { x:  700, y: 190 },
    mcp:                { x:  700, y: 280 },
    graph_rag:          { x:  700, y: 370 },
    research_fallback:  { x:  840, y: 260 },
    thinking:           { x:  970, y: 260 },
    strategy_review:    { x: 1100, y: 260 },
    merger:             { x: 1230, y: 260 },
    // merger's conditional edge picks one of these three siblings
    resolve_conflicts:  { x: 1370, y: 180 },
    self_critique:      { x: 1370, y: 340 }, // loops back to merger
    critic:             { x: 1500, y: 180 },

    // Agentic tool-calling fast path — separate lane, genuinely serial per
    // the real code flow (each step's output gates the next).
    tool_entry:         { x:   40, y: 520 },
    agent_cache:        { x:  190, y: 520 },
    agent_graphrag:     { x:  340, y: 520 },
    tool_model_call:    { x:  490, y: 520 },
    agent_writeback:    { x:  640, y: 520 }, // async, fires after the response
  };

  function presetPositions() {
    return NODE_POSITIONS;
  }

  // Afterglow window: a stage that fires and completes faster than one poll
  // tick (1.5s) would otherwise jump straight from "idle" to the flat
  // "done" color the moment it's first observed, with no visible sign it
  // was ever actively in progress. Instead, a freshly-completed stage
  // starts at the "active" color and is blended toward "done" over this
  // many seconds, recomputed on every poll — a fading afterglow rather
  // than an instant snap.
  const RECENT_FADE_SECONDS = 5;

  function hexToRgb(hex) {
    const h = hex.replace('#', '');
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }

  function lerpColor(hexA, hexB, t) {
    const a = hexToRgb(hexA), b = hexToRgb(hexB);
    const c = a.map((v, i) => Math.round(v + (b[i] - v) * t));
    return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
  }

  function classifyStage(entries, isLatestOverall) {
    if (!entries || entries.length === 0) return 'idle';
    const last = entries[entries.length - 1];
    const status = String(last.status || '');
    if (status.includes('error')) return 'error';
    if (status === 'started') return isLatestOverall ? 'active' : 'done';
    if (status.includes('miss') || status.includes('skip')) return 'skip';
    return 'done'; // hit_*, done, matched, confirmed, corrected, cache_hit, fast_path, ...
  }

  function applyTrace(stageTrace) {
    if (!cy) return;
    cy.nodes().removeClass('pd-active pd-done pd-skip pd-error pd-dim');
    cy.edges().removeClass('pd-lit');

    const byStage = {};
    (stageTrace || []).forEach(e => {
      if (!e || !e.stage) return;
      (byStage[e.stage] = byStage[e.stage] || []).push(e);
    });
    Object.values(byStage).forEach(list => list.sort((a, b) => (a.ts || 0) - (b.ts || 0)));

    let latestTs = -Infinity, latestStage = null;
    (stageTrace || []).forEach(e => {
      if (e && typeof e.ts === 'number' && e.ts > latestTs) { latestTs = e.ts; latestStage = e.stage; }
    });

    const seenStages = new Set(Object.keys(byStage));
    // First TOPOLOGY node (in array order) whose id was actually traced
    // determines which branch this request took — a request only ever
    // travels through one of the two.
    const usedBranch = seenStages.size === 0 ? null
      : (TOPOLOGY.nodes.find(n => seenStages.has(n.id)) || {}).branch || null;

    const nowTs = Date.now() / 1000;
    const palette = PALETTES[currentPaletteName];
    TOPOLOGY.nodes.forEach(n => {
      const entries = byStage[n.id];
      const cls = classifyStage(entries, n.id === latestStage);
      const node = cy.getElementById(n.id);
      // Clear any afterglow override from a previous poll before
      // reapplying — otherwise a stage that ages past the fade window
      // would keep its last interpolated color forever instead of
      // settling into the flat class-based "done" color.
      node.removeStyle('background-color border-color');
      if (cls !== 'idle') node.addClass(`pd-${cls}`);
      if (usedBranch && n.branch !== usedBranch) node.addClass('pd-dim');

      if (cls === 'done' && entries && entries.length) {
        const age = nowTs - (entries[entries.length - 1].ts || 0);
        if (age >= 0 && age < RECENT_FADE_SECONDS) {
          const t = Math.min(1, age / RECENT_FADE_SECONDS);
          node.style({
            'background-color': lerpColor(palette.active.bg, palette.done.bg, t),
            'border-color':     lerpColor(palette.active.border, palette.done.border, t),
          });
        }
      }
    });

    // Label the untouched branch's group box explicitly instead of leaving
    // it as an unexplained grey block.
    cy.getElementById(GROUPS.interactive.id).data('label',
      GROUPS.interactive.baseLabel + (usedBranch === 'agent' ? UNUSED_SUFFIX : ''));
    cy.getElementById(GROUPS.agent.id).data('label',
      GROUPS.agent.baseLabel + (usedBranch === 'interactive' ? UNUSED_SUFFIX : ''));

    TOPOLOGY.edges.forEach(([s, t]) => {
      if (seenStages.has(s) && seenStages.has(t)) {
        cy.getElementById(`${s}__${t}`).addClass('pd-lit');
      }
    });

    renderExpertsPanel(byStage);

    const statusEl = document.getElementById('pd-status');
    if (statusEl) {
      const terminal = latestStage && ['critic', 'agent_writeback', 'merger'].includes(latestStage)
        && classifyStage(byStage[latestStage], true) === 'done';
      statusEl.textContent = seenStages.size === 0 ? 'waiting…' : (terminal ? 'complete' : 'running');
      statusEl.className = 'badge ms-2 ' + (terminal ? 'bg-success' : (seenStages.size === 0 ? 'bg-secondary' : 'bg-warning text-dark'));
    }
    return { seenStages, latestStage };
  }

  async function pollOnce(chatId, traceUrlBase) {
    try {
      const r = await fetch(`${traceUrlBase}${encodeURIComponent(chatId)}`);
      const data = await r.json();
      lastTrace = sortByTs(data.stage_trace || []);
      if (data.resolved_tmpl_name !== undefined) resolvedTmplName = data.resolved_tmpl_name || '';
      if (data.available_experts) availableExperts = data.available_experts;
      if (data.available_mcp_tools) availableMcpTools = data.available_mcp_tools;
      applyTrace(data.stage_trace || []);
      lastTraceOkTs = Date.now();
      // "active" is the authoritative still-running signal from the
      // backend (moe:active:{chat_id} existence) — default true so a
      // response shape without the field (older cache, unexpected error
      // body) never falsely triggers "beendet".
      return data.active !== false;
    } catch (e) {
      console.warn('pipeline trace fetch failed:', e);
      // A failed fetch must NOT be read as "request finished" — keep
      // polling and let the heartbeat age instead (see renderHeartbeats()).
      return true;
    }
  }

  // ── Replay controls ───────────────────────────────────────────────────────
  function updateReplayUI() {
    const slider = document.getElementById('pd-replay-slider');
    const counter = document.getElementById('pd-replay-counter');
    const btn = document.getElementById('pd-replay-playpause');
    if (!slider) return;
    slider.max = String(lastTrace.length);
    slider.value = String(replayIndex);
    counter.textContent = `${replayIndex}/${lastTrace.length}`;
    btn.innerHTML = replayPlaying
      ? '<i class="bi bi-pause-fill"></i>'
      : '<i class="bi bi-play-fill"></i>';
  }

  function enterReplayMode() {
    replayMode = true;
    replayIndex = lastTrace.length; // start showing the final state, as before
    const bar = document.getElementById('pd-replay-bar');
    if (bar) bar.classList.remove('d-none');
    updateReplayUI();
  }

  function applyReplayStep(idx) {
    replayIndex = Math.max(0, Math.min(lastTrace.length, idx));
    applyTrace(lastTrace.slice(0, replayIndex));
    updateReplayUI();
  }

  function playReplay() {
    if (!replayMode || replayPlaying) return;
    replayPlaying = true;
    if (replayIndex >= lastTrace.length) replayIndex = 0; // restart from the beginning
    if (replayTimer) clearInterval(replayTimer);
    replayTimer = setInterval(() => {
      if (replayIndex >= lastTrace.length) { pauseReplay(); return; }
      applyReplayStep(replayIndex + 1);
    }, replaySpeedMs);
    updateReplayUI();
  }

  function pauseReplay() {
    replayPlaying = false;
    if (replayTimer) { clearInterval(replayTimer); replayTimer = null; }
    updateReplayUI();
  }

  function stepReplay(delta) {
    if (!replayMode) return;
    pauseReplay();
    applyReplayStep(replayIndex + delta);
  }

  function handleReplayKeydown(e) {
    if (!replayMode) return;
    const tag = (e.target && e.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    if (e.code === 'Space') { e.preventDefault(); replayPlaying ? pauseReplay() : playReplay(); }
    else if (e.code === 'ArrowRight') { e.preventDefault(); stepReplay(1); }
    else if (e.code === 'ArrowLeft') { e.preventDefault(); stepReplay(-1); }
  }

  window.openPipelineDiagram = function (chatId, traceUrlBase) {
    ensurePanel();
    document.getElementById('pd-chat-id').textContent = (chatId || '').slice(-16);

    currentChatId = chatId;
    // Works for both '/api/live/request-trace/' (admin) and
    // '/user/api/live/request-trace/' (portal) — each has matching
    // process-logs/file-touches endpoints with their own auth/ownership gate.
    logsUrlBase = traceUrlBase.replace('request-trace', 'process-logs');
    filesUrlBase = traceUrlBase.replace('request-trace', 'file-touches');
    document.getElementById('pd-logs-toggle').classList.remove('d-none');
    document.getElementById('pd-files-toggle').classList.remove('d-none');
    // Reset the log/files view for the newly-selected request instead of
    // showing the previous row's stale content until the first poll lands.
    logsOpen = false;
    document.getElementById('pd-logs').classList.add('d-none');
    document.getElementById('pd-logs-content').textContent = '';
    if (logsPollTimer) { clearInterval(logsPollTimer); logsPollTimer = null; }
    filesOpen = false;
    document.getElementById('pd-files').classList.add('d-none');
    document.getElementById('pd-files-content').textContent = '';
    if (filesPollTimer) { clearInterval(filesPollTimer); filesPollTimer = null; }
    expertsOpen = false;
    document.getElementById('pd-experts').classList.add('d-none');
    document.getElementById('pd-experts-content').innerHTML = '';
    resolvedTmplName = '';
    availableExperts = {};
    availableMcpTools = [];

    // Reset replay state for the newly-selected request.
    pauseReplay();
    replayMode = false;
    lastTrace = [];
    replayIndex = 0;
    document.getElementById('pd-replay-bar').classList.add('d-none');

    // eslint-disable-next-line no-undef
    const modal = bootstrap.Modal.getOrCreateInstance(panelEl);
    modal.show();
    initCy();

    lastTraceOkTs = 0;
    lastLogsOkTs = 0;
    traceFinished = false;
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    heartbeatTimer = setInterval(renderHeartbeats, 1000);
    renderHeartbeats();

    // Unified poll step used both for the immediate first check and every
    // subsequent tick — previously only the interval callback checked
    // "stillActive", so opening the panel for an already-finished request
    // (browsing history) never triggered replay mode until a redundant
    // extra tick 1.5s later just to notice nothing had changed.
    async function pollStep() {
      const stillActive = await pollOnce(chatId, traceUrlBase);
      if (!stillActive) {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        traceFinished = true;
        renderHeartbeats();
        enterReplayMode();
      }
    }
    if (pollTimer) clearInterval(pollTimer);
    pollStep();
    // Stop only once the backend confirms moe:active:{chat_id} is gone —
    // NOT after N polls with no new stage. A single legitimately slow step
    // (e.g. a 20s expert LLM call) produces no new stage for far longer than
    // the old 12s window, which falsely marked genuinely active requests as
    // "beendet". A failed fetch returns true (keep polling), so a network
    // hiccup can't be misread as "finished" either.
    pollTimer = setInterval(pollStep, 1500);

    document.addEventListener('keydown', handleReplayKeydown);
    panelEl.addEventListener('hidden.bs.modal', window.closePipelineDiagram, { once: true });
  };

  window.closePipelineDiagram = function () {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (logsPollTimer) { clearInterval(logsPollTimer); logsPollTimer = null; }
    if (filesPollTimer) { clearInterval(filesPollTimer); filesPollTimer = null; }
    if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
    pauseReplay();
    replayMode = false;
    document.removeEventListener('keydown', handleReplayKeydown);
    logsOpen = false;
    filesOpen = false;
    expertsOpen = false;
    if (cy) { cy.destroy(); cy = null; }
  };
})();
