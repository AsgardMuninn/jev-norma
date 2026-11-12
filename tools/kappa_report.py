#!/usr/bin/env python3
"""Une dos etiquetados y reporta κ de Cohen, acuerdo por fuente y de severidad.

Uso:
    python tools/kappa_report.py wu22/gold_multi.jsonl wu22b/respuestas_anotador2.jsonl \\
        --out wu22b/evidence-wu22b-kappa.json

`A` es el etiquetado de referencia (gold, con `source` y `severity`); `B` es el del
segundo anotador (basta `id`, `bucket` y, si aplica, `severity`). El reporte une por
`id`, dice qué ids faltan y no inventa κ cuando es indefinido.

0 llamadas a la API. Puro salvo la lectura/escritura de archivos.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jev_norma.metrics import KAPPA_MIN, kappa_report  # noqa: E402


def load_labels(path: Path) -> list[dict]:
    """Lee JSONL o CSV (hoja de cálculo). En CSV exige `bucket` por fila: no se adivina."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() != ".csv":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        rows = []
        for raw in csv.DictReader(io.StringIO(text)):
            row = {(k or "").strip(): (v.strip() if isinstance(v, str) else v)
                   for k, v in raw.items()}
            if not row.get("id"):
                continue
            if not row.get("bucket"):
                sys.exit("error: fila %r sin bucket (¿anotación incompleta?)" % row["id"])
            if row.get("severity") in ("", None):
                row.pop("severity", None)
            else:
                try:
                    row["severity"] = int(row["severity"])
                except ValueError:
                    sys.exit("error: severidad no numérica en %r: %r" % (row["id"], row["severity"]))
            rows.append(row)
    if not rows:
        sys.exit("error: %s está vacío" % path)
    return rows


def _fmt(v) -> str:
    return "indefinido" if v is None else "%.3f" % v


def print_report(rep: dict) -> None:
    print("n: A=%d  B=%d  comunes=%d" % (rep["n_a"], rep["n_b"], rep["n_comun"]))
    if rep["solo_a"]:
        print("faltan en B (%d): %s" % (len(rep["solo_a"]), ", ".join(map(str, rep["solo_a"]))))
    if rep["solo_b"]:
        print("ids extra en B (%d): %s" % (len(rep["solo_b"]), ", ".join(map(str, rep["solo_b"]))))
    print("κ global: %s   acuerdo observado: %s   veredicto: %s (umbral %.2f)"
          % (_fmt(rep["kappa"]), _fmt(rep["acuerdo_observado"]), rep["veredicto"], rep["umbral"]))
    print("\npor fuente:")
    for src, d in rep["por_fuente"].items():
        print("  %-38s n=%-3d κ=%s  acuerdo=%s"
              % (src[:38], d["n"], _fmt(d["kappa"]), _fmt(d["acuerdo_observado"])))
    if rep["severidad"]:
        s = rep["severidad"]
        print("\nseveridad (n=%d): exacta=%.3f  MAE=%.2f  dentro_de_1=%.3f"
              % (s["n"], s["exacta"], s["mae"], s["dentro_de_1"]))
    print("\nmatriz de acuerdo (filas=A, columnas=B):")
    labels = list(rep["matriz"])
    print("            " + "".join("%12s" % L[:11] for L in labels))
    for la in labels:
        print("%-12s" % la[:11] + "".join("%12d" % rep["matriz"][la].get(lb, 0) for lb in labels))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="κ de Cohen entre gold y segundo anotador")
    ap.add_argument("a", help="etiquetado de referencia (gold)")
    ap.add_argument("b", help="etiquetado del segundo anotador")
    ap.add_argument("--out", default=None, help="ruta de la evidencia JSON")
    ap.add_argument("--umbral", type=float, default=KAPPA_MIN,
                    help="umbral pre-registrado de κ aceptable (default %.2f)" % KAPPA_MIN)
    args = ap.parse_args(argv)

    a, b = Path(args.a), Path(args.b)
    for p in (a, b):
        if not p.is_file():
            sys.exit("error: no existe %s" % p)
    rep = kappa_report(load_labels(a), load_labels(b), threshold=args.umbral)
    if not rep["n_comun"]:
        sys.exit("error: los dos etiquetados no comparten ningún id")

    evidence = {
        "fecha": datetime.now().astimezone().isoformat(timespec="seconds"),
        "gold": str(a),
        "anotador": str(b),
        **rep,
    }
    print_report(rep)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n(evidencia en %s)" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
