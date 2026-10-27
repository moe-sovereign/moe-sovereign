# Experten-Parallelisierung & Qualitätssteigerung: Bewertung des Agy-Plans und Gegenentwurf

**Status:** research / planned (AGENTS.md §9). Die Messwerte in §1 sind *validated* für den genannten Zeitpunkt und die genannten Requests; Prognosen in §4 sind *research* (Hypothesen); die Maßnahmen P0–P4 sind *planned* und werden im [Runbook](2026-09-18-parallel-review-wave-runbook.md) umgesetzt.
**Methode:** Auswertung des Orchestrator-Logs (Requests `chatcmpl-4b48ac5e`, `chatcmpl-5d4618fd`, `chatcmpl-fddd53b5`), Codeanalyse (Commit `e3e29a87`), Datenbankabfragen (`admin_expert_templates`, `routing_telemetry`, `usage_log`), Benchmark-Reports vom 18.09.2026.
**Stichprobe / Grenzen:** n = 4–5 Messungen pro Bedingung im Basislauf (`053904`), einzelne Requests für die Phasenzeiten; alter Benchmark-Judge ohne Referenzantwort (siehe §6). Alle Vorher/Nachher-Werte in §4 sind Prognosen, keine Messungen.

> **Korrektur (2026-09-19):** Zwei Aussagen dieses Dokuments sind nach weiteren Messungen **nicht belegt** und ersetzt durch:
> 1. *„Die Pipeline ist schlechter (5,17 vs. 5,58) und 3,5× langsamer als die Baseline“* (§1.2): Belastbar ist nur die Latenzaussage (judge-unabhängig; im Live-Test ca. 14× langsamer als ein nativer OLMo-7B-Aufruf, 1660 s gegenüber ca. 115 s). Der Qualitätsvergleich ist nicht belastbar: n = 4 je Bedingung, und Judge- sowie Native-Modell des Laufs `053904` sind im Report nicht aufgezeichnet.
> 2. *„Der Judge bewertete ohne Referenz und erklärt daher Judge-Werte von 1,0–5,5“* (§6, A1): Der Judge erhielt zwar tatsächlich keine Referenz und keine Rubrik (Fix B1 bleibt richtig), aber der Spur-2-Lauf `191206` mit demselben Judge-Modell und ohne Referenz vergab 8,5 für dieselbe Aufgabe. Die niedrigen Werte des Laufs `053904` sind daher nicht auf die fehlende Referenz zurückzuführen; die Ursache ist offen.
> Messung mit korrigiertem Judge (Arm N, natives OLMo-7B, 4 Aufgaben, Runde 1): Gesamtscore Ø 9,2, Judge Ø 9,4, deterministisch Ø 9,0. Das ist ein **Deckeneffekt**: Der Benchmark lässt in dieser Form nur wenig Spielraum, um einen Qualitätsgewinn der Pipeline nachzuweisen. Die Hypothese H2 (+0,5 bis +1,5 Judge-Score) ist damit im Wesentlichen nicht prüfbar; die Latenzhypothesen (P0) sind es weiterhin.

**Stand:** 18.09.2026 · **Bezug:** `~/.gemini/antigravity-cli/brain/31048543-…/plan_komplementaere_experten_parallelisierung.md`
**Leitprinzip:** Ökonomisches Maximumprinzip – mit den vorhandenen Mitteln (8 dedizierte M60-Ports, 1 Judge auf N04-RTX, 1 Planner auf N04-RGTX, bestehender Code) den größtmöglichen Qualitäts- und Latenzgewinn erzielen. Kein neues Framework, keine neue Hardware, kein neues Training.

---

## 1. Erhobene Systembefunde (Messung statt Annahme)

### 1.1 Phasenzeiten eines vollständigen Requests

Referenz: `chatcmpl-4b48ac5e` (18.09., 19:12–19:40 UTC, Benchmark-Task `sci-sysprog-01-lockfree-ringbuffer`, Score 9,1, 1676 s). Quelle: Orchestrator-Log.

| Phase | Dauer | Anteil | Modell/Ort |
|---|---:|---:|---|
| Planner | 39 s | 2 % | N04-RGTX |
| Experten (1 Task!) | 202 s | 12 % | qwen3.5:4b auf N04-TM10-01 (M10) |
| Merger #1 | 369 s | 22 % | Judge 27B, N04-RTX |
| Self-Critique R1 | 72 s | 4 % | Judge |
| Merger #2 | 301 s | 18 % | Judge |
| Self-Critique R2 | 68 s | 4 % | Judge |
| Konflikt-Judge + Merger #3 | 299 s | 18 % | Judge |
| Critic (Halluzinationsprüfung) | 325 s | 19 % | Judge |
| **Summe** | **1676 s** | | **Judge-Kette: 1434 s = 86 %** |

