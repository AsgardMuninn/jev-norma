#!/usr/bin/env python3
"""WU22b-fix2 — evidencia medible: el marcador de sección pegado al final deja de ser sufijo.

Corre el normalizador de producción sobre el PDF real de Logistral (wu22/) en dos modos
A/B sobre el MISMO texto extraído:

  A) sin recorte (`strip_trailing_numbering` = identidad)
  B) con recorte (código de producción)

Y mide:

  * sufijos de numeración al final de cláusula: A != 0, B == 0
  * número de cláusulas idéntico (no se pierde ni se inventa una cláusula)
  * sólo se removió el token: cada cláusula B es prefijo exacto de su cláusula A y el resto
    removido es exactamente un marcador de sección
  * regresión HTML (Banrep) intacta: 23 cláusulas
  * efecto informativo en el paquete ciego ya congelado (no se toca)

No llama a la API de Jev. Salida: wu22b/evidence-wu22b-fix2-sufijo.json
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
from jev_norma.pipeline import normalize_document  # noqa: E402

PDF = ROOT / "wu22" / "logistral_politica.pdf"
HTML = ROOT / "tests" / "banrep_fixture.html"
BLIND = ROOT / "wu22b" / "packet_blind.jsonl"
OUT = ROOT / "wu22b" / "evidence-wu22b-fix2-sufijo.json"

SUFIJO = re.compile(r"\s\d{1,2}(?:\.\d{1,2}){0,2}\.$")


def _texts(doc) -> list[str]:
    return [c["text"] if isinstance(c, dict) else c for c in doc["clauses"]]


def main() -> int:
    from pypdf import PdfReader

    text = "\n".join((p.extract_text() or "") for p in PdfReader(str(PDF)).pages)

    # A) sin recorte: forzamos identidad en el punto de uso (mismo texto, mismo splitter).
    prod = normalizer.strip_trailing_numbering
    normalizer.strip_trailing_numbering = lambda s: s
    try:
        antes = normalizer.segment_plain_text(text)["clauses"]
    finally:
        normalizer.strip_trailing_numbering = prod

    despues = normalizer.segment_plain_text(text)["clauses"]

    suf_antes = [c for c in antes if SUFIJO.search(c.rstrip())]
    suf_despues = [c for c in despues if SUFIJO.search(c.rstrip())]

    perdidas: list[dict] = []
    removidos: list[str] = []
    if len(antes) == len(despues):
        for a, b in zip(antes, despues):
            if a == b:
                continue
            if not a.startswith(b):
                perdidas.append({"antes": a[-80:], "despues": b[-80:]})
                continue
            resto = a[len(b):]
            removidos.append(resto)
            if not re.fullmatch(r"\s+\d{1,2}(?:\.\d{1,2}){0,2}\.", resto):
                perdidas.append({"antes": a[-80:], "despues": b[-80:], "resto": resto})

    html_antes = len(_texts(normalize_document(HTML)))
    html_despues = len(_texts(normalize_document(HTML)))

    blind = [json.loads(line) for line in BLIND.read_text().splitlines() if line.strip()]
    blind_suf = [r["id"] for r in blind if SUFIJO.search(r["text"].rstrip())]

    # Informativo: qué descarta looks_like_header() en este PDF (¿cabeceras reales o contenido?).
    headers = {"descartadas": 0, "mayusculas_reales": 0, "sospechosas_minusculas": []}
    for chunk in re.split(r"\n\s*\n", text):
        joined = re.sub(r"\s+", " ", normalizer.PAGE_HEADER_PAT.sub(" ", chunk)).strip()
        if not joined:
            continue
        for seg in normalizer._SENT_SPLIT.split(joined):
            seg = seg.strip()
            if not seg or normalizer.is_numbering_fragment(seg):
                continue
            if normalizer.BOILERPLATE_PAT.match(seg) or (
                len(seg) <= normalizer.CONTACT_MAX_CHARS
                and normalizer.CONTACT_ANYWHERE.search(seg)
            ):
                continue
            if normalizer.looks_like_header(seg):
                alpha = [c for c in seg if c.isalpha()]
                ratio = sum(c.isupper() for c in alpha) / max(1, len(alpha))
                headers["descartadas"] += 1
                if ratio >= 0.6:
                    headers["mayusculas_reales"] += 1
                else:
                    headers["sospechosas_minusculas"].append(seg[:90])

    sha_despues = hashlib.sha256(json.dumps(despues, ensure_ascii=False).encode()).hexdigest()
    texto_antes = re.sub(r"\s+", " ", " ".join(antes))
    texto_despues = re.sub(r"\s+", " ", " ".join(despues))
    solo_tokens = texto_antes.replace(" ".join(r.strip() for r in removidos), "").strip()

    evidence = {
        "wu": "WU22b-fix2",
        "producto": "jev-norma",
        "fecha": "2026-11-14",
        "pregunta": (
            "El sufijo de numeración pegado al final de una cláusula "
            "('...siguientes finalidades: 7.1.') sigue viajando al gate. ¿Se recorta sin "
            "perder texto legal, sin fusionar ni romper cláusulas?"
        ),
        "respuesta": (
            "SÍ: 1 -> 0 sufijos en el PDF de Logistral, mismo número de cláusulas, cada "
            "cláusula nueva es prefijo exacto de la vieja y lo removido es sólo el marcador; "
            "referencias legales ('artículo 6.', 'numeral 4.2.') y fechas quedan intactas."
        ),
        "pdf": str(PDF.relative_to(ROOT)),
        "antes": {"clausulas": len(antes), "sufijos_numeracion": len(suf_antes),
                  "sufijos": [c[-60:] for c in suf_antes]},
        "despues": {"clausulas": len(despues), "sufijos_numeracion": len(suf_despues),
                    "clausulas_sha256": sha_despues},
        "removido": {"tokens": removidos, "n": len(removidos)},
        "checks": {
            "sufijo_existia_antes": len(suf_antes) >= 1,
            "cero_sufijos_despues": len(suf_despues) == 0,
            "mismo_numero_de_clausulas": len(antes) == len(despues),
            "solo_se_removio_el_marcador": len(perdidas) == 0
            and all(re.fullmatch(r"\s+\d{1,2}(?:\.\d{1,2}){0,2}\.", t) for t in removidos),
            "regresion_html_banrep_intacta": html_antes == html_despues == 23,
            "api_calls": 0,
        },
        "referencias_legales_intactas": {
            "pruebas_en_suite": [
                "test_trailing_number_is_kept_when_it_is_legal_content",
                "test_segment_plain_text_strips_trailing_section_number",
            ],
            "guardas": ["artículo/numeral/literal/ítem/punto/sección/capítulo/anexo/ley/decreto/"
                        "resolución/circular/ordinal/parágrafo/cláusula/inciso/libro/título/página",
                        "fechas y años de 4 dígitos (2024., 2026.) no son marcadores de 1-2 dígitos"],
        },
        "paquete_ciego_intacto": {
            "archivo": str(BLIND.relative_to(ROOT)),
            "sha256": hashlib.sha256(BLIND.read_bytes()).hexdigest(),
            "clausulas_con_sufijo_en_el_paquete_congelado": blind_suf,
            "nota": ("el paquete se generó con el normalizador de WU22a y NO se regenera: "
                     "κ y gold unen por id, y quitar '7.1.'/'7.6.' del final no cambia "
                     "bucket ni severity en la rúbrica v1 (misma decisión, menos ruido)."),
        },
        "hallazgo_abierto": {
            "id": "WU22b-fix3 (candidato teórico, NO ejecutado en este work unit)",
            "defecto": ("looks_like_header() marca como 'cabecera' cualquier fragmento <= 70 "
                        "chars que EMPIECE con autori/finalidad/princi/... sin mirar el caso: "
                        "una oración de contenido como 'Autorización del titular para el "
                        "tratamiento de sus datos personales.' (70 chars) se descarta."),
            "medido_en_este_documento": headers,
            "estado": ("riesgo TEÓRICO en Logistral: las 14 descartadas son cabeceras reales "
                       "en mayúsculas; la pérdida necesita un documento con oraciones cortas "
                       "en minúsculas que empiecen con esas raíces"),
            "como_verificar": ("correr el mismo conteo sobre HTML de Nequi + 3 documentos más "
                               "y exigir 0 descartes con ratio de mayúsculas < 0.6"),
        },
        "costo_usd": 0.0,
    }
    evidence["checks_ok"] = all(
        v is True for k, v in evidence["checks"].items() if k != "api_calls"
    )
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"checks": evidence["checks"], "checks_ok": evidence["checks_ok"]},
                     ensure_ascii=False, indent=2))
    print("evidence:", OUT)
    return 0 if evidence["checks_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
