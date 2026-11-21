# LUMI-G-Endspurt: Präregistrierung von sechs Experimenten (21.11.2026)

**Stand:** geschrieben und committet am 21.11.2026, bevor ein Ergebnis der folgenden Experimente existiert. Hypothesen, Metriken, Schwellen,
Splits und Abbruchregeln sind hier festgelegt; Abweichungen werden in den [Befunden](lumi_findings_2026-11.md) und im
[Abschlussbericht](lumi_final_report.md) als Abweichungen berichtet. Kontingent: project_465003058, Ablauf 05.12.2026. Geplanter Verbrauch
≈ 920–1.725 der verbleibenden ≈ 12.700 Karten-Stunden; der Rest bleibt absichtlich ungenutzt.

**Regeln für alle Experimente.** Korrektheit ist die Gate-freie Entwurfsgenauigkeit (`effective_correct`); Vergleiche sind je Aufgabe gepaart mit
Bootstrap über Aufgaben (95 %-Intervall); eine Aussage braucht ein Intervall ohne Null *und* den genannten Mindesteffekt. Infrastrukturfehler sind
keine Ergebnisse: analysieren, beheben, neu einreichen. Aufgaben, mit denen eine Schwelle gewählt wurde, werden nie zur Berichterstattung derselben benutzt.

## A. Verifier als kalibrierte Freigaberegel

*Hintergrund.* Der Trust-Score trennt richtige von falschen Antworten mit AUROC 0,60; der 7B-Verifier erreicht 0,95 auf ungesehenen Aufgaben derselben
Familien (F1, F10). Verwandte Arbeiten: kalibrierte Abgabe von einem kleinen an ein großes Modell (ReDAct, ICML-2026-Guardrail-Kaskaden),
Prover-Verifier-Selektivprädiktion.
- **A-H1.** Bei gleicher Abdeckung (Anteil freigegebener Antworten) ist die Genauigkeit freigegebener Workflow-Antworten mit dem Verifier um
  ≥ +0,10 höher als mit dem Trust-Gate.
- **A-H2.** „Workflow-Antwort freigeben, wenn Verifier-Score ≥ t, sonst die Antwort des direkten 32B“ schlägt den direkten 32B allein um ≥ +0,02.
- **Daten.** Workflow- und Direktantworten der Held-out-Aufgaben vom 20.11.2026 (491). Split: `sha256(task_id) mod 2` in Dev und Test, jetzt festgelegt;
  t wird auf Dev gewählt, auf Test berichtet. Externe Daten: nur Verifier v1 (v2 sah externe Antworten im Training).
- **Metriken.** Abdeckungs-Genauigkeits-Kurven, AUROC, ECE (10 Bins), Eskalationsrate gegen Genauigkeit.
- **Abbruch.** A-H1 nicht erreicht → kein Lauf in der Schleife; das Ergebnis wird berichtet.

## B. Wo verliert der Workflow? Synthese-Ablation bei gleichem Rechenaufwand

*Hintergrund.* LLM-Teams fallen oft unter ihr bestes Mitglied, weil sie Expert- und Nicht-Experten-Sicht mitteln (Pappu et al., ICML 2026).
- **B-H1.** Der Verlust entsteht im Merge-Schritt: `MERGER_MODE=evidence` schlägt `default` um ≥ +0,05 auf den Held-out-Aufgaben; `judge_solo` entspricht
  dem direkten 32B innerhalb 0,03 (sonst liegt der Verlust in der Pipeline-Hülle).
- **B-H2.** Bei gleichem Token-Budget erreicht der direkte 32B mit Mehrheitsentscheid mindestens die Genauigkeit des Workflows.
- **Arme.** `judge_solo`, `evidence`, `best_member` gegen die vorhandenen Läufe `default` (3 × 2 Wiederholungen) und direkt (8 Wiederholungen). B-H2 braucht
  Token-Zahlen je Episode; ohne sie wird nur B-H1 geprüft.

## C. Feingetunte Experten

- **C-H1.** Ersetzt man die feingetunten 3B-Experten durch die SmolLM3-3B-Basis, ändert sich die Pipeline-Genauigkeit um weniger als ±0,03 (Held-out und extern).
  Eine größere Änderung ist ein Befund über die Experten; keine ist das bereits für Planner und Judge gefundene Nullergebnis (F12).

## D. Generativer Verifier

*Hintergrund.* Verifier, die vor dem Urteil begründen, schlagen diskriminative; kleine werkzeuggestützte Verifier messen sich mit viel größeren Belohnungsmodellen.
- **D-H1.** Ein 7B-Verifier, trainiert auf Lehrer-Begründungen (Qwen3-Next-80B, gefiltert auf Begründungen, deren Urteil zum Label passt), erreicht AUROC ≥ 0,70
  auf externem Text-QA (diskriminatives v1: 0,59–0,61) bei einem In-Domain-Verlust ≤ 0,02 gegenüber v2.
- **Abbruch.** Begründungs-Ausbeute < 30 % oder keine Verlustabnahme im Smoke-Lauf. Kein zweiter Lauf nach einem Verfehlen.

## E. Reinforcement Learning mit Ausführungsbelohnung auf großen Tabellen

*Hintergrund.* GRPO mit Ausführungsbelohnung liefert kleinen Modellen starke Text-zu-SQL-Ergebnisse; einfache Belohnungen werden feinen vorgezogen;
RLVR ist durch die Verteilung des Basismodells begrenzt.
- **E-H1.** Ein 3B- oder 7B-Modell mit SQL-Werkzeug, per GRPO auf einer Ausführungsbelohnung trainiert, schlägt den direkten 32B (Tabelle inline) um ≥ +0,20
  bei ≥ 3-facher Basis-Tabellengröße (≈ 57 Zeilen).
- **Pflichtkontrolle.** Der 32B *mit* demselben SQL-Werkzeug und ohne Training; erreicht er das trainierte Kleinmodell, lautet der Befund „Werkzeug, nicht Training“.
- **Zusätzlich berichtet.** pass@k, k = 1…64, vor und nach dem Training. **Abbruch.** Kein Belohnungsanstieg im 2-Stunden-Pilot.

## F. Prospektiver Router und Speicher-Ablation

- **F1-H1.** Zwei jetzt festgelegte Regeln, ausgewertet auf einer neuen Held-out-Menge (neuer Seed, 491 Aufgaben, bekannte IDs ausgeschlossen): R1 Workflow nur für
  `cypher_sql_calc`, sonst direkt; R2 Workflow-Antwort, wenn ihr Verifier-Score den der Direktantwort übersteigt, sonst direkt. Beide schlagen direkt
  (post hoc auf der alten Menge ergab R1 +0,017, 95-%-KI +0,007 … +0,028; Orakel je Aufgabe 0,555).
- **F2-H1.** Mit Correction Memory und Graph-Ingest an steigt die Genauigkeit bei einer wiederkehrenden Aufgabenvorlage vom ersten zum zweiten Auftreten stärker als
  mit ausgeschaltetem Speicher. Voraussetzung (Smoke): Korrekturen oder Fakten werden im Benchmark tatsächlich geschrieben; sonst entfällt F2, und das ist das Ergebnis.

## Laufend und unverändert

Gate P0 des Knowledge-Brokers (Jobs 23900909–14) und der Planner-Interview-Pilot behalten ihre eigenen Kriterien (siehe Knowledge-Broker-Plan).
