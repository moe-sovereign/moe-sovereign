# Claude Code Profile

Claude-Code-Profile steuern, wie der MoE-Sovereign-Orchestrator Anfragen von
Claude Code CLI, der VS-Code-Extension und anderen Anthropic-API-Clients
verarbeitet. Jedes Profil bildet auf einen eigenen Verarbeitungsmodus mit
spezifischen Kompromissen ab.

## Drei Referenzprofile

### Native (Direktes LLM)

```
Profil: cc-ref-native
Modus:  native
```

Die Anfrage wird **direkt** an ein einzelnes LLM (z.B. `phi4:14b-fp16`)
weitergeleitet, ohne jegliche MoE-Pipeline-Beteiligung. Das Modell verarbeitet
Tool Calls nativ.

- **Latenz**: 5-30 Sekunden
- **Einsatzzweck**: Schnelle Aenderungen, einfache Bugfixes, interaktives Coding
- **Kompromiss**: Keine Multi-Experten-Synthese, kein GraphRAG, keine Wissensakkumulation

### Reasoning (Thinking Node)

```
Profil: cc-ref-reasoning
Modus:  moe_reasoning
```

Die Anfrage durchlaeuft die MoE-Pipeline mit aktiviertem **Thinking Node**.
Das LLM fuehrt eine Chain-of-Thought-Analyse mit `<think>`-Bloecken durch,
bevor es die Antwort generiert.

- **Latenz**: 30-120 Sekunden
- **Einsatzzweck**: Architekturentscheidungen, komplexes Debugging, Code-Review
- **Kompromiss**: Tiefere Analyse, aber langsamer; kein paralleles Experten-Routing

### Orchestrated (Vollstaendige Pipeline)

```
Profil: cc-ref-orchestrated
Modus:  moe_orchestrated
```

Die vollstaendige MoE-Pipeline: **Planner** zerlegt die Aufgabe,
**parallele Experten-LLMs** bearbeiten Teilaufgaben, **Merger** synthetisiert
die Ergebnisse, **Judge** bewertet die Qualitaet, und **GraphRAG** akkumuliert
Wissen fuer kuenftige Anfragen.

- **Latenz**: 2-10 Minuten
- **Einsatzzweck**: Tiefgehende Recherche, domaenen-uebergreifende Synthese, wissensgestuetzte Analyse
- **Kompromiss**: Hoechste Qualitaet, aber unpraktisch fuer interaktives Coding

## Profilwahl

| Szenario | Empfohlenes Profil |
|----------|-------------------|
| Tippfehler oder Syntaxfehler beheben | Native |
| Einfaches Feature hinzufuegen | Native |
| Komplexe Race Condition debuggen | Reasoning |
| Architektur-Review | Reasoning |
| Sicherheitsaudit einer Codebasis | Orchestrated |
| Recherche + Implementierungsplan | Orchestrated |
| Multi-Datei-Refactoring mit Tests | Reasoning |

## Konfiguration

### Admin-UI

Unter **CC Profile** in der Admin-Navigation. Jedes Profil umfasst:

| Feld | Beschreibung |
|------|--------------|
| `name` | Anzeigename in Clients |
| `moe_mode` | `native`, `moe_reasoning` oder `moe_orchestrated` |
| `tool_model` | LLM fuer Tool-Ausfuehrung (z.B. `phi4:14b-fp16`) |
| `tool_endpoint` | Inferenz-Server-Knoten (z.B. `N04-RTX`) |
| `expert_template_id` | Experten-Vorlage fuer Orchestrated-Modus (optional) |
| `tool_max_tokens` | Max. Ausgabe-Tokens fuer Tool Calls |
| `reasoning_max_tokens` | Max. Tokens fuer Denkbloecke |
| `tool_choice` | `auto`, `required` oder `any` |

### User Portal

Benutzer koennen persoenliche Profile unter **My Templates** > **CC Profiles**
erstellen. Persoenliche Profile ueberschreiben administrativ zugewiesene Profile.

### API-Key-Binding

Jeder API-Key kann an ein bestimmtes CC-Profil gebunden werden:

1. Admin-UI > **Users** > Benutzer auswaehlen > **API Keys**
2. Im **CC Profile**-Dropdown das Profil fuer den Key setzen
3. Alle Anfragen mit diesem Key nutzen nun das gebundene Profil

### Client-Konfiguration

Claude Code auf die MoE-Sovereign-Instanz ausrichten:

```bash
# Claude Code CLI
export ANTHROPIC_BASE_URL=https://your-moe-instance.example.com
export ANTHROPIC_API_KEY=moe-sk-xxxxxxxx...

# VS Code settings.json
{
  "claude-code.apiEndpoint": "https://your-moe-instance.example.com",
  "claude-code.apiKey": "moe-sk-xxxxxxxx..."
}
```

## Innovator-Profile

Die **Innovator**-Profilfamilie (`cc-innovator-*`) richtet sich an
Claude-Code-Power-User, die die vollstaendige MoE-Pipeline mit
unterschiedlichen Qualitaets-/Geschwindigkeits-Kompromissen nutzen moechten.
Alle drei Profile verwenden den `moe_orchestrated`-Modus mit dedizierten
Experten-Vorlagen.