Spur-1-Request `chatcmpl-5d4618fd` (SmolLM3-Template, 4 Tasks), Expertenphase:

| Task | Kategorie | Port | Start → Ende | Dauer | Tokens out |
|---|---|---|---|---:|---:|
| 1 | code_reviewer | 11442 | 14:36:10 → 14:36:44 | 34 s | 789 |
| 3 | research | 11437 | 14:36:10 → 14:36:55 | 45 s | 911 |
| 4 | code_reviewer | 11442 | 14:36:44 → 14:36:51 | 7 s | 182 |
| 2 | code_reviewer | 11442 | 14:36:51 → 14:37:10 | 19 s | 543 |

→ Expertenphase **60 s**, ideal parallel **45 s**. Der Semaphore-Stau kostet **15 s**. Danach folgten THINKING mit **141 s** und die Merger-Kette. SmolLM3 auf M60 liefert **20–23 tok/s**.

### 1.2 Qualitäts- und Latenz-Basis (Lauf `053904`, n = 4–5 pro Bedingung)

| Bedingung | Score | Judge-Score | Ø Latenz |
|---|---:|---:|---:|
| native_baseline | **5,58** | 2,62 | **500 s** |
| compound_ai | 5,17 | 2,50 | 1728 s |
| ablation_no_graphrag | 4,19 | 2,20 | 1104 s |
| compound_ai_debate | 4,12 | 1,80 | 1553 s |

**Das Kernproblem ist nicht die Expertenparallelität. Die Pipeline ist heute schlechter und 3,5× langsamer als das native Baseline-Modell.** Jede Maßnahme muss sich an dieser Relation messen lassen. (Lauf `123541` ist wegen 95,5 % Fallback-Rate ein Infrastrukturfehler und nicht verwertbar.)

### 1.3 Neue, im Agy-Plan nicht erkannte Befunde

| # | Befund | Beleg | Wirkung |
|---|---|---|---|
| B1 | **Self-Critique bläht den Trust-Score mechanisch auf.** Das Self-Critique-Ergebnis wird als `[SELF_CRITIQUE_Rn / judge]` in `expert_results` gehängt und zählt in `trust_score.py:168-176` als unabhängiger Experte: +1/5 × 0,25 = **+0,05**. | Log: Trust-Deltas R1→R2 sind fast immer exakt +0,05 (0,25→0,30; 0,555→0,605; 0,00→0,05; 0,523→0,567) | Aus einem Start unter 0,55 kann die Schleife 0,65 (PROCEED) nie erreichen. Es laufen **immer beide Runden** und danach der Critic. 39 von 80 Self-Critique-Läufen im aktuellen Log sind Runde 2. |
| B2 | **Self-Critique-Router ohne Abbruchkriterium.** `synthesis.py:2011` prüft nur `round < max`. `JUDGE_REFINE_MIN_IMPROVEMENT=0.15` existiert, gilt aber nur für den Judge-Refine im Merger. | Code | Jede sinnlose Runde kostet ~70 s Critique + ~300 s Merger-Neulauf = **~370 s**. |
| B3 | **Critic-Ergebnis wird in 21 % der Fälle verworfen** ("non-compliant judge format"). | 6 von 29 Critic-Läufen im Log | ~325 s Judge-Zeit ohne Ergebnis. |
| B4 | **Systemprompt hängt an der Kategorie, nicht am Modell** (`expert.py:333`, `_get_expert_prompt(cat, …)`). | Code | Agy-Phase 1 (forced Security-Modell unter `code_reviewer`) liefert eine **zweite, schwächere Implementierung** mit Coder-Prompt statt eines Security-Audits. |
| B5 | **Micro-Debatte ist in allen SmolLM3-Templates strukturell tot.** Sie braucht ≥ 2 Modelle pro Kategorie, alle 8 Kategorien haben genau 1. | DB `admin_expert_templates`, `expert.py:1057` | Die Qualitätsidee "Proponent → Skeptiker" existiert bereits im Code, kann aber nie greifen. |
| B6 | **Moderierte Debatte ist pro Runde strikt seriell.** Jeder Teilnehmer sieht die Züge derselben Runde (`expert.py:1398-1420`). | Code, Policy `max_model_calls: 18` | Bis zu 18 serielle Calls, obwohl die Teilnehmer auf verschiedenen, sonst idlen Ports liegen. |
| B7 | **Die Task-Obergrenze für "complex" liegt weiter bei 4.** `complexity_estimator.py:346` gibt `max_tasks: 4` vor und landet als "TASK BUDGET" im Planner-Prompt. `PLANNER_MAX_TASKS=8` ist nur die harte Contract-Grenze. | Log `max_tasks: 4`, `planner.py:911` | Die Erhöhung auf 8 ändert am Planverhalten praktisch nichts. |
| B8 | **`MAX_EXPERT_OUTPUT_CHARS=262144` in `.env`.** Der Agy-Plan führt den Default 2400 als "aktiven Guardrail" gegen Merger-Überlastung an. | `.env:147` | Den angenommenen Guardrail gibt es nicht. |
| B9 | **Template-`planner_prompt` ersetzt `DEFAULT_PLANNER_ROLE`**, die Regelblöcke in `planner.py` wirken aber global auf alle Templates. Der Spur-1-Planner ist zudem ein finegetuntes OLMo-7B. | `planner.py:708` | Agy-Phase 2 (Regel in `planner.py`) würde **alle** Templates verändern und kollidiert möglicherweise mit der trainierten Planverteilung. |
| B10 | `routing_telemetry` hat seit dem **14.09., 12:37** keine neuen Zeilen. | DB | Die Telemetrie für die A/B-Auswertung fehlt. Muss vor Phase 4 repariert werden. |

