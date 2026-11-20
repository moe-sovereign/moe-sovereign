# LUMI-G-Abschlussbericht (Entwurf, aktualisiert am 20.11.2026)

**Status:** Entwurf. Abschnitte mit **zu ergänzen** werden nach Ablauf des Kontingents am **05.12.2026** gefüllt; alles andere
stammt aus dem [Aktivitätslogbuch](lumi_activity_log.md) und trägt dessen Statuskennzeichnungen. In einem Abschnitt „zu
ergänzen“ darf nichts aus Erwartung stehen: Jede Aussage braucht einen Job, eine Datei oder eine Befehlsausgabe als Quelle.

**Adressaten:** Betreiber, Fördergeber und öffentliche Dokumentation (Website und Whitepaper übernehmen nur die
gekennzeichneten Auszüge der Abschnitte 3–5).

---

## 1. Zusammenfassung (Entwurf 20.11.2026, nach dem 05.12.2026 abzuschließen)

Von 18.000 Karten-h waren am 20.11.2026 5.108 gebucht. Was sie erbracht haben, alles gemessen auf einer synthetischen Aufgabensuite mit
exakter Zahlenbewertung, einer zurückgehaltenen Menge von 491 Aufgaben und 770 externen Aufgaben ([Befunde](lumi_findings_2026-11.md)):
(1) Das Quality-Gate verdeckt das meiste Signal (Korrelation mit Korrektheit 0,10, AUROC 0,60), der Gegenversuchs-Datensatz der Wellen 2/3 ist
für ein Prozess-Belohnungsmodell daher unbrauchbar, und ein 7B-Modell auf gate-freien Labels schlägt die Baselines auf Schrittebene nur
knapp; (2) der Produkt-Router fragte den Graph bei Nachschlage-Aufgaben nie ab, obwohl Erzwingen +0,60 Genauigkeit bringt; (3) der
orchestrierte Workflow schlägt das darin enthaltene 32B-Einzelmodell **nicht** (−0,13 auf der Testmenge, −0,09 bis −0,32 auf externen Daten),
wobei der größte einzelne Kostenpunkt eine Contract-Prüfung und ein abstürzender Planner-Server waren, die gefunden und behoben wurden;
(4) ein 7B-**Antwort-Verifier** wählt korrekte Antworten weit besser als Trust-Gate oder Mehrheitsentscheid (AUROC 0,95; Best-of-N 0,64
gegen 0,44 für eine Einzelantwort, Übertragung auf externe Daten offen). Offen: die Base-gegen-Feintune-Läufe, das Knowledge-Broker-Gate
und die Verifier-Pipeline. Etwa 350 Karten-h gingen nach einem Serverfehler in Daten, die verworfen werden mussten; der Bericht benennt
das als Fehler, der ungenutzte Rest steht in Abschnitt 2. Artefakte: Abschnitt 7.

## 2. Kontingent und Verbrauch

| Größe | Wert | Quelle |
|---|---|---|
| Grant | 18.000 GPU-Stunden (MI250X-Karten; der Zähler zählt Karten, 4 pro Knoten, nicht GCDs) | Betreiber, 05.11.2026; geklärt durch die Welle-2-Abrechnung (74,1 Knoten-h → 296 Zählereinheiten) |
| Verfall | 05.12.2026 | Betreiber |
| Verbraucht am 10.11.2026 | 3.858 (21,4 %) | `lumi-allocations` |
| Verbraucht zum Verfall | **zu ergänzen** | `lumi-allocations` am letzten Tag, danach noch einmal nach der Schlussbuchung |
| Jobs, Zustände, Partitionen | **zu ergänzen** | `sacct -S 2026-06-01 -X -o JobID,JobName%24,Partition,AllocNodes,Elapsed,State` |
| Ungenutzter Rest und Grund | **zu ergänzen** | Grant − Verbrauch; offen benennen, wenn er verfällt |

Buchungsverzug: Welle 3 erschien etwa einen Tag nach ihrem Ende im Zähler (rund 567 Einheiten auf einmal). Den Endwert erst
nach Buchung des letzten Jobs ablesen, nicht beim Jobende.

## 3. Was geschah (Phasen A–J)

Pro Phase ein Absatz aus dem Logbuch, jeweils mit Ergebnisstatus.

