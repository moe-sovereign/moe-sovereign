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

## Nachtrag 23.11.2026 (2) (geschrieben vor jedem Ergebnis zu O1–O3): Ist die MoE-Logik falsch, oder wurde sie nie getestet?

*Hintergrund.* F30: Der Planner schickt 74 % der Teilaufgaben auf die Kategorie `precision_tools`, und 56 % der Anfragen nutzen außer diesem Werkzeugpfad keinen Experten; jeder Pipeline-Ausfall (F26) ist ein
Planner-Vertragsfehler. Der Vergleich „Workflow gegen direkten Judge“ hat „spezialisierte Experten gegen einen Generalisten“ daher nicht getestet. Drei Behauptungen werden getrennt: (a) Die Experten sind auf ihrem
Thema gut; (b) richtiges Routing ließe sie helfen; (c) ihre Antworten lassen sich gut auswählen. Aufgaben: die 491 Held-out-Aufgaben (`orch_tasks_heldout.jsonl`); Vergleichsmodell: der direkte feingetunte 32B-Judge
(8 Stichproben vorhanden, 16 weitere kommen hinzu, insgesamt 24); die Experten sind die acht feingetunten SmolLM3-3B-Experten. Das Vergleichsmodell ist ein offenes 32B-Modell, kein Frontier-Modell.

**O1. Jeder Experte allein, und was Routing oder Auswahl bestenfalls gewinnen könnten.** Jeder Experte beantwortet jede Aufgabe 3-mal, mit dem System-Prompt seines Feintunings (primär) und ohne ihn (beschreibend),
ohne Planner, Judge und Gate; der Judge antwortet 16-mal zusätzlich. Aufteilung der Aufgaben nach `sha256(task_id) mod 2` (Dev / Test), jetzt festgelegt.
- **O1-H1 (Spezialisten auf ihrem Thema).** Mindestens ein Paar (Experte, Familie) schlägt den direkten Judge auf dieser Familie um ≥ +0,05 (mittlere Genauigkeit je Aufgabe, gepaarter Bootstrap, KI ohne 0).
- **O1-H2 (realistisches Routing).** Ein Router, der je Familie das Beste aus {Judge, die acht Experten} auf Dev wählt und auf Test bewertet wird, schlägt den Judge auf den Testaufgaben um ≥ +0,02 (KI ohne 0).
- **O1-H3 (Auswahl unter Antworten).** Wählt der Verifier v2 aus einer gleichen Zahl von 24 Kandidaten je Aufgabe eine Antwort, schlägt ein Pool aus 12 Judge- und 12 Expertenantworten einen Pool aus 24 Judge-Antworten um ≥ +0,02 (KI ohne 0):
  Die Experten tragen etwas bei, das über den Gewinn des Auswählens unter mehr Antworten hinausgeht. Mitberichtet: 24 Expertenantworten, 24 Judge-Antworten und die Kurve über die Kandidatenzahl. Zufällige Teilmengen werden je Aufgabe 200-mal gezogen.
- **Entscheidungsregel.** Ist H2 oder H3 erreicht, ist die MoE-Logik für diese Aufgaben gerechtfertigt, und der nächste Schritt ist ein gelernter Router oder Selektor. Ist keine der Hypothesen H1–H3 erreicht, tragen Experten
  dieser Qualität auf diesen Aufgaben nichts bei, und der Test wandert zu wissensintensiven Aufgaben mit echten Spezialmodellen und einem stärkeren Generalisten.

**O2. Erzwungenes Routing statt LLM-Planner.** `PLANNER_MODE=rule` ersetzt den Planner durch Regeln: eine Teilaufgabe je Anfrage, Kategorie nach Schlüsselwörtern (Datentabellen, SQL, Datenbank → `data_analyst`; Graph, Knoten, Beziehungen, Cypher
→ `compounding_knowledge`; Berechnung von Größen → `precision_tools`; sonst `general`), kein Planner-Aufruf, keine Vertragsfehler. Held-out-Aufgaben `orch_tasks_heldout2.jsonl`, der Judge wie im Standardlauf.
- **O2-H1.** Der Workflow mit Regel-Routing schlägt den Standard-Workflow (0,331) um ≥ +0,05 (der Planner war also ein Hindernis).
- **O2-H2.** Er erreicht den direkten Judge (0,489) bis auf 0,03 oder schlägt ihn (Routing war also das Haupthindernis).
- Beschreibend: Anteil gescheiterter Episoden (erwartet 0), Genauigkeit je Familie, Tokens je Episode, und dasselbe mit `MERGER_MODE=best_member`.

**O3. Routing-Audit.** Aus den Plänen des Standardlaufs: je Familie die vom Planner gewählten Kategorien, verglichen mit dem Experten, den O1 auf dieser Familie als besten findet; Anteil der Anfragen, deren Plan den besten Experten enthält. Beschreibend.

## Nachtrag 23.11.2026 (3) (geschrieben vor jedem Ergebnis zu O4–O6): Helfen die Experten, wenn ihr Trainings-Prompt wegfällt und die Route nach Kompetenz gewählt wird?

