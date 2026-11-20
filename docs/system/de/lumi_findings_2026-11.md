# LUMI-G-Forschungsbefunde, November 2026 (konsolidiert)

**Stand:** 20.11.2026, das Kontingent verfällt am 05.12.2026. Diese Seite hält die *Ergebnisse* der Phase J (Verifier-geführtes
Orchestrator-Lernen) an einem Ort fest, mit Belegen und Grenzen, damit sie ohne das ganze [Aktivitätslog](lumi_activity_log.md)
nutzbar sind. Jeder Befund nennt seine Jobs; Zahlen tragen ein 95-%-Intervall, wo eines berechnet wurde. Der
[Abschlussbericht](lumi_final_report.md) enthält die Kontingentabrechnung und die Lauf-für-Lauf-Bewertung. Was als *offen*
markiert ist, hat einen Job in der Queue und ist noch kein Ergebnis.

## 1. Aufbau in Kürze

- **Stack.** Der volle MoE-Sovereign-Orchestrator in einem Slurm-Job (PostgreSQL, Valkey, Chroma, Kafka, Neo4j, Embeddings, MCP-
  Präzisionswerkzeuge, zehn vLLM-Server): feingetunter OLMo-3-7B-Planner, feingetunter OLMo-3.1-32B-Judge (zwei Replikate),
  acht feingetunte SmolLM3-3B-Experten. Forschungsrouten forken eine Episode an jedem LangGraph-Checkpoint.
- **Aufgaben.** Synthetische Familien `sql_calc`, `cypher_calc`, `cypher_sql_calc` mit den Daten im Prompt (491 zurückgehaltene
  Aufgaben wurden nie für Erhebung oder Training genutzt); synthetische Einzelwert-Abfragen für GraphRAG; externe Sätze (770
  Aufgaben: TAT-QA-Tabellen, GSM8K, HotpotQA/MuSiQue, 20 Graph-Fragen).
- **Metrik.** *Entwurfsgenauigkeit*: die Antwort, falls eine die Pipeline verließ, sonst die vom Quality-Gate zurückgehaltene
  Antwort. Vergleiche sind je Aufgabe gepaart mit Bootstrap-Intervall. Die **Direkt-Baseline ist derselbe feingetunte 32B-Judge,
  der allein antwortet**, kein Base-Modell.
- **Grenzen, die für alles Folgende gelten.** Ein Stack-Katalog ohne SQL-Werkzeug; synthetische Aufgabenfamilien; Bewertung über
  exakte Zahl; meist eine Wiederholung je Bedingung; die feingetunten Gewichte sind die September-Modelle.

## 2. Befunde