| Phase | Gegenstand | Ergebnis in einer Zeile | Status |
|---|---|---|---|
| A | Router-Warm-up | ONNX-Router erzeugt; Lehre: Container gehören auf Scratch | verifiziert |
| B | Judge v1/v2 | viele Fehlschläge durch ZeRO-3 + PEFT auf multimodalem Checkpoint; behoben | validiert |
| C | Planner v1–v4 | siehe Logbuch | siehe Logbuch |
| D | Student und regelbasiertes DPO | regelbasiertes DPO war ein Datenproblem, kein Gewinn | siehe Logbuch |
| E | August-Expertenkampagne | größter Verlust des Grants; Datenproblem, nicht Rechenzeit | siehe Logbuch |
| F–G | Neutraining, zwei Modellspuren | Open-Source-Spur (Tier 1) und Open-Weight-Spur (Tier 2) | verifiziert |
| H | Lokale Bewertung | informelle Qualitätslücke zwischen den Spuren, noch nicht systematisch | dokumentiert |
| I | P200: GRPO mit Ausführungsbelohnungen | erster klarer Held-out-Gewinn des Grants; `token_truncate`-Importance-Sampling behebt den Zusammenbruch des Standards `sequence_mask` (Precision 24/22, OOD 41/46 gegen HF-Baseline 26/27, 54/52; ein Seed) | validiert |
| J | Verifier-geführtes Orchestrator-Lernen | siehe Abschnitt 4 | teilweise offen |

## 4. Verifier-geführtes Orchestrator-Lernen (Phase J)

### 4.1 Gegenversuch-Datensatz (fertig)
Wellen 2 und 3: Zahlen zu Aufgaben, Episoden, Entscheidungspunkten und Signalanteil stehen im Logbuch (Welle 3: 6.963
Aufgaben, 217.704 Episoden, 52.769 Entscheidungspunkte, 24,1 % mit Signal). **Zu ergänzen:** Endsummen über beide Wellen,
Splitgrößen von `data_ood` / `data_all`, Ablageort der Dateien.

### 4.2 Orchestrierungs-Prozess-Belohnungsmodell und Antwort-Verifier
| Punkt | Wert |
|---|---|
| Baselines auf dem gate-freien Label (`test_ood`, Paargenauigkeit innerhalb einer Aufgabe) | Knoten-Mittel 0,537, Text (TF-IDF + Ridge) 0,525; auf dem verfälschten Label der Wellen 2/3 erreichte die Textbaseline 0,60 (Gate-Signal) und nur 0,50–0,53 auf gate-bestandenen Forks |
| 7B-Smoke, 32B-Smoke (4 Knoten) | Trainer läuft Ende zu Ende; 32B ≈ 2,4 Samples/s auf 32 Ranks; die beiden 32B-Läufe (23603688/89) wurden in der Wartezeit abgebrochen |
| **7B-PRM, drei Seeds** (23791608, 23814361–62), saubere Welle-4-Zeilen | AUROC 0,873 (sd 0,003), Brier 0,078, Paargenauigkeit innerhalb einer Aufgabe **0,564 (sd 0,007)**; Lernkurve +0,01 AUROC je Verdopplung (25 % / 50 % / 100 % der Daten: 0,848 / 0,862 / 0,874) |
| Urteil PRM | Um mehrere Seed-Streuungen besser als beide Baselines, der Gewinn bei der Schrittwahl gegenüber dem Knoten-Mittel ist aber klein (+0,027); kein nachgewiesener Wert auf Entscheidungsebene. Eine Einzelzahl für den Planner ist nicht definiert (ein Planner-Punkt je Aufgabe); der „Planner-Wert 0,611“ vom 17.11. war ein Artefakt und ist zurückgezogen. Ein 32B-Lauf war nicht gerechtfertigt |
| **7B-Antwort-Verifier** (23842900): Aufgabe + Entwurf → korrekt, 112.057 Zeilen, eine Epoche | Test (4.247 Antworten des direkten Judge und vier Workflow-Varianten auf die 491 zurückgehaltenen Aufgaben): AUROC **0,952**, Paargenauigkeit innerhalb einer Aufgabe 0,884, Brier 0,093; Trust-Score AUROC 0,60. Best-of-N-Genauigkeit: direkt N ≈ 3 einzeln 0,531 / Mehrheit 0,567 / **Verifier 0,606** / Orakel 0,687; Workflow N ≈ 6 0,411 / 0,511 / **0,577** / 0,684; gepoolt N ≈ 8,6 0,440 / 0,589 / **0,635** / 0,776 |
| Urteil Verifier | Der größte Effekt des Kontingents. Offen: Übertragung auf externe Daten (23874318) und auf ungesehene Aufgabenfamilien; die Einbindung in die Pipeline |

