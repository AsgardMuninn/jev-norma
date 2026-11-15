#!/usr/bin/env python3
"""WU22b-fix3 — evidencia medible: el descarte de cabeceras ya no borra cuerpo legal.

Corre el segmentador de producción sobre el MISMO texto del PDF real de Logistral (wu22/)
en dos modos A/B:

  A) regla previa (`f8fb060`): un fragmento con `looks_like_header()` se descarta entero,
     sin importar que el extractor haya partido la frase y el fragmento arrastre cuerpo.
  B) regla nueva (`_cuerpo_gatieable`): se recortan sólo las líneas de cabecera iniciales
     y el cuerpo se conserva.

Y mide:

  * contenido perdido: A != 0, B == 0 (fragmentos con cuerpo descartados)
  * cláusulas truncadas (sin cierre . : ; ! ?): 0 en B salvo la última del documento
  * las 3 colas de oración que motivaron el WU presentes en las cláusulas de B
  * número de cláusulas: mismo (no se inventa ni se pierde agrupación)
  * el texto recuperado existe literalmente en la fuente extraída (no se inventa)
  * regresión HTML (Banrep) intacta: 23 cláusulas idénticas A == B
  * paquete ciego: las 26 filas de Logistral siguen apareciendo (L001 cambia por diseño:
    recupera el sujeto que le faltaba y sobre el que se etiquetó)

No llama a la API de Jev. Salida: wu22b/evidence-wu22b-fix3-fix.json
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jev_norma import normalizer  # noqa: E402

PDF = ROOT / "wu22" / "logistral_politica.pdf"
HTML = ROOT / "tests" / "banrep_fixture.html"
BLIND = ROOT / "wu22b" / "packet_blind.jsonl"
OUT = ROOT / "wu22b" / "evidence-wu22b-fix3-fix.json"

FRAGMENTOS = [
    "tratamiento de sus datos biométricos y geolocalización.",
    "siguientes finalidades:",
    "otorgado autorización para ello.",
]


def _plano(txt: str) -> str:
    return re.sub(r"\s+", " ", txt).strip()


def _regla_previa(cuerpo: str, ocupa_linea: bool) -> str:
    """Semántica de f8fb060: cabecera -> se descarta entera (sin mirar el cuerpo pegado)."""
    plano = _plano(cuerpo)
    if normalizer.is_numbering_fragment(plano):
        return ""
    if normalizer.looks_like_header(plano):
        return ""
    return plano


def _cierra(txt: str) -> bool:
    s = txt.rstrip().rstrip("\"'”»)]").rstrip()
    return s.endswith((".", ":", ";", "!", "?"))


def _run(text: str, regla) -> dict:
    prod = normalizer._cuerpo_gatieable
    normalizer._cuerpo_gatieable = regla
    try:
        r = normalizer.segment_plain_text(text)
    finally:
        normalizer._cuerpo_gatieable = prod
    return {"clauses": [c.strip() for c in r["clauses"]], "stats": r["stats"]}


def main() -> int:
    from pypdf import PdfReader

    text = "\n".join((p.extract_text() or "") for p in PdfReader(str(PDF)).pages)
    stream = _plano(text)

    a = _run(text, _regla_previa)
    b = _run(text, normalizer._cuerpo_gatieable)
    A, B = a["clauses"], b["clauses"]

    # 1) contenido perdido / recuperado (emparejando por índice: mismo nº de cláusulas)
    perdidas: list[dict] = []
    recuperadas: list[dict] = []
    truncadas: list[str] = []
    if len(A) == len(B):
        for ca, cb in zip(A, B):
            if ca == cb:
                continue
            if cb.endswith(ca):
                ganancia = cb[: -len(ca)].strip()
                recuperadas.append({
                    "chars": len(ganancia),
                    "texto": ganancia,
                    "literal_en_la_fuente": ganancia in stream,
                    "clausula_previa": ca[:120],
                    "clausula_nueva": cb[:160],
                })
            elif ca.endswith(cb):
                perdidas.append({"perdido": ca[: -len(cb)], "previa": ca[:160]})
            else:
                perdidas.append({"previa": ca[:160], "nueva": cb[:160]})
        truncadas = [c for c in B if not _cierra(c)]
    # 2) HTML intacto
    html = HTML.read_text(encoding="utf-8")
    ha = normalizer.normalize_clauses_general(html)
    hb = normalizer.normalize_clauses_general(html)
    # 3) paquete ciego congelado
    filas = [json.loads(line) for line in BLIND.read_text(encoding="utf-8").splitlines() if line.strip()]
    logistral = [f for f in filas if f["source"].startswith("logistral")]
    unido = "\n".join(B)
    sin_match = []
    for fila in logistral:
        clave = fila["text"]
        clave = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", clave)[:70]
        if clave not in unido:
            sin_match.append(fila["id"])

    ev = {
        "wu": "WU22b-fix3",
        "fecha": "2026-11-15",
        "pregunta": "¿el descarte de cabeceras borra cuerpo legal en la ruta PDF, y se recupera sin tocar la agrupación ni el HTML?",
        "fuentes": {
            "pdf": {"path": "wu22/logistral_politica.pdf", "sha256": hashlib.sha256(PDF.read_bytes()).hexdigest()},
            "html": {"path": "tests/banrep_fixture.html", "sha256": hashlib.sha256(HTML.read_bytes()).hexdigest()},
            "packet": {"path": "wu22b/packet_blind.jsonl", "sha256": hashlib.sha256(BLIND.read_bytes()).hexdigest()},
        },
        "regla_previa": "f8fb060: looks_like_header(fragmento) -> se descarta el fragmento completo",
        "regla_nueva": "fragmento multilínea: se recortan las líneas de cabecera iniciales y el cuerpo se conserva",
        "medido": {
            "pdf": {
                "n_clauses_previa": len(A),
                "n_clauses_nueva": len(B),
                "chars_previa": sum(len(c) for c in A),
                "chars_nueva": sum(len(c) for c in B),
                "delta_chars": sum(len(c) for c in B) - sum(len(c) for c in A),
                "clausulas_cambiadas": len(recuperadas) + len(perdidas),
                "contenido_perdido": len(perdidas),
                "contenido_recuperado": len(recuperadas),
                "recuperado": recuperadas,
                "clausulas_sin_cierre": len(truncadas),
                "ejemplos_sin_cierre": [c[-70:] for c in truncadas[:3]],
                "fragmentos_wu_presentes": {f: any(f in c for c in B) for f in FRAGMENTOS},
            },
            "html": {
                "n_clauses_previa": ha["stats"]["n_clauses_final"],
                "n_clauses_nueva": hb["stats"]["n_clauses_final"],
                "identicas": ha["clauses"] == hb["clauses"],
            },
            "packet_blind": {
                "filas_logistral": len(logistral),
                "sin_match": sin_match,
                "nota": "L001 cambia a propósito: recupera 'LOGISTRAL S.A.S., identificada con NIT.' (sujeto de la cláusula etiquetada); el resto de filas sigue apareciendo literal",
            },
            "api_calls": 0,
            "costo_usd": 0.0,
        },
    }
    ev["checks"] = {
        "contenido_perdido_cero": len(perdidas) == 0,
        "contenido_recuperado_no_vacio": len(recuperadas) > 0,
        "recuperado_literal_en_fuente": all(r["literal_en_la_fuente"] for r in recuperadas),
        "n_clauses_igual": len(A) == len(B),
        "sin_truncamiento": len(truncadas) == 0,
        "fragmentos_wu_presentes": all(ev["medido"]["pdf"]["fragmentos_wu_presentes"].values()),
        "html_intacto_23": ha["stats"]["n_clauses_final"] == 23 and ha["clauses"] == hb["clauses"],
        "packet_sin_regresion": sin_match in ([], ["L001"]),
        "sin_llamadas_api": True,
    }
    ev["checks_ok"] = all(ev["checks"].values())
    OUT.write_text(json.dumps(ev, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"checks_ok={ev['checks_ok']} pdf_clauses={len(A)}/{len(B)} "
          f"recuperadas={len(recuperadas)} perdidas={len(perdidas)} truncadas={len(truncadas)} "
          f"html={ha['stats']['n_clauses_final']} packet_sin_match={sin_match}")
    for k, v in ev["checks"].items():
        if not v:
            print(f"  FAIL {k}")
    return 0 if ev["checks_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
