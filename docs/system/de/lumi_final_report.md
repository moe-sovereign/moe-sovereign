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
