# LUMI-G Trainingsbericht

**LLM-Fine-Tuning auf AMD MI250X: Ein Praxisbericht**
*EuroHPC-Grant EHPC-DEV-2026D06-XXX · Juli–August 2026 · Aktualisierung 06.11.2026*

Das PDF deckt die Kampagne bis August 2026 ab. Spätere Phasen, Sackgassen und Korrekturen fasst der Abschnitt
[Spätere Phasen](#spatere-phasen-august-november-2026) unten zusammen; jeder Job des Grants steht im
[LUMI-G-Aktivitätslogbuch](lumi_activity_log.md).

[PDF herunterladen (25 Seiten)](../../assets/papers/moe-sovereign-lumi-paper.pdf){ .md-button .md-button--primary }

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
| 8 | **Sehr hohe Token-Accuracy auf SFT-Daten zeigt Auswendiglernen, keinen Erfolg.** Alle Experten-Adapter vom August 2026 endeten über 98 % Token-Accuracy auf Template-Daten und wurden verworfen. | Vor dem Training echte Beispiele lesen und Diversität prüfen; Modelle nur an zurückgehaltenen, per Ausführung geprüften Aufgaben bewerten. |
| 9 | **GRPO mit Ausführungsprüfung brachte die ersten klaren Held-out-Gewinne.** SmolLM3-3B precision 5/7 → 26/27 von 36 (P200, Oktober 2026). | Für Fähigkeiten mit prüfbarer Antwort Verifier-Belohnungen statt weiterer SFT-Runden. |
| 10 | **Der TRL-Standardmodus für vLLM-Importance-Sampling kann das Lernen still stoppen.** `sequence_mask` trieb das Verhältnis über ~380 Tokens auf ≈ 0,005. | `vllm_importance_sampling_mode=token_truncate` setzen und `sampling/importance_sampling_ratio` loggen. |
| 11 | **Mit transformers 5 gespeicherte Checkpoints lädt vLLM 0.20.1 nicht** (verschachtelte `rope_parameters` je Layer-Typ, OLMo-3). | Bibliotheksversionen mit jedem Checkpoint festhalten; Serving testen, bevor man sich auf einen Merge verlässt. |

---

## Kampagnen-Zusammenfassung

### Phase 1 — Judge-Modell

Das Judge-Modell (`sovereign-judge:35b-q4km`, Basis: Qwen3.6-35B-A3B) wurde
auf einem parakonsistenten Datensatz von 90.000 Samples trainiert, die
asynchron auf LUMI-G generiert wurden.
Die Ground-Truth-Qualität wurde durch eine
GPT-4-Fürsprecher / Mixtral-8x22B-Kritiker / Qwen2.5-32B-Lehrer-Synthese-Pipeline
sichergestellt (so im PDF beschrieben; beim Abgleich 11/2026 nicht erneut geprüft).
Das Judge-Modell schloss das Training im Juli 2026 ab und war
**ab dem 19. Juli 2026 produktiv im Einsatz**. Inzwischen ist es abgelöst: durch einen Qwen3.8-27B-Judge
(August 2026, im September 2026 neu trainiert, nachdem die August-Daten verworfen wurden) und in der
Open-Source-Spur durch einen OLMo-3.1-32B-Judge (September 2026).

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

Die v4-Kette erreichte `checkpoint-6060` und wurde nach GGUF exportiert (Job 20933544, 10.08.2026).
Der vorherige Lauf (Jobs 20188267/20190726) war **wirkungslos**: `max_seq_len=1536` bei einem
tokenisierten p99 von 3.628 schnitt jedes Beispiel ab. Ein früherer Phi-4-Planner-Versuch (Jobs
20053764–20081793) wurde nach API-Inkompatibilitäten und einer ETA über 100 h aufgegeben.
Seit 19.09.2026 ist der OLMo-3-7B-Planner der Open-Source-Spur der Standard-Planner.

### Phase 3 — Teacher-Student-Distillation

Eine Distillationskampagne zielt auf ein kleineres 4B-Studentenmodell ab,
um On-Device-Inferenz auf `N04-RTX` (24 GiB VRAM) zu ermöglichen:

| Schritt | Konfiguration |
|---------|--------------|
| Teacher | Qwen3.5-35B-A3B (ZeRO-3, no-4bit, `max_seq_len=4096`) |
| Student-SFT | Qwen3.5-4B, ZeRO-2, lr=2×10⁻⁴, LoRA r=16/α=32, no-packing — erledigt: `moe_qwen35_4b_distilled` (13.08.2026) |
| DPO-Alignment | geplant: lr=1×10⁻⁵, Basis = SFT-Checkpoint; **noch kein brauchbarer Präferenzdatensatz** |

Verbleibende Schritte: verifizierte Präferenzpaare, DPO, LoRA-Merge, GGUF-Quantisierung, On-Device-Validierung.
Das frühere `moe_rule_based_rl_dpo.jsonl` (aus `scripts/compile_rule_based_rl_dataset.py`) besteht aus
schablonenhaften Platzhalter-Paaren ohne ausgeführten Verifier und darf nicht für Training verwendet
werden; daraus ist kein DPO-Checkpoint entstanden.

---

## Spätere Phasen (August – November 2026)

Zusammenfassung dessen, was nach dem PDF geschah; Details, Job-IDs und Quellen stehen im
[LUMI-G-Aktivitätslogbuch](lumi_activity_log.md).

| Zeitraum | Aktivität | Ergebnis | Status |
|---|---|---|---|
| 15.–17.08.2026 | Expertenkampagne (`moe_expert_pipe`, Planner + 8 Experten + 27B-Judge) | Trainiert, aber **am 10.09. verworfen**: Daten aus einem Template-Generator ohne LLM (z. B. coder: 3 Vorlagen, 150.000 Zeilen), Token-Accuracy > 98 % = Auswendiglernen | Sackgasse, dokumentiert |
| 01.–11.09.2026 | Lehrer-Smoketests (5 Open-Weight-Lehrer + OLMo-3.1-32B), kuratierte Rollendatensätze, Trainings-Smoketests | Alle Lehrer nach Fixes verifiziert; OLMo-3 braucht `--enforce-eager`; OLMo-3.1-32B braucht ZeRO-3 | validiert |
| 11.–12.09.2026 | Neutraining zweier Spuren: Open Weight (Qwen3.5-4B-Experten, 9B-Planner, Qwen3.8-27B-Judge) und Open Source (SmolLM3-3B-Experten, OLMo-3-7B-Planner, OLMo-3.1-32B-Judge) | Gesunde Trainingsverläufe; 20 Modelle veröffentlicht | verifiziert |
| 19.–22.09.2026 | Lokaler Benchmark der Fine-Tunes | Open-Weight-Spur: fine-getuntes Compound 6,91 gegen Pre-Finetune 7,20 gegen native Baseline 8,16 (n = 8 Aufgaben, 1 Runde): **kein gemessener Gewinn durch SFT**. Open-Source-Lauf unvollständig | validiert (Open Weight), offen (Open Source) |
| 25.–31.10.2026 | P200: GRPO + LoRA mit Ausführungsbelohnungen (PostgreSQL/Neo4j im Job) | Erste klare Held-out-Gewinne (precision, graphrag); DPO allein, volles Fine-Tuning, Gewichtsmischung, OSRM: kein Gewinn | validiert, siehe [P200-Momentaufnahme](../../experiments/2026-11-05-lumi-p200-snapshot.md) |
| ab 05.11.2026 | Endspurt: vLLM-beschleunigtes GRPO (3,85× schnellere Rollouts) und Rollout-Umgebung mit vollem Stack für ein Wertmodell der Orchestrierung | IS-Modus-Falle behoben; Stackstart durch die RoPE-Konfiguration von transformers 5 blockiert | läuft; Phasen 1–3 bis 05.12.2026 geplant |

Zähler am 06.11.2026: 2.944 von 18.000 GPU-Stunden verbraucht; der Grant verfällt am 05.12.2026.

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
