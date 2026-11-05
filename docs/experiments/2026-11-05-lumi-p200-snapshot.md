# LUMI-G: Momentaufnahme P200 (Open-Source-Spur, GRPO mit Ausführungsprüfung), Stand 05.11.2026

**Status:** Die Messwerte in §4 sind *validated* für die genannten Modelle und Testsätze (lokal bewertet von der P200-Sitzung, Quelle: `docs/evidence/P200.md` im ai-village-Worktree). Die Inventur in §2, §3 und §6 ist am 05.11.2026 per SSH auf LUMI bzw. lokal *verifiziert*. Die Folgerungen in §7 sind *planned*.
**Zweck:** Ergebnisse und Artefakte der P200-Kampagne (25.10.–31.10.2026) sichern. Die Sitzung, die sie erzeugt hat, läuft nicht mehr, und alle Quelldateien waren unversioniert.
**Bezug:** Endspurt-Plan für das LUMI-Kontingent (Verfall 05.12.2026), Strang „Verifier-guided Orchestrator Learning“.

---

## 1. Kurzfassung

- Auf LUMI-G (MI250X, gfx90a) läuft **GRPO mit TRL und LoRA**, belohnt durch echte Ausführungsprüfung: Rechenergebnis, SQL gegen Postgres im Job, Cypher gegen Neo4j im Job. verl wird dafür nicht gebraucht.
- Größter Einzelgewinn der Spur: GRPO auf precision. SmolLM3-3B steigt bei precision von 5/7 auf **26/27** (von 36), bei nicht trainierten Aufgabentypen (OOD) von 12/15 auf 54–59 (von 70). Qwen3.5-4B erreicht mit GRPO 36/36 und OOD 63/65.
- Cypher-GRPO hebt graphrag bei allen drei Modellen; SmolLM3 von 8/10 (SFT-v3) auf **19/20** (von 35).
- **Ohne messbaren Vorteil oder schädlich:** DPO allein, volles Fine-Tuning (mehr Vergessen als LoRA), naives Mischen der Gewichte (0,5), OSRM-Initialisierung (verworfen).
- **Parrondo-Wechsel der Aufgabentypen (7 Läufe vom 31.10., nachausgewertet am 05.11.):** Die Hypothese ist in diesem Aufbau verworfen bzw. nicht prüfbar, weil kein Einzelarm verliert. Nebenbefund (nur Seed 1): Alle Aufgabentypen im selben Schritt zu mischen schadet deutlich (§5.1).

## 2. Verbrauch

| Größe | Wert |
|---|---|
| Zähler `lumi-allocations` (05.11., 19:39) | **2.933 / 18.000 Karten-h** (16,3 %), keine laufenden Jobs |
| P200-Phase 25.10.–05.11. (`sacct`, GPU-Jobs) | 270 Jobs (264 COMPLETED, 6 FAILED), ca. **306 Karten-h** |
| davon small-g / dev-g | ca. 303 / 4 Karten-h |

Karten-h = GCD-h / 2 (der Zähler rechnet pro MI250X-Karte).

## 3. Auf gfx90a nachgewiesen lauffähig

| Baustein | Nachweis | Hinweise |
|---|---|---|
| TRL-GRPO + LoRA (`train_grpo_pilot.py`, num_generations 8, beta 0) | precision-, SQL-, Cypher- und Mix-Läufe | Generierung **ohne vLLM**, 28–80 s/Schritt auf 1 Knoten |
| DPO mit LoRA (`train_dpo_pilot.py`) | Läufe `dpo` (SmolLM3, OLMo-3) | – |
| Volles Fine-Tuning ZeRO-3 (`train_expert_pilot.py --full`) | Läufe `full` | `ds_zero3_pilot.json` |
| Postgres 16.2 im Job (pgserver, /dev/shm, DB je Rang) | Smoke 23285309, Läufe `sql-grpo` | `initdb` nur mit `LC_ALL=C`; Test lokal mit Postgres 17 |
| Neo4j 5.26 + JDK 21 im Job (8 Instanzen, Port 7474+Rang) | Diagnose 23285880 | System-Java 11 reicht nicht; Installation unter `neo4jtest/` |
| GGUF-Export Qwen3.5-Textmodelle | `rewrap_qwen35.py` (738 Tensoren) | Ohne Rewrap fehlen Vision- und MTP-Tensoren, das GGUF lädt dann nicht |
| Q4_K_M gegen bf16 | graphrag 35 Aufgaben, llama-server CPU | kein messbarer Q4-Verlust bei OLMo-3 (8 gegen 9/8) |

