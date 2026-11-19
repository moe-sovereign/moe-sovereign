# LUMI-G-Abschlussbericht (Entwurf, vorbereitet am 10.11.2026)

**Status:** Entwurf. Abschnitte mit **zu ergänzen** werden nach Ablauf des Kontingents am **05.12.2026** gefüllt; alles andere
stammt aus dem [Aktivitätslogbuch](lumi_activity_log.md) und trägt dessen Statuskennzeichnungen. In einem Abschnitt „zu
ergänzen“ darf nichts aus Erwartung stehen: Jede Aussage braucht einen Job, eine Datei oder eine Befehlsausgabe als Quelle.

**Adressaten:** Betreiber, Fördergeber und öffentliche Dokumentation (Website und Whitepaper übernehmen nur die
gekennzeichneten Auszüge der Abschnitte 3–5).

---

## 1. Zusammenfassung (zu ergänzen)

Zuletzt schreiben, höchstens fünf Sätze: was die 18.000 Karten-Stunden hervorgebracht haben, welche Ergebnisse gemessen sind,
welche negativ, was offen bleibt, wo die Artefakte liegen. Keine Aussage ohne einen Abschnitt unten, der den Beleg trägt.

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

### 4.2 Orchestrierungs-Prozess-Belohnungsmodell
| Punkt | Wert |
|---|---|
| Referenzen (`test_iid`) | Familie/Knoten-Mittel: AUROC 0,71, paarweise innerhalb der Aufgabe 0,51; **Textreferenz (TF-IDF + Ridge): AUROC 0,88, paarweise 0,58** (test_ood 0,60). Das PRM muss die Textreferenz schlagen |
| 7B-Smoke (1 Knoten), 32B-Smoke (4 Knoten) | Trainer läuft durch; 32B ≈ 2,4 Beispiele/s auf 32 Rängen |
| 7B-Seeds, danach 32B nur bei Signal (die beiden 32B-Jobs 23603688/89 wurden abgesagt, solange sie PENDING waren) | **zu ergänzen:** Endmetriken aus `eval.json` (AUROC, Brier, Spearman, paarweise innerhalb der Aufgabe auf `test_iid` und `test_ood`), Seeds, Laufzeit, Karten-h |
| Urteil | **zu ergänzen:** Schlägt das PRM die Familien-/Knoten-Referenz innerhalb der Aufgabe und auf der zurückgehaltenen Domäne? Auch ein Nein wird so festgehalten |

Übertrifft die paarweise Genauigkeit innerhalb der Aufgabe die Referenz nicht um mehr als die Streuung über Seeds, steht im
Bericht, dass das PRM keinen Nutzen auf Entscheidungsebene gezeigt hat, und Phase 3 ist mit dieser Begründung als nicht
gestartet dokumentiert.

### 4.3 GraphRAG-Ablation
| Befund | Status |
|---|---|
| Wellen 2/3: GraphRAG in ~90 % der Episoden an, aber gegen einen leeren `:Entity`-Graphen (Aufgabengraphen nutzten andere Labels) | verifiziert |
| Fuzzy-Router schaltete GraphRAG für Lookup-Pläne ab (0,30 < 0,35); Logzeile „template toggle“ irreführend | verifiziert |
| Bei erzwungenem GraphRAG enthält der Kontext die richtigen Werte (10/10), trotzdem wird nichts beantwortet: Der Planner schickt Arithmetik mit erfundenen Ausdrücken an MCP `calculate`, das Trust-Gate blockiert, der Merger liefert leeren Text | validiert (negativ) |
| Aufgaben ohne Arithmetik (Smoke 3, 23603690) | **zu ergänzen:** `answer_in_context`, Abrufanteil, korrekt und Entwurf korrekt je Arm |
| Mini-Welle, externe Datensätze (TAT-QA, FinQA, GSM8K, MuSiQue, HotpotQA, CypherBench) | **zu ergänzen:** gelaufen oder „nicht gelaufen“ mit Grund |

### 4.4 Policy-Pilot (Phase 3)
**Zu ergänzen:** Lauf (CSO-DPO gegen SFT v4) mit Metriken, oder „nicht gestartet“ mit Grund und dem ungenutzten Budget.

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
| 12 | Externe Daten und Größenkurve | 23842891, 23842892, 23843014/16 | Externe Gültigkeit, Schwierigkeit nach Größe. | Direktlauf fertig: Mathe 0,94, Tabellen 0,67, Text-QA 0,59. Workflow und Größenkurve laufen. | Offen | Erstmals echte Daten; zeigt, wo das Einzelmodell Lücken hat. |
| 13 | Antwort-Verifier | 23842900 | Lernbarer Prüfer gegen den Trust-Score (AUROC 0,60). | Läuft. Offline: Mehrheitsvotum hebt `cypher_calc` von 0,73 auf 0,92. | Offen | Es gibt Spielraum, das Training entscheidet. |
| 14 | Kontrolläufe | 23843015/17/18 | Saubere Wiederholung der ungültigen Läufe. | Laufen. | Offen | Zeigen, wie stark die `calculate`-Prüfung die Genauigkeit drückt. |

Gesamtbild:

- **Verbraucht:** 4.998 von 18.000 Karten-h (27,8 %) am 19.11.2026. Etwa 350 gingen in Daten, die verworfen werden mussten, ≈ 15 in ungültige Läufe. Voraussichtlich bleiben rund 11.000–12.000 Karten-h ungenutzt; das ist wissenschaftlich richtig, wenn die Gründe stimmen.
- **Belastbare Befunde:** Das Trust-Gate verdeckt Effekte (Korrelation mit Korrektheit 0,10, AUROC 0,60); der Produkt-Router nutzt den Graph nicht; ein Einzelmodell schlägt den Workflow auf dieser Suite; das PRM-Signal ist klein; Mehrheitsentscheid über Stichproben wirkt stark.
- **Meine Fehler:** zwei 32B-Läufe voreilig eingereicht; Smokes ohne Prüfung der Planner-Logs; der Watchdog beendete gesunde Server; die `calculate`-Prüfung scheitert nach drei Wiederholungen hart; der Treiber übernahm den Entwurf freigegebener Episoden nicht; der PRM-Planner-Wert war ein Artefakt.
- **Für ein Ergebnis noch offen:** Workflow gegen Direkt auf externen Daten, die sauberen Kontrolläufe zur `calculate`-Prüfung und das Verifier-Training. DPO, ein 32B-PRM und weitere Wellen bleiben zurückgestellt.

## 5. Negative Ergebnisse und Sackgassen

Aus dem Logbuch gesammelt, damit sie als Befunde gelesen werden und nicht verschwinden: ZeRO-3 + PEFT auf multimodalen
Checkpoints; regelbasiertes DPO; die August-Expertenkampagne; GRPO-Importance-Sampling `sequence_mask`; der Zwei-Knoten-Hänger
ohne Slingshot-Bindings; GraphRAG gegen ein leeres Schema; Rechenaufgaben, die den Planner messen. **Zu ergänzen:** was die
letzten Wochen hinzufügen.

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

Was ohne LUMI nicht weitergeht (größere Wellen, Seeds, Phase 3), was lokal auf N04 laufen kann und welche Fragen die Ergebnisse
hinterlassen: **zu ergänzen**.