| # | Befund | Beleg | Grenzen | Folge |
|---|---|---|---|---|
| F1 | **Das Trust-Gate entscheidet die meisten Labels und sagt wenig über Korrektheit.** Rund die Hälfte aller Forks wurde blockiert; Korrektheit und Gate-Urteil korrelieren mit 0,10; Trust-Score → Korrektheit AUROC 0,60 über 8.365 Episoden; das Urteil PROCEED (Fall mit deterministischem Tool) war nur zu 14 % korrekt (n = 59). | Wellen 2–5, Auswertung der Basis-Episoden | synthetische Aufgaben; PROCEED mit kleinem n | Labels fürs Lernen müssen ohne Gate erfasst werden (`quality_blocked_draft`); das Gate ist ein Kandidat für den Ersatz durch einen gelernten Verifier (F10). Gate unverändert. |
| F2 | **Ein vom Gate verfälschtes Label trägt kein lernbares Entscheidungssignal.** Textbaseline (TF-IDF + Ridge), Paargenauigkeit innerhalb einer Aufgabe 0,60 auf allen Forks, **0,50–0,53 nur auf gate-bestandenen Forks**. | aus den Roh-Rollouts der Wellen 2+3 neu gebaut | eine Baselinefamilie | Welle 2/3 sind keine PRM-Daten; das negative Ergebnis bleibt als Befund. |
| F3 | **GraphRAG hilft, wenn es erreicht wird, und das Produkt erreicht es nicht.** Produkt-Router: Abrufanteil 0,00 in allen vier Armen (480 Lookup-Aufgaben). Router erzwungen: Entwurf korrekt graph 0,61, noisy 0,61, inline 0,75, empty 0,00; graph − empty +0,60 (KI +0,50 … +0,68), graph − inline −0,14 (−0,23 … −0,04), veraltete Duplikate kosten nichts Messbares. | 23795184, 23814342, 23642846 | synthetische Einzelwert-Abfragen; ein Modellsatz | Ursache: Die Graph-Konfidenz eines Lookup-Plans ist konstant 0,30 gegen die Schwelle 0,35, und die Planner-Kategorie `compounding_knowledge` zählt nicht als graph-lastig. Entitätstreffer führen jetzt zum Graph (Knowledge-Broker-Branch). |
| F4 | **Der Planner-Server starb in 8 von 11 langen Jobs** (vLLM 0.20.1, Hybrid-KV-Cache-Manager, `Cannot get N free blocks from the pool`); der Orchestrator ersetzte danach jeden Plan stillschweigend durch einen Ein-Task-Fallback. 49 % der Aufgabensätze der Welle 4 und der Planner-Welle waren unbrauchbar (≈ 350 Karten-h). | 23662477–87, 23667179; Wiederholungen 23791554–61, 23814340 | — | Fix: kein Hybrid-KV-Manager und begrenzte Parallelität für den Planner-Server, Neustart nur wenn der Prozess beendet ist, `PLANNER_RUNTIME_FALLBACK=0` in Forschungs-Stacks. Belegt: 0 Abstürze in sechs langen Wiederholungen, 0 von 1.201 Planner-Punkten mit nur einem Plan. |
| F5 | **Auf dieser Suite schlägt der Workflow das Einzelmodell nicht, das er enthält.** Testaufgaben: direkter Judge 0,485, Workflow 0,352 (gepaart −0,133, KI −0,172 … −0,094). Externe Daten (n = 150 / 295 / 287): GSM8K 0,944 gegen 0,707 (−0,24), TAT-QA 0,675 gegen 0,356 (−0,32), Text-QA 0,597 gegen 0,512 (−0,085). No-GraphRAG, Review-Welle und Review ohne Self-Correction unterscheiden sich nicht über das Rauschen vom Workflow; Deliberation ist etwas schlechter (0,323, −0,030, KI −0,064 … +0,003). | 23795177–82, 23814341 → 23843015, 23843014 | Hypothese, nicht geprüft: Die 3B-Experten antworten und der Judge führt zusammen, das verliert Genauigkeit gegenüber dem 32B allein. Eine Wiederholung für den Workflow auf externen Daten. | Auf diesen Aufgaben darf keine Aussage über einen Genauigkeitsvorteil des orchestrierten Systems gegenüber einem großen Einzelmodell ruhen. Zu F11 siehe unten. |
| F6 | **Die `calculate`-Ausdrucksprüfung (ergänzt am 10.11.) kostet auf den synthetischen Aufgaben Genauigkeit und hilft auf externen Daten.** 153 von 212 Workflow-Ausfällen waren Contract-Fehler dieser Prüfung. Sauberer Wiederholungslauf ohne Prüfung: Testaufgaben 0,410 gegen 0,352 (gepaart +0,055, KI +0,022 … +0,089), gescheiterte Episoden 4,8 % gegen 21,6 %. Externe Daten, Prüfung aus − an: −0,061 / −0,059 / −0,031 (Intervalle schließen 0 ein, TAT-QA grenzwertig). | 23795178, 23843017, 23843014, 23843016 | Der erste Lauf ohne Prüfung war ungültig (Watchdog-Fehler, siehe unten). | Hart zu scheitern nach drei Wiederholungen ist der falsche Endzustand; Abstufen (unbrauchbare Tool-Aufgabe fallen lassen, Experten antworten lassen) ist der Vorschlag und wurde nicht gelaufen. Default unverändert. |
| F7 | **Planner-Präferenzpaare (DPO) trägt die Datenlage nicht.** Das Ausgangsrauschen nach dem Planner ist so groß wie der Planner-Effekt (gemischte Ausgänge 47 % am Planner, 45 % bei `workers`/`thinking`, wo der Plan schon feststeht); von den `calculate`-Plänen sind 69 % nicht auswertbar (meist SQL-Text), 28,5 % auswertbar aber falsch, 2,2 % gleich der Referenz, die Entwurfskorrektheit liegt aber bei 0,41 / 0,61 / 0,77, d. h. die Experten antworten aus den Inline-Daten. Obergrenze: Ein perfekter Planner käme auf ≈ 0,45 gegen 0,49 beim Einzelmodell. | 23618242, 23791558–61, 23843018 | — | Es wurde kein DPO-Satz gebaut. |
| F8 | **Ein 7B-Prozess-Belohnungsmodell schlägt die Baselines, auf Schrittebene aber nur knapp.** Test_ood, drei Seeds: AUROC 0,873 (sd 0,003), Paargenauigkeit innerhalb einer Aufgabe 0,564 (sd 0,007) gegen 0,537 (Knoten-Mittel) und 0,525 (Text); Brier 0,078. Lernkurve ≈ +0,01 AUROC je Verdopplung der Daten. Der am 17.11. berichtete „Planner-Wert 0,611“ war ein Artefakt (Paare verschiedener Knoten derselben Aufgabe) und ist zurückgezogen. | 23791608, 23814361–64 | ein 32B-Lauf war mit diesem Signal nicht gerechtfertigt | Mehr Wellen brächten wenig; das PRM ist nicht der Hebel. |
| F9 | **Abstimmen über Stichproben ist ein starker Hebel für den Workflow und ein schwacher für das Einzelmodell.** Planner-Forks, N = 1 → 8 (Mehrheitsentscheid): `cypher_calc` 0,73 → 0,92, `sql_calc` 0,31 → 0,43, `cypher_sql_calc` 0,16 → 0,12 (Abstimmen schadet, wo die meisten Stichproben falsch sind). Der direkte Judge bei Temperatur 0,7 liefert fast identische Stichproben (N = 1 → 3: 0,71 → 0,76 und 0,67 → 0,72). | offline auf Wellen 4/5, 23795177 | Workflow-Kurven zählen nur Episoden mit Antwort | Vielfalt der Pläne, nicht der Tokens, macht Stichproben lohnend. |
| F10 | **Ein auf Entwürfen trainierter Antwort-Verifier wählt korrekte Antworten weit besser als Gate oder Abstimmung.** 7B, eine Epoche auf 112.057 (Aufgabe, Entwurf, korrekt)-Zeilen aus den sauberen Forks; Test = 4.247 Antworten des direkten Judge und vier Workflow-Varianten auf die 491 zurückgehaltenen Aufgaben. AUROC **0,952** (cypher_calc 0,951, cypher_sql_calc 0,948, sql_calc 0,889; direkte Antworten 0,915). Best-of-N, Genauigkeit der gewählten Antwort: direkter Judge N ≈ 3: einzeln 0,531, Mehrheit 0,567, **Verifier 0,606**, Orakel 0,687; Workflow N ≈ 6: 0,411 / 0,511 / **0,577** / 0,684; alle gepoolt N ≈ 8,6: 0,440 / 0,589 / **0,635** / 0,776. | 23842900 | dieselben synthetischen Familien in Training und Test (ungesehene Aufgaben, nicht ungesehene Familien); exakte Zahl als Label. **Übertragung auf externe Daten (23874318, keine externen Daten im Training):** AUROC GSM8K 0,815–0,925, TAT-QA 0,719–0,803, Text-QA 0,59–0,61; Workflow-Antworten N = 2 einzeln 0,490 → Verifier **0,580** (Mathe 0,68 → 0,81, Tabellen 0,40 → 0,53); direkter Judge N = 3 nur 0,675 → 0,696; gepoolt N ≈ 4,8 einzeln 0,601, Mehrheit 0,680, Verifier **0,699**. Numerische Aufgaben übertragen sich, Freitext nicht. | Größter gefundener Effekt. Als Nächstes: die Pipeline (Auswahl aus N Kandidatenantworten, Ersatz oder Ergänzung des Trust-Scores); Training auf numerischen und Textantworten mehrerer Familien. |
| F11 | **Ein Einzelmodell bricht bei großen Tabellen ein.** Direkter Judge, `sql_calc`: 0,66 bei ≈ 19 Zeilen, 0,42 bei ≈ 57, 0,16 bei ≈ 190, 0,05 bei ≈ 570; die 100×-Prompts (≈ 48.000 Zeichen) liefen überwiegend in Fehler. | 23842892 (806 von 1.000 Episoden, 4-h-Limit) | ein Modell, eine Familie | In diesem Bereich müssen Werkzeuge oder Retrieval tragen; hier liegt eine faire Suite für Orchestrierung. |
| F12 | **Finetuning hat auf Pipeline-Ebene keine nachgewiesene Wirkung.** September-Benchmark (Qwen-Track, 8 Aufgaben, eine Runde): feingetunt 6,91 gegen Pre-Finetune 7,20 über alle Läufe, 7,77 gegen 7,20 nur über gültige Läufe; OLMo/SmolLM3-Track: abgebrochen, −0,28 ± 1,15 (n = 12). GRPO auf prüfbaren Einzelschritt-Aufgaben hilft kleinen Modellen (SmolLM3-3B Precision 5/7 → 26/27 von 36). Auf Komponentenebene (nur Planner, nur Judge) wurde nie gemessen. | `eval_scientific_benchmark_20260922-140103.json`, P200 | winziges n im September | Faktorielle Base-gegen-Feintune-Läufe eingereicht (23858647–54), *offen*. |
| F13 | **Knowledge-Broker, Abgleich und Experten-Injektion sind implementiert** (Branch `feat/knowledge-broker`, 23 Commits, alles Neue hinter Schaltern, standardmäßig aus). Gate P0 auf LUMI (Konfliktarme, Lookup, Testmenge, Broker aus/an) als 23874159–64 eingereicht, *offen*. | Repository | noch keine Genauigkeitsaussage | Plan siehe Historie des Branches. |

