# LUMI-G-Aktivitätslogbuch (Juni – November 2026)

**Status:** Abgleich aller LUMI-G-Aktivitäten im Projekt `project_465003058` (EuroHPC-Grant EHPC-DEV-2026D06-XXX,
18.000 GPU-Stunden, AMD MI250X), Stand **06.11.2026**. Jeder Eintrag ist gekennzeichnet als *validiert* (Messergebnis mit
Quelle), *verifiziert* (Artefakt oder Jobstatus auf LUMI bzw. in Git geprüft), *nur dokumentiert* (aus Agenten-Statuslog oder
Memory-Notiz, nicht erneut geprüft), *offen* oder *geplant*. Negative Ergebnisse und Sackgassen sind bewusst aufgenommen: Sie
erklären spätere Entscheidungen und sollen Wiederholungen verhindern.

**Quellen:** `sacct -S 2026-06-01 -X` auf LUMI (694 Jobs, gelesen am 06.11.2026), Checkpoint-Verzeichnisse unter
`/scratch/project_465003058/hornphil/`, `lumi-allocations`, Git-Historie von `moe-infra` und des Whitepaper-Repos,
`agent_status/agy.md`, `agent_status/claude-code.md`, `docs/experiments/*`, `docs/system/benchmark-optimization-2026-09-21.md`,
`docs/experiments/2026-11-05-lumi-p200-snapshot.md`.

**Begleitseiten:** [LUMI-G-Trainingsbericht](lumi_training_paper.md) (Methoden und AMD-Erfahrungen bis August 2026),
[P200-Momentaufnahme](../../experiments/2026-11-05-lumi-p200-snapshot.md) (GRPO-Kampagne Oktober/November 2026),
[Antigravity-Postmortem](../../experiments/antigravity_frontier_pipeline_postmortem.md),
[Lehrer-Verifikation](../../experiments/lumig_openrouter_teacher_verification.md).

---

## Budget

| Größe | Wert | Status |
|---|---|---|
| Grant | 18.000 GPU-Stunden (MI250X), Verfall **05.12.2026** | verifiziert (Betreiber, 05.11.2026) |
| `lumi-allocations` am 06.11.2026 | **2.944 / 18.000** (16,4 %) | verifiziert |
| Jobs seit 01.06.2026 | 694 (small-g 567, standard-g 58, dev-g 27, debug 3, CPU `small` 39) | verifiziert |
| Jobstatus | 463 COMPLETED, 173 FAILED, 39 CANCELLED, 18 TIMEOUT, 1 OUT_OF_MEMORY (inkl. CPU-Jobs) | verifiziert |

**Abrechnungsvorbehalt (offen):** Der Zählerzuwachs zwischen 05.11. 19:39 und 06.11. (+11) entspricht etwa der Hälfte der
GCD-Stunden der Jobs in diesem Zeitraum (25,4 GCD-h); der Zähler rechnet also pro MI250X-Karte (2 GCDs). Die `sacct`-Summe aller
Jobs seit Juni ergibt dagegen ebenfalls rund 2.944 GCD-Stunden. Beides kann für die gesamte Historie nicht zugleich stimmen. Bis
CSC die Abrechnung klärt: mit dem Zählerwert planen und 1 Knotenstunde = 4 Zählereinheiten ansetzen.

---

## Phase A — Router-Einstieg (Juni 2026)

| Datum | Jobs | Ziel | Ergebnis | Status |
|---|---|---|---|---|
| 11.06. | 19166029, 19166081 (`moe-router-train`), 19239779 (`-onnx`) | Erste Nutzung: IMoE-Router-Klassifikator trainieren, ONNX-Export | `sovereign_router.onnx` aus 19166081, in den Orchestrator kopiert, MD5 im Container geprüft | verifiziert (Statuslog) |
| 11.–29.06. | `hostname`, `rocm-smi`, `tmux`, `ls`, `df` auf dev-g | Interaktive Erkundung | 3 × `tmux` FAILED: Batch-artige interaktive Nutzung funktioniert auf dev-g nicht | dokumentiert |
| 13.06. | 19220741–19221170 (`moe-dataset-gen`, 3 abgebrochen) | Test Datensatzerzeugung | Zweck nicht festgehalten | nur dokumentiert |

