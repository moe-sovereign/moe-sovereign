"""
watchdog — Proactive alert service for MoE Sovereign (Star Trek Layer 1: Observe).

Reads Prometheus gauge values already collected by _gauge_updater_loop (main.py).
Evaluates thresholds every WATCHDOG_INTERVAL_SECONDS and writes alerts to:
  - Valkey list  moe:watchdog:alerts  (LPUSH + LTRIM to 100 entries)
  - Kafka topic  moe.watchdog.alerts  (best-effort, non-blocking)
  - Email        via SMTP (guarded by cooldown TTL in Valkey)

No additional HTTP calls to inference nodes — all data comes from existing gauges.

Config is hot-reloadable: stored as JSON in Valkey key moe:watchdog:config.
Falls back to .env values when the key is absent.
"""

import asyncio
import email.mime.multipart
import email.mime.text
import json
import logging
import os
import smtplib
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import httpx

logger = logging.getLogger("MOE-SOVEREIGN.watchdog")

# ── Valkey key names ──────────────────────────────────────────────────────────
VALKEY_ALERT_KEY   = "moe:watchdog:alerts"
VALKEY_CONFIG_KEY  = "moe:watchdog:config"
VALKEY_ALERT_MAX   = 100
KAFKA_TOPIC        = "moe.watchdog.alerts"

# ── .env defaults (fallback when no Valkey config exists) ────────────────────
_ENV_INTERVAL       = int(os.getenv("WATCHDOG_INTERVAL_SECONDS", "60"))
_ENV_DOWN_THRESH    = int(os.getenv("WATCHDOG_DOWN_THRESHOLD",   "2"))
_ENV_VRAM_THRESH    = float(os.getenv("WATCHDOG_VRAM_THRESHOLD", "0.90"))

# ── SMTP config (shared with admin_ui via same .env) ─────────────────────────
_SMTP_HOST      = os.getenv("SMTP_HOST", "")
_SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))
_SMTP_USER      = os.getenv("SMTP_USER", "")
_SMTP_PASS      = os.getenv("SMTP_PASS", "")
_SMTP_FROM      = os.getenv("SMTP_FROM", "noreply@example.com")
_SMTP_STARTTLS  = os.getenv("SMTP_STARTTLS", "1") == "1"
_SMTP_SSL       = os.getenv("SMTP_SSL", "0") == "1"


# ── Data models ───────────────────────────────────────────────────────────────

class AlertSeverity(str, Enum):
    INFO     = "info"
    WARNING  = "warning"
    CRITICAL = "critical"


class AlertType(str, Enum):
    NODE_DOWN        = "node_down"
    NODE_RECOVERED   = "node_recovered"
    VRAM_HIGH        = "vram_high"
    NO_MODELS_LOADED = "no_models"
    BENCHMARK_STUCK  = "benchmark_stuck"
    LOAD_STALLED     = "load_stalled"


_DEFAULT_CONFIG = {
    "down_threshold":               _ENV_DOWN_THRESH,
    "vram_threshold":               _ENV_VRAM_THRESH,
    "stuck_minutes":                30,
    "load_stall_seconds":           300,
    "email_escalation_enabled":     False,
    "escalation_to":                "",
    "escalation_cooldown_minutes":  30,
    "escalation_severities":        ["critical"],
}


def _make_alert(
    alert_type: AlertType,
    severity: AlertSeverity,
    node: Optional[str],
    message: str,
    extra: Optional[dict] = None,
) -> dict:
    return {
        "type":     alert_type.value,
        "severity": severity.value,
        "node":     node,
        "message":  message,
        "ts":       datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }


# ── Config hot-reload ─────────────────────────────────────────────────────────

async def _load_config(redis_client) -> dict:
    """Read watchdog config from Valkey; merge with defaults for missing keys."""
    cfg = dict(_DEFAULT_CONFIG)
    if redis_client is None:
        return cfg
    try:
        raw = await redis_client.get(VALKEY_CONFIG_KEY)
        if raw:
            overrides = json.loads(raw)
            cfg.update({k: v for k, v in overrides.items() if k in cfg})
    except Exception as exc:
        logger.debug("Watchdog: config read failed, using defaults: %s", exc)
    return cfg