## 3. Sackgassen und eigene Fehler

Hier festgehalten, weil sie Rechenzeit kosteten und etwas lehren:

- **Voreilig eingereichte 32B-PRM-Läufe**, bevor eine Baseline existierte (während der Wartezeit abgebrochen, keine Kosten).
- **Smokes ohne Blick in die Planner-Logs**; der Absturz aus F4 wäre nach dem ersten Smoke sichtbar gewesen.
- **Mein Watchdog** beendete einen gesunden, ausgelasteten Planner bei einem `/v1/models`-Timeout; jede Episode im Neustartfenster
  scheiterte (machte den ersten Deliberation- und den ersten Prüfung-aus-Lauf ungültig, 23814341 und 23814831). Er startet jetzt nur
  noch neu, wenn der Prozess beendet ist.
- **Die `calculate`-Prüfung scheitert hart** nach drei Wiederholungen (F6).
- **Der Treiber verlor den Entwurf freigegebener (HITL-)Basis-Episoden**, daher wurden einige Basis-Entwürfe bis zum 17.11. falsch
  bewertet; Forks waren nicht betroffen.
- **Der „Planner-Wert 0,611“ des PRM** war ein Artefakt und ist zurückgezogen (F8).
- **Durch die Tests des Knowledge-Broker-Branches gefundene Fehler** (Produktcode, dort behoben): Die Correction-Memory führte ihre
  Ähnlichkeitssuche nie aus (eine Neo4j-Schlüsselwortkollision, von einem `except` verschluckt), Streaming-Anfragen ignorierten
  Template-Schalter und Mandantentrennung, die Ersatzkategorie hing vom Hash-Seed ab.