**Lehre:** Rechenknoten binden `/pfs/lustref1` nicht ein; der gemeinsame Container unter `/appl/local/laifs/` scheitert in
Batchjobs. `lumi-multitorch-latest.sif` ins Projekt-Scratch kopieren (gefunden in der Judge-Kampagne, `agent_status/agy.md`).

## Phase B — Judge v1/v2 (Juni – Juli 2026)

| Datum | Jobs | Ziel / Begründung | Ergebnis | Status |
|---|---|---|---|---|
| 25.06. | 19517008–19534326 (`sovereign-judge-lora`, 7 FAILED, 1 OOM) | Parakonsistenter Judge als QLoRA auf Qwen2.5-32B | Kein brauchbarer Lauf; OOM und Setup-Fehler | verifiziert (sacct) |
| 26.06. | 19540774 (`merge`) | Merge v1 | 62 GB FP16-Modell, CPU, 22 min | dokumentiert |
| 26.06.–03.07. | 19540836–19682381 (`judge-datagen`, 6 TIMEOUT, 3 FAILED) | 90.000 parakonsistente Beispiele mit Lehrer-Ensemble | Nach Resume-Jobs fertig; erste Versuche scheiterten, weil `/pfs/lustref1` auf Rechenknoten fehlt | dokumentiert |
| 03.–12.07. | 19686758–19832821 (`judge-lora-large`, 14 FAILED, 2 TIMEOUT) | Judge auf Qwen3.6-35B-A3B (MoE) mit ZeRO-3 | Hauptursache: ZeRO-3 + PEFT auf multimodalem Checkpoint (`AssertionError: NOT_AVAILABLE` bei leeren Vision-Tower-Parametern). Fix: Vision Tower vor `get_peft_model()` einfrieren, `stage3_param_persistence_threshold=1e7`. Checkpoint-704: Loss 0,485, Token-Accuracy 85,7 % | validiert (Trainingslog, Memory) |
| 15.–16.07. | 19926224, 19948455 (`judge-lora-resume`) | Training abschließen | COMPLETED | verifiziert |
| 16.–19.07. | Merge 19951999–19964923 (3 FAILED), Quant 19970104–19978657, GGUF 19987816–19996375 | Export | PEFT-Versionskonflikt beim Merge (Whitepaper Episode 9), eine GGUF-Neukonvertierung gescheitert | verifiziert (sacct) |
| 19.07. | – | Einsatz | `sovereign-judge:35b-q4km` produktiv; im August/September abgelöst (Phase E/G) | dokumentiert |

## Phase C — Planner v1–v4 (Juli – August 2026)

| Datum | Jobs | Ziel / Begründung | Ergebnis | Status |
|---|---|---|---|---|
| 20.–22.07. | 20018741–20140407 (`planner_dataset`) | Zerlegungs-Datensatz für den Planner | 2 COMPLETED, 2 FAILED, 1 CANCELLED | verifiziert |
| 21.07. | 20053764–20081793 (`phi4_planner_sft`, 5 FAILED, 3 CANCELLED) | Phi-4 als Planner | **Sackgasse.** Nacheinander: `--local_rank` nicht akzeptiert, TRL-API-Änderung (`max_seq_length` nicht mehr in `SFTConfig`), NCCL/TCPStore-Abbruch, `zip()`-Längenfehler in den Daten; der letzte Lauf schaffte 63 s/Schritt, ETA > 100 h, abgebrochen. Phi-4 ist zudem nur Open-Weight und kam deshalb später für die Open-Source-Spur nicht in Frage | verifiziert (Joblogs) |
| 23.–24.07. | 20149876–20177964 (`planner_dynamic_topup`), 20177965–20190726 (`qwen3_sft_merged`) | Qwen3-8B-Planner-SFT (259.829 Beispiele) | **Wirkungsloses Fine-Tuning**: `max_seq_len=1536` bei p99 3.628 Tokens schnitt 100 % der Beispiele ab; das Lernziel kam nie im Training an (Whitepaper Episode 11) | validiert (Trainingslog) |
| 25.07.–10.08. | 20242417–20734381 (`qwen3_planner_sft`, 6-Job-Kette v4: 1 COMPLETED, 9 FAILED, 6 TIMEOUT, 3 CANCELLED) | Planner v4 mit `max_seq_len=4096`, Gradient Checkpointing, `save_steps=100` | Drei Resume-Bugs behoben (Laufname mit `$SLURM_JOB_ID`, `save_steps` über dem Jobbudget, GC durch Experiment abgeschaltet). `qwen3_planner_v4/checkpoint-6060` erreicht; GGUF 20933544 | verifiziert (Checkpoint auf Scratch) |
| – | – | Einsatz | `qwen3-planner:q4km` in dynamischen Templates; Standard-Planner seit 19.09. ist der OLMo-3-Planner | dokumentiert |