async def save_config(redis_client, patch: dict) -> dict:
    """Merge-update the watchdog config in Valkey and return the new full config."""
    cfg = await _load_config(redis_client)
    # Only accept known keys to avoid config pollution.
    for k, v in patch.items():
        if k in _DEFAULT_CONFIG:
            cfg[k] = v
    if redis_client is not None:
        await redis_client.set(VALKEY_CONFIG_KEY, json.dumps(cfg))
    return cfg


# ── Core threshold logic ──────────────────────────────────────────────────────

def _evaluate_alerts(
    server_states: dict,
    was_down: dict,
    active_requests: int,
    stuck_since: Optional[float],
    cfg: dict,
) -> list[dict]:
    """Evaluate all threshold conditions and return a list of alert dicts.

    server_states: per-node snapshot with keys up, loaded_models, vram_bytes,
                   vram_capacity, down_streak.
    was_down:      per-node bool — True if the previous cycle had a NODE_DOWN alert.
    active_requests: number of currently in-flight LLM requests.
    stuck_since:   time.time() when active_requests first froze at its current value.
    cfg:           hot-loaded watchdog config (thresholds, email settings).
    """
    alerts: list[dict] = []
    down_thresh  = int(cfg.get("down_threshold", _ENV_DOWN_THRESH))
    vram_thresh  = float(cfg.get("vram_threshold", _ENV_VRAM_THRESH))
    stuck_limit  = float(cfg.get("stuck_minutes", 30)) * 60

    total_loaded = sum(s["loaded_models"] for s in server_states.values())
    any_up       = any(s["up"] == 1.0 for s in server_states.values())

    for name, state in server_states.items():
        streak = state["down_streak"]

        # NODE_DOWN — fire exactly on the first cycle that crosses the threshold,
        # then every down_thresh cycles thereafter to keep alerting during outage.
        if state["up"] == 0.0 and streak >= down_thresh and streak % down_thresh == 0:
            alerts.append(_make_alert(
                AlertType.NODE_DOWN, AlertSeverity.CRITICAL, name,
                f"Node {name} unreachable for {streak} consecutive cycles "
                f"({streak * _ENV_INTERVAL}s)",
                extra={"down_streak": streak},
            ))

        # NODE_RECOVERED — only if we were in a down state before.
        if state["up"] == 1.0 and was_down.get(name, False):
            alerts.append(_make_alert(
                AlertType.NODE_RECOVERED, AlertSeverity.INFO, name,
                f"Node {name} is back online",
            ))

        # VRAM_HIGH — only for nodes with known capacity.
        cap = state["vram_capacity"]
        if cap > 0 and state["up"] == 1.0:
            ratio = state["vram_bytes"] / cap
            if ratio >= vram_thresh:
                used_gb  = round(state["vram_bytes"] / 1e9, 1)
                total_gb = round(cap / 1e9, 0)
                alerts.append(_make_alert(
                    AlertType.VRAM_HIGH, AlertSeverity.WARNING, name,
                    f"Node {name} VRAM at {ratio:.0%} ({used_gb} / {total_gb:.0f} GB)",
                    extra={
                        "vram_used_gb":  used_gb,
                        "vram_total_gb": total_gb,
                        "vram_pct":      round(ratio * 100, 1),
                    },
                ))

    # NO_MODELS_LOADED intentionally omitted — Ollama unloads models after
    # inactivity by design; zero loaded models is the normal idle state.

    # BENCHMARK_STUCK — active request count frozen for longer than stuck_limit.
    if stuck_since is not None and active_requests > 0:
        elapsed = time.time() - stuck_since
        if elapsed >= stuck_limit:
            alerts.append(_make_alert(
                AlertType.BENCHMARK_STUCK, AlertSeverity.WARNING, None,
                f"Active request count frozen at {active_requests} for "
                f"{elapsed / 60:.0f} min — possible hung inference",
                extra={
                    "active_requests": active_requests,
                    "stuck_minutes":   round(elapsed / 60, 1),
                },
            ))

    return alerts