Anlass (F32–F34): Die Experten verlieren unter dem Prompt, mit dem sie feingetunt wurden, die Hälfte ihrer Genauigkeit (F33); Routing nach Themenname schickt Tabellenaufgaben an den schwächsten Experten (F32); die einzige Konfiguration, die den direkten Judge schlägt, ist die mit Verifier-Gate (F34). Drei Stränge trennen die verbleibenden Erklärungen. Aufgaben `orch_tasks_heldout2.jsonl` (491 Aufgaben × 2), sofern nicht anders angegeben; gepaarter Bootstrap über Aufgaben (2000 Ziehungen); Laufeinstellungen wie bei O2/A5.

**O4. Workflow ohne den Trainings-Prompt der Experten.** Neuer Schalter `EXPERT_SYSTEM_PROMPT=plain` (der eigene System-Prompt des Experten entfällt; der Modus-Zusatz bleibt). O4a = `PLANNER_MODE=rule`, `MERGER_MODE=best_member`, Prompt aus, kein Gate (identisch zu O2b bis auf den Prompt). O4b = O4a + `PIPELINE_FALLBACK_DIRECT=1` + Verifier-Gate mit Eskalation (identisch zu A5b bis auf Routing, Merger und Prompt).
- **O4-H1.** O4a schlägt O2b (0,457) um ≥ +0,04, KI schließt 0 aus (der Prompt war auch im Workflow ein Hindernis).
- **O4-H2.** O4b schlägt A5b (0,584) um ≥ +0,02, KI schließt 0 aus.
- Lesart: Scheitert O4-H1, schadet der Template-Prompt der Pipeline nicht so wie der Trainings-Prompt, und die Expertenseite ist nicht die Grenze.

**O5. Basismodell gegen Rollen-Feintunes.** Die acht Experten beantworten die 491 Held-out-Aufgaben (3 Stichproben), serviert vom SmolLM3-3B-Basis-Checkpoint statt der Rollen-Feintunes (`EXPERT_CKPT`), mit und ohne Rollen-Prompt, sonst wie O1.
- **O5-H1.** Die feingetunten Experten ohne Prompt (Mittel über die acht Rollen 0,428, O1) schlagen das Basismodell ohne Prompt um ≥ +0,03 (gepaart über Aufgabe × Rolle, KI schließt 0 aus): Das Rollen-Feintuning hat Kompetenz hinzugefügt.
- **O5-H2.** Für mindestens eine Familie schlägt der beste feingetunte Experte die beste Basismodell-Rolle um ≥ +0,03 (KI schließt 0 aus).
- Lesart: Gilt keines von beiden, hat das Rollen-Feintuning auf diesen Aufgaben keinen messbaren Nutzen, und ein Basismodell würde acht Experten ersetzen.

**O6. Kompetenz-Router.** `RULE_ROUTER_MATRIX_FILE` ordnet die Aufgabenfamilie (per Stichwort erkannt: nur Tabellen, nur Graph, beides) dem Experten zu, der auf der **Dev-Hälfte** der Aufgaben (sha256(task_id) mod 2 = 0) in O1s Variante ohne Prompt am besten ist (`experts/build_competence_matrix.py`; Gleichstand geht an die alphabetisch erste Rolle). Die Matrix steht vor dem Lauf fest: `cypher_calc` → general, `cypher_sql_calc` → governance, `sql_calc` → code_reviewer. O6a = O4a mit diesem Routing; O6b = O4b damit. **Auswertung nur auf der Test-Hälfte** (mod 2 = 1).
- **O6-H1.** O6a schlägt O4a um ≥ +0,03, KI schließt 0 aus (Routing nach Kompetenz schlägt Routing nach Thema).
- **O6-H2.** O6b schlägt O4b um ≥ +0,02, KI schließt 0 aus.

**Entscheidungsregel.** Ist O4-H2 oder O6-H2 erfüllt, ist die beste Konfiguration der Workflow mit Gate und Experten ohne Prompt (und Kompetenz-Routing), und der nächste Schritt ist der Vergleich Verifier gegen starke Baselines mit Kostenmaß. Ist keine der Hypothesen O4-H1, O4-H2, O6-H1, O6-H2 erfüllt, begrenzt die Expertenseite den Workflow nicht, und der Gewinn aus F34 ist allein Auswahl durch den Verifier. In jedem Fall berichtet: Anteil gescheiterter Episoden, Genauigkeit nach Familie, Tokens je Episode und der Anteil der Episoden, die das Gate an den direkten Fallback geschickt hat.

**Vorab genannte Grenzen.** Die Familienerkennung per Stichwort ist auf diesem synthetischen Generator exakt (491 von 491) und sagt nichts über freien Text; ein Lauf je Arm; die Matrix wird auf 50 % der Aufgaben gewählt und auf den anderen 50 % ausgewertet, O6 hat also etwa die halbe Trennschärfe von O4; der Verifier v2 stammt aus demselben Aufgabengenerator; `precision_tools` bleibt eine MCP-Kategorie und kein Modell in der Pipeline (in O1 war es ein feingetuntes Modell) und wird daher von der Matrix nie gewählt.