**Lehre:** `max_seq_len` vor jedem SFT-Lauf gegen das tokenisierte p99 prüfen; mit `--no-packing` beschleunigt eine kürzere
`max_seq_len` kaum (gemessen 1,2× für 8192 → 4096).

## Phase D — Destillations-Student und „regelbasiertes DPO“ (August 2026)

| Datum | Jobs | Ziel / Begründung | Ergebnis | Status |
|---|---|---|---|---|
| 11.–12.08. | 21015892, 21017107 (`moe_synth_dataset`) | Synthetischer SFT-Korpus | COMPLETED. Eine separate Datei `moe_sovereign_200k_synthetic.jsonl` (1,6 Mio. Zeilen) enthält nur Platzhalter („Synthetic CoT prompt N“, kein Assistant-Feld); sie wurde für die Destillation nicht verwendet | verifiziert (Statuslog 10.09.) |
| 11.–13.08. | 21015893–21032430 (`moe_distill_student`), 21041838–21090807 (`moe_qwen35_4b_distill`) | 4B-Student für N04-RTX | Student-Modell-ID innerhalb eines Tages mehrfach gewechselt (Qwen2.5-3B, Qwen3.5-3B, Qwen3.5-4B). Ergebnis `moe_qwen35_4b_distilled`: trainiert auf `moe_system_knowledge_sft.jsonl` (37.875 Zeilen, 1.338 eindeutige Prompts, 16,7 % ein Changelog-Template) — mäßig divers; einziger als wiederverwendbar eingestufter August-Checkpoint | verifiziert (Scratch, Statuslog) |
| 12.–13.08. | 21017109–21090808 (`moe_rl_dpo_alignment`, 5 COMPLETED) | „Regelbasiertes RLVR/DPO“-Alignment | **Ungültig.** `scripts/compile_rule_based_rl_dataset.py` hat nie einen Verifier ausgeführt: 4 feste Prompts, konstante chosen/rejected-Texte, fest eingetragene Flags, fehlende Flags galten als bestanden. `moe_3b_rl_dpo_aligned` enthält nur Rank-JSONs, `moe_qwen35_4b_dpo_aligned` ist leer: Es gibt kein DPO-Modell. Seit 06.11.2026 abgesichert (Commit „refuse placeholder rule-based DPO pairs“) | verifiziert |

## Phase E — August-Expertenkampagne `moe_expert_pipe` (15.–17.08.2026)

| Datum | Jobs | Ziel / Begründung | Ergebnis | Status |
|---|---|---|---|---|
| 15.–16.08. | 81 FAILED, 1 TIMEOUT von `moe_expert_pipe` (21169247 …) | Planner, 8 Experten und Judge in einer Pipeline (SFT → Merge → GGUF) | Viele Pipeline-Iterationen; die 9 Rollenjobs 21189555–21190762 trainierten und mergten, scheiterten aber in der GGUF-Stufe (behoben am 03.09., Job 21698556) | verifiziert (sacct) |
| 17.–18.08. | 21263413 | Judge auf Qwen3.8-27B | COMPLETED (24,7 h). Export als `sovereign-judge:27b` | verifiziert |
| 10.09. | – | Prüfung der August-Adapter | **Alle August-SFT-Adapter verworfen.** Die Trainingsdaten stammten aus einem Template-Generator ohne LLM, nicht aus den von Antigravity geplanten Lehrer-Ensembles (siehe Postmortem). Beispiel coder: 150.000 Zeilen aus 3 Vorlagen, 72,5 % Sprachfehler; Loss 3,11 → 0,0103, Token-Accuracy 0,996. Alle Rollen einschließlich judge_27b endeten über 98 % Token-Accuracy = Auswendiglernen. Verschoben nach `checkpoints/_rejected_aug_campaign/` | validiert (Statuslog 10.09.) |
| 18.–19.08. | lokal | T1-Benchmark nach der Destillation | Kein verwertbares Ergebnis: Template-Namen im Runner waren veraltet, alle Anfragen scheiterten | dokumentiert (Whitepaper) |