## 4. Messergebnisse (bestandene Aufgaben greedy / Sampling, Seed 1)

Testsätze: Hardtest 109 Aufgaben (datainfra 38, graphrag 35, precision 36); OOD-Präzision 70 Aufgaben in 14 Familien, die nicht im Training vorkommen; Suite P183 (180 Aufgaben, Messseed 183). Messregel der P200-Sitzung: Unterschiede unter 3 Aufgaben gelten als Rauschen.

### 4.1 precision-GRPO und Folgevarianten
| Modell | datainfra | graphrag | precision | OOD (70) |
|---|---|---|---|---|
| Qwen3.5-4B Basis | 19 / 22 | 4 / 7 | 17 / 14 | 44 / 41 |
| Qwen3.5-4B + GRPO | 21 / 19 | 4 / 4 | **36 / 36** | **63 / 65** |
| SmolLM3-3B Basis | 7 / 3 | 2 / 2 | 5 / 7 | 12 / 15 |
| SmolLM3 + GRPO (150 Schritte) | 6 / 12 | 1 / 1 | **26 / 27** | 54 / 52 |
| SmolLM3 v3-grpo | 3 / 3 | 1 / 1 | 24 / 27 | 59 / 55 |
| SmolLM3 mrg-grpo-v3 | 8 / 9 | 1 / 1 | 29 / 26 | 57 / 55 |
| OLMo-3-7B Basis | 6 / 5 | – | 12 / 10 | 25 / 28 |
| OLMo-3 GRPO (grpo-b, Batch 64, 900 Token) | 7 / 4 | 2 / 2 | 15 / 8 | 26 / 22 |
| OLMo-3 v3-grpo | 6 / 7 | 3 / 1 | 17 / 16 | 33 / 39 |

Befund OLMo-3: Im ersten Lauf wurden 40 % der Antworten bei 600 Token abgeschnitten und bekamen dadurch Belohnung 0; außerdem lag der Batch bei 16 statt 64. Auch mit korrigierten Einstellungen (grpo-b) gibt es keinen Gewinn. Gewinn zeigt sich nur nach vorherigem SFT-v3.

### 4.2 SQL- und Cypher-GRPO (Start ab datainfra-v3 bzw. graphrag-v3, je 100 Schritte)
| Modell | datainfra | graphrag | precision | OOD | gegen Start |
|---|---|---|---|---|---|
| SmolLM3 cy-grpo | 5 / 5 | **19 / 20** | 7 / 4 | 20 / 23 | graphrag +10 |
| Qwen3.5-4B cy-grpo | 21 / 21 | **26 / 21** | 17 / 15 | – | graphrag +3 |
| OLMo-3 cy-grpo | 6 / 5 | 13 / 9 | 5 / 8 | 14 / – | graphrag +4 / +2 |
| Qwen3.5-4B sql-grpo | **22 / 28** | 8 / 9 | 13 / 13 | 36 / 36 | datainfra +2 / +10 |
| SmolLM3 sql-grpo | 11 / 6 | 1 / 2 | 5 / 8 | 13 / 15 | kein Gewinn |
| OLMo-3 sql-grpo | 9 / 9 | 3 / 3 | 6 / 5 | 8 / 13 | im Rauschen |

