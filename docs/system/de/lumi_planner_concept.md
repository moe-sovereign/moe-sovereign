# Konzeptnotiz: ein Planner, der sich Wissen holt, das er nicht hat

**Stand: Konzept, 22.11.2026. Nur Option A hat Rechenzeit dahinter (Jobs in der Queue); B und C sind beschrieben, damit sie auf Belegen entschieden werden können.**

**Ziel.** Ein kleines Modell, das kann, was das System von einem Planner braucht: entscheiden, was zu fragen ist, ein Wissensmodell nur nach dem fragen, was die Infrastruktur (Graph, Korrekturen,
Episoden) nicht hält, Werkzeuge mit gültigen Verträgen aufrufen und im Katalog der Installation bleiben. Wissen bleibt außerhalb der Gewichte: Es kommt zuerst aus der Infrastruktur und
danach aus einem austauschbaren Wissens-LLM; Konflikte werden nach Autorität vor Aktualität abgeglichen (siehe Knowledge-Broker-Entwurf). Ein Modell, das das gut kann, ist über eine Installation hinaus
nützlich, denn Wissensmodell oder Wissensbasis lassen sich tauschen, ohne es neu zu trainieren.

## Drei Optionen

| | A: LoRA-SFT von OLMo-3-7B (läuft) | B: fortgesetztes Pretraining auf Ökosystem-Daten | C: kleines Modell von Grund auf, Wissen ausgelagert |
|---|---|---|---|
| Idee | Interview- und Planformat aus Lehrer-Traces lernen | Dem Basismodell Kataloge, Schemata, Tool-Verträge und Pläne in großem Umfang zeigen | Ein 0,3–1B-Modell vortrainieren, dessen Ziele abrufbare Fakten ausschließen, sodass es nur Schlussfolgern, Planen und Nachschlagen lernt |
| Daten | 1.095 Aufgaben-Prompts, Lehrer Qwen3-Next-80B, regelbasiert je Aufgabenart akzeptiert | Synthetische Kataloge und Pläne, einige Milliarden Tokens | Synthetische Pläne, Tool-Call-Traces und retrieval-annotierter Text; zig Milliarden Tokens |
| Rechenzeit | ≈ 100–150 Karten-h (Traces, SFT, Evaluation) | einige hundert Karten-h plus Datenarbeit | ≈ 150–400 Karten-h, je nach Tokens |
| Hauptrisiko | Der Gewinn verschwindet auf Pipeline-Ebene (wie beim feingetunten Planner und Judge, F12) | Gewinn gegenüber A unklar, schwer messbar | Viele ungeprüfte Annahmen; Leistungsuntergrenze winziger Modelle; die Datenpipeline ist der eigentliche Aufwand |
| Messbar durch | Interview-Genauigkeit, Rate unnötiger Fragen, Konfliktarme von Gate P0 | Dasselbe, plus Katalog-Transfer | Dasselbe, plus Tausch von Wissensmodell und Katalog ohne Neutraining |

## Warum Rechenzeit nur an A geht
- Ein perfekter Planner erreicht ≈ 0,45 gegen 0,49 für das Einzelmodell auf den synthetischen Aufgaben (F7): Der Planner ist dort nicht der Engpass.
- Feintuning von Planner und Judge hat keinen messbaren Pipeline-Effekt (F12, ±0,03).
- Den Bedarf „dem Modell fehlt Wissen“ deckt der Knowledge-Broker ohne Training ab (Graphfakten, Korrekturen, optionales Wissensmodell); sein Abnahmetest (Gate P0) ist noch nicht gelaufen.
- Das Fenster bis 05.12.2026 ist kurz, und die Queue-Wartezeit beträgt ein bis zwei Tage: Ein Lauf von Grund auf lässt sich nicht iterieren.

## Was C zeigen müsste, bevor es Rechenzeit bekommt
1. G1 besteht (Interview-Planner besser als der aktuelle Planner auf den Konfliktarmen; unnötige Fragen < 10 %).
2. Ein vorab festgelegter, billigerer Pilot: Ein 100–300M-Modell, ≤ 20 Karten-h auf synthetischen Interview- und Plandaten trainiert, schlägt die *untrainierte* Basis derselben Größe deutlich und nähert sich dem A-Modell im Offline-Interviewtest.
3. Eine vorab festgelegte Evaluation, die das Wissensmodell (zwei verschiedene) und den Katalog (umbenannte Kategorien und Werkzeuge) ohne Neutraining variiert.

**Abbruch.** Ein Pilot, der die untrainierte Basis gleicher Größe nicht schlägt, oder ein A-Modell, das den aktuellen Planner nicht schlägt, beendet die Linie; das Negativergebnis wird berichtet.

## Verwandte Arbeiten (aus Abstracts gelesen, an den Volltexten zu prüfen)
Ausgelagertes Wissen in kleinen Modellen (Sprachmodell mit externer Faktendatenbank, ICLR 2026; Retrieval-unterstützte Schlussfolgerungsmodelle, die Wissensspeicher und Schlussfolgern trennen), RL mit Ausführungsbelohnungen für kleine werkzeugnutzende Modelle,
gelernte Abgabe von klein nach groß. Keine untersucht einen Planner, dessen Wissensquelle austauschbar ist und mit einer eigenen Infrastruktur abgeglichen wird.