### 4.3 GraphRAG-Ablation
| Befund | Status |
|---|---|
| Wellen 2/3: GraphRAG in ~90 % der Episoden an, aber gegen einen leeren `:Entity`-Graphen (die Aufgabengraphen nutzten andere Labels) | geprüft |
| Der Fuzzy-Router schaltete GraphRAG für Nachschlage-Pläne ab (Graph-Konfidenz 0,30 < 0,35; die Planner-Kategorie `compounding_knowledge` ist nicht graph-lastig) | geprüft |
| Mit erzwungenem GraphRAG enthält der Kontext die richtigen Werte (10/10), aber Rechnungen gingen mit erfundenen Ausdrücken an MCP `calculate`, und das Trust-Gate blockierte alles; rechenfreie Aufgaben und gate-freie Entwürfe behoben das | belegt |
| Erzwungener Router, 480 Aufgaben (23814342): graph 0,61, noisy 0,61, inline 0,75, empty 0,00; graph − empty +0,60 (KI +0,50 … +0,68), graph − inline −0,14 (−0,23 … −0,04); veraltete Duplikate kosten nichts Messbares | belegt |
| **Produkt-Router, 480 Aufgaben (23795184): Abrufanteil 0,00 in jedem Arm** | belegt |
| Externe Datensätze: ein Funktionstest lief als Vergleich von Workflow und Direktmodell auf externen Daten (770 Aufgaben, [Befund F5](lumi_findings_2026-11.md)); der Graph-Arm mit 20 Graph-Fragen war ohne den Graphen nicht aussagekräftig | teilweise |
| Folge im Code: Entitätstreffer führen zum Graph, der Knowledge-Broker liefert Graph-Fakten vor dem Planner (Branch `feat/knowledge-broker`); Gate P0 (23874159–64) | offen |

### 4.4 Policy-Pilot (Phase 3)
**Nicht gestartet, mit Begründung.** Ein Satz Planner-Präferenzpaare wurde erhoben und ausgewertet (23618242, 23791558–61, 23843018): Das
Ausgangsrauschen nach dem Planner ist so groß wie der Planner-Effekt, 69 % der `calculate`-Pläne sind nicht auswertbar und die Experten
antworten aus den Inline-Daten, und ein perfekter Planner käme auf etwa 0,45 gegen 0,49 beim Einzelmodell ([F7](lumi_findings_2026-11.md)).
Das Budget, das diese Phase gebraucht hätte, bleibt ungenutzt; es wird als solches in Abschnitt 2 ausgewiesen und nicht für einen Lauf ohne
Fragestellung ausgegeben.

### 4.5 Bewertung aller Läufe (Stand 19.11.2026)

Die Kosten sind aus Laufzeit × 4 Karten je Knoten geschätzt und gerundet. Wertung: erreicht / teilweise / verfehlt / ungültig / offen.