### 4.3 Verworfene oder schwache Methoden
- **DPO allein:** schlechter als SFT-v3; bei SmolLM3 sinkt kb_conflict der Suite von 0,92 auf 0,58.
- **Volles Fine-Tuning:** kein Vorteil gegenüber LoRA Rang 128; SmolLM3 datainfra fällt von 7/3 auf 0/1 (Vergessen).
- **Gewichte mischen (0,5 · Rollen):** kostet Rollenleistung (Qwen graphrag 18/22 → 9/9).
- **OSRM-Initialisierung:** weder bei Einzelrollen noch beim Mischen ein Vorteil, verworfen.
- **Selbst-Stichproben-SFT (rs2):** OLMo-3 precision 14/15 (an der Rauschgrenze), dafür datainfra-Einbruch.

### 4.4 Nebenwirkungen (Suite P183)
Das Rezept v3 behebt die Einbrüche des früheren Rollentrainings, z. B. kb_answer Qwen 0,50 → 1,00. Nach GRPO sinkt brevity (SmolLM3 0,00, weil die Antworten länger werden), und probe_report fällt bei GRPO- und Merge-Modellen auf 0,17–0,50.

### 4.5 Verlaufsbefunde zum GRPO-Signal
Bei SmolLM3 stieg die Belohnung über den Lauf von 0,36 auf 0,98, der Anteil der Gruppen ohne Streuung von 64 auf 91 %. Gegen Ende fehlt also das Lernsignal. Pass@k aus 600 × 4 Stichproben: SmolLM3 pass@1 0,29 / pass@4 0,43; OLMo-3 pass@1 0,48 / pass@4 0,74.

## 5. Offen: Parrondo-Mix-Läufe (P1)

- Sieben Läufe am 31.10. (Jobs 23325903–909), SmolLM3, Seed 1, je ca. 2–2,4 h auf 1 Knoten: Einzelarme `prec`, `sql`, `cypher` sowie Mischungen `seq`, `cycle`, `random`, `batch`. Aufgabenpool je Lauf: 272 prec, 264 sql, 264 cypher.
- Checkpoints: `pilot-checkpoints/smollm3/mix-*-s1` (je 6,0 GB). Bis 05.11. gab es weder Antworten noch Bewertung; die Nachauswertung steht in §5.1.
- Die mittlere Belohnung im Trainingslog ist zwischen den Armen nicht vergleichbar, weil der Aufgabenmix je Arm verschieden ist.
- Zur Bewertung fehlen: Hardtest und Suite (greedy und Sampling) für alle 7 Checkpoints, ein zweiter Seed sowie der Arm „gierig“. Versuchsplan und Entscheidungskriterien: `docs/TRAINING-METHODEN.md` §7.7–7.8 im Archiv. Kosten der Bewertung: 7 × `hardtest_gen` mit 1 GCD, je ca. 10 min.

### 5.1 Ergebnis der Nachauswertung (05.11.2026, *validated* für Seed 1)
Erzeugung: 35 Jobs `hardtest_gen` (23500330–357 plus 7 Suite-Jobs, small-g, je 1 GCD). Bewertung lokal mit `hardtest/run.py score` (ht_pg/ht_neo), `precision_ood.py score` und `expert_suite.py score`. Alle Arme starten von `smollm3/mrg-roles05`.

| Arm | datainfra (38) | graphrag (35) | precision (36) | Summe | OOD (70) |
|---|---|---|---|---|---|
| Start mrg-roles05 | 6 / 11 | 5 / 4 | 10 / 8 | 21 / 23 | 21 / 23 |
| nur prec | 8 / 5 | 7 / 5 | 30 / 26 | 45 / 36 | **61 / 54** |
| nur sql | **12 / 12** | 5 / 5 | 9 / 9 | 26 / 26 | 25 / 21 |
| nur cypher | 8 / 9 | **9 / 8** | 10 / 5 | 27 / 22 | 25 / 22 |
| seq (prec → sql → cypher in Blöcken) | 9 / 11 | 7 / 6 | **31 / 25** | **47 / 42** | 49 / 54 |
| cycle (Typ wechselt je Schritt) | 9 / 11 | **9 / 9** | 15 / 21 | 33 / 41 | 39 / 44 |
| random (Typ je Schritt zufällig) | 6 / 3 | 6 / 5 | 20 / 24 | 32 / 32 | 43 / 37 |
| batch (alle Typen in jedem Schritt) | 10 / 12 | 8 / 8 | 11 / 6 | 29 / 26 | 19 / 18 |

