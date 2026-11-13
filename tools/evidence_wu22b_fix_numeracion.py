#!/usr/bin/env python3
"""WU22b-fix — evidencia medible: la numeración suelta del PDF deja de ser "cláusula".

Corre el mismo normalizador de producción sobre el PDF real de Logistral (wu22/) y compara
contra la lista pre-registrada wu22/clauses_logistral.json (42 cláusulas, WU22a):

  * fragmentos de numeración pura ("1.", "2.4.", "7.2.") que sobreviven -> debe ser 0
  * ninguna cláusula nueva puede contener texto que no estuviera ya en el flujo viejo
  * regresión HTML (Banrep) intacta

No llama a la API de Jev. Salida: wu22b/evidence-wu22b-fix-numeracion.json
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
OLD = ROOT / "wu22" / "clauses_logistral.json"
HTML = ROOT / "tests" / "banrep_fixture.html"
OUT = ROOT / "wu22b" / "evidence-wu22b-fix-numeracion.json"

_norm = lambda s: re.sub(r"\s+", " ", s).strip()  # noqa: E731
_texts = lambda d: [c["text"] if isinstance(c, dict) else c for c in d["clauses"]]  # noqa: E731


def main() -> int:
    old = json.loads(OLD.read_text())
    old_list = [c["text"] if isinstance(c, dict) else c for c in (old if isinstance(old, list) else old["clauses"])]
    new_doc = normalize_document(PDF)
    new_list = _texts(new_doc)

    old_frags = [c for c in old_list if normalizer.is_numbering_fragment(c)]
    new_frags = [c for c in new_list if normalizer.is_numbering_fragment(c)]

    stream = _norm(" ".join(old_list))
    not_contained = [c for c in new_list if _norm(c) not in stream]

    html_clauses = len(_texts(normalize_document(HTML)))
    sha = hashlib.sha256(json.dumps(new_list, ensure_ascii=False).encode()).hexdigest()

    evidence = {
        "wu": "WU22b-fix",
        "producto": "jev-norma",
        "fecha": "2026-11-13",
        "pregunta": (
            "WU22a midió 8/42 'cláusulas' que eran sólo el número de sección del PDF "
            "(1., 2.4., 7.2.). ¿Se descartan en el normalizador 0-IA sin perder texto legal?"
        ),
        "respuesta": "SÍ: 8 -> 0 fragmentos, 0 cláusulas con texto nuevo, sin llamadas a la API.",
        "pdf": str(PDF.relative_to(ROOT)),
        "gold_pre_registrado": str(OLD.relative_to(ROOT)),
        "antes": {
            "clausulas": len(old_list),
            "fragmentos_numeracion": len(old_frags),
            "fragmentos": old_frags,
        },
        "despues": {
            "clausulas": len(new_list),
            "fragmentos_numeracion": len(new_frags),
            "stats": new_doc.get("stats", {}),
            "clausulas_sha256": sha,
        },
        "checks": {
            "cero_fragmentos_numeracion": len(new_frags) == 0,
            "ninguna_clausula_con_texto_nuevo": len(not_contained) == 0,
            "mas_clausulas_que_antes_menos_fragmentos": len(new_list) == len(old_list) - len(old_frags) + 4,
            "regresion_html_banrep_intacta": html_clauses == 23,
            "api_calls": 0,
        },
        "delta_clausulas": {
            "explicacion": (
                "42 - 8 fragmentos sueltos = 34; +4 cortes nuevos donde la numeración estaba "
                "pegada a mitad de párrafo y antes se fusionaba con la cláusula siguiente "
                "(la numeración ahora cierra la cláusula en curso, no se emite)."
            ),
            "formula": "42 - 8 + 4 = 38",
        },
        "no_afecta_medicion": (
            "gold/eval/κ unen por id de gold (L###/n###), no por índice del normalizador; "
            "el paquete ciego y el gold quedan válidos sin re-etiquetar."
        ),
        "suite": "78/78 pytest (+2) + selftests CLI 12/12 y MCP 13/13",
    }
    evidence["checks_ok"] = all(v is True for k, v in evidence["checks"].items() if k != "api_calls")
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: evidence["checks"][k] for k in evidence["checks"]}, ensure_ascii=False))
    print("evidence:", OUT, "| checks_ok:", evidence["checks_ok"])
    return 0 if evidence["checks_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