**Lehre:** Eine hohe Token-Accuracy auf dem Trainingssplit ist ein Warnsignal, kein Erfolgsmaß. Vor jedem Training echte Beispiele
lesen und prüfen, ob die angegebene Datenquelle tatsächlich verwendet wurde.

## Phase F — Vorbereitung des Neutrainings (01.–11.09.2026)

| Datum | Jobs | Ziel / Begründung | Ergebnis | Status |
|---|---|---|---|---|
| 01.–07.09. | 21676084–21799570 (`moe_enrichment_smoketest`), 21698556 (`stage3`) | Datenanreicherung, Fix der GGUF-Stufe | Stage 3 behoben (COMPLETED, 3,5 min) | verifiziert |
| 03.–07.09. | 21699042, 21701726/21705930, 21701731/21705837, 21701732/21705838, 21701733 | Lehrer-Smoketests (Qwen3-Next-80B, GLM-4.5-Air, Qwen3-235B-A22B, DeepSeek-Coder-V2, Mistral-Large-2411) | Alle 5 nach Fixes verifiziert: GLM-4.5-Air 24/24 nach Parser-Fix, Mistral-Large 16/16, Qwen3-235B und DeepSeek-Coder-V2 14/16 nach Walltime 4 h. Neue Randbedingung: Checkpoints über 90 % des Knoten-RAM schalten vLLMs Lustre-Prefetch ab (100–228 s pro Shard statt ~1 s) | validiert (Joblogs) |
| 07.09. | 21794616–21798692 | Smoketest `role_sft`-Erzeugung | Erster Lauf 0/5 (JSON-Ausgabeformat); Wechsel auf Trennzeichenformat | dokumentiert |
| 08.–09.09. | 21805142, 21814113–21829009, 21832981–21845727 | Rollendatensätze für 9 Rollen + Ergänzungen | Themenkollaps im generischen Prompt gefunden und behoben (coder 577 → 564 nach Filter) | dokumentiert |
| 09.09. | 21848558 (FAILED), 21849323, 21855388–21855393 | OLMo-3.1-32B als zusätzlicher Lehrer | `hipErrorCapturedEvent` beim CUDA-Graph-Capture für `Olmo3ForCausalLM`; mit `--enforce-eager` lauffähig | validiert |
| 09.–10.09. | 21853583/4, 21856449, 21891384–21902215 | Open-Source-Student-Kandidaten | OLMo-2-7B vor dem Training verworfen (4k Kontext). SmolLM3-3B-Retest 21/24; OLMo-3-7B zuerst 25 % brauchbar, Prompt v2 fiel auf 0/24 zurück, Prompt v3 behob es. Instella-MoE verworfen | dokumentiert |
| 10.09. | – | Qualitätsprüfung Q1 der Rollendatensätze | Dolci-Long-Context-Dateien für planner/omni/research in Quarantäne (falsches Rollenverhalten) | dokumentiert |
| 10.–11.09. | 21902216–21915742 (`phaseC_*_trainsmoke`) | Trainings-Smoketests | SmolLM3, OLMo-3-7B, Qwen-4B bestanden; OLMo-3.1-32B OOM mit ZeRO-2, mit ZeRO-3 bestanden | validiert |
| 11.09. | 21916644, 21916682 | Piloten: coder (Qwen-4B), Judge (OLMo-3.1-32B) | Gesunde Verläufe: coder Loss 1,70 → 1,26, Accuracy 0,63 → 0,68; Judge Loss 2,52 → 0,97, Accuracy 0,62 → 0,85 (45,7 % der Judge-Ziele sind das Wort „CONFIRMED“) | validiert (Trainingslog) |

## Phase G — Zwei Modellspuren (11.–12.09.2026)

| Spur | Modelle | Jobs | Status |
|---|---|---|---|
| Open Weight (Qwen) | 8 Qwen3.5-4B-Experten, Students 4B und 9B (Planner `moe-sovereign-planner-9b`), Judge Qwen3.8-27B | `moe_expert_pipe` 11./12.09. (12 COMPLETED, 1 FAILED), Text-Tower-Extraktion 21915558, 21965905 | verifiziert (Checkpoints vom 12.09.) |
| Open Source (OLMo/SmolLM3) | 8 SmolLM3-3B-Experten, Planner OLMo-3-7B, Judge OLMo-3.1-32B | 21965771–21965871 (8 COMPLETED, 1 CANCELLED), 21965942, 21916682 | verifiziert |

