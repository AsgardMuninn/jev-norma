"""jev_norma.cli — interfaz de línea de comandos del paquete `jev-norma`.

Uso:
  jev-norma --selftest                          -> tests unitarios (sin API)
  jev-norma <doc.html|doc.pdf> --no-api         -> solo normalización (sin API)
  jev-norma <doc.html|doc.pdf> [--sample N]     -> normaliza + gate Jev real
  jev-norma <doc.html|doc.pdf> --output out.json
Requiere TYPESAFE_API_KEY en ~/.hermes/.env (leída del proceso, nunca impresa).
PDF requiere pypdf (pip install jev-norma[pdf]).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .gate import sanitize_clause
from .normalizer import MAX_CLAUSE_CHARS, segment_plain_text
from .pipeline import build_report, detect_input, gate, normalize_document, sample_indices
from .fetch import fetch_html


def _selftest() -> bool:
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("PASS" if cond else "FAIL"), name)
        ok = ok and cond

    # 1. detección de tipo
    check("detect html", detect_input(Path("p.html")) == "html")
    check("detect pdf", detect_input(Path("p.PDF")) == "pdf")
    check("detect desconocido", detect_input(Path("p.txt")) == "desconocido")

    # 2. segment_plain_text (PDF): párrafos divididos, boilerplate fuera
    tx = ("NIT: 901.633.276-0\n\n"
          "La sociedad tratará los datos conforme a la Ley 1581 y el Decreto 1377.\n\n"
          "Se solicitará la autorización previa, expresa e informada del titular. "
          "Se conservará la prueba de dichas autorizaciones.\n\n"
          "Carrera 48 # 18A-14, Medellín")
    r = segment_plain_text(tx)
    joined = " ".join(r["clauses"]).lower()
    check("pdf: párrafo tratara en clausulas", "tratará los datos conforme" in joined)
    check("pdf: parrafo autorizacion merged", "autorización previa" in joined and "conservará la prueba" in joined)
    check("pdf: boilerplate NIT fuera", "nit:" not in joined)
    check("pdf: boilerplate direccion fuera", "carrera 48" not in joined)
    check("pdf: n_clauses == 2", r["stats"]["n_clauses_final"] == 2)
    check("pdf: latencia < 50ms", r["stats"]["latencia_ms"] < 50)

    # 3. reporte: fail-open, umbral, conteo de buckets
    rep = build_report({"a": "xx", "b": "yy", "c": "zz"},
                       {"a": {"bucket": "obligacion", "confianza": 0.95},
                        "b": {"bucket": "riesgo", "confianza": 0.42},
                        "c": {"bucket": "sin_accion", "confianza": 0.90}},
                       1000, 25)
    check("reporte por_bucket", rep["por_bucket"] == {"obligacion": 1, "riesgo": 1, "sin_accion": 1})
    check("reporte fail-open marca b", rep["clausulas_a_revision"] == ["b"])
    check("reporte costo", rep["costo_usd_estimado"] == round(1000 * 42e-9, 6))
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="jev-norma: triaje normativo LatAm (gate System One)")
    ap.add_argument("doc", nargs="?", help="documento HTML o PDF")
    ap.add_argument("--url", default=None, help="URL de documento legal (fetcher resiliente a WAF)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--no-api", action="store_true", help="solo normalización (sin gate Jev)")
    ap.add_argument("--sample", type=int, default=12, help="cláusulas al gate (0 = todas)")
    ap.add_argument("--output", default=None, help="guardar reporte JSON en esta ruta")
    args = ap.parse_args(argv)

    if args.selftest:
        return 0 if _selftest() else 1
    if not args.doc and not args.url:
        ap.print_usage()
        return 2

    if args.url:
        if args.doc:
            print("error: usa --url O doc, no ambos", file=sys.stderr)
            return 2
        try:
            raw = fetch_html(args.url)
        except (ValueError, ConnectionError, RuntimeError) as e:
            print(f"error de fetch: {e}", file=sys.stderr)
            return 2
        path = Path(__file__).resolve().parent.parent / f".cache_{abs(hash(args.url))}.html"
        path.write_text(raw, encoding="utf-8")
    else:
        path = Path(args.doc)
        if not path.exists():
            print(f"error: no existe {path}", file=sys.stderr)
            return 2

    typ = detect_input(path)
    if typ == "desconocido":
        print("error: extensión no soportada (usa .html o .pdf)", file=sys.stderr)
        return 2

    norm = normalize_document(path)
    print(json.dumps({"entrada": str(path), "tipo": typ, "stats": norm["stats"],
                      "headers": norm.get("headers", []),
                      "primeras_clausulas": [sanitize_clause(c) for c in norm["clauses"][:6]]},
                     ensure_ascii=False, indent=2))
    if args.no_api:
        print("--no-api: normalización OK, gate Jev omitido.", file=sys.stderr)
        return 0

    clauses = norm["clauses"]
    indices = sample_indices(clauses, args.sample)
    sample = {f"{'d' if typ == 'pdf' else 'g'}{i:02d}": clauses[i] for i in indices}
    r = gate(sample)
    report = build_report(sample, r["answers"], r["in_tokens"], r["latencia_ms"])
    report["NOTA"] = "cláusulas normalizadas por heurística 0-IA (no curadas a mano)"
    out = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(out)
        print(f"(reporte guardado en {args.output})", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())