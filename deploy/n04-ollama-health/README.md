# N04: Ollama-Scheduler-Gesundheit

Macht einen stillen Ausfall des Modell-Ladens sichtbar (Vorfall 31.10.2026, N04-RTX): Ollama beantwortete
`/api/tags` und `/api/ps` weiter, lud aber kein weiteres Modell, und wartende Anfragen erzeugten keinerlei Logzeile.
Etwa 22 h vorher stand im Log `llama-server GPU discovery watchdog timed out`.

Installation auf dem N04-Host (braucht root):

```bash
sudo install -m 0755 ollama_scheduler_health.sh /usr/local/bin/
sudo install -m 0644 ollama-scheduler-health.service ollama-scheduler-health.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ollama-scheduler-health.timer
curl -s localhost:9100/metrics | grep ^ollama_
```

Die Alertregeln dazu stehen in `prometheus/alert_rules.yml` (`OllamaGpuDiscoveryFailed`).
Zweite, unabhängige Erkennung: `watchdog.py` meldet `load_stalled`, wenn Anfragen >300 s auf ein nicht geladenes Modell warten.

Erste Hilfe bei Alarm: `docker restart ollama` (entlädt das geladene Modell; Modelldateien bleiben erhalten).