Alle 20 Modelle sind auf Hugging Face veröffentlicht (Konto h3rb3rn: 2 Judges, 2 Planner, 8 + 8 Experten; Statuslog 19.09.).

**Namensvorbehalt:** Im Statuslog vom 11.09. bezeichnet „Spur 1“ die Qwen-Spur und „Spur 2“ die OLMo-Spur; ab 19.09. verwendet
das Benchmark-Werkzeug „Spur 1 = Open Source“ und „Spur 2 = Open Weight“. Dieses Logbuch nennt die Spuren daher nur
*Open Source* und *Open Weight*.

## Phase H — Bewertung der September-Modelle (lokale Inferenz, 18.–22.09.2026)

| Datum | Was | Ergebnis | Status |
|---|---|---|---|
| 18.09. | Benchmarklauf, beide Spuren | Ungültig: Orchestrator nach der ersten Anfrage von einer anderen Sitzung neu gestartet | dokumentiert |
| 19.–20.09. | Open-Source-Spur, 7 Bedingungen × 8 Aufgaben | Nach 4 von 8 Aufgaben unterbrochen. Zwischenstand gepaarte Differenz Fine-Tuned minus Pre-Finetune −0,28 ± 1,15 (n = 12): kein Beleg für besser oder schlechter. Ein Bug schickte Experten-Prompts als Dict-Repr an Ollama (100 % der Expertenaufrufe seit Juli); danach behoben | dokumentiert, unvollständig |
| 21.09. | Template-Optimierung an einer MCP-Aufgabe | Open-Source-Template 2,7 → 5,2 Mittelwert durch Code-/Infra-Fixes; Open-Weight-Template 10/10 Läufe korrekt (Mittel 9,3) | validiert (`benchmark-optimization-2026-09-21.md`) |
| 22.09. | Open-Weight-Spur, 8 Aufgaben × 7 Bedingungen, 1 Runde | Native Baseline 8,16 > jede Compound-Bedingung (6,09–7,20); fine-getuntes Compound (6,91) nicht besser als Pre-Finetune (7,20) | validiert (`eval_scientific_benchmark_20260922-140103.json`), n = 8 je Bedingung |

**Einordnung:** Die SFT-Runde im September lieferte lauffähige Modelle, aber keine gemessene Verbesserung gegenüber den
Basismodellen in der Pipeline. Ein vollständiger Open-Source-Lauf existiert nicht. Das ist der Hauptgrund für das
Verifier-basierte Training (Phase I).

## Phase I — P200: GRPO mit Ausführungsbelohnungen (25.–31.10.2026)

Vollständig: [P200-Momentaufnahme](../../experiments/2026-11-05-lumi-p200-snapshot.md). Rund 306 Karten-Stunden, 270 Jobs.

- **Erfolg:** TRL-GRPO + LoRA läuft auf gfx90a mit Belohnungen aus echter Ausführung (Zahlenergebnis, SQL gegen PostgreSQL im
  Job, Cypher gegen Neo4j im Job). SmolLM3-3B precision 5/7 → 26/27 (von 36), OOD 12/15 → 54/52 (von 70); Qwen3.5-4B precision
  36/36; Cypher-GRPO hebt graphrag bei allen drei Basismodellen.
- **Sackgassen:** DPO allein (schlechter als SFT-v3), volles Fine-Tuning (mehr Vergessen als LoRA), naives Mischen der Gewichte
  0,5, OSRM-Initialisierung, Rejection-Sampling-SFT, GRPO auf OLMo-3-7B ohne vorheriges SFT (40 % abgeschnittene Antworten im
  ersten Lauf).
- **Parrondo-Hypothese** (abwechselnde Aufgabentypen schlagen Einzeltypen): in diesem Aufbau nicht prüfbar, weil kein Einzelarm
  verliert; Nebenbefund (nur Seed 1): Alle Aufgabentypen in einem Schritt zu mischen (`batch`) senkt OOD auf 19/18 gegenüber
  39–54 bei homogenen Schritten.
