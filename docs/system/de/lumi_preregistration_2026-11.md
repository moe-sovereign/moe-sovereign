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

## Nachtrag 22.11.2026 (geschrieben vor jedem Ergebnis zu G–J)

**G. Planner mit ausgelagertem Wissen (Entscheidung festgehalten).** Der Planner, der entscheidet, was er ein Wissensmodell fragt, wird stufenweise entwickelt; nur die erste Stufe
verbraucht vor dem 05.12.2026 Rechenzeit. *G1* (läuft): ein LoRA-SFT-OLMo-3-7B-Interview-Planner aus Lehrer-Traces (Qwen3-Next-80B; regelbasiert je Aufgabenart akzeptiert), offline auf 369 Held-out-Interviewaufgaben
(öffentliche Fakten, Zwei-Hop-QA, Antwort schon bekannt, private Inline-Daten) und im Stack auf den Konfliktarmen von Gate P0 ausgewertet. **G-H1:** Er schlägt den aktuellen Planner (regelbasierte Lückenfragen) auf den Konfliktarmen von
Gate P0 und hält den Anteil unnötiger Fragen bei bekannter Antwort und Inline-Aufgaben unter 10 %; **Abbruch:** nicht besser auf den Konfliktarmen. *G2* (ein kleiner, von Grund auf trainierter Planner, dessen Gewichte kein Faktenwissen tragen, oder
fortgesetztes Pretraining auf Ökosystem-Daten) ist in der [Konzeptnotiz](lumi_planner_concept.md) beschrieben und erhält **vor dem 05.12.2026 keine Rechenzeit, außer G1 besteht**; Gründe: Ein perfekter
Planner erreichte auf diesen Aufgaben nur ≈ 0,45 gegen 0,49 für das Einzelmodell (F7), und Feintuning von Planner und Judge hat keinen messbaren Effekt (F12).

**H. Ungesehene Aufgabenfamilien.** Der Verifier wurde auf `sql_calc`, `cypher_calc`, `cypher_sql_calc` und externen Mengen trainiert. Neue Aufgaben der Familien `tool_chain`
(mehrstufige Rechnung mit Werkzeugen) und `direct` (einstufige Arithmetik), Seed 888, IDs ungesehen (250 + 250 Aufgaben, `orch_tasks_heldout4.jsonl`), direkter Judge × 3 und Workflow × 2.
**H-H1:** AUROC von Verifier v1 und v2 ≥ 0,80 je ungesehener Familie. **H-H2:** Die Freigaberegel aus A-H2 mit den *jetzt* festgelegten Schwellen aus den gesehenen Familien (t = 0,80 für v2, 0,85 für v1)
schlägt den direkten Judge allein um ≥ +0,02 (KI ohne Null) auf den ungesehenen Familien. Ein Verfehlen wird als Grenze des Verifiers berichtet, nicht wiederholt.

**I. Seed-Wiederholung des Verifiers.** Ein zweites Training von Verifier v2 (Seed 2, gleiche Daten und Einstellungen). **I-H1:** AUROC auf `test_iid`, `test_ood`, `test_ext` innerhalb ±0,01 von Seed 1, und der
A-H2-Effekt von Seed 2 im Konfidenzintervall von Seed 1. Beide Seeds werden berichtet.

**J. Freigaberegel auf externen Daten.** Verifier **nur v1** (v2 sah externe Antworten im Training) bewertet die externen Antworten der Base-gegen-Feintune-Läufe (7.490 Antworten zu 770 Aufgaben). Berichtet je externer Familie
(Mathe, Tabellen, Text-QA): AUROC, ECE und Abdeckungs-Genauigkeits-Kurve gegen den Trust-Score. **J-H1 (beschreibend):** AUROC ≥ 0,80 bei Mathe und Tabellen und < 0,65 bei Text-QA, wie in der früheren Transfermessung.

**Auswertungsregel.** Jeder Strang wird ausgewertet, sobald alle seine Jobs mit Exit-Code 0 beendet sind, mit dem in seinem Abschnitt genannten Skript; Infrastrukturfehler werden behoben und neu eingereicht und sind keine Ergebnisse.

## Nachtrag 23.11.2026 (geschrieben vor jedem Ergebnis zu A5)

**A5. Rückfall auf den direkten Judge bei Pipeline-Ausfall.** F26 zeigte: Das Verifier-Gate mit Eskalation hebt die Genauigkeit bei den Episoden, die eine Antwort liefern (0,594 gegen 0,500
für den direkten Judge auf denselben Aufgaben), aber 23 % der Episoden scheitern vor dem Gate und zählen als falsch. Der Produktschalter `PIPELINE_FALLBACK_DIRECT=1` beantwortet eine solche Anfrage
(Pipeline-Fehler oder Zeitlimit) mit einem direkten Judge-Aufruf (Temperatur 0, nur die ursprüngliche Anfrage) und kennzeichnet die Antwort. Held-out-Aufgaben (`orch_tasks_heldout2.jsonl`, 491 Aufgaben × 2),
zwei Arme, sonst derselbe Stack wie der A4-Lauf: **A5a** nur Rückfall (Verifier aus), **A5b** Rückfall mit `VERIFIER_MODE=gate`, `VERIFIER_ESCALATE=1`, `VERIFIER_PASS=0.8`.
- **A5-H1.** A5b schlägt den direkten Judge (Mittel aus 8 Stichproben, 0,489) um ≥ +0,02 mit einem Konfidenzintervall ohne 0 (Entwurfsgenauigkeit, gepaart je Aufgabe). Das Gedankenexperiment aus F26 sagt etwa +0,07 voraus.
- **A5-H2 (Zerlegung).** A5a schlägt den Workflow-Standard (0,331) um ≥ +0,05, und A5b schlägt A5a um ≥ +0,02.
- **Beschreibend.** Anteil der vom Rückfall beantworteten Episoden; Genauigkeit der Rückfall-Antworten gegen das Mittel des direkten Judges auf denselben Aufgaben; Genauigkeit je Familie; Tokens je Episode.
- **Abbruch.** Ein Smoke-Lauf mit 30 Aufgaben muss Rückfall-Antworten zeigen (sonst wird der Haken nicht erreicht), bevor die beiden vollen Läufe eingereicht werden.