- **Frühere Phasen** (siehe Log): regelbasiertes DPO, die August-Expertenkampagne (Template-generierte Daten, Token-Genauigkeit über
  98 %), ZeRO-3 + PEFT auf einem multimodalen Checkpoint, GRPO mit `sequence_mask`-Importance-Sampling.

## 4. Was daraus folgt

| Ergebnis | Verwendung |
|---|---|
| F10 | Verifier-Pipeline bauen: N Kandidatenantworten bewerten, die beste zurückgeben; mit dem Trust-Gate vergleichen. Zuerst die Übertragung auf externe Daten (23874318). |
| F3, F13 | Entitätstreffer führen zum Graph; der Broker stellt Infrastrukturfakten vor Experten und Judge; Gate P0 entscheidet. |
| F6 | Abstufen statt Scheitern bei unbrauchbaren `calculate`-Aufgaben; auf Testaufgaben und externen Daten prüfen. |
| F5, F11 | Keinen Genauigkeitsvorteil des Workflows behaupten; eine Tabellengrößen-Suite bauen, in der Werkzeuge nötig sind. |
| F7, F8 | Kein Planner-DPO, keine weiteren Wellen, kein 32B-PRM. |
| F12 | Faktorielle Base/Feintune-Läufe entscheiden, was die Feintunes wert sind. |

