"""WU22b-fix3 (medición) — ¿looks_like_header() descarta CONTENIDO por su HEADER_PAT sin mirar el caso?

Precondición escrita en el checkpoint WU22b-fix2: medir sobre Nequi HTML + 3 documentos más y
sólo declarar defecto real si aparece >=1 descarte con ratio de mayúsculas < 0.6 (es decir,
clasificado como cabecera SÓLO por el prefijo case-insensitive, no por la forma).

Este script NO llama a ninguna API: puro normalizador + heurística de conteo.
Escribe wu22b/evidence-wu22b-fix3-headers.json.

Uso: python3 tools/evidence_wu22b_fix3_headers.py [--refetch]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jev_norma.fetch import fetch_html  # noqa: E402
from jev_norma.normalizer import (  # noqa: E402
    BLOCK_RE,
    HEADER_PAT,
    _is_boilerplate,
    _strip_html,
    extract_content,
    looks_like_header,
    normalize_clauses_general,
    segment_plain_text,
)

CORPUS_DIR = ROOT / "wu22b" / "corpus"
EVIDENCE = ROOT / "wu22b" / "evidence-wu22b-fix3-headers.json"

# "Nequi HTML + 3 documentos más" (precondición del checkpoint WU22b-fix2) + 2 anclas locales
# ya usadas en los WU anteriores, para no perder la referencia histórica (logistral: 14/14).
CORPUS = [
    {
        "id": "nequi-html",
        "kind": "html",
        "url": "https://www.nequi.com.co/legal/informacion/politica-para-el-tratamiento-de-datos-personales-de-nequi-s-a",
    },
    {
        "id": "cisa-html",
        "kind": "html",
        "url": "https://www.cisa.gov.co/contenido/sesion/la-entidad/quienes-somos/politica-de-tratamiento-de-datos-personales",
        "nota": (
            "SPA: el HTML servido no trae ni un <p> ni <main>/<article> (0 cláusulas). Queda en el corpus "
            "como punto NO evaluable para cabeceras: no hay bloques que clasificar, y el fallo de extracción "
            "en páginas renderizadas por JS es un hallazgo aparte, no este."
        ),
    },
    {
        "id": "bancolombia-html",
        "kind": "html",
        "url": "https://bancolombia.com/personas/documentos-legales/proteccion-datos/bancolombia-sa",
    },
    {
        "id": "mercadolibre-html",
        "kind": "html",
        "url": "https://www.mercadolibre.com.co/privacidad",
    },
    {"id": "logistral-pdf", "kind": "pdf", "path": "wu22/logistral_politica.pdf"},
    {"id": "banrep-html", "kind": "html", "path": "tests/banrep_fixture.html"},
]


def upper_ratio(txt: str) -> float:
    alpha = sum(c.isalpha() for c in txt)
    if not alpha:
        return 0.0
    return round(sum(c.isupper() for c in txt if c.isalpha()) / alpha, 3)


def load_doc(doc: dict, refetch: bool) -> tuple[str, str]:
    """Devuelve (texto_crudo, procedencia). Usa caché salvo --refetch."""
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    if doc["kind"] == "html":
        if "url" in doc:
            cache = CORPUS_DIR / f"{doc['id']}.html"
            if refetch or not cache.exists():
                cache.write_text(fetch_html(doc["url"]), encoding="utf-8")
            return cache.read_text(encoding="utf-8"), doc["url"]
        return (ROOT / doc["path"]).read_text(encoding="utf-8"), doc["path"]
    # pdf -> texto con pypdf (misma vía que el pipeline)
    from pypdf import PdfReader

    reader = PdfReader(str(ROOT / doc["path"]))
    return "\n\n".join((p.extract_text() or "") for p in reader.pages), doc["path"]


def scan_html(raw: str) -> dict:
    content = extract_content(raw)
    p_blocks = 0
    uppercase_headers: list[dict] = []
    pattern_only: list[dict] = []
    contenido_perdido: list[dict] = []
    for m in BLOCK_RE.finditer(content):
        tag, inner = m.group(1), m.group(2)
        txt = _strip_html(inner)
        if not txt or _is_boilerplate(txt):
            continue
        if tag.startswith("h") or tag != "p" or not looks_like_header(txt):
            continue
        p_blocks += 1
        rec = {
            "texto": txt[:160],
            "chars": len(txt),
            "ratio_mayusculas": upper_ratio(txt),
            "header_pat": bool(HEADER_PAT.match(txt)),
            "termina_en_punto": txt.rstrip()[-1:] in {".", "?", "!"},
        }
        if rec["ratio_mayusculas"] >= 0.6:
            uppercase_headers.append(rec)
        elif rec["header_pat"]:
            pattern_only.append(rec)
            # Un <p> standalone que es una ORACIÓN completa en minúsculas/title-case es contenido,
            # no cabecera: sólo lo descarta el prefijo case-insensitive.
            if rec["termina_en_punto"] and len(txt.split()) >= 4:
                contenido_perdido.append(rec)
    return {
        "n_p_blocks_cabecera": p_blocks,
        "cabeceras_por_mayusculas": len(uppercase_headers),
        "descartes_solo_por_patron": len(pattern_only),
        "ejemplos_descarte": pattern_only[:5],
        "contenido_perdido": len(contenido_perdido),
        "ejemplos_contenido_perdido": contenido_perdido[:5],
        "chars_contenido_descartado": sum(r["chars"] for r in contenido_perdido),
    }


def scan_plain(text: str) -> dict:
    lines = text.splitlines()
    uppercase_headers: list[dict] = []
    pattern_only: list[dict] = []
    contenido_perdido: list[dict] = []
    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line or len(line) > 70:
            continue
        if _is_boilerplate(line) or not looks_like_header(line):
            continue
        # ¿La línea es continuación de una oración? (hay texto antes en la misma línea, o la
        # línea anterior no cierra oración: el extractor PDF parte el párrafo y la cola de una
        # oración no es una cabecera de sección).
        prev = next((l.strip() for l in reversed(lines[:i]) if l.strip()), "")
        offset = raw_line.find(line)
        hay_texto_antes = offset > 0 and bool(raw_line[:offset].strip())
        continuacion = hay_texto_antes or not prev.endswith((".", ":", ";", "!", "?"))
        rec = {
            "texto": line[:160],
            "chars": len(line),
            "ratio_mayusculas": upper_ratio(line),
            "header_pat": bool(HEADER_PAT.match(line)),
            "continuacion_de_oracion": bool(continuacion),
            "previo": prev[-70:],
            "texto_antes_en_la_linea": hay_texto_antes,
        }
        if rec["ratio_mayusculas"] >= 0.6:
            uppercase_headers.append(rec)
        elif rec["header_pat"]:
            pattern_only.append(rec)
            if rec["continuacion_de_oracion"]:
                contenido_perdido.append(rec)
    return {
        "n_p_blocks_cabecera": len(uppercase_headers) + len(pattern_only),
        "cabeceras_por_mayusculas": len(uppercase_headers),
        "descartes_solo_por_patron": len(pattern_only),
        "ejemplos_descarte": pattern_only[:5],
        "contenido_perdido": len(contenido_perdido),
        "ejemplos_contenido_perdido": contenido_perdido[:5],
        "chars_contenido_descartado": sum(r["chars"] for r in contenido_perdido),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refetch", action="store_true")
    args = ap.parse_args()

    filas = []
    for doc in CORPUS:
        raw, procedencia = load_doc(doc, args.refetch)
        sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        scan = scan_html(raw) if doc["kind"] == "html" else scan_plain(raw)
        if doc["kind"] == "html":
            norm = normalize_clauses_general(raw)
        else:
            norm = segment_plain_text(raw)
        filas.append(
            {
                "id": doc["id"],
                "procedencia": procedencia,
                "sha256_fuente": sha,
                **({"nota": doc["nota"]} if "nota" in doc else {}),
                **scan,
                "clausulas": len(norm["clauses"]),
                "cabeceras_totales_normalizador": len(norm.get("headers", [])),
            }
        )
        print(
            f"{doc['id']:<20} cabeceras={scan['n_p_blocks_cabecera']:>3} "
            f"mayus={scan['cabeceras_por_mayusculas']:>3} solo_patron={scan['descartes_solo_por_patron']:>3} "
            f"perdidos={scan['contenido_perdido']:>2} chars={scan['chars_contenido_descartado']:>4} "
            f"clausulas={len(norm['clauses'])} "
            f"sha={sha[:12]}"
        )

    total_patron = sum(f["descartes_solo_por_patron"] for f in filas)
    total_perdido = sum(f["contenido_perdido"] for f in filas)
    total_chars = sum(f["chars_contenido_descartado"] for f in filas)
    docs_ok = [f["id"] for f in filas if f["clausulas"] > 0]
    perdido_html = sum(f["contenido_perdido"] for f in filas if f["id"].endswith("-html"))
    perdido_pdf = sum(f["contenido_perdido"] for f in filas if f["id"].endswith("-pdf"))
    defecto = total_perdido >= 1
    evidencia = {
        "wu": "WU22b-fix3-medicion",
        "fecha": subprocess.run(["date", "-Iseconds"], capture_output=True, text=True).stdout.strip(),
        "pregunta": (
            "looks_like_header() marca como cabecera cualquier fragmento <=70 chars que empiece con "
            "autori/finalidad/princi/... aunque esté en minúsculas (HEADER_PAT es case-insensitive) y lo "
            "descarta del gate: ¿pasa en documentos reales?"
        ),
        "precondicion_del_checkpoint": (
            "medir Nequi HTML + 3 documentos más; >=1 descarte con ratio de mayúsculas <0.6 => defecto real"
        ),
        "definicion_de_defecto": (
            "descartes_solo_por_patron = fragmento clasificado cabecera con ratio de mayúsculas <0.6, "
            "aceptado únicamente por HEADER_PAT (prefijo, IGNORECASE). contenido_perdido = subconjunto que "
            "además es texto de cuerpo: en HTML un <p> que es oración completa (termina en . ? ! y >=4 "
            "palabras); en texto plano una línea que continúa la oración anterior (texto antes en la misma "
            "línea o línea previa sin cierre . : ; ! ?)"
        ),
        "corpus": filas,
        "docs_con_clausulas": docs_ok,
        "docs_evaluables": [f["id"] for f in filas if f["clausulas"] > 0],
        "docs_no_evaluables": [f["id"] for f in filas if f["clausulas"] == 0],
        "totales": {
            "docs": len(filas),
            "descartes_solo_por_patron": total_patron,
            "contenido_perdido": total_perdido,
            "chars_contenido_descartado": total_chars,
            "contenido_perdido_html": perdido_html,
            "contenido_perdido_pdf": perdido_pdf,
            "cabeceras_mayusculas": sum(f["cabeceras_por_mayusculas"] for f in filas),
        },
        "veredicto": "DEFECTO_REAL" if defecto else "FALSO_POSITIVO",
        "alcance": (
            "ruta de texto plano/PDF (segment_plain_text): la cola de oración partida por el extractor se "
            "descarta como cabecera y la cláusula queda truncada a media frase. En HTML los 4 descartes "
            "medidos son subtítulos reales en title case ('Clientes y/o usuarios', 'Revocatoria de la "
            "autorización'): ahí el prefijo hace lo correcto y un guard por mayúsculas los rompería."
        ),
        "api_calls": 0,
        "costo_usd": 0.0,
    }
    EVIDENCE.write_text(json.dumps(evidencia, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"\nveredicto={evidencia['veredicto']} solo_patron={total_patron} perdidos={total_perdido} "
        f"(html={perdido_html} pdf={perdido_pdf}) chars={total_chars}"
    )
    print(f"evidencia -> {EVIDENCE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
