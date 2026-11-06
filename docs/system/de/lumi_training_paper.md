# LUMI-G Trainingsbericht

**LLM-Fine-Tuning auf AMD MI250X: Ein Praxisbericht**
*EuroHPC-Grant EHPC-DEV-2026D06-XXX · Juli–August 2026*

[PDF herunterladen (23 Seiten)](../../assets/papers/moe-sovereign-lumi-paper.pdf){ .md-button .md-button--primary }

---

## Überblick

Dieses Paper dokumentiert die vollständige Fine-Tuning-Kampagne für die
MoE-Sovereign-Judge- und -Planner-Modelle auf dem
[LUMI-G](https://lumi-supercomputer.eu/)-Supercomputer
(AMD MI250X, 64 GiB HBM2e pro GCD, ROCm-Stack).
Es umfasst zwei Modelltypen — dicht (Qwen3-8B) und spärlich MoE
(Qwen3.6-35B-A3B) — zwei Trainingsrollen (Judge und Planner) sowie
über 20 SLURM-Job-Iterationen mit Fehlern, Diagnosen und Korrekturen.

Das Paper ist aus der Perspektive eines Praktikers geschrieben: Jede
Konfigurationsentscheidung wird begründet, jeder OOM-Fehler auf seine
Ursache zurückgeführt, und jeder entdeckte Bug mit einem minimalen
reproduzierbaren Fix dokumentiert.
Es dient als Referenz für Teams, die ähnliche Kampagnen auf EuroHPC
oder vergleichbarer AMD-ROCm-Infrastruktur planen.

---

## Zentrale Erkenntnisse

| # | Erkenntnis | Konsequenz |
|---|-----------|-----------|
| 1 | **64 GiB pro GCD sind knapp für dichte Modelle unter ZeRO-2 mit Eager Attention.** Ein dichtes 8B-Modell belegt ≈30 GiB in statischen Tensoren. | Gradient Checkpointing ist zwingend. |
| 2 | **Ein spärliches MoE-Modell mit kleinerem `d_model` kann günstiger sein als ein dichtes Modell mit weniger Parametern.** Qwen3.6-35B-A3B (3B aktiv) hat kleinere Attention-Matrizen als Qwen3-8B (alle aktiv). | *Aktive* Dimensionen vergleichen, nicht Gesamtparameter. |
| 3 | **Speicherfragmentierung und Speicherdruck sind unterschiedliche Fehlerbilder.** Reserved-but-unallocated > 2 GiB → Fragmentierung; Behebung via `expandable_segments`. | OOM-Meldung auf Reserved-Wert prüfen, bevor Batch-Größe angepasst wird. |
| 4 | **Flash Attention unter ROCm muss verifiziert, nicht vorausgesetzt werden.** Ohne Flash Attention dominiert Eager Attention bei T > 2048 die Trainingskosten. | `max_seq_len` auf p99 des tokenisierten Datensatzes setzen. |
| 5 | **Durchsatz bei Schritt 25 messen und ETA berechnen.** Das Schritt-Zeit-Signal ist innerhalb der ersten 25 Schritte stabil. | Job abbrechen und neu konfigurieren, wenn ETA > SLURM-Zeitlimit. |
| 6 | **`max_seq_len` reduzieren hilft nur zusammen mit `--packing`.** Mit `--no-packing` werden Micro-Batches auf die längste Probe aufgefüllt, nicht auf `max_seq_len`. Der gemessene Speedup von 8192 → 4096 war nur **1,2×** (erwartet: 4×). | Packing-Verhalten analysieren, bevor Speedup-Vorhersagen getroffen werden. |
| 7 | **`expandable_segments` kann still inaktiv sein.** Der LUMI-G-PyTorch/ROCm-Build (Juli 2026) gibt eine Warnung pro Rank aus und lässt den Fragmentierungsschutz deaktiviert. | Job-Logs auf `expandable_segments not supported` prüfen. |

---

## Kampagnen-Zusammenfassung

### Phase 1 — Judge-Modell

Das Judge-Modell (`sovereign-judge:35b-q4km`, Basis: Qwen3.6-35B-A3B) wurde
auf einem parakonsistenten Datensatz von 90.000 Samples trainiert, die
asynchron auf LUMI-G generiert wurden.
Die Ground-Truth-Qualität wurde durch eine
GPT-4-Fürsprecher / Mixtral-8x22B-Kritiker / Qwen2.5-32B-Lehrer-Synthese-Pipeline
sichergestellt.
Das Judge-Modell schloss das Training im Juli 2026 ab und ist
**seit dem 19. Juli 2026 produktiv im Einsatz**.

### Phase 2 — Planner v4

Die Planner-SFT-Kampagne (Qwen3-8B, 6.060 Schritte, ~125 s/Schritt,
ETA ≈210 h) wurde als **sechs-Job-SLURM-Dependency-Chain**
(Jobs 20469441–20470181, kombiniertes Budget 228 h) eingereicht, nachdem
festgestellt wurde, dass ein einzelner 38-Stunden-Job nicht ausreicht.

Drei Resume-Bugs wurden identifiziert und behoben:

| Bug | Ursache | Behebung |
|-----|---------|---------|
| Neustart ab Schritt 0 | `OUTPUT_DIR` enthielt `$SLURM_JOB_ID` | Fester Run-Name für alle Jobs der Chain |
| Kein Checkpoint erstellt | `save_steps=500` > Job-Schrittbudget | Reduziert auf `save_steps=100` (~3,5 h bei 125 s/Schritt) |
| Sofortiger OOM | Gradient Checkpointing durch Speed-Experiment deaktiviert | Einstellung im SLURM-Skript gesperrt |

### Phase 3 — Teacher-Student-Distillation

Eine Distillationskampagne zielt auf ein kleineres 4B-Studentenmodell ab,
um On-Device-Inferenz auf `N04-RTX` (24 GiB VRAM) zu ermöglichen:

| Schritt | Konfiguration |
|---------|--------------|
| Teacher | Qwen3.5-35B-A3B (ZeRO-3, no-4bit, `max_seq_len=4096`) |
| Student-SFT | Qwen3.5-4B, ZeRO-2, lr=2×10⁻⁴, LoRA r=16/α=32, no-packing |
| DPO-Alignment | geplant: lr=1×10⁻⁵, Basis = SFT-Checkpoint; **noch kein brauchbarer Präferenzdatensatz** |

Verbleibende Schritte: verifizierte Präferenzpaare, DPO, LoRA-Merge, GGUF-Quantisierung, On-Device-Validierung.
Das frühere `moe_rule_based_rl_dpo.jsonl` (aus `scripts/compile_rule_based_rl_dataset.py`) besteht aus
schablonenhaften Platzhalter-Paaren ohne ausgeführten Verifier und darf nicht für Training verwendet
werden; daraus ist kein DPO-Checkpoint entstanden.

---

## Praktische Empfehlungen (Auszug)

Vollständige Checklisten und Entscheidungsbäume sind im Paper (§7).

```bash
# Pflicht-Umgebungsvariablen für ROCm-Training auf LUMI-G
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True
export PYTORCH_ALLOC_CONF=expandable_segments:True
export NCCL_SOCKET_IFNAME=hsn0
export NCCL_NET_GDR_LEVEL=3
export HF_HOME=/scratch/$PROJECT/$USER/hf_cache   # Home-Quota zu klein
```

**ZeRO-Stage-Auswahl:**

| Szenario | ZeRO-Stage |
|----------|-----------|
| Dicht ≤ 13B, 8 GCDs, 64 GiB | ZeRO-2 |
| Dicht > 13B, 8 GCDs, 64 GiB | ZeRO-3 |
| Spärlich MoE, gesamt > 30B | ZeRO-3 |
| Spärlich MoE, gesamt ≤ 30B | ZeRO-2 |

**OOM-Entscheidungsbaum:**

1. Reserved-but-unallocated > 2 GiB → Fragmentierung → `expandable_segments` aktivieren
2. Wenig unallokiert, OOM tritt weiter auf → Speicherdruck → Gradient Checkpointing prüfen; `max_seq_len` oder Batch-Größe reduzieren
3. OOM beim Checkpoint-Resume → `max_seq_len` reduzieren oder `per_device_eval_batch_size=1` setzen

---

## Zitation

```
Horn, P. (2026). Fine-Tuning LLMs on AMD MI250X: A Practitioner's Report
on the MoE Sovereign LUMI-G Training Campaign.
EuroHPC-Grant EHPC-DEV-2026D06-XXX.
Als Teil des MoE-Sovereign-Projekts verfügbar.
```

---

## Verwandte Seiten

- [EuroHPC-Trainingskonzept (EN)](../eurohpc_training_concept.md) — ursprünglicher Grant-Antrag und Distillationsplan
- [Hardware (EN)](../hardware.md) — lokaler GPU-Cluster (`N04-RTX`, `ollama-rgtx`)
- [Intelligence & Learning (EN)](../intelligence/index.md) — RL Flywheel, Agentic Re-Planning Loop
- [Whitepaper (DE)](../../assets/papers/moe-sovereign-whitepaper-de-v2.8.pdf) — vollständiges technisches Whitepaper (v2.8, PDF)