---

## 2. Bewertung des Agy-Plans

| Element | Richtig? | Ökonomischer Wert | Urteil |
|---|---|---|---|
| Ursache "Semaphore(1)-Hotspot" | ✅ belegt | **Gering:** 15 s von ~1700 s (< 1 %) | Korrekt diagnostiziert, für die Latenz aber irrelevant |
| Ursache "DAG-Ketten" | ⚠️ im Belegrequest nicht nachweisbar (alle 4 Tasks auf Level 0) | unbekannt | Erst messen |
| Ursache "Judge-Dominanz 90 %" | ✅ belegt (86 %) | **Hoch** | Richtig erkannt, im Maßnahmenteil aber **nicht adressiert** |
| Phase 1: forced Shadow-Security | ❌ technisch falsch (B4) | negativ: zweite Implementierung, mehr Konflikte, mehr Judge-Last | **Verwerfen** in dieser Form |
| Phase 2: Multi-Disziplin-Regel in `planner.py` | ⚠️ falscher Ort (B9), Nebenwirkungen auf alle Templates, Konflikt mit Finetune | gering bis mittel | Nur templategebunden und als A/B-Arm |
| Phase 3a: Keyword-Remapping auf "freie" Ports | ❌ schickt Implementierungsarbeit an fachfremde Spezialisten (`datainfra`, `omni`) | Spart ≤ 15 s, kostet Fachqualität | **Verwerfen** |
| Phase 3b: depends_on-Entschlackung | ⚠️ `normalize_task_dependencies` repariert bereits ungültige Referenzen. Echte Abhängigkeiten zu kappen, verschlechtert den Kontext. | unbelegt | Erst Häufigkeit messen |
| Phase 4: A/B-Benchmark | ✅ | notwendig | Übernehmen, aber mit Telemetrie-Fix (B10) und festgelegter Pipeline-Version |
| Risiko-Tabelle | ⚠️ zwei von drei Guardrails existieren nicht (B8; `graph/critic.py` und der 0,35-Filter liegen tatsächlich in `expert.py`/`synthesis.py`) | – | Korrigieren |

**Gesamturteil:** Die Diagnose ist zu etwa 70 % zutreffend. Die Maßnahmen verfehlen jedoch den ökonomischen Hebel: Sie optimieren die 4–12 % Expertenzeit und lassen die 86 % Judge-Zeit unangetastet. Phase 1 würde in der beschriebenen Form die Qualität eher senken und die Judge-Last erhöhen.

---

## 3. Gegenentwurf: Qualitätsarbeit vom seriellen Judge auf die idle M60-Ports verlagern

### Kerngedanke

Ein Judge-Durchlauf (27–32B, seriell, eine Instanz) kostet **~300–370 s**. Eine SmolLM3-Welle über 4–8 M60-Ports kostet **~35–45 s Wanduhrzeit**, gleichgültig ob 1 oder 8 Ports rechnen. Die heutige Qualitätsschleife (Self-Critique) ist teuer, sieht nur 3 × 400 Zeichen Expertenauszug und erhöht den Trust-Score nur mechanisch (B1).

