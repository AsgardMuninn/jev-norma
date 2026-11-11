# jev-norma

Triaje normativo LatAm con **Jev (System One)** como gate. Toma una política de
tratamiento de datos / términos en español (Ley 1581 de 2012, Colombia), normaliza las
cláusulas con heurística **0-IA** (sin palabras, determinista), y clasifica cada cláusula
con Jev en `choice [obligacion | riesgo | sin_accion]` + `score` de severidad 0–2,
ambos con `confidence`, con una
**cola de revisión humana fail-open** (confianza < 0.8 → humano; nunca matar por Jev solo).

El núcleo es una **decisión tipada de Jev** (System 1), no generar texto. Jev corre
~40-200x más rápido y es órdenes de magnitud más barato que un LLM frontier
(~$0.00002/cláusula medido).

## ¿Qué resuelve?

El buyer es pymes / legal-tech LatAm que deben auditar políticas de privacidad y términos
ante la SIC (Colombia) / AEPD (España). Hoy eso es revisión legal manual cara. `jev-norma`
convierte el documento crudo en un tablero de obligaciones + banderas de riesgo, y solo
enruta a humano lo genuinamente ambiguo.

## Pipeline

```
documento (HTML/PDF) ──> cláusulas (heurística 0-IA) ──> gate Jev (choice+score+confidence)
                                                              │
                                              confianza < 0.8 ─┴─> cola de revisión humana
```

- **Normalizador 0-IA** (`jev_norma.normalizer`): extracción posicional de contenido
  (`<main>` > `<article>` > `<body>`, sin chrome nav/header/footer), `<h1..h6>` y líneas
  cortas en mayúsculas como cabeceras de sección, merge de `<p>`+`<li>`, descarte de
  boilerplate (NIT/dirección/www/©/email). Funciona con cualquier CMS (Webflow, WordPress,
  Drupal…). PDF con capa de texto vía `pypdf`; PDF escaneado vía `--ocr` (WU11).
- **Gate Jev** (`jev_norma.gate`): cada cláusula se embebe en sus preguntas
  (self-contained) — regla de state engineering verificada empíricamente. Chunking para
  respetar el presupuesto de contexto (~32k) y el timeout. En una request mixta por cláusula,
  `choice` clasifica el tipo y `score` ubica la severidad en una escala ordenada de 0 a 2.
- **Reporte fail-open** (`jev_norma.pipeline`): bucket, severidad y sus confianzas;
  confianza < 0.8 en cualquiera de las dos decisiones envía la cláusula a revisión humana.

## Instalación

```bash
pip install jev-norma            # CLI + MCP (stdlib puro, 0-deps)
pip install jev-norma[pdf]       # + soporte PDF (pypdf)
pip install jev-norma[ocr]       # + OCR PDFs escaneados (pymupdf + visión OpenRouter)
```

Requiere `TYPESAFE_API_KEY` en `~/.hermes/.env` o en el entorno (leída del proceso,
nunca impresa).

## CLI

```bash
jev-norma --selftest                          # tests unitarios (sin API)
jev-norma politica.html --no-api              # solo normalización (sin gate)
jev-norma politica.html [--sample 12]         # normaliza + gate Jev real
jev-norma politica.pdf --output reporte.json
jev-norma politica_escaneada.pdf --ocr --sample 6   # PDF escaneado: OCR visión + gate
jev-norma escaneada.pdf --ocr --ocr-workers 8   # OCR paralelo (WU12) + gate
jev-norma --url https://…/politica.html       # fetch resiliente a WAF + normaliza + gate
```

`--ocr` detecta PDFs sin capa de texto (escaneados/imagen), los transcribe página a
página con un VLM barato vía OpenRouter (`z-ai/glm-5.3-flash`, visión) y vuelve a
alimentar el pipeline normal sin más cambios; requiere `OPENROUTER_API_KEY` en
`~/.hermes/.env` y `pip install jev-norma[ocr]` (WU11).

`--ocr-workers N` (default 4) transcribe las páginas de un escaneado **en paralelo**
(la llamada a la visión es I/O-bound: ~6–19 s/página), preservando siempre el orden
de página en el texto. Medido sobre un escaneado real de 5 páginas: **3.9×** más rápido
(82.0 s → 21.2 s) con `N=5` (WU12). `--ocr-workers 1` reproduce el comportamiento serial.

`--url` descarga el documento con un fetcher resiliente (User-Agent de navegador real,
rotación de UA ante 403/5xx, reintento de fallos transitorios, declinación clara en 404)
— el patrón con el que se evita el bot-protection que responde 403/000 a `urllib` pelado
en bancos/SIC/superintendencias colombianas. Luego corre el mismo pipeline local.

## MCP server

`jev-norma-mcp` expone el pipeline como la tool `triage` sobre stdio (MCP 2024-11-05,
JSON-RPC 2.0, stdlib puro), para que un agente (Hermes/Claude) lo consuma sin UI:

```json
{"jsonrpc":"2.0","id":1,"method":"tools/call",
 "params":{"name":"triage","arguments":{"source":"politica.html","sample":12,"api":true}}}
```

## Tests

```bash
pip install -e .[dev]
pytest
```

## Evaluación y calibración (gold set)

El gate no se declara preciso por anécdota: hay un arnés que corre el pipeline de
producción contra un set etiquetado y mide accuracy, matriz de confusión, MAE de
severidad, ECE (calibración) y cobertura del gate.

```bash
python tools/eval_gold.py wu20/gold.jsonl --out wu20/evidence-wu20.json
```

Resultado medido el 2026-11-11 (modelo **jev-1.14.0**, 26 cláusulas de una política
real, 21,270 tokens in, 963 ms, $0.000893):

- accuracy bucket **0.846**; `obligacion` P 0.93 / R 0.81 · `riesgo` P 1.00 / R 0.75 · `sin_accion` P 0.67 / R 1.00.
- severidad: exacta 0.615 · MAE **0.42** · dentro de 1 nivel **0.962**.
- calibración: **ECE choice 0.023** (confianza fiable) vs **ECE severidad 0.198** (no usar para automatizar prioridades).
- gate a 0.8: **7/26 auto-resueltas (cobertura 0.27) con 0 errores**; el resto a humano.
- trade-off medido: si se ignora la confianza del eje severidad la cobertura sube a
  0.73 con 1 error (obligación→sin_accion). La curva, no un umbral dogmático, es el
  producto: el comprador elige el punto de operación.

Limitación declarada: **n=26, un solo anotador, una sola política**. No es ground
truth legal; es una prueba de conformidad con la rúbrica. Para mover el umbral global
hace falta un gold set multi-política con segundo anotador.

Pinnea la versión cuando un umbral dependa de la calibración (el alias `jev-latest`
se movió a 1.14 el 14-oct-2026):

```bash
JEV_MODEL=jev-1.14.0 jev-norma politica.pdf --sample 12
```

## Costo medido (API real, 2026-09/10)

| Fuente | Cláusulas | Acierto | Costo | Latencia |
|---|---|---|---|---|
| Sintético (WU1) | 12 | 11/12 (0.917) | $0.00017 | 12.9s (2 chunks) |
| Nequi real (WU2) | 12 | 12/12 (1.0) | $0.00022 | 377 ms |
| Banrep real no-Webflow (WU4/WU5) | 23 | gate fail-open | $0.00019 | 411 ms |
| Gold set Nequi (WU20) | 26 | 22/26 (0.846) | $0.000893 | 963 ms |

## Licencia

MIT. Proyecto open-source — tracción/redistribución, no venta directa.