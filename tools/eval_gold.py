#!/usr/bin/env python3
"""Evalúa el gate de jev-norma contra un gold set etiquetado (API real) y guarda evidencia.

Uso:
    python tools/eval_gold.py wu20/gold.jsonl --out wu20/evidence-wu20.json

El gold es JSONL con: id, text, bucket (obligacion|riesgo|sin_accion), severity (0|1|2),
source y rationale. El script corre el MISMO pipeline de producción (gate + build_report)
y calcula accuracy, matriz de confusión, MAE de severidad, ECE y cobertura del gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jev_norma.metrics import evaluate, rows_from_report  # noqa: E402
from jev_norma.pipeline import CHUNK_SIZE, GATE_UMBRAL, build_report, gate  # noqa: E402


def load_gold(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    required = {"id", "text", "bucket", "severity"}
    for r in rows:
        missing = required - set(r)
        if missing:
            sys.exit(f"error: fila {r.get('id')} sin campos {sorted(missing)}")
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Evalúa jev-norma contra un gold set (API real de Jev)")
    ap.add_argument("gold", help="gold.jsonl etiquetado")
    ap.add_argument("--out", default=None, help="ruta de la evidencia JSON")
    ap.add_argument("--model", default=None, help="modelo a pinear (default: JEV_MODEL o jev-latest)")
    args = ap.parse_args(argv)

    gold = load_gold(Path(args.gold))
    sample = {g["id"]: g["text"] for g in gold}
    gold_by_id = {g["id"]: {"bucket": g["bucket"], "severity": g["severity"]} for g in gold}

    prod = gate(sample, model=args.model)
    report = build_report(sample, prod["answers"], prod["in_tokens"], prod["latencia_ms"],
                          modelo=prod["modelo"])
    rows = rows_from_report(report["tabla"], gold_by_id)
    ev = evaluate(rows, threshold=GATE_UMBRAL)

    evidence = {
        "fecha": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "gold": {"ruta": str(args.gold), "n": len(gold),
                 "fuente": sorted({g.get("source", "?") for g in gold}),
                 "nota": "etiquetado por un solo anotador con rúbrica documentada; no es ground truth legal"},
        "modelo": prod["modelo"],
        "ejecucion": {"chunk_size": CHUNK_SIZE, "in_tokens": prod["in_tokens"],
                      "out_tokens": prod["out_tokens"], "latencia_ms": prod["latencia_ms"],
                      "costo_usd_estimado": round(prod["in_tokens"] * 42e-9, 6)},
        "evaluacion": ev,
        "filas": rows,
        "reporte_produccion": report,
    }

    out = json.dumps(evidence, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")

    print(f"n={ev['n']}  modelo={prod['modelo']}  tokens={prod['in_tokens']}  "
          f"latencia={prod['latencia_ms']}ms  costo=${evidence['ejecucion']['costo_usd_estimado']}")
    print(f"accuracy bucket = {ev['accuracy_bucket']:.3f}")
    for label, m in ev["por_clase"].items():
        prec = "n/a" if m["precision"] is None else f"{m['precision']:.2f}"
        rec = "n/a" if m["recall"] is None else f"{m['recall']:.2f}"
        print(f"  {label:<11} support={m['support']:<2} precision={prec} recall={rec}")
    print(f"severidad: exacta={ev['severidad_accuracy_exacta']:.3f}  MAE={ev['severidad_mae']:.2f}  "
          f"dentro_de_1={ev['severidad_dentro_de_1']:.3f}")
    print(f"ECE bucket={ev['ece_bucket']:.3f}  ECE severidad={ev['ece_severidad']:.3f}")
    g = ev["gate"]
    auto = "n/a" if g["accuracy_auto"] is None else f"{g['accuracy_auto']:.2f}"
    print(f"gate@{g['umbral']}: auto={g['auto']}/{g['n']} (cobertura {1 - g['fraccion_revision']:.2f})  "
          f"accuracy_auto={auto}  errores_auto={g['errors_auto']}")
    if args.out:
        print(f"(evidencia en {args.out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