| # | Projekt | Jobs | Beabsichtigt | Tatsächlich | Wertung | Schlussfolgerung |
|---|---|---|---|---|---|---|
| 1 | Stack-Aufbau und Smokes | z. B. 23515523 ff. | Der volle MoE-Sovereign-Stack läuft in einem Slurm-Job. | Läuft (Start ≈ 13 min); viele Portierungsfallen dokumentiert. | Erreicht | Voraussetzung erfüllt. Etwa 20 % der Jobstarts scheitern auf einzelnen Knoten (vLLM-Segfault, 8 Fälle, Kosten: Minuten). |
| 2 | Welle 2 und 3 (Gegenversuchs-Datensatz) | 23522672–75, 23535809–14 | Gate-freie Orchestrierungs-Labels für ein PRM. | 217.000 Episoden, 52.000 Entscheidungspunkte. Das Label `q` ist durch das Gate verfälscht: 50 % der Forks blockiert, Textbaseline 0,60 innerhalb einer Aufgabe, nur 0,50–0,53 auf gate-bestandenen Forks. Zwei Jobs im Timeout. ≈ 950 Karten-h. | Teilweise | Als PRM-Trainingsdatensatz unbrauchbar. Der negative Befund („das Gate dominiert das Label“) war der wichtigste Gewinn dieser Phase. |
| 3 | PRM-Frühtests | 23549095/96, 23550288, 23556411, 23562153 | Trainingsstack für 7B und 32B auf mehreren Knoten prüfen. | Slingshot-Bindings, `transformers` 5 und Shutdown-Hang behoben; 32B ≈ 2,4 Samples/s; ein Fehlstart, zwei Timeouts. | Erreicht | Infrastruktur trägt, Kosten je Lauf bekannt. Wenige Karten-h. |
| 4 | 32B-PRM-Läufe | 23603688/89 | Volles 32B-PRM (je ≈ 90 Karten-h). | Vor dem Start abgebrochen, 0 verbraucht. Die Einreichung war voreilig, der Textbaseline-Check hätte vorher kommen müssen. | Richtig gestoppt | Ohne Signal im Label wäre es verschwendete Rechenzeit gewesen. |
| 5 | GraphRAG-Smokes 1–4 | 23567371, 23569439✗/23572488, 23603690, 23642846 | GraphRAG-Effekt messen. | Smoke 1: Graph leer und Router aus. Smoke 2: 0 richtig, der Planner schickt Rechnungen an `calculate`. Smoke 3: alles vom Gate blockiert. Smoke 4: erster Effekt (+0,53). | Teilweise | Jeder Smoke deckte einen realen Mess- oder Produktfehler auf, erst der vierte maß etwas. ≈ 20 Karten-h. |
| 6 | GraphRAG-Ablation | 23795184, 23814342, 23795183✗ | Nutzt der Produkt-Router den Graph? Kosten veraltete Fakten Genauigkeit? | Produkt-Router: Abruf 0,00. Erzwungen: graph − empty +0,60, graph − inline −0,14 (KI −0,23 … −0,04), noisy ≈ graph. | Erreicht | Der Effekt existiert, das Produkt erreicht ihn aber nicht; Retrieval ist schlechter als Daten im Prompt. Grenze: synthetische Einzelwert-Abfragen. |
| 7 | Planner-Paar-Smoke | 23618242 | Machbarkeit von Planner-Paaren prüfen. | Sechs verschiedene Pläne je Punkt, Korrektheit unabhängig vom Gate. ≈ 5 Karten-h. | Erreicht | Zeigte, dass das gate-freie Label funktioniert. |
| 8 | Welle 4 und Planner-Welle | 23662477–87, 23667179 | PRM-v2-Labels und ≈ 7.700 Planner-Paare. | Der Planner-Server stürzte in 8 von 11 Jobs ab (vLLM-KV-Cache-Fehler), danach nur noch Fallback-Pläne. 49 % der Aufgabensätze unbrauchbar, ≈ 350 Karten-h verloren. | Teilweise | Mein Versäumnis: Planner-Logs nach dem Smoke nicht geprüft, keine Gesundheitsprüfung. Entdeckt über „1 statt 6 verschiedene Pläne“. |
| 9 | Wiederholungen | 23791554–61, 23814340, 23814343✗/23843018 | Unbrauchbare Teile ersetzen. | Planner-Fix hält: 0 Abstürze, 0 von 1.201 Punkten mit nur einem Plan. Welle 5: 2.757 Aufgaben, Planner-Paare 1.211 (808 läuft). ≈ 300 Karten-h. | Erreicht | Saubere Daten liegen vor. Die Kosten waren eine Folge von Zeile 8. |
| 10 | PRM v2 (7B) | 23791608, 23814361–64 | Schlägt ein gelerntes Wertmodell die Baselines? | AUROC 0,873 (sd 0,003), innerhalb einer Aufgabe 0,564 (sd 0,007) gegen 0,537 (Knoten-Mittel) und 0,525 (Text). Lernkurve ≈ +0,01 AUROC je Datenverdopplung. Ein früherer „Planner-Wert 0,611“ war ein Artefakt, korrigiert. ≈ 36 Karten-h. | Teilweise | Das Signal ist real, aber klein; ein Nutzen für Schrittwahl oder Plan-Auswahl ist nicht gezeigt. 32B nur als saubere Skalierungsfrage. |
| 11 | Direkt gegen Workflow auf der Testmenge | 23795177–82, 23814341✗, 23814831✗ | Zeigen, dass der Workflow ein Einzelmodell schlägt, plus Stufen-Ablation. | Direkt 0,485, Workflow 0,352 (−0,13, KI −0,17 … −0,09). Die Varianten unterscheiden sich vom Workflow nicht über das Rauschen. 22 % der Workflow-Episoden scheitern am Planner-Contract (153 davon an der von mir ergänzten `calculate`-Prüfung). Gleich bei `cypher_calc` (0,72 gegen 0,71). Zwei Läufe durch meinen Watchdog ungültig. | Teilweise | Ein belastbares, **negatives** Ergebnis: Auf dieser Suite zeigt der Workflow keinen Vorteil. Gegenteilige Aussagen auf Website und im Whitepaper sind nicht gedeckt. |
| 12 | Externe Daten und Größenkurve | 23842891, 23842892, 23843014/16 | Externe Gültigkeit, Schwierigkeit nach Größe. | Direkter Judge auf externen Daten: Mathe 0,94, Tabellen 0,68, Text-QA 0,60. Der Workflow liegt in allen drei Familien darunter (−0,24 / −0,32 / −0,085); Prüfung aus gegen an gemischt. Größenkurve des direkten Judge: 0,66 / 0,42 / 0,16 / 0,05 bei 19 / 57 / 190 / 570 Zeilen. | Erreicht | Das Einzelmodell ist bei kleinen Daten stark und bricht ab etwa 60 Zeilen ein; der Workflow hat auf externen Daten keinen Vorteil. |
| 13 | Antwort-Verifier | 23842900 | Lernbarer Prüfer gegen den Trust-Score (AUROC 0,60). | AUROC 0,952 auf Antworten zu zurückgehaltenen Aufgaben; Best-of-N 0,635 gegen 0,440 für eine Einzelantwort (gepoolt N ≈ 8,6), 0,606 gegen 0,531 für den direkten Judge bei N ≈ 3. ≈ 20 Karten-h. | Erreicht | Der größte Effekt des Kontingents; Übertragung auf externe Daten offen (23874318), danach die Pipeline. |
| 14 | Kontrolläufe | 23843015/17/18 | Saubere Wiederholung der ungültigen Läufe. | Deliberation 0,323 (−0,03 gegen Workflow); Prüfung aus 0,410 gegen 0,352 auf der Testmenge (+0,055), umgekehrtes Vorzeichen auf externen Daten; Planner-Paare 808 erhoben. | Erreicht | Die Prüfung sollte abstufen statt scheitern; Deliberation bringt nichts. |
| 15 | Knowledge-Broker (Produktcode) | Branch `feat/knowledge-broker` | Infrastrukturwissen vor dem Planner, Abgleich, Experten-Injektion, Routing nach Entitätstreffern. | 23 Commits, 1.452 Tests, alles Neue standardmäßig aus; unterwegs Fehler in Correction-Memory, Streaming und Mandantentrennung gefunden und behoben. | Erreicht (Code) | Wirkung ungemessen bis Gate P0 (23874159–64). |
| 16 | Beim Schreiben offen | 23857467–73, 23858647–54, 23874159–64, 23874318 | Stichproben-Ertrag, Router-Schwelle, Base gegen Feintune, Gate P0, Verifier-Übertragung. | In der Queue. | Offen | Siehe Befunde, Abschnitt 6. |