Werte greedy / Sampling. Suite P183: Alle Arme liegen gleichauf mit dem Start (14 Fähigkeiten, Abweichungen ≤ 0,08, nur bei probe_report und code), also keine Nebenwirkungen.

**Urteil nach den Kriterien aus `TRAINING-METHODEN.md` §7.8:**
1. **Vorbedingung nicht erfüllt.** Kein Einzelarm verschlechtert den Startwert; jeder verbessert mindestens seine eigene Rolle (sql: datainfra +6/+1, cypher: graphrag +4/+4, prec: precision +20/+18). Damit fehlt das „Verlustspiel“, und die Anordnung ist **kein gültiger Parrondo-Test**.
2. Auch das Erfolgskriterium wird verfehlt: cycle (33/41) und random (32/32) liegen unter dem besten Einzelarm (prec 45/36) und unter seq (47/42).
3. **Parrondo-Hypothese H1: in diesem Aufbau verworfen bzw. nicht prüfbar.**

**Nebenbefund mit eigenem Wert (Hinweis, nur Seed 1):** Die vier Mischarme sehen dieselben Daten (272 prec, 264 sql, 264 cypher) und unterscheiden sich nur in der zeitlichen Anordnung. Das Mischen innerhalb jedes Schritts (`batch`) schneidet bei precision (11/6) und OOD (19/18, unter dem Start) deutlich schlechter ab als Anordnungen, bei denen jeder Schritt nur einen Aufgabentyp enthält (OOD 39–54). Der Abstand von etwa 20 OOD-Aufgaben liegt weit über der Rauschgrenze von 3. Das passt zu den Berichten über Gradienten-Interferenz bei Multi-Task-GRPO (arXiv 2602.05547, 2510.19178). Die Ursache ist **nicht gemessen**; eine Vermutung sind unterschiedliche Antwortlängen und Belohnungsskalen (precision binär, SQL/Cypher mit Teilkredit) innerhalb derselben Verlust-Aggregation. Für eine belastbare Aussage fehlen ein zweiter Seed und eine Messung der Gradientennormen je Aufgabentyp.

**Folgerung für eigene GRPO-Läufe:** Jeden Optimierungsschritt nach Aufgabentyp homogen halten. `seq` (in Blöcken) bewahrt den precision-Gewinn bis zum Ende, obwohl Cypher als Letztes trainiert wird; ein starkes Vergessen nach Blockwechseln war hier also nicht zu sehen.

## 6. Wo die Artefakte liegen

### 6.1 LUMI (`/scratch/project_465003058/hornphil/`)
| Pfad | Größe | Inhalt |
|---|---|---|
| `pilot-scripts/` | klein | Trainings- und Slurm-Skripte, `grpo/hardtest/` (Belohnungen) |
| `pilot-logs/` | 29 MB, 629 Dateien | Trainings- und Erzeugungslogs |
| `pilot-checkpoints/{smollm3,olmo3,qwen3.5-4b}/` | **874 GB** | alle Varianten (u. a. grpo, v3-grpo, cy-grpo, sql-grpo, mix-*) |
| `pilot-gguf/` | **273 GB** | GGUF-Exporte (u. a. ft-qwen35-4b-prec-grpo, ft-smollm3-3b-cy-grpo, ft-olmo3-7b-cy-grpo) |
| `hardtest/` | 32 MB | Modellantworten (answers-*.json) |
| `pilot-bundle/` | 157 MB | Trainingspakete |
| `neo4jtest/`, `pgtest/` | – | Neo4j 5.26 + JDK 21, Postgres-Bibliotheken für den Container |