## 5. Reproduktion

- **Branches** (origin und github): `feat/orchestration-prm-stack` (Forschungsrouten, Stack, Werkzeuge),
  `docs/lumi-activity-reconciliation` (diese Dokumentation), `feat/knowledge-broker` (Produktänderungen auf Basis von `origin/main`),
  `eval/knowledge-broker` (deren Merge mit den Auswertungswerkzeugen).
- **Stack-Job:** `benchmarks/orchestration_suite/lumi/lumig_moe_stack.slurm` (Beispiele in `submit_prepared.sh`,
  `submit_knowledge_gate.sh`); Direkt-Baseline `DIRECT_ONLY=1`; Base-Modelle über `JUDGE_CKPT`/`PLANNER_CKPT`.
- **Auswertung:** `graphrag/analyze.py`, `graphrag/compare_runs.py`, `prm/verifier_bestofn.py`, `selfconsistency_curve.py`;
  Datenbauer `prm/build_dataset.py`, `prm/build_verifier_data.py`, `prm/build_answer_pool.py`.
- **Daten:** Rollouts unter `$SCRATCH/moe-stack/runs/<Job>/rollouts.jsonl` auf LUMI, lokal kopiert nach `/opt/tmp/lumi-rollouts/`
  (Wellen, Testmenge, extern, Verifier-Daten); Checkpoints unter `$SCRATCH/prm/runs/`.
- Aufgabendateien: `orch_tasks_w3.jsonl` (Testmenge = Zeilen 7200–7690), `ext_tasks.jsonl`, `lookup_simple.jsonl`,
  `orch_tasks_scale.jsonl`, `lookup_conflicts.jsonl`.

## 6. Offene Fragen

1. Der Verifier überträgt sich auf numerische externe Aufgaben, nicht auf Freitext (F10): Schließt Training auf mehr Familien samt Textantworten die Lücke?
2. Was fügen feingetunter Planner, Judge und Experten ihren Base-Modellen in der Pipeline hinzu (offen, 23858647–54)?
3. Besteht der Knowledge-Broker Gate P0, und wählt der Abgleich auf den Konfliktarmen den erwarteten Wert (offen)?
4. Wo schlägt ein orchestriertes System das Einzelmodell: eine Suite mit Tabellen ab 60 Zeilen und einem Werkzeug, das aggregieren kann.
5. Gewinnt Abstufen der `calculate`-Prüfung die Genauigkeit zurück, die die Prüfung kostet, ohne ihren Vorteil auf externen Daten zu verlieren?