Gesamtbild:

- **Verbraucht:** 5.108 von 18.000 Karten-h (28,4 %) am 19.11.2026. Etwa 350 gingen in Daten, die verworfen werden mussten, ≈ 15 in ungültige Läufe. Voraussichtlich bleiben rund 11.000–12.000 Karten-h ungenutzt; das ist wissenschaftlich richtig, wenn die Gründe stimmen.
- **Belastbare Befunde:** Das Trust-Gate verdeckt Effekte (Korrelation mit Korrektheit 0,10, AUROC 0,60); der Produkt-Router nutzt den Graph nicht; ein Einzelmodell schlägt den Workflow auf dieser Suite; das PRM-Signal ist klein; Mehrheitsentscheid über Stichproben wirkt stark.
- **Meine Fehler:** zwei 32B-Läufe voreilig eingereicht; Smokes ohne Prüfung der Planner-Logs; der Watchdog beendete gesunde Server; die `calculate`-Prüfung scheitert nach drei Wiederholungen hart; der Treiber übernahm den Entwurf freigegebener Episoden nicht; der PRM-Planner-Wert war ein Artefakt.
- **Für ein Ergebnis noch offen:** Workflow gegen Direkt auf externen Daten, die sauberen Kontrolläufe zur `calculate`-Prüfung und das Verifier-Training. DPO, ein 32B-PRM und weitere Wellen bleiben zurückgestellt.

## 5. Negative Ergebnisse und Sackgassen