→ **Echte Gegenprüfung durch komplementäre Spezialisten parallel auf idle Ports ausführen. Dafür die nachweislich wirkungslosen seriellen Judge-Runden streichen.** Das ist gleichzeitig schneller *und* inhaltlich stärker, weil ein Reviewer die tatsächliche Experten-Ausgabe prüft und nicht einen 400-Zeichen-Auszug.

### Maßnahmen, nach ökonomischem Wert priorisiert

| Prio | Maßnahme | Aufwand | Latenzwirkung | Qualitätswirkung |
|---|---|---|---|---|
| **P0-a** | **Trust-Score-Bug (B1):** `[SELF_CRITIQUE_…]`- und Judge-eigene Einträge aus `expert_count` ausschließen (`trust_score.py:168`) | ~5 Zeilen + Test | indirekt | Trust-Score wird wieder aussagekräftig |
| **P0-b** | **Abbruchregel im Self-Critique-Router (B2):** Runde n+1 nur, wenn (a) Runde n den Score um ≥ `SELF_CRITIQUE_MIN_GAIN` (neu, Default 0,05 nach Fix) gehoben hat **und** (b) PROCEED rechnerisch noch erreichbar ist | ~20 Zeilen + Test | **−370 s** in ~50 % der komplexen Requests | neutral (Runde 2 bringt heute messbar nichts) |
| **P0-c** | **Critic-Formatfehler (B3):** Ursache der 21 % non-compliant analysieren (Prompt vs. Parser `_critic_is_noncompliant_confirmation`) und beheben | 0,5–1 Tag | −325 s verlorene Arbeit in 21 % der Fälle | Critic wirkt wieder in allen Fällen |
| **P0-d** | **Telemetrie (B10)** reparieren, sonst ist das A/B nicht auswertbar | klein–mittel | – | Voraussetzung |
| **P0-e** | **Benchmark-Judge bekommt keine Referenz (§6, A1):** `judge_evaluation` liest `ground_truth_reference` und `evaluation_rules.semantic_criteria`. Der Datensatz enthält aber `expected_answer` und `scoring.rubric`, und beide werden nie übergeben. Der Judge bewertet also ohne Referenz und ohne Rubrik. | ~10 Zeilen | – | **Voraussetzung jeder Qualitätsaussage** |
| **P0-f** | **Quality-Probe tot (§6, A4):** `MOE_QUALITY_PROBE=1` ist im Container gesetzt, `pipeline_quality_log` hat trotzdem 0 Zeilen und im Log gibt es keine Probe-Einträge | klein | – | Liefert die Online-Antwort auf die Frage "lohnt sich die Pipeline?" |
| **P1** | **Komplementäre Review-Welle** (siehe 3.1): nach der Expertenphase eine einzige `asyncio.gather`-Welle mit Reviewern anderer Kategorien auf idle Ports; Reviewer bekommen *Task + tatsächliche Experten-Ausgabe* und nutzen ihren **eigenen** Kategorie-Prompt | ~150 Zeilen, nutzt `run_single`, Semaphoren, Egress-Guard, Konfliktregister | **+35–45 s** | **Haupthebel**: echte Zweitmeinung, füllt das Konfliktregister mit Substanz |
| **P2** | **Self-Critique durch Review-Welle ersetzen** (Template-Flag): wenn die Review-Welle lief, `self_critique_max = 0` | Konfig + 5 Zeilen | **−370 s** zusätzlich | als A/B-Arm messen |
| **P3** | **Delphi-Modus für moderierte Debatte (B6):** innerhalb einer Runde parallel, jeder sieht nur die Vorrunden | ~30 Zeilen, Policy-Flag `round_mode: delphi` | Debatte ~6 min → ~2 min | Delphi reduziert Anchoring, ob besser oder schlechter ist offen, daher A/B |
| **P4** | **Diversitäts-Hinweis im Template-`planner_prompt`** (nicht in `planner.py`) plus Hotspot-**Messung** (`endpoint_queue_wait_ms` aus `_track_node_load`) | klein | ≤ 15 s | gering, erst bei belegtem Stau ausbauen |
| – | ~~forced Shadow~~, ~~Keyword-Remapping~~, ~~`OLLAMA_NUM_PARALLEL=2` auf M60~~ | – | – | verworfen (B4; Fachfremdheit; bei 48k ctx passen 2 KV-Slots mit f16 nicht sicher in 8 GB: ~1,9 GB Gewichte + 2 × ~3,5 GB KV) |

