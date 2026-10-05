# jev-norma

Triaje normativo LatAm con **Jev (System One)** como gate. Toma una política de
tratamiento de datos / términos en español (Ley 1581 de 2012, Colombia), normaliza las
cláusulas con heurística **0-IA** (sin palabras, determinista), y clasifica cada cláusula
con Jev en `choice [obligacion | riesgo | sin_accion]` + `confidence` calibrada, con una
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
documento (HTML/PDF) ──> cláusulas (heurística 0-IA) ──> gate Jev (choice+confidence)
                                                              │
                                              confianza < 0.8 ─┴─> cola de revisión humana
```

- **Normalizador 0-IA** (`jev_norma.normalizer`): extracción posicional de contenido
  (`<main>` > `<article>` > `<body>`, sin chrome nav/header/footer), `<h1..h6>` y líneas
  cortas en mayúsculas como cabeceras de sección, merge de `<p>`+`<li>`, descarte de
  boilerplate (NIT/dirección/www/©/email). Funciona con cualquier CMS (Webflow, WordPress,
  Drupal…). PDF con capa de texto vía `pypdf`.
- **Gate Jev** (`jev_norma.gate`): cada cláusula se embebe en la instrucción de su pregunta
  (self-contained) — regla de state engineering verificada empíricamente. Chunking para
  respetar el presupuesto de contexto (~32k) y el timeout.
- **Reporte fail-open** (`jev_norma.pipeline`): `choice` + `confidence` + `gate_humano`.

## Instalación

```bash
pip install jev-norma            # CLI + MCP (stdlib puro, 0-deps)
pip install jev-norma[pdf]       # + soporte PDF (pypdf)
```

Requiere `TYPESAFE_API_KEY` en `~/.hermes/.env` o en el entorno (leída del proceso,
nunca impresa).

## CLI

```bash
jev-norma --selftest                          # tests unitarios (sin API)
jev-norma politica.html --no-api              # solo normalización (sin gate)
jev-norma politica.html [--sample 12]         # normaliza + gate Jev real
jev-norma politica.pdf --output reporte.json
jev-norma --url https://…/politica.html       # fetch resiliente a WAF + normaliza + gate
```

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

## Costo medido (API real, 2026-09/10)

| Fuente | Cláusulas | Acierto | Costo | Latencia |
|---|---|---|---|---|
| Sintético (WU1) | 12 | 11/12 (0.917) | $0.00017 | 12.9s (2 chunks) |
| Nequi real (WU2) | 12 | 12/12 (1.0) | $0.00022 | 377 ms |
| Banrep real no-Webflow (WU4/WU5) | 23 | gate fail-open | $0.00019 | 411 ms |

## Licencia

MIT. Proyecto open-source — tracción/redistribución, no venta directa.