def _evaluate_load_stalls(
    active: list[dict],
    loaded_by_node: dict[str, set],
    stall_seconds: float,
    now: Optional[datetime] = None,
) -> list[dict]:
    """Requests that wait longer than stall_seconds for a model that the node has not loaded.

    A cold load of a 35B model takes ~3 min, so the default threshold is 300 s. A scheduler
    that no longer loads anything (seen on N04-RTX after a failed GPU discovery) answers
    /api/tags and /api/ps normally, so node_up stays 1 and only this check notices it.

    active:         decoded moe:active:* entries (need backend_host, backend_model, started_at).
    loaded_by_node: node name -> model names from that node's /api/ps (nodes without a
                    successful probe are left out and therefore never alerted on).
    """
    now = now or datetime.now(timezone.utc)
    stalled: dict[tuple, dict] = {}
    for req in active:
        node, model = req.get("backend_host") or "", req.get("backend_model") or ""
        if not node or not model or node not in loaded_by_node:
            continue
        try:
            started = datetime.fromisoformat(str(req["started_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age = (now - started).total_seconds()
        if age < stall_seconds:
            continue
        loaded = loaded_by_node[node]
        if model in loaded or f"{model}:latest" in loaded:
            continue  # model is loaded: the request is generating, not waiting for a load
        entry = stalled.setdefault((node, model), {"count": 0, "oldest": 0.0})
        entry["count"] += 1
        entry["oldest"] = max(entry["oldest"], age)
    alerts = []
    for (node, model), e in stalled.items():
        alerts.append(_make_alert(
            AlertType.LOAD_STALLED, AlertSeverity.WARNING, node,
            f"{e['count']} request(s) wait for {model} on {node} for up to {e['oldest'] / 60:.0f} min "
            f"but the model is not loaded (loaded: {', '.join(sorted(loaded_by_node[node])) or 'none'}) "
            f"— Ollama scheduler may be stuck",
            extra={"model": model, "waiting": e["count"], "oldest_seconds": round(e["oldest"])},
        ))
    return alerts


async def _collect_load_stalls(redis_client, inference_servers: list[dict], cfg: dict) -> list[dict]:
    """Read active requests from Valkey, probe /api/ps of the involved Ollama nodes, evaluate."""
    if redis_client is None:
        return []
    stall_seconds = float(cfg.get("load_stall_seconds", 300))
    try:
        keys = [k for k in await redis_client.keys("moe:active:*") if not str(k).endswith(":trace")]
        active = []
        for k in keys:
            raw = await redis_client.get(k)
            if raw:
                active.append(json.loads(raw))
    except Exception as exc:
        logger.debug("Watchdog: active request read failed: %s", exc)
        return []
    now = datetime.now(timezone.utc)
    suspects = {r["backend_host"] for r in active if r.get("backend_host") and r.get("backend_model")}
    urls = {s["name"]: s["url"] for s in inference_servers if s.get("name") in suspects and s.get("url")}
    loaded_by_node: dict[str, set] = {}
    if urls:
        async with httpx.AsyncClient(timeout=5.0) as client:
            for name, url in urls.items():
                base = url.rstrip("/").removesuffix("/v1")
                try:
                    r = await client.get(f"{base}/api/ps")
                    r.raise_for_status()
                    loaded_by_node[name] = {m.get("name", "") for m in r.json().get("models", [])}
                except Exception as exc:
                    logger.debug("Watchdog: /api/ps probe of %s failed: %s", name, exc)
    return _evaluate_load_stalls(active, loaded_by_node, stall_seconds, now)


# ── Email escalation ──────────────────────────────────────────────────────────

def _smtp_send_sync(to: str, subject: str, body_html: str) -> bool:
    """Blocking SMTP send — called via asyncio.to_thread from the watchdog loop."""
    if not _SMTP_HOST:
        logger.debug("Watchdog email: SMTP_HOST not configured, skipping")
        return False
    try:
        msg = email.mime.multipart.MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = _SMTP_FROM
        msg["To"]      = to
        msg.attach(email.mime.text.MIMEText(body_html, "html", "utf-8"))

        if _SMTP_SSL:
            s = smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT, timeout=10)
        else:
            s = smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=10)
            s.ehlo()
            if _SMTP_STARTTLS:
                s.starttls()
                s.ehlo()
        if _SMTP_USER:
            s.login(_SMTP_USER, _SMTP_PASS)
        s.send_message(msg)
        s.quit()
        return True
    except Exception as exc:
        logger.warning("Watchdog: SMTP send failed: %s", exc)
        return False


def _alert_email_html(alert: dict) -> str:
    """Build an HTML email body for a single watchdog alert."""
    severity_colors = {
        "critical": "#d0021b",
        "warning":  "#f5a623",
        "info":     "#4a90d9",
    }
    color = severity_colors.get(alert.get("severity", "info"), "#333")
    node  = alert.get("node") or "System"
    ts    = alert.get("ts", "")[:16].replace("T", " ")

    rows = "".join(
        f"<tr><td style='padding:4px 8px;color:#666;font-size:13px;'>{k}</td>"
        f"<td style='padding:4px 8px;font-size:13px;'>{v}</td></tr>"
        for k, v in alert.items()
        if k not in {"type", "severity", "node", "message", "ts"}
    )
    extra_table = (
        f"<table style='border-collapse:collapse;margin-top:12px;'>{rows}</table>"
        if rows else ""
    )

    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
      <div style="background:{color};color:#fff;padding:16px 20px;border-radius:6px 6px 0 0;">
        <strong style="font-size:16px;text-transform:uppercase;letter-spacing:.05em;">
          {alert.get('type','').replace('_',' ')}
        </strong>
        <span style="float:right;font-size:13px;opacity:.85;">{ts} UTC</span>
      </div>
      <div style="background:#f9f9f9;padding:16px 20px;border:1px solid #e5e5e5;border-top:none;">
        <p style="margin:0 0 8px;font-size:14px;">
          <strong>Node:</strong> {node} &nbsp;|&nbsp;
          <strong>Severity:</strong> {alert.get('severity','').upper()}
        </p>
        <p style="margin:0;font-size:15px;">{alert.get('message','')}</p>
        {extra_table}
      </div>
      <div style="padding:10px 20px;font-size:12px;color:#999;border:1px solid #e5e5e5;border-top:none;border-radius:0 0 6px 6px;">
        MoE Sovereign Watchdog &middot; <a href="" style="color:#999;">Starfleet Dashboard</a>
      </div>
    </div>
    """


async def _maybe_escalate(alert: dict, cfg: dict, redis_client) -> None:
    """Send an escalation email if enabled and not in cooldown."""
    if not cfg.get("email_escalation_enabled"):
        return
    to = cfg.get("escalation_to", "").strip()
    if not to:
        return
    allowed_severities = cfg.get("escalation_severities", ["critical"])
    if alert.get("severity") not in allowed_severities:
        return

    # Cooldown check via Valkey TTL key.
    node    = alert.get("node") or "system"
    ck_key  = f"moe:watchdog:cooldown:{alert['type']}:{node}"
    cooldown_secs = int(cfg.get("escalation_cooldown_minutes", 30)) * 60

    if redis_client is not None:
        try:
            existing = await redis_client.get(ck_key)
            if existing:
                return  # still in cooldown
            await redis_client.setex(ck_key, cooldown_secs, "1")
        except Exception as exc:
            logger.debug("Watchdog: cooldown check failed: %s", exc)

    subject  = f"[MoE Watchdog] {alert['type'].replace('_',' ').title()} — {node}"
    body     = _alert_email_html(alert)
    sent     = await asyncio.to_thread(_smtp_send_sync, to, subject, body)
    if sent:
        logger.info("📧 Escalation mail sent to %s for %s/%s", to, alert["severity"], alert["type"])


# ── Persistence helpers ───────────────────────────────────────────────────────

async def _push_alert(alert: dict, redis_client, kafka_producer) -> None:
    payload = json.dumps(alert, ensure_ascii=False)
    if redis_client is not None:
        try:
            async with redis_client.pipeline(transaction=False) as pipe:
                pipe.lpush(VALKEY_ALERT_KEY, payload)
                pipe.ltrim(VALKEY_ALERT_KEY, 0, VALKEY_ALERT_MAX - 1)
                await pipe.execute()
        except Exception as exc:
            logger.warning("Watchdog: Valkey write failed: %s", exc)

    if kafka_producer is not None:
        try:
            await kafka_producer.send_and_wait(
                KAFKA_TOPIC,
                value=payload.encode(),
                key=alert["type"].encode(),
            )
        except Exception as exc:
            logger.debug("Watchdog: Kafka publish failed (non-critical): %s", exc)


async def _read_gauge_value(gauge, labels: dict) -> float:
    try:
        return gauge.labels(**labels)._value.get()
    except Exception:
        return 0.0


# ── Main loop ─────────────────────────────────────────────────────────────────

async def watchdog_loop(
    redis_client,
    inference_servers: list[dict],
    kafka_producer=None,
    prom_server_up=None,
    prom_loaded_models=None,
    prom_vram_bytes=None,
) -> None:
    """Persistent background task — evaluates alert conditions every _INTERVAL seconds.

    Config is hot-reloaded from Valkey at each cycle (single GET, negligible cost).
    Prometheus gauges are passed from main.py to avoid circular imports.
    """
    logger.info("🛸 Watchdog loop started (base interval=%ds)", _ENV_INTERVAL)

    # Grace period: the Prometheus gauge loop needs one full cycle (60 s) to
    # populate values after a container restart. During this window all gauges
    # are 0.0 (default), which would cause spurious NODE_DOWN alerts for every
    # node. We skip two full intervals before evaluating any thresholds.
    _GRACE_CYCLES  = 2
    _cycles_done   = 0

    down_streaks:   dict[str, int]  = {s["name"]: 0 for s in inference_servers}
    was_down:       dict[str, bool] = {s["name"]: False for s in inference_servers}
    vram_capacity:  dict[str, int]  = {
        s["name"]: int(s.get("vram_gb", 0)) * 1_000_000_000
        for s in inference_servers
    }
    prev_active:    int             = 0
    stuck_since:    Optional[float] = None

    while True:
        cfg = await _load_config(redis_client)
        interval = _ENV_INTERVAL  # interval not hot-reloaded to avoid drift

        await asyncio.sleep(interval)
        _cycles_done += 1
        if _cycles_done <= _GRACE_CYCLES:
            logger.info("🛸 Watchdog grace period — skipping evaluation (cycle %d/%d)", _cycles_done, _GRACE_CYCLES)
            continue
        try:
            # Build per-node snapshot from in-process Prometheus gauges.
            server_states: dict[str, dict] = {}
            for srv in inference_servers:
                name = srv["name"]
                up_val     = await _read_gauge_value(prom_server_up,    {"server": name}) if prom_server_up    else 0.0
                loaded_val = await _read_gauge_value(prom_loaded_models, {"server": name}) if prom_loaded_models else 0.0
                vram_val   = await _read_gauge_value(prom_vram_bytes,    {"server": name}) if prom_vram_bytes   else 0.0

                if up_val == 0.0:
                    down_streaks[name] = down_streaks.get(name, 0) + 1
                else:
                    down_streaks[name] = 0

                server_states[name] = {
                    "up":            up_val,
                    "loaded_models": loaded_val,
                    "vram_bytes":    vram_val,
                    "vram_capacity": vram_capacity.get(name, 0),
                    "down_streak":   down_streaks[name],
                }

            # Active request count from Valkey.
            active_requests = 0
            if redis_client is not None:
                try:
                    active_keys = await redis_client.keys("moe:active:*")
                    active_requests = len(active_keys)
                except Exception:
                    pass

            # Benchmark-stuck tracking.
            if active_requests > 0 and active_requests == prev_active:
                if stuck_since is None:
                    stuck_since = time.time()
            else:
                stuck_since = None
            prev_active = active_requests

            # Evaluate.
            alerts = _evaluate_alerts(
                server_states=server_states,
                was_down=was_down,
                active_requests=active_requests,
                stuck_since=stuck_since,
                cfg=cfg,
            )

            alerts.extend(await _collect_load_stalls(redis_client, inference_servers, cfg))

            # Update recovery state AFTER evaluation.
            for name, state in server_states.items():
                was_down[name] = state["down_streak"] >= int(cfg.get("down_threshold", _ENV_DOWN_THRESH))

            # Publish and escalate.
            for alert in alerts:
                logger.info(
                    "🚨 [%s/%s] node=%s — %s",
                    alert["severity"], alert["type"], alert.get("node"), alert["message"],
                )
                await _push_alert(alert, redis_client, kafka_producer)
                await _maybe_escalate(alert, cfg, redis_client)

        except asyncio.CancelledError:
            logger.info("🛸 Watchdog loop cancelled — shutting down")
            raise
        except Exception as exc:
            logger.error("Watchdog loop error (non-fatal, continuing): %s", exc, exc_info=True)