### 3.1 Design der Review-Welle (P1)

- **Konfiguration pro Template** (opt-in, kein globaler Effekt):
  ```json
  "complementary_review": {
    "enabled": true,
    "min_complexity": "moderate",
    "max_reviewers": 4,
    "lenses": {
      "code_reviewer": ["security", "precision_tools"],
      "research":      ["governance"],
      "data_analyst":  ["security"],
      "governance":    ["research"]
    }
  }
  ```
- **Ablauf in `graph/expert.py`**, nach `_topological_levels`-Ausführung (~Z. 1752):
  1. Für jedes erfolgreiche Primärergebnis die Lens-Kategorien bestimmen und Doppelte zusammenfassen (ein Security-Reviewer prüft alle Code-Ergebnisse in *einem* Call). So bleibt jeder Port bei höchstens einem Call, und es entsteht **kein neuer Semaphore-Hotspot**.
  2. Reviewer-Task nach dem Muster des bestehenden Skeptic-Prompts (`expert.py:1086`): `[User Query]` + `[Expert Output (code_reviewer)]` + Lens-Auftrag ("Prüfe ausschließlich aus Sicht deiner Disziplin; nenne konkrete Fehler mit Begründung; keine Neuimplementierung").
  3. Eine `asyncio.gather`-Welle über `run_single` mit Budget `max_reviewers`. Ports, deren Kategorie schon als Primär-Task lief, sind zu diesem Zeitpunkt frei.
  4. Ergebnisse als `[REVIEW security→code_reviewer]: …` an `expert_results`. Divergenz über `_improvement_ratio ≥ 0,35` ins bestehende `conflict_registry` (identisch zur Micro-Debatte), damit `resolve_conflicts_node` sie arbitriert.
- **Trust-Score (korrigiert, siehe §6):** Reviews dürfen **nicht** in `expert_count` einfließen. Der Trust-Verdikt steuert Self-Critique, den Halluzinations-Critic (nur bei `PROCEED_WITH_ASSUMPTION`) und das HITL-Gate. Zusätzliche Reviews würden den Score mechanisch Richtung PROCEED schieben und damit den Critic abschalten. Das wäre dasselbe Artefakt wie B1, nur in größerem Umfang. Reviews werden deshalb mit `[REVIEW …]` markiert und in `trust_score.py` wie Self-Critique-Einträge ausgeschlossen.
- **Scope-Guard:** Reviewer-Tasks bekommen `allowed_domains = [Primärkategorie, Reviewerkategorie]`, sonst meldet `scope_guard.check_scope` (`expert.py:961`) für einen Security-Reviewer auf einer Code-Aufgabe einen `SCOPE_VIOLATION`.
- **Guardrail gegen Merger-Überlast (B8):** Review-Ausgaben auf `MAX_REVIEW_OUTPUT_CHARS` (Default 3000) kappen, unabhängig vom derzeit wirkungslosen globalen Limit.

---

## 4. Prognose Vorher/Nachher

Bezugsfall: komplexer Systems-Programming-Request, SmolLM3-Template, 4 Primär-Tasks. Die Zeiten stammen aus den beiden gemessenen Requests. Die Nachher-Werte sind **Prognosen**.

| Phase | Vorher (gemessen) | P0 | P0+P1 | P0+P1+P2 |
|---|---:|---:|---:|---:|
| Planner | 40 s | 40 s | 40 s | 40 s |
| Experten | 60 s | 60 s | 60 s | 60 s |
| Review-Welle | – | – | +40 s | +40 s |
| THINKING | 140 s | 140 s | 140 s | 140 s |
| Merger #1 | 370 s | 370 s | 390 s¹ | 390 s¹ |
| Self-Critique R1 + Merger | 370 s | 370 s | 370 s | – |
| Self-Critique R2 + Merger | 370 s | – ² | – | – |
| Konflikt-Judge | 30 s | 30 s | 60 s³ | 60 s³ |
| Critic | 325 s | 325 s | 325 s | 325 s |
| **Summe** | **~1705 s** | **~1335 s (−22 %)** | **~1425 s (−16 %)** | **~1055 s (−38 %)** |
| erwartete Verwerfungen (Critic 21 %) | +0 s nutzbar, 68 s verloren | P0-c: 0 s verloren | 0 s | 0 s |

