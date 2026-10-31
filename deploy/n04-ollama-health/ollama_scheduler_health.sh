#!/bin/bash
# Schreibt Ollama-Scheduler-Gesundheit als node_exporter-Textfile-Metrik.
#
# Hintergrund: Auf N04-RTX meldete Ollama ~22 h vor einem Totalausfall des Modell-Ladens
# "llama-server GPU discovery watchdog timed out" / "unable to refresh free memory, using old
# values". Danach lud die Instanz kein weiteres Modell mehr, beantwortete aber /api/tags und
# /api/ps weiter (Knoten "up"). Dieses Skript macht das Log-Signal als Metrik sichtbar.
#
# Aufruf alle 60 s per systemd-Timer (siehe ollama-scheduler-health.service/.timer).
# Optional: OLLAMA_CONTAINERS="ollama ollama-rgtx ..." ; OUT=/pfad/ollama_scheduler.prom
set -euo pipefail
OUT="${OUT:-/var/lib/prometheus/node-exporter/ollama_scheduler.prom}"
CONTAINERS="${OLLAMA_CONTAINERS:-$(docker ps --format '{{.Names}}' | grep -E '^ollama' || true)}"
WINDOW="${WINDOW:-24h}"
tmp="$(mktemp "${OUT}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
{
  echo '# HELP ollama_gpu_discovery_failures Zeilen "GPU discovery watchdog timed out" im Zeitfenster'
  echo '# TYPE ollama_gpu_discovery_failures gauge'
  echo '# HELP ollama_last_gpu_discovery_failure_timestamp_seconds Zeitpunkt der letzten solchen Zeile (0 = keine)'
  echo '# TYPE ollama_last_gpu_discovery_failure_timestamp_seconds gauge'
  echo '# HELP ollama_container_uptime_seconds Laufzeit des Containers seit letztem (Neu-)Start'
  echo '# TYPE ollama_container_uptime_seconds gauge'
  for c in $CONTAINERS; do
    lines="$(docker logs --since "$WINDOW" "$c" 2>&1 | grep -E 'GPU discovery watchdog timed out|unable to refresh free memory' || true)"
    n="$(printf '%s' "$lines" | grep -c 'GPU discovery watchdog timed out' || true)"
    last="$(printf '%s' "$lines" | grep -oE 'time=[0-9T:.-]+Z' | tail -1 | cut -d= -f2 || true)"
    ts=0; [ -n "$last" ] && ts="$(date -d "$last" +%s 2>/dev/null || echo 0)"
    started="$(docker inspect -f '{{.State.StartedAt}}' "$c" 2>/dev/null || true)"
    up=0; [ -n "$started" ] && up=$(( $(date +%s) - $(date -d "$started" +%s 2>/dev/null || date +%s) ))
    echo "ollama_gpu_discovery_failures{container=\"$c\"} ${n:-0}"
    echo "ollama_last_gpu_discovery_failure_timestamp_seconds{container=\"$c\"} $ts"
    echo "ollama_container_uptime_seconds{container=\"$c\"} $up"
  done
} > "$tmp"
chmod 0644 "$tmp"
mv "$tmp" "$OUT"
trap - EXIT