- **Prozessfehler:** 11 Smoke-Jobs liefen auf dev-g/debug (28./29.10.); für agenteneingereichte Jobs ist das seit 05.11.2026
  nicht mehr erlaubt.

## Phase J — Endspurt: Wertmodell für Orchestrierung (ab 05.11.2026)

| Datum | Jobs | Ziel / Begründung | Ergebnis | Status |
|---|---|---|---|---|
| 05.11. | – | CSC-Regel | dev-g/debug: 1 laufender Job pro Nutzer und Projekt, nur interaktives menschliches Debugging; Agenten-Jobs auf small-g/standard-g. Durchgesetzt in `scripts/lumig_preflight_check.py` und einem Repo-Test (Branch `feat/orchestration-prm-stack`) | verifiziert |
| 05.11. | 23500330–23500432 (35 × `hardtest_gen`) | Bewertung der 7 Parrondo-Checkpoints | siehe Phase I | validiert |
| 05.11. | 23500457 (HF), 23500458 (vLLM) | Rollout-Durchsatz | vLLM-Colocate 3,85× schneller (192 s statt 738 s für 12 Schritte) | validiert |
| 05.11. | 23501446 | 150 GRPO-Schritte mit vLLM | **Fehlschlag:** precision 10/10, OOD 17/23. TRL-Standard `vllm_importance_sampling_mode=sequence_mask` multipliziert eine kleine Logprob-Abweichung pro Token (0,026) über ~380 Tokens; IS-Verhältnis ≈ 0,005 dämpft die Gradienten | validiert |
| 06.11. | 23503154 | Dasselbe mit `token_truncate` | Belohnung 0,16 → 0,94, Verhältnis 0,975, precision 24/22, OOD 41/46 (HF-Referenz 26/27, 54/52); Training 37 min statt 2,5–3 h. OOD-Abstand zu HF offen (1 Seed) | validiert |
| 06.11. | 23511683 | Voller MoE-Sovereign-Stack auf einem Knoten (PostgreSQL, Valkey, Chroma, Kafka, Neo4j, Embeddings, MCP, Orchestrator, 10 vLLM-Server) | FAILED nach 14 s: `CREATE DATABASE` als ein Mehrfach-Statement in `psql -c` läuft in einer Transaktion. Behoben mit getrennten `-c` | verifiziert |
| 06.11. | 23511738 | Wiederholung | Alle Datendienste liefen; beide vLLM-Server lehnten die gemergten OLMo-Checkpoints ab: `rope_parameters should have a 'rope_type' key`. Die September-Merges wurden mit transformers 5.16.0.dev0 gespeichert, das `rope_parameters` je Layer-Typ verschachtelt; das transformers 4.57 im Container kennt dieses Format nicht (vLLM 0.20.1 selbst ist nicht die Ursache). Behoben, indem die vLLM-Server dasselbe `.user_site`-Overlay (transformers 5.16) wie der vLLM-GRPO-Pilot nutzen; vorab auf dem Login-Knoten mit vLLMs `ModelConfig` für Planner, Judge und Experte geprüft. Zudem wartete das Startskript 20 min auf einen toten Server; seitdem bricht `wait_http` ab, sobald der Prozess weg ist | verifiziert, behoben |
| 06.11. | 23512110 | Wiederholung | FAILED nach 3 min: `No HIP GPUs are available` für alle Server auf Einzel-GCDs. Ursache: `ROCR_VISIBLE_DEVICES=2` nummeriert die sichtbare GCD auf HIP-Index 0 um; das zusätzlich gesetzte `HIP_VISIBLE_DEVICES=2` verweist dann ins Leere. Der Judge auf `0,1` fiel nicht auf, weil diese Indizes die Umnummerierung überleben. Lehre: auf LUMI nur `ROCR_VISIBLE_DEVICES` setzen, HIP/CUDA-Variablen entfernen. Die neue Lebendprüfung begrenzte den Verlust auf 3 statt 20 min | verifiziert, behoben |
| 06.11. | 23514199 | Wiederholung | FAILED nach 3 min: `KeyError: 'rope_theta'` im OLMo-Modellcode von vLLM (`olmo2.py`). Er liest ein flaches `rope_parameters` und leitet die Sliding-Layer selbst ab; transformers 5 verschachtelt je Layertyp. Behoben mit `--hf-overrides` (Full-Attention-Werte, Gleichheit von theta vorab geprüft) und `--enforce-eager` für OLMo-3 (bekannter `hipErrorCapturedEvent` aus Job 21848558) | verifiziert, behoben |
| 06.11. | 23514795 | Wiederholung | Erstmals alle 10 vLLM-Server bereit (Planner nach ~3 min, Experten je ~1 min nacheinander, Judge parallel; zusammen ~12 min bis „bereit“). Abbruch, weil der Embedding-Dienst auf 11434 statt 21434 lauschte: Das Image setzt `OLLAMA_HOST` selbst, und das schlägt geerbte Variablen. Behoben mit `singularity --env` | verifiziert, behoben |
| 06.11. | 23515108 | Wiederholung, 20 Aufgaben | Voller Stack lief („stack up“ nach 13 min), alle 7 abgeschlossenen Episoden aber unbrauchbar, Job abgebrochen. Drei Ursachen: (1) MCP starb beim Import, weil `/app` im SIF schreibgeschützt ist (`/app/generated`); der Orchestrator startete trotzdem und leerte seinen Werkzeugkatalog. (2) Die Inline-Tabellen trugen die Beschriftung `shipments.csv`; `_detect_file_skill` hielt das für einen Dateianhang und stellte den Skill `/xlsx` voran, worauf der Planner Excel-Arbeit plante. (3) Der Treiber wertete 422 (Quality-Gate) und 202 (HITL-Gate) als Fehler statt als Episodenausgang. Behoben: schreibbares `MOE_GENERATED_DIR` und MCP-Health-Check vor dem Orchestrator; Tabellen als „Table … (CSV)“ mit Test gegen den echten Detektor; Ausgänge `blocked:<grund>` und `hitl` (Entwurf automatisch freigegeben wie in `run_scientific_benchmark.py`), Fork-Route liefert die Gate-Felder | verifiziert, behoben |
| 06.11. | 23515523 | Kalibrierungslauf, 20 Aufgaben, K=2, 8 parallel | COMPLETED (1:25 h, davon 13 min Start). 336 Episoden in 1,19 h Treiberzeit ≈ 281 Episoden je Knotenstunde; 4 Infrastrukturfehler. Ausgänge: 49 % vom Quality-Gate blockiert, 36 % HITL (automatisch freigegeben), 15 % direkte Antwort. 44/336 richtig, fast nur die 2 `direct`-Aufgaben; `tool_chain` 0 %. 27 von 160 Entscheidungspunkten mit unterschiedlichem Ausgang, am häufigsten am Planner (7/20). Zunächst lagen alle Werte bei 0, weil der Scorer Zahlen aus der vom Modell angehängten `[Correction-Note: …]` las; nach der Korrektur offline neu bewertet. Befund: Der Trust-Score zählt im Prompt mitgelieferte Daten nicht als Quelle, daher meist BLOCK ohne MCP-Pfad; bewusst nicht geändert, weil das Gate so die Delegation an `precision_tools` belohnt (vom Projektverantwortlichen am 06.11. bestätigt) | verifiziert |
| 06.11. | 23520649–23520652 | Phase 1, erste Welle: 4 × 24 h, je 1 Knoten, K=4, 16 parallel, Familien ohne `direct`, disjunkte Aufgabenbereiche | Eingereicht. Erste Einreichung (23520639–42) sofort storniert: `sbatch --export` trennt an Kommas und hätte die Familienliste in `DRIVER_ARGS` zerschnitten; neu eingereicht mit exportierter Shell-Variable. **Nach ~1,5 h abgebrochen:** Forks endeten mit HTTP 500, wenn der Planner MCP-Werkzeuge erfand (`sql_query`, `db_query`, `database_query`); der `PlannerContractError` lief aus der Fork-Route heraus, der Treiber verbuchte einen Infrastrukturfehler statt eines falschen Ausgangs, und der Entscheidungspunkt verlor seinen Wert. Behoben: Die Fork-Route liefert `pipeline_error`, der Treiber erfasst `failed:<Ursache>` (Basis-500 `orchestration_failed` ebenso), mit Tests. Erfundene Werkzeuge sind ein echter Modellfehler und gehören als Ausgang 0 in den Datensatz | abgebrochen, behoben |
| 06.11. | 23522672–23522675 | Phase 1, zweite Welle, gleiche Konfiguration | Zwischenstand nach ~17 h (1527 Aufgaben, 46.986 Episoden): **~690 Episoden je Knotenstunde** (2,5× Kalibrierung). Fork-Ausgänge: 55 % blockiert, 38 % HITL, 7 % Antwort, 0,7 % failed. 17,6 % von 11.449 Entscheidungspunkten tragen Signal (Planner 35 %, Merger 16 %, Critic/Konfliktauflösung 10 %). Nach Familie: `cypher_calc` 34 % richtige Forks / 27 % Signal, `sql_calc` 10 % / 18 %, `cypher_sql_calc` 8 % / 17 %, `tool_chain` 0,8 % / 1,9 %. Die Hälfte aller Forks endet als `incomplete_task_execution:task-1:failed`: Der Planner schreibt Platzhalter, SQL oder `MAX(…)` direkt in `calculate`, statt die Werte erst berechnen zu lassen – ein echter Modellfehler, bleibt im Datensatz. 369 Datensätze mit leerem Fehlertext waren Treiber-Timeouts (900 s; httpx-Timeouts haben leeren Text). Ursache: ein Judge mit ~14 gleichzeitigen Anfragen bei ~9,5 Tok/s je Anfrage, während die 8 Experten-Server im Mittel 0,17 Anfragen bearbeiteten | Zwischenstand |
| 07.11. | – | Korrekturen für Welle 3 | Zweites Judge-Replikat auf GCD 6,7; der Shim verteilt nach geringster Zahl laufender Anfragen; Experten auf GCD 3–5 zusammengelegt. Treiber protokolliert den Ausnahmetyp, Timeout 1800 s. `tool_chain` gestrichen (kaum Signal). Zweiter Aufgabensatz (Seed 2), gegen Seed 1 über den Inline-Prompt dedupliziert: 7691 offene Aufgaben aus drei Familien. Außerdem auf Wunsch des Projektverantwortlichen: `_detect_file_skill` reagiert nur noch auf echte Anhänge, nicht mehr auf Dateinamen im Prompt-Text | verifiziert (Tests) |
| 07.11. | 23535809–23535814 | Phase 1, dritte Welle: 6 × 24 h, K=4, 32 parallel, je 1200 Aufgaben | Eingereicht | läuft |