¹ +~2.000 Input-Tokens für den Judge: Prompt-Eval auf N04-RTX im Bereich von Sekunden bis ~20 s.
² In ~50 % der komplexen Requests (Start-Trust < 0,55) entfällt R2 nach der Abbruchregel. Für den Rest bleibt sie wirksam.
**Gegeneffekt von P0-a:** Requests mit Start-Trust um 0,555 erreichen heute über das Artefakt 0,655, also PROCEED, und **überspringen dadurch den Critic** (0,555 → 0,605 → 0,655 kommt im Log vor). Nach dem Fix läuft der Critic dort wieder (+325 s). Das ist fachlich richtig, weil die Prüfung bisher fälschlich ausfiel, drückt aber die Latenzersparnis von P0 in diesen Fällen auf etwa null. Die −22 % gelten daher nur für die Gruppe mit Start-Trust < 0,55. Über alle Requests realistisch: **−10 bis −20 %**.
³ Mehr echte Konflikte, dafür mehr Arbitrierung.

**Qualität (Hypothesen, zu prüfen in Phase 4):**

| Hypothese | Erwartung | Begründung |
|---|---|---|
| H1: P0 senkt die Latenz ohne Qualitätsverlust | Score ±0,3 | R2 erzeugt heute nachweislich nur ein Trust-Artefakt |
| H2: P1 hebt den Judge-Score in `systems_programming` | +0,5 bis +1,5 | Heute prüft kein Modell die konkrete Implementierung vor dem Merger. Die Lens-Reviews liefern konkrete Fehlerhinweise. |
| H3: P2 (Review statt Self-Critique) ist qualitativ ≥ P0 | ≥ 0 | Der Reviewer sieht die vollständige Ausgabe statt 3 × 400 Zeichen |
| H4: Die Lücke zu `native_baseline` (heute −0,41 Score bei 3,5× Latenz) schließt sich | Ziel: Score ≥ Baseline bei ≤ 2× Latenz | Das ist das eigentliche ökonomische Ziel der Pipeline |

Die Unsicherheit ist hoch: n = 4–5 pro Bedingung und 1 Runde im Basislauf. Für belastbare Aussagen braucht es mindestens 3 Runden × alle Tasks. Konfidenzintervalle liefert der Benchmark bereits (`confidence_interval_95`).

---

## 5. Umsetzungsreihenfolge

1. **Nicht während des laufenden Spur-2-Laufs deployen.** Ein Neustart von `langgraph-orchestrator` bricht den Benchmark ab, und eine Codeänderung mitten im Lauf verfälscht den Spur-1/Spur-2-Vergleich.
2. Feature-Branch `feature/parallel-review-wave` (nie direkt auf `main`).
3. **P0-a/b/d** mit Unit-Tests, danach **P0-c** mit Log-Analyse der 6 verworfenen Critic-Antworten.
4. **P1** hinter Template-Flag, Tests für: Lens-Auflösung, Port-Deduplizierung (max. 1 Call/Port), Budget, Konfliktregistrierung, `local_only`-Egress-Guard.
5. **Benchmark-A/B** mit festgelegter Pipeline-Version (Commit-Hash im Run-Header), gleiche Tasks, ≥ 3 Runden:
   - Arm A: Ist-Stand (Commit `e3e29a87` + `PLANNER_MAX_TASKS=8`)
   - Arm B: P0
   - Arm C: P0 + P1
   - Arm D: P0 + P1 + P2
   - Referenz: `native_baseline`
   - Metriken: Score / Judge-Score / Det-Score pro Kategorie, Wanduhrzeit pro Phase, Anzahl registrierter/aufgelöster Konflikte, Critic-Verwerfungsquote, Score pro Minute (`pareto_score_per_minute`)
6. **P3 (Delphi)** und **P4** erst nach Auswertung von Schritt 5, nur wenn Daten dafür sprechen.

**Abbruchkriterium:** Senkt Arm C den Score gegenüber Arm B um mehr als eine Standardabweichung, wird P1 nicht übernommen. P0 bleibt in jedem Fall, weil es Bugfixes sind.

---

## 6. Abgleich aller Bewertungsverfahren (Nachtrag 18.09.)

Die erste Fassung hat nur Trust-Score, Konfliktregister und die Benchmark-Mittelwerte berücksichtigt. Hier der vollständige Abgleich.

### 6.1 Benchmark-Seite (`run_scientific_benchmark.py`)