Hier gesammelt, damit sie als Befunde gelesen werden: ZeRO-3 + PEFT auf multimodalen Checkpoints; regelbasiertes DPO; die August-
Expertenkampagne; GRPO mit `sequence_mask`-Importance-Sampling; der 2-Knoten-Hang ohne Slingshot-Bindings; GraphRAG gegen ein leeres
Schema; Rechenaufgaben, die den Planner messen. Aus Phase J: ein vom Quality-Gate verfälschtes Label (Wellen 2/3 als PRM-Daten
unbrauchbar); der Planner-Präferenzpaar-Weg (Rauschen so groß wie der Effekt); **der orchestrierte Workflow schlägt das Einzelmodell auf
dieser Suite und auf den externen Sätzen nicht**; Deliberation bringt nichts; Finetuning hat keine nachgewiesene Wirkung auf
Pipeline-Ebene (September-Benchmark, Base-gegen-Feintune-Läufe offen); ein PRM mit kleinem Gewinn auf Schrittebene. Eigene Fehler, die
Rechenzeit kosteten, stehen in den [Befunden, Abschnitt 3](lumi_findings_2026-11.md): der Serverfehler, der ≈ 350 Karten-h verwerfen ließ,
ein Watchdog, der einen gesunden Server beendete, eine Contract-Prüfung, die hart scheitert, ein Treiberfehler, eine zurückgezogene Zahl.

## 6. Lehren (aus dem Logbuch übernommen, am Ende erweitern)

Daten vor Rechenzeit; Trainingsmetriken sind keine Fähigkeit; Verifier-Belohnungen wirken, wo SFT stagnierte; Bibliotheksversionen
gehören zum Experiment; früh scheitern; agenteneingereichte Arbeit gehört auf small-g/standard-g. Neu in Phase J: den Kanal
instrumentieren (`answer_in_context`), bevor das Modell gemessen wird; prüfen, ob ein Schalter die Komponente wirklich erreicht
(der Router überschrieb den Template-Schalter); Rechenbedarf aus gemessener Schrittzeit schätzen, bevor budgetiert wird (der
32B-Lauf braucht etwa 90 Karten-h, nicht 4.500).

## 7. Artefakte und Export (Frist 05.12.2026)

| Artefakt | Ort auf LUMI | Export | Status |
|---|---|---|---|
| Rollouts Wellen 2+3, PRM-Daten | `$SCRATCH/moe-stack/runs`, `$SCRATCH/prm/data*` | lokale Kopie unter `/opt/tmp/lumi-rollouts/` vorhanden | **zu ergänzen:** finale Prüfsummenliste |
| 32B-PRM-Checkpoints (je etwa 60 GB) | `$SCRATCH/prm/runs/prm32b_*` | **zu ergänzen:** kopieren oder veröffentlichen (HF), siehe Regeln unten | offen |
| Logs, Jobskripte, Konfigurationen | `$SCRATCH/*/logs`, Repository | Skripte liegen im Repository; Logs **zu ergänzen** | offen |
| Container-Images | `$SCRATCH/*.sif` | nicht exportiert (neu baubar) | entschieden |

Exportregeln (feststehend): Keine Nutzerdaten, Logs echter Nutzer oder Memory-Inhalte verlassen das System; keine Secrets im
Repository; der private Schlüssel wird nie übertragen; aus Fremddatensätzen abgeleitete Daten folgen deren Lizenzen (HotpotQA
ist Share-Alike, SynthCypher nicht kommerziell und ausgeschlossen; FinQA: Repository und Hugging-Face-Karte widersprechen sich).

## 8. Datenerhebung am Ende (Befehle)

```bash
lumi-allocations
sacct -S 2026-06-01 -X -o JobID,JobName%24,Partition,AllocNodes,Elapsed,State,Start > sacct_all.tsv
cat $SCRATCH/prm/runs/prm32b_*/eval.json
du -sh $SCRATCH/prm/runs/* $SCRATCH/moe-stack/runs
sha256sum <Exportdateien> > SHA256SUMS
```

## 9. Offene Punkte nach dem Kontingent

Nicht ohne LUMI fortsetzbar: größere Stichprobenwellen, Seeds, ein 32B-Modell. Lokal oder auf Colab fortsetzbar: die Verifier-Pipeline
(ein 7B-Scorer), der Knowledge-Broker (Produktcode, Tests, N04-Stack), die Tabellengrößen-Suite, das Abstufen der `calculate`-Prüfung.
Offene Fragen: [Befunde, Abschnitt 6](lumi_findings_2026-11.md). Beim Schreiben noch in der Queue: Base gegen Feintune (23858647–54),
Stichproben-Ertrag und Router (23857467–73), Knowledge-Gate P0 (23874159–64), Verifier-Übertragung (23874318).