Geplant bis 05.12.2026 (siehe Endspurt-Plan): Gegenversuch-Rollouts an Orchestrierungsentscheidungen (Phase 1), ein
Prozess-Wertmodell für Orchestrierung auf OLMo-3.1-32B (Phase 2), ein CSO-DPO/GRPO-Policy-Pilot (Phase 3), Export von
Datensätzen und Checkpoints. Alles *geplant*.

---

## Übergreifende Lehren

1. **Daten vor Rechenzeit.** Die beiden größten Verluste des Grants (August-Expertenkampagne, regelbasiertes DPO) waren
   Datenprobleme, die keine GPU-Zeit beheben konnte. Echte Beispiele lesen; Eindeutigkeit, Sprache und Rollenpassung prüfen;
   nie auf Generator-Platzhaltern trainieren.
2. **Trainingsmetriken sind keine Fähigkeit.** Token-Accuracy über ~95 % auf SFT-Daten deutet auf Auswendiglernen. Zählen nur
   zurückgehaltene, per Ausführung geprüfte Bewertungen (P200-Hardtest, OOD-Satz).
3. **Verifier-Belohnungen wirken, wo SFT stagnierte.** GRPO mit Ausführungsprüfung brachte die ersten klaren Held-out-Gewinne
   des Grants.
4. **Bibliotheksversionen gehören zum Experiment.** TRL-, transformers-, vLLM- und PEFT-Version je Job festhalten; die
   TRL-Umbenennung `max_seq_length`, der PEFT-Merge-Konflikt, die RoPE-Konfiguration von transformers 5 und der TRL-IS-Standard
   haben jeweils Läufe gekostet.
5. **Früh abbrechen.** Schrittzeit und ETA in den ersten 25 Schritten prüfen; Startskripte müssen stoppen, wenn ein Kindprozess
   stirbt.
6. **Partitionsregel.** Agenteneingereichte Arbeit gehört auf small-g/standard-g; dev-g nur für interaktives menschliches
   Debugging.