**Risiko:** Scratch wird nach Projektende gelöscht. Lokal ist dafür kein Platz (`/opt` zu 95 % belegt, 25 GB frei). Vor dem 05.12. muss feststehen, welche Checkpoints bzw. GGUFs gesichert werden (Vorschlag: nur die GGUFs der Gewinner aus §4 sowie die LoRA-Adapter, nicht die zusammengeführten Vollmodelle).

### 6.2 Lokal
- Quellcode und Doku der P200-Sitzung: `/opt/deployment/ai-village/.claude/worktrees/p124-entfaltung` (Branch `p124-entfaltung`, HEAD `6b1451b`). **Alle P200-Dateien sind unversioniert** (`scripts/lumi/`, `slurm/`, `hardtest/`, `docs/evidence/P200.md`, `docs/TRAINING-METHODEN.md` usw.). Wird der Worktree entfernt, sind sie weg.
- Gesichert am 05.11.2026 als `/home/philipp/archive/p200-snapshot-2026-11-05.tar.gz` (50 MB, 730 Einträge, SHA-256 in der `.sha256`-Datei daneben). Inhalt: die genannten Quellen und Dokus, `build/hardtest` (Bewertungen `score-*.json`), die Trainingspakete `build/pilot2` bis `build/pilot4`, `notebooks/p200_colab.ipynb`, `tests/test_mix_schedule.py`, `config/expert-eval`.

## 7. Bedeutung für den Endspurt-Plan

1. Die Phase-0-Smoketests für verl entfallen; RL läuft mit dem TRL-Stack aus §3.
2. Die Umgebung mit Datenbanken im Job ist erprobt. Die Generatoren `precision_gen.py`, `sql_gen.py`, `cypher_gen.py` und `precision_ood.py` bilden die Basis der Task-Suite für mehrschrittige Orchestrierung.
3. Neuer Engpass ist der Rollout-Durchsatz: Smoketest TRL-GRPO mit `use_vllm` (Colocate) auf gfx90a.
4. Lehre aus §4.1/§4.5 für eigene Läufe: Grenze der Antwortlänge ausreichend hoch setzen, Batch gleich halten, Belohnung mit Teilkredit gegen Gruppen ohne Streuung, Rollen vorher per SFT stabilisieren.
5. **vLLM-Smoketest (05.11.2026, *validated* als Smoketest):** A/B-Vergleich auf small-g, jeweils 1 Knoten, SmolLM3-Basis, Paket `grpo` (540 precision-Aufgaben), 12 Schritte, 8 × 8 Stichproben je GCD, 700 Token. `train_grpo_pilot.py --vllm` setzt `use_vllm=True, vllm_mode='colocate'` (vLLM-Anteil 0,35).

   | | HF generate (23500457) | vLLM Colocate (23500458) |
   |---|---|---|
   | Schrittzeit (Schritte 2–12) | 57–59 s | **14,6–15,4 s** |
   | `train_runtime` | 738 s | **192 s** (Faktor 3,85) |
   | mittlere Belohnung (erstes → letztes Log) | 0,16 → 0,28 (Spitze 0,41) | 0,19 → 0,38 (Spitze 0,44) |
   | Gruppen ohne Streuung | 0,38–0,81 | 0,38–0,75 |
   | Fehler | keine | keine |

   Der vLLM-Pfad lief auf allen 8 Rängen (`trl/generation/vllm_generation.py` aktiv). TRL warnt, dass vLLM 0.20.1 außerhalb des getesteten Bereichs 0.12–0.18 liegt. Dass die Gewichte zwischen den Schritten synchronisiert werden, ist nur indirekt belegt: Die Belohnung steigt wie im HF-Arm. Ein längerer Lauf (z. B. 150 Schritte bis zur bekannten Belohnung von ≈ 0,9 bei SmolLM3) steht als Bestätigung noch aus.
6. Die P200-Sitzung nutzte dev-g/debug für Smoketests (28./29.10., insgesamt 11 Jobs). Seit der CSC-Mail vom 05.11.2026 gilt dort maximal 1 Job pro Nutzer und Projekt, und agenteneingereichte Arbeit gehört auf small-g/standard-g. Künftige Smokes laufen deshalb auf small-g.