### Profilvergleich

| Profil | ID | Tool-Modell | Ziel-Latenz | Thinking | Max Tokens |
|--------|-----|-----------|-------------|----------|------------|
| Fast | `cc-innovator-fast` | `phi4:14b-fp16` | 30-90s | aus | 4.096 |
| Balanced | `cc-innovator-balanced` | `Qwen3-Coder-Next` | 2-5 Min | an | 8.192 / 16K Reasoning |
| Deep | `cc-innovator-deep` | `Qwen3-Coder-Next` | 5-15 Min | an | 8.192 / 32K Reasoning |

**Wesentliche Unterschiede:**

- **Fast** nutzt `tool_choice: required` mit leichtgewichtigen Modellen und
  `stream_think: false` fuer minimalen Overhead. Ideal fuer schnelle
  Iterationszyklen.
- **Balanced** aktiviert Denkbloecke und eskaliert ueber T2-Fallback zu
  Domaenen-Spezialistenmodellen. Guter Standard fuer die taegliche Entwicklung.
- **Deep** setzt die groessten verfuegbaren Modelle ein, inklusive einer
  Security-Analyst-Expertenkategorie und einem assertiven System-Prompt, der
  vollstaendigen, produktionsreifen Code verlangt. Ideal fuer Sicherheitsaudits,
  Architektur-Reviews und komplexes Refactoring.

### 5-Epochen-Benchmark-Ergebnisse

Ein kontrollierter Benchmark ueber 5 aufeinanderfolgende Durchlaeufe misst den
**kumulativen Effekt** der MoE-Wissens-Pipeline. Jede Epoche durchlaeuft
dieselbe Testsuite; GraphRAG akkumuliert Wissen aus frueheren Durchlaeufen,
verbessert die Genauigkeit und reduziert die Latenz.

| Epoche | Durchschn. Score | Durchschn. Latenz | Latenz vs. Epoche 1 |
|-------:|-----------------:|------------------:|--------------------:|
| 1 | 5,2 / 10 | 280s | Baseline |
| 2 | 6,4 / 10 | 125s | 0,45x |
| 3 | 7,1 / 10 | 72s | 0,26x |
| 4 | 7,8 / 10 | 45s | 0,16x |
| 5 | 8,1 / 10 | 30s | 0,11x |

**Kumulativer Effekt:** Ab Epoche 5 liefert das System **9,3-mal schnellere**
Antworten als in Epoche 1 bei gleichzeitiger Qualitaetssteigerung um 56 %.
Drei Mechanismen treiben diesen Effekt:

1. **GraphRAG-Kontextanreicherung** -- fruehere Synthese-Ergebnisse werden
   als `SYNTHESIS_INSIGHT`-Relationen gespeichert und in kuenftige
   Experten-Prompts injiziert
2. **L2-Plan-Cache** -- identische Aufgabenzerlegungen treffen den Valkey
   SHA-256-Plan-Cache und ueberspringen das Planner-LLM vollstaendig
3. **Modell-Waerme** -- Sticky Sessions und die Modell-Registry halten
   haeufig genutzte Modelle im VRAM geladen und eliminieren
   Cold-Start-Overhead

Der kumulative Effekt ist in den Epochen 1-3 am staerksten (steile Verbesserung)
und flacht ab Epoche 4-5 ab, wenn der Wissensgraph fuer die Testdomaene
gesaettigt ist.

---

## Referenzprofile herunterladen

Vorkonfigurierte Profil-JSONs stehen zum Download bereit:

- [`cc-ref-native.json`](https://github.com/moe-sovereign/moe-sovereign/blob/main/configs/cc_profiles/downloads/cc-ref-native.json) -- Direktes LLM
- [`cc-ref-reasoning.json`](https://github.com/moe-sovereign/moe-sovereign/blob/main/configs/cc_profiles/downloads/cc-ref-reasoning.json) -- Thinking Node
- [`cc-ref-orchestrated.json`](https://github.com/moe-sovereign/moe-sovereign/blob/main/configs/cc_profiles/downloads/cc-ref-orchestrated.json) -- Vollstaendige Pipeline

Die Platzhalter `<YOUR_OLLAMA_HOST>` und `<YOUR_TEMPLATE_ID>` durch die
tatsaechlichen Werte ersetzen.

## API-Kompatibilitaet

Der `/v1/messages`-Endpunkt ist vollstaendig kompatibel mit der Anthropic
Messages API. Claude Code CLI, das Anthropic Python SDK und VS-Code-Extensions
funktionieren ohne Anpassung -- einfach `ANTHROPIC_BASE_URL` auf die
MoE-Sovereign-Instanz zeigen lassen.

Unterstuetzte Features:

- Streaming-Antworten (SSE)
- Tool Use / Function Calling
- Mehrteilige Konversationen
- System-Prompts
- Denkbloecke (Reasoning-Modus)
- Bildeingaben (werden an vision-faehige Modelle weitergeleitet)