| Verfahren | Ist-Zustand | Folge für Plan & Prognose |
|---|---|---|
| **A1 Judge-Score** (60 % Gewicht) | Der Prompt erwartet `ground_truth_reference` und `evaluation_rules.semantic_criteria`. **Beides fehlt im Datensatz.** Vorhanden sind `expected_answer` und `scoring.rubric` (Z. 666–667), die aber nicht an `judge_evaluation` übergeben werden. | Der Judge bewertet ohne Referenz und ohne Rubrik. Das erklärt Judge-Scores von 1,0–5,5 und fast nur `FAIL`-Verdikte, auch für `native_baseline`. **Die Basiswerte aus §1.2 sind als Qualitätsbasis nur eingeschränkt belastbar.** → P0-e, danach neue Basismessung |
| **A2 Deterministischer Score** (40 %) | Genutzt wird `deterministic_score()`: reine Substring-Suche nach `required_keywords`. Die strengere `deterministic_evaluation()` (Regex, verbotene Begriffe, exakte Zahlen) ist **toter Code**, der Datensatz hat keine `evaluation_rules`. `tolerance_pct` (precision-02) wird nicht ausgewertet. | **Deckeneffekt:** 13 von 18 Werten liegen bei 10,0. Die 40 % tragen fast konstant 4,0 Punkte bei. Zugleich **belohnt der Score Textmenge**: Mehr Experten- und Review-Text trifft mehr Keywords. Eine Review-Welle kann den Det-Score deshalb scheinbar heben, ohne dass die Qualität steigt. → Im A/B den Judge-Score separat auswerten, Det-Score nur als Plausibilitätsprüfung |
| **A3 Gesamtscore** 0,4·Det + 0,6·Judge | wie oben | Ein Judge-Gewinn von +1,0 wirkt nur mit +0,6 auf den Gesamtscore |
| **A4 Judge-Fallback** | Nach erfolglosen Versuchen `overall_score: 5.0`, Verdikt `UNSCORED_FALLBACK`. Seit dem Fix gibt es `summary_valid_only`, im Lauf `053904` fehlt das Feld noch. | Auswertung **ausschließlich** über `summary_valid_only`. Fallback-Quoten pro Arm mit ausweisen, weil sie bei code-lastigen Antworten höher liegen. |
| **A5 Statistik** | `std_dev`, `sem`, `confidence_interval_95` pro Bedingung | Ein Effekt gilt nur, wenn sich die CIs nicht überlappen. Bei n = 8 Tasks × 5 Runden = 40 ist das für Effekte ≥ ~1 Punkt erreichbar. |
| **A6 Ökonomie-Kennzahlen** | `pareto_score_per_minute`, `pareto_score_per_k_tokens` | **Primär-KPI für das Maximumprinzip ist `pareto_score_per_minute`.** Ein Arm, der Score und Zeit verbessert, hebt ihn doppelt. |
| **A7 Deltas** | `lumi_finetuning_validation`, `knowledge_graph_impact_delta`, `deliberation_debate_impact_delta`, `slm_graphrag_vs_dense_baseline` | P1/P2 verändern `compound_ai` **und** `compound_ai_debate` und damit alle vier Deltas. **Die Spur-1/Spur-2-Ergebnisse müssen mit festgelegter Pipeline-Version zu Ende laufen, bevor etwas deployt wird.** Sonst sind die Deltas nicht vergleichbar. |
| **A8 Judge-Identität** | Der Benchmark-Judge (`sovereign-judge-olmo31-32b`) ist in Spur 1 **auch der Pipeline-Judge** (Merger, Self-Critique, Critic). | **Self-Preference-Bias** zugunsten der Compound-Bedingungen in Spur 1, nicht in Spur 2 (Pipeline-Judge dort `qwen3.8:27b`). Beim Spur-Vergleich berücksichtigen, optional ein Kreuzjudge. |
| **A9 Head-to-Head-Plan** (`MOE-EXP-2026-H2H-01`) | Isolierter Vergleich SFT vs. Basis pro Experte, gleiche Score-Formel | Unabhängig von P1, weil direkte Inferenz ohne Pipeline. Er erbt aber **A1 und A2**: Ohne P0-e bewertet auch der H2H-Judge ohne Referenz. **Synergie:** Die Befunde der Review-Welle (z. B. Security findet Race Conditions im Coder-Output) liefern genau die Gap-Evidenz für das Nachtraining, die du ableiten willst. Deshalb die Reviews strukturiert loggen (Kategorie, Fehlerklasse). |

### 6.2 Laufzeit-Seite (Pipeline)

| Verfahren | Rolle | Wechselwirkung mit dem Plan |
|---|---|---|
| **Trust-Score** (`trust_score.py`) | steuert Self-Critique, Critic und HITL | B1-Artefakt; Reviews ausschließen (§3.1); Gegeneffekt von P0-a (§4, Fußnote 2) |
| **Unsupported-Claims-Penalty** (Halluzinationsproxy, Gewicht 0,10) | Teil des Trust-Scores | Mehr SLM-Text bringt mehr ungedeckte Eigennamen und Zahlen und damit mehr Penalty. Bestehende Vorgabe: vor einer Gewichtsänderung erst die Häufigkeit im Decision-Log beobachten. Im A/B beobachten, nicht tunen. |
| **Konfliktregister** (`_improvement_ratio ≥ 0,35`, `resolve_conflicts_node`) | Arbitrierung durch den Judge | Die Review-Welle erzeugt mehr Konflikte, jeder kritische kostet einen Judge-Call. Im Log wurden 43 von 53 als "non-critical" verworfen, das kostet also günstig. Metrik: registriert/aufgelöst pro Arm. |
| **Critic** (Halluzinationsprüfung) | nur bei `PROCEED_WITH_ASSUMPTION` | 21 % Formatverwerfung (B3, P0-c) |
| **Judge-Refine** (`JUDGE_REFINE_MIN_IMPROVEMENT=0.15`) | Abbruchregel im Merger | Vorbild für die Abbruchregel P0-b |
| **Quality-Gate** (`quality_gate.py`) | Precision-Evidenz, Pflicht-Tool-Contracts | Review-Tasks dürfen keine Precision-Tasks ersetzen oder abwerten. `precision_tools` bleibt als Lens nur beratend, ohne MCP-Anspruch. |
| **DoR-Check** (`dor_check.py`) | Vorbedingungen pro Task, Token-Warnschwelle | Reviewer-Tasks durchlaufen ihn wie jeder Task. Die Token-Warnschwelle ist durch die Review-Ausgaben leichter erreichbar. |
| **Scope-Guard** | Kategorie vs. `allowed_domains` | siehe §3.1 |
| **Constitution** (5 Regeln, deterministisch) | Endprüfung | unverändert |
| **HITL-Gate** | `PROCEED_WITH_ASSUMPTION` + komplex | Der Benchmark genehmigt automatisch (`/gates/…/approve`). Trust-Verschiebungen durch P0-a ändern die Gate-Häufigkeit. |
| **Judge-Gate** (`MOE_JUDGE_GATE`, aus) | überspringt den Judge bei nur einem Experten | Bleibt aus. Mit der Review-Welle gibt es nie nur einen Experten. |
| **Quality-Probe** (`MOE_QUALITY_PROBE=1`) | Online-A/B Pipeline vs. bester Einzelexperte | **Aktiv konfiguriert, liefert aber keine Daten** (P0-f). Ideal als zweite, unabhängige Messquelle für das Maximumprinzip. |
| **Expert-Score** (`_get_expert_score`, `EXPERT_MIN_SCORE=0.3`) | Auswahl innerhalb einer Kategorie | Bei einem Modell pro Kategorie ohne Auswahlwirkung. Reviews dürfen den Score des geprüften Experten nicht automatisch verändern, sonst entsteht eine unkalibrierte Feedbackschleife. |
| **Retrieval-Attribution** (aus) | GraphRAG-Nutzen | irrelevant |

### 6.3 Konsequenzen für den Plan

1. **P0-e (Benchmark-Judge mit Referenz und Rubrik) kommt vor jede Qualitätsmessung.** Ohne ihn misst das A/B im Wesentlichen Textmenge (Det) und referenzloses Judge-Urteil.
2. Danach eine **neue Basismessung** für alle Arme, denn die Werte aus §1.2 sind nicht vergleichbar.
3. KPI-Set pro Arm: Judge-Score (valid only) mit CI · Fallback-Quote · `pareto_score_per_minute` · Phasenzeiten · Konflikte registriert/aufgelöst · Critic-Läufe und Verwerfungsquote · Häufigkeit der Trust-Verdikte · Quality-Probe-Winrate (nach P0-f).
4. Qualitätsprognose H2 (+0,5 bis +1,5 Judge) bleibt eine Hypothese, **ist mit dem heutigen Messverfahren aber nicht prüfbar**.
